"""Firestore-backed rate limiting.

Two things make this worth testing carefully:

1. Every function here is wrapped in a fail-open ``except Exception``. A bug that
   makes the limiter throw does not surface as an error — it silently disables
   rate limiting entirely, and the first symptom is an exhausted API quota.
   Several tests below therefore assert the *decision*, not just the absence of
   an exception.
2. ``check_global_rate_limit`` runs its logic inside a real Firestore
   transaction. The decorator is swapped for a pass-through here so the window
   arithmetic itself is exercised against the fake.
"""

import datetime
import time

import pytest

import rate_limiter
from rate_limiter import (
    GROQ_RPD,
    GROQ_RPM,
    IMAGE_USER_LIMIT,
    SARVAM_RPM,
    USER_COOLDOWN_SECONDS,
    VOICE_USER_LIMIT,
    RateLimitConfig,
    UserLimitConfig,
    check_global_rate_limit,
    check_user_cooldown,
    check_user_limit,
    record_rate_limit_hit,
)

RATE_DOC = "_system/rate_limits"


@pytest.fixture(autouse=True)
def passthrough_transactions(monkeypatch):
    """Replace google.cloud.firestore's `transactional` with a direct call.

    The real decorator drives a live backend session. Without this the fail-open
    handler swallows the resulting error and every test trivially "passes" with
    (True, 0.0), testing nothing.
    """
    monkeypatch.setattr(rate_limiter, "_firestore_transactional", lambda func: func)


class TestConfiguredLimits:
    """These numbers are the actual production quota headroom."""

    def test_groq_rpm_is_80_percent_of_the_free_tier(self):
        assert GROQ_RPM.max_requests == 24
        assert GROQ_RPM.window_seconds == 60

    def test_groq_rpd_uses_the_daily_counter_path(self):
        assert GROQ_RPD.window_seconds >= 3600

    def test_sarvam_rpm(self):
        assert SARVAM_RPM.max_requests == 48
        assert SARVAM_RPM.window_seconds == 60

    def test_feature_budgets_are_separate_documents(self):
        """Image uploads must not be able to consume the voice quota."""
        assert VOICE_USER_LIMIT.doc != IMAGE_USER_LIMIT.doc


class TestSlidingWindow:
    config = RateLimitConfig(
        name="Test API", max_requests=3, window_seconds=60, firestore_key="test_key"
    )

    def test_first_request_is_allowed_and_recorded(self, fake_db):
        allowed, retry_after = check_global_rate_limit(fake_db, self.config)

        assert (allowed, retry_after) == (True, 0.0)
        assert len(fake_db.docs[RATE_DOC]["test_key"]) == 1

    def test_requests_up_to_the_limit_are_allowed(self, fake_db):
        for i in range(3):
            allowed, _ = check_global_rate_limit(fake_db, self.config)
            assert allowed, f"request {i + 1} of 3 should have been allowed"

    def test_request_over_the_limit_is_blocked(self, fake_db):
        for _ in range(3):
            check_global_rate_limit(fake_db, self.config)

        allowed, retry_after = check_global_rate_limit(fake_db, self.config)
        assert allowed is False
        assert retry_after > 0

    def test_blocked_request_is_not_recorded(self, fake_db):
        """A rejected call must not push the window further out."""
        for _ in range(4):
            check_global_rate_limit(fake_db, self.config)
        assert len(fake_db.docs[RATE_DOC]["test_key"]) == 3

    def test_expired_timestamps_are_pruned(self, fake_db):
        stale = time.time() - 120  # older than the 60s window
        fake_db.seed(RATE_DOC, {"test_key": [stale, stale, stale]})

        allowed, _ = check_global_rate_limit(fake_db, self.config)

        assert allowed is True
        assert fake_db.docs[RATE_DOC]["test_key"] == pytest.approx([time.time()], abs=5), (
            "stale timestamps should have been dropped, not kept"
        )

    def test_retry_after_points_at_when_a_slot_opens(self, fake_db):
        oldest = time.time() - 20
        fake_db.seed(RATE_DOC, {"test_key": [oldest, time.time(), time.time()]})

        allowed, retry_after = check_global_rate_limit(fake_db, self.config)

        assert allowed is False
        # A slot frees 60s after the oldest request, i.e. ~40s from now.
        assert retry_after == pytest.approx(40, abs=2)

    def test_retry_after_never_below_half_a_second(self, fake_db):
        """Prevents a client hot-looping when the window is about to roll."""
        about_to_expire = time.time() - 59.99
        fake_db.seed(RATE_DOC, {"test_key": [about_to_expire] * 3})

        allowed, retry_after = check_global_rate_limit(fake_db, self.config)
        assert allowed is False
        assert retry_after >= 0.5

    def test_separate_apis_have_independent_windows(self, fake_db):
        other = RateLimitConfig(
            name="Other", max_requests=3, window_seconds=60, firestore_key="other_key"
        )
        for _ in range(3):
            check_global_rate_limit(fake_db, self.config)

        allowed, _ = check_global_rate_limit(fake_db, other)
        assert allowed is True, "exhausting one API must not block another"

    def test_fails_open_when_firestore_raises(self, fake_db, monkeypatch):
        def _boom(func):
            def _raise(*args, **kwargs):
                raise RuntimeError("firestore unavailable")

            return _raise

        monkeypatch.setattr(rate_limiter, "_firestore_transactional", _boom)

        # Infra trouble must not lock every shopkeeper out of the app.
        assert check_global_rate_limit(fake_db, self.config) == (True, 0.0)


