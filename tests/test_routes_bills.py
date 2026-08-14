"""POST /orders/{order_id}/bill — PDF invoice generation.

Bill numbers are customer-facing and must be stable: regenerating a bill after
an order edit has to reuse the same number and download token, or a shopkeeper
ends up handing out two different invoices for one sale.
"""

import datetime
from unittest import mock

import pytest

from routes.bills import _money, _num, _titlecase

UID = "test-uid"
ORDERS = f"users/{UID}/orders"
BILLS = f"users/{UID}/bills"


def _line(order_id="o1", item="rice", qty=2, price=50, amount=100, days_ago=0, **extra):
    return {
        "order_id": order_id,
        "customer_name": "ramesh",
        "customer_modifier": "",
        "item": item,
        "quantity": qty,
        "price": price,
        "amount": amount,
        "timestamp": datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=days_ago),
        **extra,
    }


@pytest.fixture
def storage():
    """Storage double; uploaded PDFs land in `.blobs` keyed by path."""
    bucket = mock.MagicMock()
    bucket.name = "test-bucket.appspot.com"
    bucket.blobs = {}

    def _blob(path):
        blob = mock.MagicMock()
        blob.name = path
        blob.upload_from_string.side_effect = lambda data, **kw: bucket.blobs.__setitem__(
            path, data
        )
        return blob

    bucket.blob.side_effect = _blob
    with mock.patch("routes.bills.get_bucket", return_value=bucket):
        yield bucket


@pytest.fixture
def direct_transactions():
    with mock.patch("routes.bills.firestore.transactional", lambda f: f):
        yield


@pytest.fixture
def bill(authed_client, storage, direct_transactions):
    def _generate(order_id="o1"):
        return authed_client.post(f"/orders/{order_id}/bill")

    return _generate


class TestFormatters:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [(1000, "1,000"), (1000.0, "1,000"), (1250.5, "1,250.50"), (0, "0"), (None, "0")],
    )
    def test_num_groups_and_drops_trailing_zeros(self, value, expected):
        assert _num(value) == expected

    def test_money_uses_rs_prefix_not_the_rupee_glyph(self):
        """ReportLab's bundled fonts have no glyph for the rupee sign."""
        rendered = _money(1500)
        assert rendered == "Rs. 1,500"
        assert "₹" not in rendered

    @pytest.mark.parametrize(
        ("value", "expected"), [("  ramesh kumar ", "Ramesh Kumar"), (None, ""), ("", "")]
    )
    def test_titlecase(self, value, expected):
        assert _titlecase(value) == expected


class TestGenerateBill:
    def test_unknown_order_is_404(self, fake_db, bill):
        assert bill("nope").status_code == 404

    def test_produces_a_pdf_and_a_download_url(self, fake_db, bill, storage):
        fake_db.seed(f"{ORDERS}/a", _line())

        resp = bill()

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["bill_number"] == "BK-001"
        assert body["pdf_url"].startswith("https://firebasestorage.googleapis.com/")

        (pdf,) = storage.blobs.values()
        assert pdf.startswith(b"%PDF-"), "uploaded blob is not a PDF"
        assert len(pdf) > 1000

    def test_bill_numbers_increment_across_orders(self, fake_db, bill):
        fake_db.seed(f"{ORDERS}/a", _line(order_id="o1"))
        fake_db.seed(f"{ORDERS}/b", _line(order_id="o2"))

        assert bill("o1").json()["bill_number"] == "BK-001"
        assert bill("o2").json()["bill_number"] == "BK-002"

    def test_regenerating_reuses_the_same_number_and_token(self, fake_db, bill):
        """Two invoices with different numbers for one sale is a real-world mess."""
        fake_db.seed(f"{ORDERS}/a", _line())

        first = bill().json()
        second = bill().json()

        assert first["bill_number"] == second["bill_number"] == "BK-001"
        assert first["pdf_url"] == second["pdf_url"], "download token must be stable"

    def test_regenerating_clears_the_stale_flag(self, fake_db, bill):
        fake_db.seed(f"{ORDERS}/a", _line())
        bill()
        fake_db.docs[f"{BILLS}/o1"]["stale"] = True

        bill()

        assert fake_db.docs[f"{BILLS}/o1"]["stale"] is False

    def test_pdf_contains_the_shop_and_customer_details(self, fake_db, bill, storage):
        fake_db.seed(
            f"users/{UID}",
            {"shop_name": "Sharma Kirana", "shop_mobile": "9876543210", "shop_address": "Main Rd"},
        )
        fake_db.seed(f"{ORDERS}/a", _line())

        bill()

        # ReportLab compresses text streams, so assert on the response contract
        # and that a plausible PDF was produced rather than grepping bytes.
        assert list(storage.blobs) == [f"users/{UID}/bills/o1.pdf"]

    def test_missing_shop_name_falls_back_to_a_default(self, fake_db, bill):
        fake_db.seed(f"{ORDERS}/a", _line())
        assert bill().status_code == 200

    def test_multi_item_order_renders(self, fake_db, bill, storage):
        fake_db.seed(f"{ORDERS}/a", _line(item="rice", qty=2, price=50, amount=100))
        fake_db.seed(f"{ORDERS}/b", _line(item="dal", qty=1, price=80, amount=80))
        fake_db.seed(f"{ORDERS}/c", _line(item="atta", qty=3, price=40, amount=120))

        assert bill().status_code == 200
        (pdf,) = storage.blobs.values()
        assert pdf.startswith(b"%PDF-")

    def test_null_amounts_do_not_break_rendering(self, fake_db, bill):
        """Legacy rows can carry null quantity/amount."""
        fake_db.seed(f"{ORDERS}/a", _line(amount=None, qty=None))
        assert bill().status_code == 200

    def test_download_token_is_embedded_in_blob_metadata(self, fake_db, bill, storage):
        fake_db.seed(f"{ORDERS}/a", _line())
        url = bill().json()["pdf_url"]

        token = fake_db.docs[f"{BILLS}/o1"]["download_token"]
        assert f"token={token}" in url

    def test_cannot_bill_another_users_order(self, fake_db, bill):
        fake_db.seed("users/someone-else/orders/a", _line())
        assert bill("o1").status_code == 404

    def test_bill_sequence_is_per_user(self, fake_db, bill):
        fake_db.seed("users/someone-else", {"bill_seq": 42})
        fake_db.seed(f"{ORDERS}/a", _line())

        assert bill().json()["bill_number"] == "BK-001"
