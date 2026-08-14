"""Order endpoints.

Orders are stored as flat line-item documents grouped by `order_id`, with a
legacy fallback key for rows written before that field existed. The grouping and
unit-price derivation are the parts most likely to break silently.
"""

import datetime
from unittest import mock

import pytest

from routes.orders import _display_price, _order_id_for_doc

UID = "test-uid"
ORDERS = f"users/{UID}/orders"


def _ts(days_ago=0):
    return datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=days_ago)


def _line(order_id="o1", customer="ramesh", item="rice", qty=2, price=50, days_ago=0, **extra):
    return {
        "order_id": order_id,
        "customer_name": customer,
        "customer_modifier": "",
        "item": item,
        "quantity": qty,
        "price": price,
        "timestamp": _ts(days_ago),
        **extra,
    }


@pytest.fixture(autouse=True)
def no_storage():
    """_attach_bills reaches for the Storage bucket; keep it off the network."""
    bucket = mock.MagicMock()
    bucket.name = "test-bucket.appspot.com"
    with mock.patch("routes.orders.get_bucket", return_value=bucket):
        yield bucket


class TestDisplayPrice:
    def test_uses_the_stored_unit_price_when_present(self):
        assert _display_price({"price": 50, "quantity": 2, "amount": 999}) == 50

    def test_derives_unit_price_from_amount_and_quantity(self):
        assert _display_price({"quantity": 4, "amount": 200}) == 50

    def test_zero_quantity_does_not_divide_by_zero(self):
        assert _display_price({"quantity": 0, "amount": 200}) == 0

    @pytest.mark.parametrize("data", [{}, {"price": None}, {"price": 0, "quantity": None}])
    def test_missing_fields_degrade_to_zero(self, data):
        assert _display_price(data) == 0


class TestOrderIdForDoc:
    def test_prefers_the_explicit_order_id(self):
        assert _order_id_for_doc({"order_id": "abc", "customer_name": "r"}) == "abc"

    def test_legacy_rows_fall_back_to_customer_and_day(self):
        key = _order_id_for_doc(
            {"customer_name": "ramesh", "customer_modifier": "tailor", "timestamp": _ts(0)}
        )
        assert key.startswith("legacy|ramesh|tailor|")

    def test_legacy_row_without_a_timestamp_is_still_groupable(self):
        assert _order_id_for_doc({"customer_name": "ramesh"}) == "legacy|ramesh||unknown"


class TestGetOrders:
    def test_empty(self, authed_client, fake_db):
        body = authed_client.get("/orders").json()
        assert body["orders"] == []

    def test_line_items_are_grouped_into_one_order(self, authed_client, fake_db):
        fake_db.seed(f"{ORDERS}/a", _line(order_id="o1", item="rice", qty=2, price=50))
        fake_db.seed(f"{ORDERS}/b", _line(order_id="o1", item="dal", qty=1, price=80))

        orders = authed_client.get("/orders").json()["orders"]

        assert len(orders) == 1
        assert len(orders[0]["items"]) == 2

    def test_separate_order_ids_stay_separate(self, authed_client, fake_db):
        fake_db.seed(f"{ORDERS}/a", _line(order_id="o1"))
        fake_db.seed(f"{ORDERS}/b", _line(order_id="o2"))

        assert len(authed_client.get("/orders").json()["orders"]) == 2

    def test_legacy_rows_without_an_order_id_are_grouped_by_customer_and_day(
        self, authed_client, fake_db
    ):
        row = _line(order_id=None, days_ago=0)
        row.pop("order_id")
        fake_db.seed(f"{ORDERS}/a", dict(row, item="rice"))
        fake_db.seed(f"{ORDERS}/b", dict(row, item="dal"))

        orders = authed_client.get("/orders").json()["orders"]
        assert len(orders) == 1, "same customer, same day should be one legacy order"

    def test_scoped_to_the_caller(self, authed_client, fake_db):
        fake_db.seed("users/someone-else/orders/x", _line())
        assert authed_client.get("/orders").json()["orders"] == []


