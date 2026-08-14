"""Ledger, settings and payment-link endpoints, end to end against the fake."""

import datetime

import pytest

UID = "test-uid"
UDHAAR = f"users/{UID}/udhaar"


def _ts(days_ago: int) -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=days_ago)


def _due(name="ramesh", modifier="", amount=100, days_ago=1, **extra):
    return {
        "customer_name": name,
        "customer_modifier": modifier,
        "item": "rice",
        "quantity": 1,
        "unit": "kg",
        "amount": amount,
        "timestamp": _ts(days_ago),
        **extra,
    }


class TestGetLedgerCustomers:
    def test_empty_ledger(self, authed_client, fake_db):
        body = authed_client.get("/ledger/customers").json()
        assert body == {"customers": [], "total_due": 0, "customer_count": 0}

    def test_groups_entries_by_customer_and_sums_dues(self, authed_client, fake_db):
        fake_db.seed(f"{UDHAAR}/a", _due(amount=100, days_ago=2))
        fake_db.seed(f"{UDHAAR}/b", _due(amount=250, days_ago=1))
        fake_db.seed(f"{UDHAAR}/c", _due(name="suresh", amount=75, days_ago=1))

        body = authed_client.get("/ledger/customers").json()

        assert body["customer_count"] == 2
        assert body["total_due"] == 425
        by_name = {c["customer_name"]: c for c in body["customers"]}
        assert by_name["ramesh"]["total_due"] == 350
        assert len(by_name["ramesh"]["items"]) == 2
        assert by_name["suresh"]["total_due"] == 75

    def test_modifier_separates_customers_with_the_same_name(self, authed_client, fake_db):
        fake_db.seed(f"{UDHAAR}/a", _due(modifier="tailor", amount=100))
        fake_db.seed(f"{UDHAAR}/b", _due(modifier="milkman", amount=200))

        body = authed_client.get("/ledger/customers").json()
        assert body["customer_count"] == 2
        assert {c["customer_modifier"] for c in body["customers"]} == {"tailor", "milkman"}

    def test_null_amounts_do_not_break_the_ledger_load(self, authed_client, fake_db):
        """One legacy row with a null amount used to TypeError the whole page."""
        fake_db.seed(f"{UDHAAR}/a", _due(amount=None))
        fake_db.seed(f"{UDHAAR}/b", _due(amount=100))

        body = authed_client.get("/ledger/customers").json()
        assert body["total_due"] == 100

    def test_missing_timestamp_is_returned_as_null(self, authed_client, fake_db):
        fake_db.seed(
            f"{UDHAAR}/a",
            {"customer_name": "ramesh", "customer_modifier": "", "amount": 50},
        )
        body = authed_client.get("/ledger/customers").json()
        assert body["customers"][0]["items"][0]["timestamp"] is None

    def test_only_the_callers_own_ledger_is_returned(self, authed_client, fake_db):
        fake_db.seed(f"{UDHAAR}/mine", _due(amount=100))
        fake_db.seed("users/someone-else/udhaar/theirs", _due(name="other", amount=999))

        body = authed_client.get("/ledger/customers").json()
        assert body["total_due"] == 100
        assert [c["customer_name"] for c in body["customers"]] == ["ramesh"]


class TestAddLedgerEntry:
    def test_creates_a_normalized_entry(self, authed_client, fake_db):
        resp = authed_client.post(
            "/ledger/entry",
            json={
                "customer_name": "  Ramesh  ",
                "customer_modifier": "  Tailor ",
                "item": "  Rice ",
                "quantity": 2,
                "unit": "kg",
                "amount": 120,
            },
        )
        assert resp.status_code == 200

        written = [fake_db.docs[p] for p in fake_db.paths_under(UDHAAR)]
        assert len(written) == 1
        entry = written[0]
        # Lowercased and trimmed so lookups and fuzzy matching stay consistent.
        assert entry["customer_name"] == "ramesh"
        assert entry["customer_modifier"] == "tailor"
        assert entry["item"] == "rice"
        assert entry["amount"] == 120
        assert entry["reminder_sent"] is False

    @pytest.mark.parametrize(
        "payload",
        [
            {"customer_name": "   ", "item": "rice", "quantity": 1},
            {"customer_name": "ramesh", "item": "  ", "quantity": 1},
        ],
    )
    def test_blank_name_or_item_is_400(self, authed_client, fake_db, payload):
        assert authed_client.post("/ledger/entry", json=payload).status_code == 400

    def test_out_of_range_amount_is_422(self, authed_client, fake_db):
        resp = authed_client.post(
            "/ledger/entry",
            json={"customer_name": "c", "item": "rice", "quantity": 1, "amount": 99_999_999},
        )
        assert resp.status_code == 422


