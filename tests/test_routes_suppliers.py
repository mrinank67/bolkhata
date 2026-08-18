"""Supplier directory endpoints."""

import datetime

import pytest

UID = "test-uid"
SUPPLIERS = f"users/{UID}/suppliers"


def _supplier(name="Sharma Traders", mobile="", gst="", days_ago=0):
    return {
        "name": name,
        "name_lower": name.lower(),
        "mobile": mobile,
        "gst_number": gst,
        "created_at": datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=days_ago),
    }


class TestListSavedSuppliers:
    def test_empty_directory(self, authed_client, fake_db):
        assert authed_client.get("/suppliers/list").json() == {"suppliers": []}

    def test_returns_newest_first(self, authed_client, fake_db):
        fake_db.seed(f"{SUPPLIERS}/a", _supplier("Old Traders", days_ago=10))
        fake_db.seed(f"{SUPPLIERS}/b", _supplier("New Traders", days_ago=1))

        names = [s["name"] for s in authed_client.get("/suppliers/list").json()["suppliers"]]
        assert names == ["New Traders", "Old Traders"]

    def test_created_at_is_epoch_millis(self, authed_client, fake_db):
        fake_db.seed(f"{SUPPLIERS}/a", _supplier())
        created = authed_client.get("/suppliers/list").json()["suppliers"][0]["created_at"]
        assert created > 1_600_000_000_000, "expected milliseconds, not seconds"

    def test_missing_created_at_degrades_to_zero(self, authed_client, fake_db):
        """SERVER_TIMESTAMP is unresolved on a read immediately after a write."""
        fake_db.seed(f"{SUPPLIERS}/a", {"name": "Sharma", "name_lower": "sharma"})
        assert authed_client.get("/suppliers/list").json()["suppliers"][0]["created_at"] == 0

    def test_scoped_to_the_caller(self, authed_client, fake_db):
        fake_db.seed("users/someone-else/suppliers/x", _supplier("Not Yours"))
        assert authed_client.get("/suppliers/list").json()["suppliers"] == []


class TestAddSupplier:
    def test_creates_and_returns_the_id(self, authed_client, fake_db):
        body = authed_client.post(
            "/suppliers/add",
            json={"name": "  Sharma Traders  ", "mobile": " 9876543210 ", "gst_number": " 27AAA "},
        ).json()

        assert body["status"] == "success"
        stored = fake_db.docs[f"{SUPPLIERS}/{body['id']}"]
        assert stored["name"] == "Sharma Traders"
        assert stored["mobile"] == "9876543210"
        assert stored["gst_number"] == "27AAA"

    def test_stores_a_lowercase_key_for_duplicate_detection(self, authed_client, fake_db):
        body = authed_client.post("/suppliers/add", json={"name": "Sharma Traders"}).json()
        assert fake_db.docs[f"{SUPPLIERS}/{body['id']}"]["name_lower"] == "sharma traders"

    def test_blank_name_is_400(self, authed_client, fake_db):
        assert authed_client.post("/suppliers/add", json={"name": "   "}).status_code == 400

    def test_duplicate_name_is_rejected_case_insensitively(self, authed_client, fake_db):
        authed_client.post("/suppliers/add", json={"name": "Sharma Traders"})
        resp = authed_client.post("/suppliers/add", json={"name": "  sharma TRADERS "})

        assert resp.status_code == 400
        assert "already exists" in resp.json()["detail"]
        assert len(fake_db.paths_under(SUPPLIERS)) == 1

    def test_another_users_supplier_is_not_a_duplicate(self, authed_client, fake_db):
        fake_db.seed("users/someone-else/suppliers/x", _supplier("Sharma Traders"))
        assert (
            authed_client.post("/suppliers/add", json={"name": "Sharma Traders"}).status_code == 200
        )

    def test_overlong_name_is_422(self, authed_client, fake_db):
        assert authed_client.post("/suppliers/add", json={"name": "a" * 101}).status_code == 422


class TestUpdateSupplier:
    def test_updates_an_existing_supplier(self, authed_client, fake_db):
        fake_db.seed(f"{SUPPLIERS}/s1", _supplier("Old Name"))

        resp = authed_client.put("/suppliers/s1", json={"name": "New Name", "mobile": "9000000000"})

        assert resp.status_code == 200
        assert fake_db.docs[f"{SUPPLIERS}/s1"]["name"] == "New Name"
        assert fake_db.docs[f"{SUPPLIERS}/s1"]["mobile"] == "9000000000"

    def test_unknown_id_is_404(self, authed_client, fake_db):
        assert authed_client.put("/suppliers/nope", json={"name": "X"}).status_code == 404

    def test_cannot_update_another_users_supplier(self, authed_client, fake_db):
        fake_db.seed("users/someone-else/suppliers/s1", _supplier("Theirs"))
        assert authed_client.put("/suppliers/s1", json={"name": "Hijacked"}).status_code == 404
        assert fake_db.docs["users/someone-else/suppliers/s1"]["name"] == "Theirs"

    def test_blank_name_is_400(self, authed_client, fake_db):
        fake_db.seed(f"{SUPPLIERS}/s1", _supplier())
        assert authed_client.put("/suppliers/s1", json={"name": "  "}).status_code == 400


