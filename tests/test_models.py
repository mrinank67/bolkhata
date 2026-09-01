"""Pydantic request-model bounds.

These caps are the server's only defence on JSON endpoints against a typo or a
hostile client writing an absurd quantity or amount into a shopkeeper's ledger.
"""

import pytest
from pydantic import ValidationError

from models import (
    ALLOWED_UNITS,
    MAX_AMOUNT,
    MAX_QTY,
    ClearDuesRequest,
    InventoryItemUpdate,
    LedgerEntryRequest,
    OrderCreateRequest,
    OrderItemCreate,
    PayLinkRequest,
    PurchaseRequest,
    SupplierCreateRequest,
    UserSettingsRequest,
    normalize_unit,
)


def test_shared_bounds_are_the_documented_values():
    assert MAX_QTY == 100_000
    assert MAX_AMOUNT == 10_000_000


class TestNormalizeUnit:
    """The voice prompt asks the LLM for a natural-language unit ("pieces",
    "boxes"), but an order line has to land in ALLOWED_UNITS or the Orders edit
    sheet rewrites it on the next save."""

    @pytest.mark.parametrize(
        ("spoken", "expected"),
        [
            ("pieces", "pcs"),
            ("piece", "pcs"),
            ("pc", "pcs"),
            ("nag", "pcs"),
            ("boxes", "box"),
            ("Boxes", "box"),
            ("  BOXES  ", "box"),
            ("carton", "box"),
            ("peti", "box"),
            ("dozens", "dozen"),
            ("dz", "dozen"),
            ("darjan", "dozen"),
            ("packets", "pack"),
            ("pkt", "pack"),
        ],
    )
    def test_spoken_words_map_onto_the_canonical_unit(self, spoken, expected):
        assert normalize_unit(spoken) == expected

    @pytest.mark.parametrize("canonical", sorted(ALLOWED_UNITS))
    def test_a_canonical_unit_is_returned_unchanged(self, canonical):
        assert normalize_unit(canonical) == canonical

    @pytest.mark.parametrize("spoken", ["kilo", "kg", "litre", "bars", "gram", "metre"])
    def test_a_unit_with_no_pack_equivalent_becomes_a_plain_count(self, spoken):
        """Forcing "kilo" into one of the four would be a lie about the sale.
        The spoken word survives on the ledger entry, which displays it."""
        assert normalize_unit(spoken) == ""

    @pytest.mark.parametrize("spoken", [None, "", "   "])
    def test_no_unit_spoken_is_a_plain_count(self, spoken):
        assert normalize_unit(spoken) == ""

    def test_every_output_is_storable_on_an_order(self):
        words = ["pieces", "boxes", "dozens", "packets", "kilo", "", None, "nonsense"]
        assert {normalize_unit(w) for w in words} <= ALLOWED_UNITS


class TestQuantityAndAmountCeilings:
    def test_quantity_at_ceiling_is_accepted(self):
        assert (
            PurchaseRequest(supplier_name="s", item_name="i", quantity=MAX_QTY, amount=0).quantity
            == MAX_QTY
        )

    def test_quantity_above_ceiling_is_rejected(self):
        with pytest.raises(ValidationError):
            PurchaseRequest(supplier_name="s", item_name="i", quantity=MAX_QTY + 1, amount=0)

    def test_amount_above_ceiling_is_rejected(self):
        with pytest.raises(ValidationError):
            PurchaseRequest(supplier_name="s", item_name="i", quantity=1, amount=MAX_AMOUNT + 1)

    def test_negative_amount_is_rejected(self):
        with pytest.raises(ValidationError):
            PurchaseRequest(supplier_name="s", item_name="i", quantity=1, amount=-1)

    def test_purchase_quantity_must_be_positive(self):
        with pytest.raises(ValidationError):
            PurchaseRequest(supplier_name="s", item_name="i", quantity=0, amount=10)


