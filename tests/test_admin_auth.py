"""The impersonation seam: who may act as another shop, and who may not.

Every ordinary endpoint now accepts an X-Acting-Uid header, which redirects the
whole request at a different shop's Firestore subtree. That is the single most
dangerous header in the app — getting it wrong exposes every shopkeeper's ledger
to every other one — so the rules are asserted here rather than left to review:

  * no header               -> the caller's own uid, exactly as before
  * header, no admin claim  -> 403, and the target is left untouched
  * header, admin claim     -> reads and writes land on the target
  * the voice endpoints     -> ignore the header entirely
"""

from unittest import mock

import pytest

from tests.conftest import ADMIN_UID, TARGET_UID, TEST_UID


@pytest.fixture
def seeded(fake_db):
    """One order line in each of two different shops."""
    fake_db.seed(
        f"users/{TEST_UID}/orders/own-line",
        {
            "customer_name": "own",
            "item": "rice",
            "quantity": 1,
            "price": 10,
            "amount": 10,
            "order_id": "own-order",
            "order_no": 1,
            "unit": "",
        },
    )
    fake_db.seed(
        f"users/{TARGET_UID}/orders/target-line",
        {
            "customer_name": "target",
            "item": "atta",
            "quantity": 2,
            "price": 20,
            "amount": 40,
            "order_id": "target-order",
            "order_no": 1,
            "unit": "",
        },
    )
    return fake_db


class TestWithoutTheHeader:
    def test_read_returns_the_callers_own_shop(self, authed_client, seeded):
        body = authed_client.get("/orders").json()
        customers = [o["customer_name"] for o in body["orders"]]
        assert customers == ["own"]

    def test_write_lands_in_the_callers_own_shop(self, authed_client, seeded):
        resp = authed_client.put("/orders/item/own-line", json={"quantity": 9})
        assert resp.status_code == 200
        assert seeded.docs[f"users/{TEST_UID}/orders/own-line"]["quantity"] == 9


class TestNonAdminIsRefused:
    """A plain shopkeeper who discovers the header gets nothing from it."""

    def test_read_is_forbidden(self, authed_client, seeded):
        resp = authed_client.get("/orders", headers={"X-Acting-Uid": TARGET_UID})
        assert resp.status_code == 403

    def test_write_is_forbidden_and_changes_nothing(self, authed_client, seeded):
        before = dict(seeded.docs[f"users/{TARGET_UID}/orders/target-line"])
        resp = authed_client.put(
            "/orders/item/target-line",
            json={"quantity": 999},
            headers={"X-Acting-Uid": TARGET_UID},
        )
        assert resp.status_code == 403
        assert seeded.docs[f"users/{TARGET_UID}/orders/target-line"] == before

    def test_refusal_is_not_a_silent_fallback_to_the_callers_own_shop(self, authed_client, seeded):
        """The caller's own identically-named record must not be edited instead."""
        resp = authed_client.delete("/orders/item/own-line", headers={"X-Acting-Uid": TARGET_UID})
        assert resp.status_code == 403
        assert f"users/{TEST_UID}/orders/own-line" in seeded.docs

    def test_an_empty_header_is_treated_as_absent(self, authed_client, seeded):
        """A client that always sends the header, empty when idle, still works."""
        resp = authed_client.get("/orders", headers={"X-Acting-Uid": ""})
        assert resp.status_code == 200
        assert [o["customer_name"] for o in resp.json()["orders"]] == ["own"]


class TestAdminActsOnTheTarget:
    def test_read_returns_the_targets_shop(self, admin_client, seeded):
        body = admin_client.get("/orders", headers={"X-Acting-Uid": TARGET_UID}).json()
        assert [o["customer_name"] for o in body["orders"]] == ["target"]

    def test_write_lands_on_the_target_not_the_admin(self, admin_client, seeded):
        resp = admin_client.put(
            "/orders/item/target-line",
            json={"quantity": 7},
            headers={"X-Acting-Uid": TARGET_UID},
        )
        assert resp.status_code == 200
        assert seeded.docs[f"users/{TARGET_UID}/orders/target-line"]["quantity"] == 7
        assert not seeded.paths_under(f"users/{ADMIN_UID}")

    def test_an_admin_without_the_header_still_sees_only_their_own_shop(self, admin_client, seeded):
        """Being an admin is not itself impersonation — it must be asked for."""
        body = admin_client.get("/orders").json()
        assert body["orders"] == []

    def test_ledger_and_suppliers_reach_the_target_too(self, admin_client, fake_db):
        fake_db.seed(
            f"users/{TARGET_UID}/udhaar/e1",
            {
                "customer_name": "ramesh",
                "customer_modifier": "",
                "item": "oil",
                "quantity": 1,
                "amount": 50,
            },
        )
        body = admin_client.get("/ledger/customers", headers={"X-Acting-Uid": TARGET_UID}).json()
        assert body["total_due"] == 50