class TestDeleteSupplier:
    def test_deletes(self, authed_client, fake_db):
        fake_db.seed(f"{SUPPLIERS}/s1", _supplier())
        assert authed_client.delete("/suppliers/s1").status_code == 200
        assert f"{SUPPLIERS}/s1" not in fake_db.docs

    def test_unknown_id_is_404(self, authed_client, fake_db):
        assert authed_client.delete("/suppliers/nope").status_code == 404

    def test_cannot_delete_another_users_supplier(self, authed_client, fake_db):
        fake_db.seed("users/someone-else/suppliers/s1", _supplier())
        assert authed_client.delete("/suppliers/s1").status_code == 404
        assert "users/someone-else/suppliers/s1" in fake_db.docs


class TestAddSupplierPurchase:
    PURCHASES = f"users/{UID}/suppliers_purchases"
    STOCK = f"users/{UID}/stock"

    def _purchase(self, authed_client, **overrides):
        payload = {
            "supplier_name": "Sharma Traders",
            "item_name": "rice",
            "quantity": 10,
            "amount": 500,
        }
        payload.update(overrides)
        return authed_client.post("/suppliers/purchase", json=payload)

    def test_records_the_purchase(self, authed_client, fake_db):
        fake_db.seed(f"{self.STOCK}/rice", {"item": "rice", "quantity": 0, "price": 40})
        assert self._purchase(authed_client).status_code == 200

        written = [fake_db.docs[p] for p in fake_db.paths_under(self.PURCHASES)]
        assert len(written) == 1
        assert written[0]["supplier_name"] == "Sharma Traders"
        assert written[0]["item_name"] == "rice"
        assert written[0]["quantity"] == 10

    def test_unknown_item_is_404_and_writes_nothing(self, authed_client, fake_db):
        """A purchase restocks an existing item; it never creates one.

        Creating here produced a stock row with no price, unit or category. The
        item check runs before the purchase write, so a rejected purchase must
        leave no orphan record behind.
        """
        res = self._purchase(authed_client, item_name="Basmati Rice")

        assert res.status_code == 404
        assert "not in your inventory" in res.json()["detail"]
        assert fake_db.paths_under(self.PURCHASES) == []
        assert fake_db.paths_under(self.STOCK) == []

    def test_item_is_matched_case_and_whitespace_insensitively(self, authed_client, fake_db):
        """Stock is keyed by the lowercased, trimmed item name."""
        fake_db.seed(f"{self.STOCK}/basmati rice", {"item": "basmati rice", "quantity": 5})

        assert self._purchase(authed_client, item_name="  Basmati Rice  ").status_code == 200
        assert fake_db.docs[f"{self.STOCK}/basmati rice"]["quantity"] == 15

    def test_adds_to_existing_stock_rather_than_overwriting(self, authed_client, fake_db):
        fake_db.seed(f"{self.STOCK}/rice", {"item": "rice", "quantity": 25})

        self._purchase(authed_client, quantity=10)

        assert fake_db.docs[f"{self.STOCK}/rice"]["quantity"] == 35

    def test_stock_updates_are_scoped_to_the_caller(self, authed_client, fake_db):
        fake_db.seed(f"{self.STOCK}/rice", {"item": "rice", "quantity": 25})
        fake_db.seed("users/someone-else/stock/rice", {"item": "rice", "quantity": 5})

        self._purchase(authed_client, quantity=10)

        assert fake_db.docs[f"{self.STOCK}/rice"]["quantity"] == 35
        assert fake_db.docs["users/someone-else/stock/rice"]["quantity"] == 5

    @pytest.mark.parametrize("field", ["supplier_name", "item_name"])
    def test_blank_required_field_is_400(self, authed_client, fake_db, field):
        assert self._purchase(authed_client, **{field: "   "}).status_code == 400
        assert fake_db.paths_under(self.PURCHASES) == []

    @pytest.mark.parametrize(
        "payload",
        [
            {"supplier_name": "s", "item_name": "rice", "quantity": 0, "amount": 100},
            {"supplier_name": "s", "item_name": "rice", "quantity": -1, "amount": 100},
            {"supplier_name": "s", "item_name": "rice", "quantity": 1, "amount": -5},
            {"supplier_name": "s", "item_name": "rice", "quantity": 200_000, "amount": 100},
            {"supplier_name": "s", "item_name": "rice", "quantity": 1, "amount": 99_999_999},
        ],
    )
    def test_out_of_range_values_are_422(self, authed_client, fake_db, payload):
        assert authed_client.post("/suppliers/purchase", json=payload).status_code == 422
