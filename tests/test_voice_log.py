"""users/{uid}/voice_logs — the support panel's only window into the pipeline.

The point of this file is the failure cases. A log that only records successful
requests is worthless for support, because the request being complained about is
by definition the one that went wrong. So there is a test per exit point in
routes/process_voice: rate limits, both audio guards, an STT crash, silence, an
LLM crash, and the happy path.

Also asserted: no audio is ever stored, and a broken log write does not take the
shopkeeper's request down with it.
"""

import datetime
import json
from unittest import mock

import pytest

import rate_limiter
from tests.conftest import TEST_UID
from voice_log import VOICE_LOG_RETENTION_DAYS, build_voice_log

AUDIO = {"audio": ("clip.webm", b"\x00\x01\x02\x03" * 64, "audio/webm")}
TINY_AUDIO = {"audio": ("clip.webm", b"\x00" * 50, "audio/webm")}
HUGE_AUDIO = {"audio": ("clip.webm", b"\x00" * (2 * 1024 * 1024 + 1), "audio/webm")}


@pytest.fixture(autouse=True)
def passthrough_transactions(monkeypatch):
    monkeypatch.setattr(rate_limiter, "_firestore_transactional", lambda func: func)


@pytest.fixture
def sarvam(monkeypatch):
    stub = mock.MagicMock()
    stub.status_code = 200
    stub.transcript = "do kilo chawal Ramesh ko"
    stub.json.side_effect = lambda: {"transcript": stub.transcript}
    stub.raise_for_status.return_value = None

    post = mock.MagicMock(return_value=stub)
    monkeypatch.setattr("routes.voice.requests.post", post)
    stub.post = post
    return stub


@pytest.fixture
def groq(monkeypatch):
    client = mock.MagicMock()
    holder = mock.MagicMock()
    holder.intent = {"transactions": []}

    def _create(**kwargs):
        completion = mock.MagicMock()
        completion.choices[0].message.content = json.dumps(holder.intent)
        return completion

    client.chat.completions.create.side_effect = _create
    holder.client = client
    monkeypatch.setattr("routes.voice._get_groq_client", lambda: client)
    return holder


def logs(fake_db, uid: str = TEST_UID) -> list[dict]:
    prefix = f"users/{uid}/voice_logs/"
    return [v for k, v in fake_db.docs.items() if k.startswith(prefix)]


def one_log(fake_db, uid: str = TEST_UID) -> dict:
    found = logs(fake_db, uid)
    assert len(found) == 1, f"expected exactly one voice log, got {len(found)}"
    return found[0]


class TestEveryOutcomeIsLogged:
    """One test per exit point in the handler. These are the support cases."""

    def test_success(self, authed_client, fake_db, sarvam, groq):
        authed_client.post("/process_voice", files=AUDIO)

        entry = one_log(fake_db)
        assert entry["status"] == "ok"
        assert entry["transcript"] == "do kilo chawal Ramesh ko"
        assert json.loads(entry["intent"]) == {"transactions": []}
        assert entry["stt_model"] == "saaras:v3"
        assert entry["llm_model"] == "openai/gpt-oss-20b"

    def test_user_rate_limit(self, authed_client, fake_db, sarvam, groq):
        authed_client.post("/process_voice", files=AUDIO)
        fake_db.docs = {k: v for k, v in fake_db.docs.items() if "/voice_logs/" not in k}

        resp = authed_client.post("/process_voice", files=AUDIO)

        assert resp.status_code == 429
        entry = one_log(fake_db)
        assert entry["status"] == "rate_limited"
        assert "cooldown" in entry["error_detail"]

    def test_global_quota(self, authed_client, fake_db, sarvam, groq, monkeypatch):
        monkeypatch.setattr(
            "routes.voice.check_global_rate_limit", lambda db, config: (False, 30.0)
        )
        authed_client.post("/process_voice", files=AUDIO)

        entry = one_log(fake_db)
        assert entry["status"] == "rate_limited"
        assert "sarvam" in entry["error_detail"]

    def test_audio_too_short(self, authed_client, fake_db, sarvam, groq):
        authed_client.post("/process_voice", files=TINY_AUDIO)

        entry = one_log(fake_db)
        assert entry["status"] == "audio_too_short"
        assert entry["audio_size"] == 50
        # Never reached Sarvam, so no transcript — but the size is what explains
        # a shopkeeper whose button presses are too quick.
        assert "transcript" not in entry

    def test_audio_too_long(self, authed_client, fake_db, sarvam, groq):
        authed_client.post("/process_voice", files=HUGE_AUDIO)

        entry = one_log(fake_db)
        assert entry["status"] == "audio_too_long"
        assert entry["audio_size"] == 2 * 1024 * 1024 + 1

    def test_stt_crash_records_the_reason_the_client_never_sees(
        self, authed_client, fake_db, sarvam, groq
    ):
        sarvam.post.side_effect = RuntimeError("sarvam exploded")

        resp = authed_client.post("/process_voice", files=AUDIO)

        assert resp.status_code == 500
        assert "exploded" not in resp.text  # not leaked to the shopkeeper
        entry = one_log(fake_db)
        assert entry["status"] == "stt_error"
        assert "sarvam exploded" in entry["error_detail"]  # but kept for support

    def test_silence(self, authed_client, fake_db, sarvam, groq):
        sarvam.transcript = "   "

        authed_client.post("/process_voice", files=AUDIO)

        entry = one_log(fake_db)
        assert entry["status"] == "stt_empty"
        assert entry["audio_size"] == len(AUDIO["audio"][1])

    def test_llm_crash_keeps_the_transcript(self, authed_client, fake_db, sarvam, groq):
        """The transcript is what separates 'STT misheard' from 'the LLM failed'."""
        groq.client.chat.completions.create.side_effect = RuntimeError("groq exploded")

        resp = authed_client.post("/process_voice", files=AUDIO)

        assert resp.status_code == 500
        entry = one_log(fake_db)
        assert entry["status"] == "llm_error"
        assert entry["transcript"] == "do kilo chawal Ramesh ko"
        assert "groq exploded" in entry["error_detail"]

    def test_resolve_round_trip(self, authed_client, fake_db):
        authed_client.post(
            "/voice/resolve",
            json={"transaction": {"item": "rice"}, "selected_modifier": "delhi"},
        )

        entry = one_log(fake_db)
        assert entry["status"] == "resolve"
        assert entry["selected_modifier"] == "delhi"