class TestDailyCounter:
    config = RateLimitConfig(
        name="Daily", max_requests=2, window_seconds=86400, firestore_key="daily_key"
    )

    def test_uses_a_counter_not_a_timestamp_array(self, fake_db):
        check_global_rate_limit(fake_db, self.config)
        doc = fake_db.docs[RATE_DOC]

        assert doc["daily_key_count"] == 1
        assert doc["daily_key_date"] == datetime.date.today().isoformat()
        assert "daily_key" not in doc, "daily limits must not store timestamp arrays"

    def test_blocks_once_the_cap_is_reached(self, fake_db):
        for _ in range(2):
            assert check_global_rate_limit(fake_db, self.config)[0] is True

        allowed, retry_after = check_global_rate_limit(fake_db, self.config)
        assert allowed is False
        assert retry_after > 0, "should report seconds until midnight"

    def test_counter_resets_on_a_new_day(self, fake_db):
        yesterday = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
        fake_db.seed(RATE_DOC, {"daily_key_count": 999, "daily_key_date": yesterday})

        allowed, _ = check_global_rate_limit(fake_db, self.config)

        assert allowed is True
        assert fake_db.docs[RATE_DOC]["daily_key_count"] == 1


class TestPerUserLimits:
    config = UserLimitConfig(doc="test_meta", cooldown_seconds=2, daily_limit=5)
    meta_path = "users/u1/_meta/test_meta"

    def test_first_request_allowed_and_state_written(self, fake_db):
        allowed, retry_after = check_user_limit(fake_db, "u1", self.config)

        assert (allowed, retry_after) == (True, 0.0)
        doc = fake_db.docs[self.meta_path]
        assert doc["daily_count"] == 1
        assert doc["daily_date"] == datetime.date.today().isoformat()

    def test_immediate_second_request_hits_the_cooldown(self, fake_db):
        check_user_limit(fake_db, "u1", self.config)
        allowed, retry_after = check_user_limit(fake_db, "u1", self.config)

        assert allowed is False
        assert 0 < retry_after <= 2

    def test_request_after_the_cooldown_is_allowed(self, fake_db):
        fake_db.seed(
            self.meta_path,
            {
                "last_request_at": time.time() - 10,
                "daily_count": 1,
                "daily_date": datetime.date.today().isoformat(),
            },
        )
        allowed, _ = check_user_limit(fake_db, "u1", self.config)

        assert allowed is True
        assert fake_db.docs[self.meta_path]["daily_count"] == 2

    def test_daily_cap_blocks_further_requests(self, fake_db):
        fake_db.seed(
            self.meta_path,
            {
                "last_request_at": time.time() - 60,
                "daily_count": 5,
                "daily_date": datetime.date.today().isoformat(),
            },
        )
        allowed, retry_after = check_user_limit(fake_db, "u1", self.config)

        assert allowed is False
        assert retry_after > 0

    def test_daily_count_resets_on_a_new_day(self, fake_db):
        fake_db.seed(
            self.meta_path,
            {
                "last_request_at": time.time() - 60,
                "daily_count": 5,
                "daily_date": (datetime.date.today() - datetime.timedelta(days=1)).isoformat(),
            },
        )
        allowed, _ = check_user_limit(fake_db, "u1", self.config)

        assert allowed is True
        assert fake_db.docs[self.meta_path]["daily_count"] == 1

    def test_users_are_isolated_from_each_other(self, fake_db):
        check_user_limit(fake_db, "u1", self.config)
        allowed, _ = check_user_limit(fake_db, "u2", self.config)
        assert allowed is True, "one user's cooldown must not affect another's"

    def test_feature_budgets_are_isolated(self, fake_db):
        """Burning the voice budget must leave the image budget untouched."""
        check_user_limit(fake_db, "u1", VOICE_USER_LIMIT)
        allowed, _ = check_user_limit(fake_db, "u1", IMAGE_USER_LIMIT)
        assert allowed is True

    def test_fails_open_when_firestore_raises(self, fake_db, monkeypatch):
        def _boom(_name):
            raise RuntimeError("firestore unavailable")

        monkeypatch.setattr(fake_db, "collection", _boom)
        assert check_user_limit(fake_db, "u1", self.config) == (True, 0.0)


class TestCheckUserCooldown:
    def test_defaults_to_the_voice_budget(self, fake_db):
        check_user_cooldown(fake_db, "u1")
        assert f"users/u1/_meta/{VOICE_USER_LIMIT.doc}" in fake_db.docs

    def test_custom_cooldown_overrides_without_mutating_the_shared_config(self, fake_db):
        original = VOICE_USER_LIMIT.cooldown_seconds
        check_user_cooldown(fake_db, "u1", cooldown_seconds=30)

        assert VOICE_USER_LIMIT.cooldown_seconds == original == USER_COOLDOWN_SECONDS

        allowed, retry_after = check_user_cooldown(fake_db, "u1", cooldown_seconds=30)
        assert allowed is False
        assert retry_after > 2, "the 30s override should be in effect, not the 2s default"


class TestRecordRateLimitHit:
    def test_records_timestamp_and_increments_count(self, fake_db):
        record_rate_limit_hit(fake_db, GROQ_RPM)

        doc = fake_db.docs["_system/rate_limit_events"]
        assert doc["count_429_groq_rpm"] == 1
        assert doc["last_429_groq_rpm"]

    def test_repeated_hits_accumulate(self, fake_db):
        for _ in range(3):
            record_rate_limit_hit(fake_db, GROQ_RPM)
        assert fake_db.docs["_system/rate_limit_events"]["count_429_groq_rpm"] == 3

    def test_never_raises(self, fake_db, monkeypatch):
        """Monitoring failures must not break the request they are observing."""

        def _boom(_name):
            raise RuntimeError("firestore unavailable")

        monkeypatch.setattr(fake_db, "collection", _boom)
        record_rate_limit_hit(fake_db, GROQ_RPM)  # must not raise
