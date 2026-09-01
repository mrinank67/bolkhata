"""The correction paths that did not exist before: one ledger line, one purchase.

Until now a mistyped udhaar entry could only be settled (which records a payment
that never happened) and a mistaken purchase could only be removed by deleting
the entire supplier, taking every other purchase from them with it.

The purchase cases carry the load-bearing detail: recording a purchase *raises*
stock, so correcting one has to move stock the same way, or the shopkeeper is
left with the inflated count that made them call in the first place.
"""

import datetime

import pytest

from tests.conftest import TEST_UID


@pytest.fixture
def ledger_entry(fake_db):
    fake_db.seed(
        f"users/{TEST_UID}/udhaar/e1",
        {
            "customer_name": "ramesh",
            "customer_modifier": "",
            "item": "chawal",
            "quantity": 5,
            "unit": "",
            "amount": 250,
            "due_note": "diwali",
            "timestamp": datetime.datetime(2026, 8, 1, tzinfo=datetime.timezone.utc),
        },
    )
    return fake_db


@pytest.fixture
def purchase(fake_db):
    fake_db.seed(f"users/{TEST_UID}/stock/rice", {"quantity": 20, "price": 50})
    fake_db.seed(f"users/{TEST_UID}/stock/atta", {"quantity": 4, "price": 40})
    fake_db.seed(
        f"users/{TEST_UID}/suppliers_purchases/p1",
        {
            "supplier_name": "Sharma Traders",
            "item_name": "rice",
            "quantity": 10,
            "amount": 500,
            "timestamp": datetime.datetime(2026, 8, 1, tzinfo=datetime.timezone.utc),
        },
    )
    return fake_db


class TestLedgerEntryUpdate:
    def test_corrects_the_amount(self, authed_client, ledger_entry):
        resp = authed_client.put("/ledger/entry/e1", json={"amount": 150})

        assert resp.status_code == 200
        assert ledger_entry.docs[f"users/{TEST_UID}/udhaar/e1"]["amount"] == 150

    def test_omitted_fields_are_left_alone(self, authed_client, ledger_entry):
        """A client that only knows about the amount must not blank the note."""
        authed_client.put("/ledger/entry/e1", json={"amount": 150})

        entry = ledger_entry.docs[f"users/{TEST_UID}/udhaar/e1"]
        assert entry["due_note"] == "diwali"
        assert entry["item"] == "chawal"

    def test_names_are_lowercased_like_every_other_writer(self, authed_client, ledger_entry):
        authed_client.put("/ledger/entry/e1", json={"customer_name": "  RAMESH  "})
        assert ledger_entry.docs[f"users/{TEST_UID}/udhaar/e1"]["customer_name"] == "ramesh"

    def test_the_timestamp_is_never_rewritten(self, authed_client, ledger_entry):
        """get_ledger_customers() orders by it, and apply_payment() settles
        oldest-first — moving it would reorder both."""
        before = ledger_entry.docs[f"users/{TEST_UID}/udhaar/e1"]["timestamp"]
        authed_client.put("/ledger/entry/e1", json={"amount": 1})
        assert ledger_entry.docs[f"users/{TEST_UID}/udhaar/e1"]["timestamp"] == before

    def test_an_empty_name_is_rejected(self, authed_client, ledger_entry):
        assert (
            authed_client.put("/ledger/entry/e1", json={"customer_name": "  "}).status_code == 400
        )

    def test_an_empty_body_is_rejected(self, authed_client, ledger_entry):
        assert authed_client.put("/ledger/entry/e1", json={}).status_code == 400

    def test_a_missing_entry_is_a_404(self, authed_client, ledger_entry):
        assert authed_client.put("/ledger/entry/nope", json={"amount": 1}).status_code == 404

    def test_bounds_are_enforced_by_the_shared_model(self, authed_client, ledger_entry):
        assert authed_client.put("/ledger/entry/e1", json={"amount": 10_000_001}).status_code == 422

    def test_the_ledger_total_reflects_the_correction(self, authed_client, ledger_entry):
        authed_client.put("/ledger/entry/e1", json={"amount": 100})
        assert authed_client.get("/ledger/customers").json()["total_due"] == 100


class TestLedgerEntryDelete:
    def test_removes_the_line(self, authed_client, ledger_entry):
        resp = authed_client.delete("/ledger/entry/e1")

        assert resp.status_code == 200
        assert f"users/{TEST_UID}/udhaar/e1" not in ledger_entry.docs

    def test_the_debt_leaves_the_total_rather_than_counting_as_paid(
        self, authed_client, ledger_entry
    ):
        authed_client.delete("/ledger/entry/e1")
        body = authed_client.get("/ledger/customers").json()
        assert body["total_due"] == 0
        assert body["customer_count"] == 0

    def test_a_missing_entry_is_a_404(self, authed_client, ledger_entry):
        assert authed_client.delete("/ledger/entry/nope").status_code == 404


