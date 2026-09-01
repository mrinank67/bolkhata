"""The /admin/* endpoints — what the support panel reads.

These are the routes that cannot be served by acting on an ordinary endpoint:
finding a shop from a phone number, the voice diagnostics, bill metadata, and
the audit trail. Firebase Auth is stubbed, since these are the only handlers in
the app that talk to it.
"""

import datetime
from types import SimpleNamespace
from unittest import mock

import pytest

from tests.conftest import ADMIN_UID, TARGET_UID


def _fb_user(uid=TARGET_UID, email="shop@example.com", phone="+919876543210", admin=False):
    return SimpleNamespace(
        uid=uid,
        email=email,
        phone_number=phone,
        display_name="Sharma Kirana",
        disabled=False,
        custom_claims={"admin": True} if admin else {},
        user_metadata=SimpleNamespace(
            creation_timestamp=1700000000000, last_sign_in_timestamp=None
        ),
    )


@pytest.fixture
def fb_auth():
    with mock.patch("routes.admin.fb_auth") as stub:
        stub.get_user.return_value = _fb_user()
        stub.get_user_by_email.return_value = _fb_user()
        stub.get_user_by_phone_number.return_value = _fb_user()
        stub.list_users.return_value = SimpleNamespace(users=[_fb_user()])
        yield stub


class TestUserLookup:
    def test_finds_a_shop_by_phone_number(self, admin_client, fake_db, fb_auth):
        body = admin_client.get("/admin/users", params={"q": "+919876543210"}).json()

        assert [u["uid"] for u in body["users"]] == [TARGET_UID]
        fb_auth.get_user_by_phone_number.assert_called_with("+919876543210")

    def test_a_bare_ten_digit_number_is_tried_with_the_india_code(
        self, admin_client, fake_db, fb_auth
    ):
        """The number a shopkeeper reads out over the phone has no +91 on it."""
        fb_auth.get_user_by_phone_number.side_effect = [Exception("no match"), _fb_user()]

        body = admin_client.get("/admin/users", params={"q": "9876543210"}).json()

        assert [u["uid"] for u in body["users"]] == [TARGET_UID]
        assert fb_auth.get_user_by_phone_number.call_args_list[-1][0][0] == "+919876543210"

    def test_finds_a_shop_by_email(self, admin_client, fake_db, fb_auth):
        body = admin_client.get("/admin/users", params={"q": "shop@example.com"}).json()
        assert [u["uid"] for u in body["users"]] == [TARGET_UID]

    def test_an_unknown_identifier_returns_no_users_rather_than_an_error(
        self, admin_client, fake_db, fb_auth
    ):
        fb_auth.get_user.side_effect = Exception("not found")
        fb_auth.get_user_by_email.side_effect = Exception("not found")
        fb_auth.get_user_by_phone_number.side_effect = Exception("not found")

        resp = admin_client.get("/admin/users", params={"q": "nobody@example.com"})

        assert resp.status_code == 200
        assert resp.json()["users"] == []

    def test_an_empty_query_lists_recent_signups(self, admin_client, fake_db, fb_auth):
        body = admin_client.get("/admin/users").json()
        assert len(body["users"]) == 1
        fb_auth.list_users.assert_called_once()


class TestOverview:
    def test_counts_each_collection(self, admin_client, fake_db, fb_auth):
        fake_db.seed(f"users/{TARGET_UID}", {"shop_name": "Sharma Kirana", "order_seq": 12})
        for i in range(3):
            fake_db.seed(f"users/{TARGET_UID}/orders/o{i}", {"item": "rice"})
        fake_db.seed(f"users/{TARGET_UID}/stock/rice", {"quantity": 5})

        body = admin_client.get(f"/admin/users/{TARGET_UID}/overview").json()

        assert body["counts"]["orders"] == 3
        assert body["counts"]["stock"] == 1
        assert body["counts"]["udhaar"] == 0
        assert body["settings"]["shop_name"] == "Sharma Kirana"
        assert body["settings"]["order_seq"] == 12

    def test_a_shop_with_no_settings_document_still_resolves(self, admin_client, fake_db, fb_auth):
        """A shop that has only ever used voice has no users/{uid} document."""
        fake_db.seed(f"users/{TARGET_UID}/orders/o1", {"item": "rice"})

        body = admin_client.get(f"/admin/users/{TARGET_UID}/overview").json()

        assert body["settings"]["shop_name"] == ""
        assert body["counts"]["orders"] == 1

    def test_reports_the_daily_voice_usage(self, admin_client, fake_db, fb_auth):
        fake_db.seed(
            f"users/{TARGET_UID}/_meta/voice_cooldown",
            {"daily_count": 17, "daily_date": "2026-09-01"},
        )
        body = admin_client.get(f"/admin/users/{TARGET_UID}/overview").json()
        assert body["voice_usage"]["daily_count"] == 17

    def test_an_unknown_uid_is_a_404(self, admin_client, fake_db, fb_auth):
        fb_auth.get_user.side_effect = Exception("not found")
        assert admin_client.get("/admin/users/nobody/overview").status_code == 404