class TestClearLedgerDues:
    def test_unknown_customer_is_404(self, authed_client, fake_db):
        resp = authed_client.post("/ledger/clear", json={"customer_name": "nobody", "amount": 100})
        assert resp.status_code == 404

    def test_full_settle_reports_settled_and_empties_the_ledger(self, authed_client, fake_db):
        fake_db.seed(f"{UDHAAR}/a", _due(amount=300))

        body = authed_client.post(
            "/ledger/clear", json={"customer_name": "ramesh", "amount": 300}
        ).json()

        assert body["settled"] is True
        assert (body["paid"], body["remaining"]) == (300, 0)
        assert fake_db.paths_under(UDHAAR) == []

    def test_partial_payment_reports_the_balance(self, authed_client, fake_db):
        fake_db.seed(f"{UDHAAR}/a", _due(amount=500))

        body = authed_client.post(
            "/ledger/clear", json={"customer_name": "ramesh", "amount": 200}
        ).json()

        assert body["settled"] is False
        assert (body["total_owed"], body["paid"], body["remaining"]) == (500, 200, 300)
        assert "300" in body["message"]

    def test_zero_amount_is_rejected_before_touching_the_ledger(self, authed_client, fake_db):
        fake_db.seed(f"{UDHAAR}/a", _due(amount=300))

        assert (
            authed_client.post(
                "/ledger/clear", json={"customer_name": "ramesh", "amount": 0}
            ).status_code
            == 422
        )
        assert fake_db.paths_under(UDHAAR), "a rejected clear must not delete anything"


class TestWhatsAppReminder:
    def test_updates_every_entry_for_the_customer(self, authed_client, fake_db):
        fake_db.seed(f"{UDHAAR}/a", _due(amount=100))
        fake_db.seed(f"{UDHAAR}/b", _due(amount=200))
        fake_db.seed(f"{UDHAAR}/c", _due(name="suresh", amount=50))

        body = authed_client.post(
            "/ledger/whatsapp-reminder",
            json={
                "customer_name": "Ramesh",
                "whatsapp_number": "9876543210",
                "reminder_schedule": "weekly",
            },
        ).json()

        assert body["updated_entries"] == 2
        assert fake_db.docs[f"{UDHAAR}/a"]["whatsapp_number"] == "9876543210"
        assert fake_db.docs[f"{UDHAAR}/c"].get("whatsapp_number", "") == ""

    def test_modifier_narrows_the_update(self, authed_client, fake_db):
        fake_db.seed(f"{UDHAAR}/a", _due(modifier="tailor"))
        fake_db.seed(f"{UDHAAR}/b", _due(modifier="milkman"))

        body = authed_client.post(
            "/ledger/whatsapp-reminder",
            json={
                "customer_name": "ramesh",
                "customer_modifier": "tailor",
                "whatsapp_number": "9876543210",
            },
        ).json()

        assert body["updated_entries"] == 1
        assert "whatsapp_number" not in fake_db.docs[f"{UDHAAR}/b"]