class TestWhatIsAndIsNotStored:
    def test_no_audio_is_stored(self, authed_client, fake_db, sarvam, groq):
        authed_client.post("/process_voice", files=AUDIO)

        entry = one_log(fake_db)
        raw = AUDIO["audio"][1]
        assert not any(v == raw for v in entry.values())
        # Size and type, yes; the bytes themselves, never.
        assert entry["audio_size"] == len(raw)
        assert entry["audio_mime"] == "audio/webm"

    def test_the_injected_context_is_recorded(self, authed_client, fake_db, sarvam, groq):
        """The first thing to check when an utterance lands on the wrong customer."""
        fake_db.seed(
            f"users/{TEST_UID}/orders/recent",
            {
                "customer_name": "ramesh",
                "customer_modifier": "delhi",
                "order_id": "order-1",
                "timestamp": datetime.datetime.now(datetime.timezone.utc),
            },
        )

        authed_client.post("/process_voice", files=AUDIO)

        entry = one_log(fake_db)
        assert entry["recent_customer"] == "ramesh"
        assert entry["recent_modifier"] == "delhi"
        assert entry["recent_order_id"] == "order-1"

    def test_timings_are_recorded(self, authed_client, fake_db, sarvam, groq):
        authed_client.post("/process_voice", files=AUDIO)

        entry = one_log(fake_db)
        for field in ("stt_ms", "llm_ms", "db_ms", "total_ms"):
            assert isinstance(entry[field], int), field

    def test_history_is_still_written_separately(self, authed_client, fake_db, sarvam, groq):
        """voice_logs supplements the user-facing history; it does not replace it."""
        groq.intent = {"transactions": [{"target": "stock", "operation": "add", "item": "rice"}]}

        authed_client.post("/process_voice", files=AUDIO)

        assert fake_db.paths_under(f"users/{TEST_UID}/history")
        assert logs(fake_db)

    def test_clearing_history_leaves_the_voice_logs(self, authed_client, fake_db, sarvam, groq):
        """A shopkeeper tidying their history must not destroy the diagnostics
        for the problem they are about to call about."""
        authed_client.post("/process_voice", files=AUDIO)
        assert logs(fake_db)

        authed_client.delete("/history")

        assert not fake_db.paths_under(f"users/{TEST_UID}/history")
        assert logs(fake_db)


class TestRetention:
    def test_expiry_is_thirty_days_out(self):
        entry = build_voice_log("ok")
        delta = entry["expires_at"] - datetime.datetime.now(datetime.timezone.utc)
        assert abs(delta.days - VOICE_LOG_RETENTION_DAYS) <= 1

    def test_a_giant_transcript_is_truncated_rather_than_dropped(self):
        """Firestore rejects documents over 1 MiB; losing the log of a runaway
        response would lose the record of the very request that misbehaved."""
        entry = build_voice_log("ok", transcript="x" * 50_000)
        assert len(entry["transcript"]) < 5_000
        assert "truncated" in entry["transcript"]

    def test_empty_fields_are_omitted_rather_than_stored_as_none(self):
        entry = build_voice_log("ok", recent_customer=None, stt_ms=12)
        assert "recent_customer" not in entry
        assert entry["stt_ms"] == 12


class TestLoggingNeverBreaksTheRequest:
    def test_a_failing_log_write_still_returns_success(self, authed_client, fake_db, sarvam, groq):
        real_collection = fake_db.collection

        def explode(name):
            ref = real_collection(name)
            if name == "users":
                ref = mock.MagicMock(wraps=ref)
                original_document = real_collection("users").document

                def document(uid):
                    doc = mock.MagicMock(wraps=original_document(uid))

                    def collection(sub):
                        if sub == "voice_logs":
                            raise RuntimeError("firestore is down")
                        return original_document(uid).collection(sub)

                    doc.collection.side_effect = collection
                    return doc

                ref.document.side_effect = document
            return ref

        with mock.patch.object(fake_db, "collection", side_effect=explode):
            resp = authed_client.post("/process_voice", files=AUDIO)

        assert resp.status_code == 200
        assert resp.json()["status"] == "success"