class TestVoiceLogs:
    def _seed(self, fake_db, n=3, status="ok"):
        for i in range(n):
            fake_db.seed(
                f"users/{TARGET_UID}/voice_logs/v{i}",
                {
                    "status": status,
                    "transcript": f"utterance {i}",
                    "intent": '{"transactions": []}',
                    "stt_ms": 400,
                    "llm_ms": 700,
                    "timestamp": datetime.datetime(2026, 9, 1, 10, i, tzinfo=datetime.timezone.utc),
                },
            )

    def test_returns_the_transcript_and_intent(self, admin_client, fake_db, fb_auth):
        self._seed(fake_db, 1)

        body = admin_client.get(f"/admin/users/{TARGET_UID}/voice-logs").json()

        entry = body["voice_logs"][0]
        assert entry["transcript"] == "utterance 0"
        assert entry["intent"] == '{"transactions": []}'
        assert entry["stt_ms"] == 400

    def test_newest_first(self, admin_client, fake_db, fb_auth):
        self._seed(fake_db, 3)
        body = admin_client.get(f"/admin/users/{TARGET_UID}/voice-logs").json()
        assert [e["transcript"] for e in body["voice_logs"]] == [
            "utterance 2",
            "utterance 1",
            "utterance 0",
        ]

    def test_filters_by_status(self, admin_client, fake_db, fb_auth):
        self._seed(fake_db, 2, status="ok")
        fake_db.seed(
            f"users/{TARGET_UID}/voice_logs/bad",
            {
                "status": "stt_error",
                "error_detail": "RuntimeError: boom",
                "timestamp": datetime.datetime(2026, 9, 1, 11, tzinfo=datetime.timezone.utc),
            },
        )

        body = admin_client.get(
            f"/admin/users/{TARGET_UID}/voice-logs", params={"status": "stt_error"}
        ).json()

        assert len(body["voice_logs"]) == 1
        assert body["voice_logs"][0]["error_detail"] == "RuntimeError: boom"

    def test_respects_the_limit(self, admin_client, fake_db, fb_auth):
        self._seed(fake_db, 5)
        body = admin_client.get(f"/admin/users/{TARGET_UID}/voice-logs", params={"limit": 2}).json()
        assert len(body["voice_logs"]) == 2

    def test_a_non_admin_cannot_read_them(self, authed_client, fake_db):
        self._seed(fake_db, 1)
        resp = authed_client.get(f"/admin/users/{TARGET_UID}/voice-logs")
        assert resp.status_code == 403


class TestBills:
    def test_lists_bill_metadata_without_the_download_token(self, admin_client, fake_db, fb_auth):
        """A token plus a storage path reconstructs a public, never-expiring URL
        to the customer's bill. docs/security.md forbids exposing those."""
        fake_db.seed(
            f"users/{TARGET_UID}/bills/order-1",
            {
                "download_token": "secret-token",
                "storage_path": f"users/{TARGET_UID}/bills/order-1.pdf",
                "stale": True,
                "generated_at": datetime.datetime(2026, 8, 1, tzinfo=datetime.timezone.utc),
            },
        )

        body = admin_client.get(f"/admin/users/{TARGET_UID}/bills").json()

        assert body["bills"][0]["order_id"] == "order-1"
        assert body["bills"][0]["stale"] is True
        assert "download_token" not in body["bills"][0]
        assert "secret-token" not in str(body)


class TestAudit:
    def test_lists_the_trail_newest_first(self, admin_client, fake_db, fb_auth, seed_audit):
        body = admin_client.get("/admin/audit").json()
        assert [e["path"] for e in body["audit"]] == ["/orders", "/ledger/customers"]
        assert body["audit"][0]["admin_uid"] == ADMIN_UID

    def test_filters_by_the_shop_that_was_acted_on(
        self, admin_client, fake_db, fb_auth, seed_audit
    ):
        body = admin_client.get("/admin/audit", params={"acting_uid": "other-shop"}).json()
        assert [e["path"] for e in body["audit"]] == ["/ledger/customers"]


@pytest.fixture
def seed_audit(fake_db):
    fake_db.seed(
        "admin_audit/a1",
        {
            "admin_uid": ADMIN_UID,
            "acting_uid": "other-shop",
            "method": "GET",
            "path": "/ledger/customers",
            "status_code": 200,
            "at": datetime.datetime(2026, 9, 1, 9, tzinfo=datetime.timezone.utc),
        },
    )
    fake_db.seed(
        "admin_audit/a2",
        {
            "admin_uid": ADMIN_UID,
            "acting_uid": TARGET_UID,
            "method": "GET",
            "path": "/orders",
            "status_code": 200,
            "at": datetime.datetime(2026, 9, 1, 10, tzinfo=datetime.timezone.utc),
        },
    )
    return fake_db