class TestSettings:
    def test_defaults_to_blanks_when_unset(self, authed_client, fake_db):
        body = authed_client.get("/settings").json()
        assert body == {"upi_id": "", "shop_name": "", "shop_mobile": "", "shop_address": ""}

    def test_round_trip(self, authed_client, fake_db):
        authed_client.put(
            "/settings",
            json={
                "upi_id": "  shop@ybl  ",
                "shop_name": " Sharma Kirana ",
                "shop_mobile": "9876543210",
                "shop_address": "Main Road",
            },
        )
        body = authed_client.get("/settings").json()

        assert body["upi_id"] == "shop@ybl", "values must be trimmed before storage"
        assert body["shop_name"] == "Sharma Kirana"

    @pytest.mark.parametrize(
        "upi_id",
        [
            "no-at-sign",
            "@ybl",
            "a@ybl",  # handle shorter than 2 chars
            "shop@y",  # PSP shorter than 2 chars
            "shop@ybl extra",
            "shop@yb1",  # digits are not valid in a PSP
            "shop name@ybl",
        ],
    )
    def test_invalid_upi_id_is_rejected(self, authed_client, fake_db, upi_id):
        resp = authed_client.put("/settings", json={"upi_id": upi_id})
        assert resp.status_code == 400, f"{upi_id!r} should not have been accepted"

    @pytest.mark.parametrize("upi_id", ["98765@ybl", "shopname@okhdfcbank", "a.b_c-d@paytm"])
    def test_valid_upi_ids_are_accepted(self, authed_client, fake_db, upi_id):
        assert authed_client.put("/settings", json={"upi_id": upi_id}).status_code == 200

    def test_empty_upi_id_is_allowed(self, authed_client, fake_db):
        """Clearing the field must not trip the format check."""
        assert authed_client.put("/settings", json={"upi_id": ""}).status_code == 200

    def test_settings_are_per_user(self, authed_client, fake_db):
        fake_db.seed("users/someone-else", {"upi_id": "other@ybl"})
        assert authed_client.get("/settings").json()["upi_id"] == ""


class TestPayLinks:
    def test_creating_a_link_requires_a_saved_upi_id(self, authed_client, fake_db):
        resp = authed_client.post("/pay/create", json={"am": 100, "tn": "note"})
        assert resp.status_code == 400
        assert "UPI" in resp.json()["detail"]

    def test_payee_comes_from_settings_not_the_request_body(self, authed_client, fake_db):
        """The whole point of the endpoint: a caller cannot choose the payee."""
        fake_db.seed(f"users/{UID}", {"upi_id": "shop@ybl"})

        token = authed_client.post(
            "/pay/create",
            json={"am": 250, "tn": "rice", "pa": "attacker@upi", "pn": "Attacker"},
        ).json()["token"]

        page = authed_client.get("/pay", params={"token": token})
        assert page.status_code == 200
        assert "shop@ybl" in page.text
        assert "attacker@upi" not in page.text
        assert "Attacker" not in page.text
        assert "BolKhata" in page.text

    def test_pay_page_renders_the_amount_and_upi_uri(self, authed_client, fake_db):
        fake_db.seed(f"users/{UID}", {"upi_id": "shop@ybl"})
        token = authed_client.post("/pay/create", json={"am": 250, "tn": "rice"}).json()["token"]

        page = authed_client.get("/pay", params={"token": token}).text
        assert "250" in page
        assert "upi://pay?pa=shop%40ybl" in page

    def test_tampered_token_shows_an_error_page_not_a_stack_trace(self, client, fake_db):
        resp = client.get("/pay", params={"token": "clearly-not-signed"})
        assert resp.status_code == 200
        assert "invalid" in resp.text.lower()

    def test_note_is_html_escaped(self, authed_client, fake_db):
        """The note is shopkeeper-supplied and rendered into the page."""
        fake_db.seed(f"users/{UID}", {"upi_id": "shop@ybl"})
        token = authed_client.post(
            "/pay/create", json={"am": 10, "tn": "<script>alert(1)</script>"}
        ).json()["token"]

        page = authed_client.get("/pay", params={"token": token}).text
        assert "<script>alert(1)</script>" not in page
        assert "&lt;script&gt;" in page