class TestCreateOrder:
    def test_creates_line_items_for_each_entry(self, authed_client, fake_db):
        resp = authed_client.post(
            "/orders",
            json={
                "customer_name": "Ramesh",
                "items": [
                    {"item": "rice", "quantity": 2, "price": 50},
                    {"item": "dal", "quantity": 1, "price": 80},
                ],
            },
        )

        assert resp.status_code == 200, resp.text
        assert len(fake_db.paths_under(ORDERS)) == 2

    def test_all_items_share_one_order_id(self, authed_client, fake_db):
        authed_client.post(
            "/orders",
            json={
                "customer_name": "Ramesh",
                "items": [
                    {"item": "rice", "quantity": 2, "price": 50},
                    {"item": "dal", "quantity": 1, "price": 80},
                ],
            },
        )

        order_ids = {fake_db.docs[p]["order_id"] for p in fake_db.paths_under(ORDERS)}
        assert len(order_ids) == 1, "line items of one order must share an order_id"

    def test_customer_name_is_normalized(self, authed_client, fake_db):
        authed_client.post(
            "/orders",
            json={
                "customer_name": "  RAMESH  ",
                "items": [{"item": "rice", "quantity": 1, "price": 5}],
            },
        )
        stored = fake_db.docs[fake_db.paths_under(ORDERS)[0]]
        assert stored["customer_name"] == "ramesh"

    @pytest.mark.parametrize(
        "items",
        [
            [{"item": "rice", "quantity": 0, "price": 50}],
            [{"item": "rice", "quantity": -1, "price": 50}],
            [{"item": "rice", "quantity": 1, "price": -5}],
            [{"item": "rice", "quantity": 200_000, "price": 5}],
        ],
    )
    def test_invalid_line_items_are_422(self, authed_client, fake_db, items):
        resp = authed_client.post("/orders", json={"customer_name": "c", "items": items})
        assert resp.status_code == 422
        assert fake_db.paths_under(ORDERS) == []


class TestModifyOrderItems:
    def test_update_quantity(self, authed_client, fake_db):
        fake_db.seed(f"{ORDERS}/i1", _line(qty=2, price=50))

        resp = authed_client.put("/orders/item/i1", json={"quantity": 5})

        assert resp.status_code == 200, resp.text
        assert fake_db.docs[f"{ORDERS}/i1"]["quantity"] == 5

    def test_update_unknown_item_is_404(self, authed_client, fake_db):
        assert authed_client.put("/orders/item/nope", json={"quantity": 1}).status_code == 404

    def test_cannot_update_another_users_item(self, authed_client, fake_db):
        fake_db.seed("users/someone-else/orders/i1", _line(qty=2))

        assert authed_client.put("/orders/item/i1", json={"quantity": 99}).status_code == 404
        assert fake_db.docs["users/someone-else/orders/i1"]["quantity"] == 2

    def test_zero_quantity_is_422(self, authed_client, fake_db):
        fake_db.seed(f"{ORDERS}/i1", _line())
        assert authed_client.put("/orders/item/i1", json={"quantity": 0}).status_code == 422

    def test_delete_item(self, authed_client, fake_db):
        fake_db.seed(f"{ORDERS}/i1", _line())

        assert authed_client.delete("/orders/item/i1").status_code == 200
        assert f"{ORDERS}/i1" not in fake_db.docs

    def test_cannot_delete_another_users_item(self, authed_client, fake_db):
        fake_db.seed("users/someone-else/orders/i1", _line())
        authed_client.delete("/orders/item/i1")
        assert "users/someone-else/orders/i1" in fake_db.docs

    def test_add_item_to_an_existing_order(self, authed_client, fake_db):
        fake_db.seed(f"{ORDERS}/i1", _line(order_id="o1", item="rice"))

        resp = authed_client.post(
            "/orders/o1/items", json={"item": "dal", "quantity": 1, "price": 80}
        )

        assert resp.status_code == 200, resp.text
        items = [fake_db.docs[p]["item"] for p in fake_db.paths_under(ORDERS)]
        assert sorted(items) == ["dal", "rice"]

    def test_added_item_joins_the_same_order_id(self, authed_client, fake_db):
        fake_db.seed(f"{ORDERS}/i1", _line(order_id="o1", item="rice"))

        authed_client.post("/orders/o1/items", json={"item": "dal", "quantity": 1, "price": 80})

        assert {fake_db.docs[p]["order_id"] for p in fake_db.paths_under(ORDERS)} == {"o1"}


class TestDeleteOrder:
    def test_removes_every_line_item_of_the_order(self, authed_client, fake_db):
        fake_db.seed(f"{ORDERS}/a", _line(order_id="o1", item="rice"))
        fake_db.seed(f"{ORDERS}/b", _line(order_id="o1", item="dal"))
        fake_db.seed(f"{ORDERS}/c", _line(order_id="o2", item="atta"))

        assert authed_client.delete("/orders/o1").status_code == 200

        remaining = [fake_db.docs[p]["order_id"] for p in fake_db.paths_under(ORDERS)]
        assert remaining == ["o2"]

    def test_cannot_delete_another_users_order(self, authed_client, fake_db):
        fake_db.seed("users/someone-else/orders/a", _line(order_id="o1"))

        authed_client.delete("/orders/o1")

        assert "users/someone-else/orders/a" in fake_db.docs