class TestVoiceIgnoresTheHeader:
    """Voice must never be impersonable.

    Two reasons, both load-bearing: an admin must not be able to inject a
    transaction into a shop's books as though the shopkeeper spoke it, and
    /process_voice spends the *target's* per-user Sarvam and Groq quota.
    """

    def test_process_voice_uses_the_callers_own_uid(self, admin_client, fake_db):
        with mock.patch("routes.voice.check_user_cooldown") as cooldown:
            cooldown.return_value = (False, 5)
            resp = admin_client.post(
                "/process_voice",
                files={"audio": ("clip.webm", b"\x00" * 200, "audio/webm")},
                headers={"X-Acting-Uid": TARGET_UID},
            )

        assert resp.status_code == 429
        # The cooldown — the first thing the handler does with a uid — was
        # charged to the admin, not to the shop they are looking at.
        assert cooldown.call_args[0][1] == ADMIN_UID

    def test_voice_resolve_uses_the_callers_own_uid(self, admin_client, fake_db):
        resp = admin_client.post(
            "/voice/resolve",
            json={"transaction": {}, "selected_modifier": ""},
            headers={"X-Acting-Uid": TARGET_UID},
        )
        assert resp.status_code == 200
        assert not fake_db.paths_under(f"users/{TARGET_UID}")


class TestAdminOnlyRoutes:
    def test_admin_routes_reject_a_non_admin(self, authed_client, fake_db):
        for path in ("/admin/me", "/admin/users", "/admin/audit"):
            assert authed_client.get(path).status_code == 403, path

    def test_admin_me_confirms_the_claim(self, admin_client, fake_db):
        body = admin_client.get("/admin/me").json()
        assert body == {"uid": ADMIN_UID, "email": "", "is_admin": True}


class TestAuditTrail:
    def _audit_rows(self, fake_db):
        return [v for k, v in fake_db.docs.items() if k.startswith("admin_audit/")]

    def test_an_impersonated_write_is_recorded(self, admin_client, seeded):
        admin_client.put(
            "/orders/item/target-line",
            json={"quantity": 3},
            headers={"X-Acting-Uid": TARGET_UID},
        )
        rows = self._audit_rows(seeded)
        assert len(rows) == 1
        assert rows[0]["admin_uid"] == ADMIN_UID
        assert rows[0]["acting_uid"] == TARGET_UID
        assert rows[0]["method"] == "PUT"
        assert rows[0]["path"] == "/orders/item/target-line"
        assert rows[0]["status_code"] == 200

    def test_an_impersonated_read_is_recorded_too(self, admin_client, seeded):
        """Reading someone's ledger is the access that most needs a trace."""
        admin_client.get("/ledger/customers", headers={"X-Acting-Uid": TARGET_UID})
        rows = self._audit_rows(seeded)
        assert len(rows) == 1
        assert rows[0]["method"] == "GET"

    def test_an_ordinary_request_is_not_recorded(self, authed_client, seeded):
        authed_client.get("/orders")
        assert not self._audit_rows(seeded)

    def test_a_refused_impersonation_is_not_recorded_as_access(self, authed_client, seeded):
        authed_client.get("/orders", headers={"X-Acting-Uid": TARGET_UID})
        assert not self._audit_rows(seeded)

    def test_a_failing_audit_write_does_not_fail_the_request(self, admin_client, seeded):
        """Fail-open: diagnostics must never block someone repairing a live shop."""
        real_collection = seeded.collection

        def explode(name):
            if name == "admin_audit":
                raise RuntimeError("firestore is down")
            return real_collection(name)

        with mock.patch.object(seeded, "collection", side_effect=explode):
            resp = admin_client.put(
                "/orders/item/target-line",
                json={"quantity": 4},
                headers={"X-Acting-Uid": TARGET_UID},
            )

        assert resp.status_code == 200
        assert seeded.docs[f"users/{TARGET_UID}/orders/target-line"]["quantity"] == 4

    def test_one_requests_identity_does_not_leak_into_the_next(self, admin_client, seeded):
        """The ContextVar must be cleared per request, or an ordinary call would
        be audited under the previous caller's impersonation."""
        admin_client.put(
            "/orders/item/target-line",
            json={"quantity": 5},
            headers={"X-Acting-Uid": TARGET_UID},
        )
        admin_client.get("/orders")
        assert len(self._audit_rows(seeded)) == 1