class TestClearDues:
    def test_zero_payment_is_rejected(self):
        """A zero-amount clear would delete dues without any money changing hands."""
        with pytest.raises(ValidationError):
            ClearDuesRequest(customer_name="ramesh", amount=0)

    def test_negative_payment_is_rejected(self):
        with pytest.raises(ValidationError):
            ClearDuesRequest(customer_name="ramesh", amount=-100)

    def test_positive_payment_is_accepted(self):
        assert ClearDuesRequest(customer_name="ramesh", amount=250).amount == 250


class TestLedgerEntry:
    def test_zero_quantity_is_allowed(self):
        """Amount-only entries (a plain cash due with no item count) are valid."""
        assert LedgerEntryRequest(customer_name="c", item="milk", quantity=0).quantity == 0

    def test_negative_quantity_is_rejected(self):
        with pytest.raises(ValidationError):
            LedgerEntryRequest(customer_name="c", item="milk", quantity=-1)

    def test_whatsapp_number_length_is_capped(self):
        with pytest.raises(ValidationError):
            LedgerEntryRequest(customer_name="c", item="milk", quantity=1, whatsapp_number="9" * 17)

    def test_optional_fields_default_to_empty(self):
        req = LedgerEntryRequest(customer_name="c", item="milk", quantity=1)
        assert req.amount == 0
        assert req.customer_modifier == ""
        assert req.due_note == ""


class TestStringLengthCaps:
    @pytest.mark.parametrize(
        ("model", "kwargs", "field"),
        [
            (PurchaseRequest, {"item_name": "i", "quantity": 1, "amount": 0}, "supplier_name"),
            (SupplierCreateRequest, {}, "name"),
            (LedgerEntryRequest, {"item": "i", "quantity": 1}, "customer_name"),
        ],
    )
    def test_names_are_capped_at_100_chars(self, model, kwargs, field):
        model(**{**kwargs, field: "a" * 100})
        with pytest.raises(ValidationError):
            model(**{**kwargs, field: "a" * 101})

    def test_upi_id_is_capped(self):
        UserSettingsRequest(upi_id="a" * 256)
        with pytest.raises(ValidationError):
            UserSettingsRequest(upi_id="a" * 257)

    def test_shop_address_is_capped(self):
        with pytest.raises(ValidationError):
            UserSettingsRequest(shop_address="a" * 301)


class TestPayLink:
    def test_payee_fields_are_not_client_settable(self):
        """pa/pn must come from the authenticated user's settings, never the body.

        Pydantic ignores unknown keys by default, so this asserts they are not
        silently absorbed into the model.
        """
        req = PayLinkRequest(am=100, tn="note", pa="attacker@upi", pn="Attacker")
        assert not hasattr(req, "pa")
        assert not hasattr(req, "pn")

    def test_amount_must_be_positive(self):
        with pytest.raises(ValidationError):
            PayLinkRequest(am=0)

    def test_amount_is_capped(self):
        with pytest.raises(ValidationError):
            PayLinkRequest(am=10_000_001)


class TestOrders:
    def test_order_items_default_to_empty_list(self):
        assert OrderCreateRequest(customer_name="c").items == []

    def test_order_item_quantity_must_be_positive(self):
        with pytest.raises(ValidationError):
            OrderItemCreate(item="rice", quantity=0, price=50)

    def test_nested_items_are_validated(self):
        with pytest.raises(ValidationError):
            OrderCreateRequest(
                customer_name="c", items=[{"item": "rice", "quantity": -1, "price": 50}]
            )


class TestInventoryUpdate:
    def test_all_fields_optional_for_partial_update(self):
        req = InventoryItemUpdate()
        assert req.item is None and req.quantity is None and req.price is None

    def test_zero_quantity_allowed_for_out_of_stock(self):
        assert InventoryItemUpdate(quantity=0).quantity == 0

    def test_negative_quantity_rejected(self):
        with pytest.raises(ValidationError):
            InventoryItemUpdate(quantity=-1)