class TestPurchaseUpdate:
    def test_raising_the_quantity_adds_only_the_difference(self, authed_client, purchase):
        """Stock was 20 with a 10-unit purchase in it; correcting to 12 adds 2."""
        resp = authed_client.put("/suppliers/purchase/p1", json={"quantity": 12})

        assert resp.status_code == 200
        assert purchase.docs[f"users/{TEST_UID}/stock/rice"]["quantity"] == 22
        assert purchase.docs[f"users/{TEST_UID}/suppliers_purchases/p1"]["quantity"] == 12

    def test_lowering_the_quantity_takes_the_difference_back_out(self, authed_client, purchase):
        authed_client.put("/suppliers/purchase/p1", json={"quantity": 4})
        assert purchase.docs[f"users/{TEST_UID}/stock/rice"]["quantity"] == 14

    def test_repointing_at_another_item_moves_the_whole_quantity(self, authed_client, purchase):
        resp = authed_client.put("/suppliers/purchase/p1", json={"item_name": "atta"})

        assert resp.status_code == 200
        assert purchase.docs[f"users/{TEST_UID}/stock/rice"]["quantity"] == 10
        assert purchase.docs[f"users/{TEST_UID}/stock/atta"]["quantity"] == 14

    def test_repointing_at_an_item_the_shop_does_not_stock_is_rejected(
        self, authed_client, purchase
    ):
        """Same rule as recording a purchase: it restocks, it never creates."""
        resp = authed_client.put("/suppliers/purchase/p1", json={"item_name": "ghee"})

        assert resp.status_code == 404
        assert f"users/{TEST_UID}/stock/ghee" not in purchase.docs
        assert purchase.docs[f"users/{TEST_UID}/suppliers_purchases/p1"]["item_name"] == "rice"

    def test_correcting_only_the_supplier_leaves_stock_alone(self, authed_client, purchase):
        authed_client.put("/suppliers/purchase/p1", json={"supplier_name": "Verma Traders"})

        assert purchase.docs[f"users/{TEST_UID}/stock/rice"]["quantity"] == 20
        assert (
            purchase.docs[f"users/{TEST_UID}/suppliers_purchases/p1"]["supplier_name"]
            == "Verma Traders"
        )

    def test_the_timestamp_is_never_rewritten(self, authed_client, purchase):
        """get_suppliers() buckets the monthly totals by it."""
        before = purchase.docs[f"users/{TEST_UID}/suppliers_purchases/p1"]["timestamp"]
        authed_client.put("/suppliers/purchase/p1", json={"amount": 400})
        assert purchase.docs[f"users/{TEST_UID}/suppliers_purchases/p1"]["timestamp"] == before

    def test_a_missing_purchase_is_a_404(self, authed_client, purchase):
        assert (
            authed_client.put("/suppliers/purchase/nope", json={"quantity": 1}).status_code == 404
        )

    def test_an_empty_body_is_rejected(self, authed_client, purchase):
        assert authed_client.put("/suppliers/purchase/p1", json={}).status_code == 400


class TestPurchaseDelete:
    def test_removes_the_record_and_the_stock_it_added(self, authed_client, purchase):
        resp = authed_client.delete("/suppliers/purchase/p1")

        assert resp.status_code == 200
        assert f"users/{TEST_UID}/suppliers_purchases/p1" not in purchase.docs
        assert purchase.docs[f"users/{TEST_UID}/stock/rice"]["quantity"] == 10

    def test_stock_is_clamped_at_zero(self, authed_client, purchase):
        """The goods may already have been sold; going negative would swap one
        wrong number for another."""
        purchase.docs[f"users/{TEST_UID}/stock/rice"]["quantity"] = 3

        authed_client.delete("/suppliers/purchase/p1")

        assert purchase.docs[f"users/{TEST_UID}/stock/rice"]["quantity"] == 0

    def test_an_item_no_longer_in_stock_does_not_break_the_delete(self, authed_client, purchase):
        del purchase.docs[f"users/{TEST_UID}/stock/rice"]

        resp = authed_client.delete("/suppliers/purchase/p1")

        assert resp.status_code == 200
        assert f"users/{TEST_UID}/suppliers_purchases/p1" not in purchase.docs

    def test_other_purchases_from_the_supplier_survive(self, authed_client, purchase):
        """The old workaround — deleting the supplier — took these with it."""
        purchase.seed(
            f"users/{TEST_UID}/suppliers_purchases/p2",
            {"supplier_name": "Sharma Traders", "item_name": "atta", "quantity": 2, "amount": 80},
        )

        authed_client.delete("/suppliers/purchase/p1")

        assert f"users/{TEST_UID}/suppliers_purchases/p2" in purchase.docs

    def test_a_missing_purchase_is_a_404(self, authed_client, purchase):
        assert authed_client.delete("/suppliers/purchase/nope").status_code == 404
