"""Core ledger arithmetic and supplier-name resolution.

apply_payment() is the highest-consequence pure-ish function in the codebase:
it decides how much of a shopkeeper's outstanding credit gets written off, and
it is shared by both the voice "payment" flow and the manual Clear Dues button.
A bug here silently destroys real debt records.
"""

import datetime

import pytest

from db_operations import (
    WALK_IN_CUSTOMER,
    _format_purchase_date,
    _match_supplier,
    _normalize_supplier_name,
    _to_number,
    apply_payment,
    process_transactions,
)
from tests.fakes import FakeFirestore

IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))


def _ts(days_ago: int) -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=days_ago)


class TestToNumber:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("800", 800),  # LLM emits numeric strings
            ("2.5", 2.5),
            (12, 12),
            (12.0, 12),  # whole floats collapse to int
            (12.5, 12.5),
            ("  30  ", 30),
            ("-5", -5),
            ("1e3", 1000),
        ],
    )
    def test_coerces_llm_values(self, value, expected):
        result = _to_number(value)
        assert result == expected
        assert isinstance(result, type(expected))

    @pytest.mark.parametrize("value", [None, "", "abc", "two kilos", {}, []])
    def test_unparseable_returns_default(self, value):
        assert _to_number(value) == 0

    def test_custom_default_is_honoured(self):
        assert _to_number(None, default=-1) == -1

    def test_bool_is_not_special_cased(self):
        """Documents actual behaviour: float(True) == 1.0."""
        assert _to_number(True) == 1


class TestNormalizeSupplierName:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("Sharma Traders", "sharma"),
            ("SHARMA  SUPPLIERS", "sharma"),
            ("Gupta Distributor", "gupta"),
            ("Ram Kumar Wholesale", "ram kumar"),
            ("  Verma Vendors  ", "verma"),
        ],
    )
    def test_strips_trailing_business_suffix(self, raw, expected):
        assert _normalize_supplier_name(raw) == expected

    def test_single_word_keeps_its_suffix(self):
        """'Traders' alone is the whole name — stripping it would leave nothing."""
        assert _normalize_supplier_name("Traders") == "traders"

    def test_suffix_only_stripped_from_the_end(self):
        assert _normalize_supplier_name("Traders Sharma") == "traders sharma"

    def test_non_suffix_names_pass_through_lowercased(self):
        assert _normalize_supplier_name("Sharma Kirana") == "sharma kirana"


class TestMatchSupplier:
    def test_empty_directory_returns_normalized_name(self):
        assert _match_supplier("Sharma Traders", {}) == "sharma"

    def test_exact_match_returns_display_name(self):
        directory = {"sharma": "Sharma Traders"}
        assert _match_supplier("Sharma Suppliers", directory) == "Sharma Traders"

    def test_near_miss_still_matches_above_threshold(self):
        """STT mishears names constantly; 'sharmaa' must still resolve."""
        directory = {"sharma": "Sharma Traders"}
        assert _match_supplier("Sharmaa", directory) == "Sharma Traders"

    def test_unrelated_name_falls_back_to_normalized_new_supplier(self):
        directory = {"sharma": "Sharma Traders"}
        assert _match_supplier("Bhatia Distributors", directory) == "bhatia"

    def test_picks_the_best_of_several_candidates(self):
        directory = {
            "sharma": "Sharma Traders",
            "gupta": "Gupta Stores",
            "verma": "Verma & Sons",
        }
        assert _match_supplier("Gupta Suppliers", directory) == "Gupta Stores"


class TestFormatPurchaseDate:
    def test_none_renders_as_dash(self):
        assert _format_purchase_date(None) == "-"

    def test_today(self):
        assert _format_purchase_date(datetime.datetime.now(IST)) == "Today"

    def test_yesterday(self):
        assert (
            _format_purchase_date(datetime.datetime.now(IST) - datetime.timedelta(days=1))
            == "Yesterday"
        )

    def test_older_dates_render_day_and_month(self):
        old = datetime.datetime(2026, 3, 14, 12, 0, tzinfo=IST)
        assert _format_purchase_date(old) == "14 Mar"

    def test_naive_or_invalid_input_degrades_to_dash(self):
        assert _format_purchase_date("not-a-timestamp") == "-"

    def test_uses_ist_not_utc(self):
        """20:00 UTC is 01:30 IST the *next* day.

        The shopkeeper's clock decides which day a purchase belongs to, not the
        server's UTC clock. Fixed date so the assertion never depends on today.
        """
        late_utc = datetime.datetime(2020, 3, 14, 20, 0, tzinfo=datetime.UTC)
        assert _format_purchase_date(late_utc) == "15 Mar"


class TestApplyPayment:
    """apply_payment returns (matched_count, total_owed, paid, remaining)."""

    def _seed_dues(self, fake_db, entries):
        for i, (amount, days_ago, modifier) in enumerate(entries):
            fake_db.seed(
                f"users/u1/udhaar/e{i}",
                {
                    "customer_name": "ramesh",
                    "customer_modifier": modifier,
                    "amount": amount,
                    "timestamp": _ts(days_ago),
                },
            )
        return fake_db.collection("users").document("u1").collection("udhaar")

    def test_no_matching_customer_returns_zeros(self, fake_db):
        ref = self._seed_dues(fake_db, [(100, 1, "")])
        assert apply_payment(ref, "unknown", "", 500) == (0, 0, 0, 0)

    def test_exact_settle_deletes_every_entry(self, fake_db):
        ref = self._seed_dues(fake_db, [(100, 2, ""), (200, 1, "")])
        matched, owed, paid, remaining = apply_payment(ref, "ramesh", "", 300)

        assert (matched, owed, paid, remaining) == (2, 300, 300, 0)
        assert fake_db.paths_under("users/u1/udhaar") == []

    def test_overpayment_settles_without_creating_credit(self, fake_db):
        ref = self._seed_dues(fake_db, [(100, 1, "")])
        matched, owed, paid, remaining = apply_payment(ref, "ramesh", "", 5000)

        assert (owed, paid, remaining) == (100, 100, 0)
        assert matched == 1
        assert fake_db.paths_under("users/u1/udhaar") == []

    def test_partial_payment_clears_oldest_debt_first(self, fake_db):
        # 100 owed 10 days ago, 200 owed yesterday. Paying 100 must clear the old one.
        ref = self._seed_dues(fake_db, [(100, 10, ""), (200, 1, "")])
        matched, owed, paid, remaining = apply_payment(ref, "ramesh", "", 100)

        assert (matched, owed, paid, remaining) == (2, 300, 100, 200)
        left = fake_db.paths_under("users/u1/udhaar")
        assert len(left) == 1
        assert fake_db.docs[left[0]]["amount"] == 200

    def test_partial_payment_reduces_rather_than_deletes(self, fake_db):
        ref = self._seed_dues(fake_db, [(500, 5, "")])
        matched, owed, paid, remaining = apply_payment(ref, "ramesh", "", 200)

        assert (matched, owed, paid, remaining) == (1, 500, 200, 300)
        doc = fake_db.docs["users/u1/udhaar/e0"]
        assert doc["amount"] == 300
        assert "last_payment_at" in doc, "partial payments must record when they happened"
        assert doc["timestamp"] is not None, "the original debt date must be preserved"

    def test_partial_payment_spills_across_entries(self, fake_db):
        ref = self._seed_dues(fake_db, [(100, 10, ""), (100, 5, ""), (100, 1, "")])
        matched, owed, paid, remaining = apply_payment(ref, "ramesh", "", 150)

        assert (matched, owed, paid, remaining) == (3, 300, 150, 150)
        remaining_amounts = sorted(
            fake_db.docs[p]["amount"] for p in fake_db.paths_under("users/u1/udhaar")
        )
        assert remaining_amounts == [50, 100]

    def test_modifier_scopes_the_payment(self, fake_db):
        """Two customers named Ramesh are told apart by their modifier."""
        ref = self._seed_dues(fake_db, [(100, 1, "tailor"), (200, 1, "milkman")])
        matched, owed, paid, _ = apply_payment(ref, "ramesh", "tailor", 100)

        assert (matched, owed, paid) == (1, 100, 100)
        left = fake_db.paths_under("users/u1/udhaar")
        assert len(left) == 1
        assert fake_db.docs[left[0]]["customer_modifier"] == "milkman"

    def test_no_modifier_matches_every_namesake(self, fake_db):
        ref = self._seed_dues(fake_db, [(100, 1, "tailor"), (200, 1, "milkman")])
        matched, owed, _, _ = apply_payment(ref, "ramesh", "", 300)
        assert (matched, owed) == (2, 300)

    def test_customer_name_is_case_insensitive(self, fake_db):
        ref = self._seed_dues(fake_db, [(100, 1, "")])
        matched, _, _, _ = apply_payment(ref, "RAMESH", "", 100)
        assert matched == 1

    def test_unpriced_entries_are_cleared_on_full_settle(self, fake_db):
        """Item rows with no amount must not strand the customer on the ledger."""
        ref = self._seed_dues(fake_db, [(0, 3, ""), (0, 1, "")])
        matched, owed, paid, remaining = apply_payment(ref, "ramesh", "", 1)

        assert (matched, owed, paid, remaining) == (2, 0, 0, 0)
        assert fake_db.paths_under("users/u1/udhaar") == []

    def test_unpriced_rows_skipped_during_partial_payment(self, fake_db):
        ref = self._seed_dues(fake_db, [(0, 10, ""), (200, 5, "")])
        matched, owed, paid, remaining = apply_payment(ref, "ramesh", "", 50)

        assert (owed, paid, remaining) == (200, 50, 150)
        assert matched == 2
        amounts = sorted(
            fake_db.docs[p].get("amount", 0) for p in fake_db.paths_under("users/u1/udhaar")
        )
        assert amounts == [0, 150]

    def test_string_amount_from_llm_is_coerced(self, fake_db):
        ref = self._seed_dues(fake_db, [(300, 1, "")])
        _, _, paid, remaining = apply_payment(ref, "ramesh", "", "100")
        assert (paid, remaining) == (100, 200)

    def test_missing_timestamps_sort_first_without_raising(self, fake_db):
        """Legacy rows predate the timestamp field; comparing them must not crash."""
        fake_db.seed(
            "users/u1/udhaar/legacy",
            {"customer_name": "ramesh", "customer_modifier": "", "amount": 100},
        )
        fake_db.seed(
            "users/u1/udhaar/new",
            {
                "customer_name": "ramesh",
                "customer_modifier": "",
                "amount": 100,
                "timestamp": _ts(1),
            },
        )
        ref = fake_db.collection("users").document("u1").collection("udhaar")
        matched, owed, paid, remaining = apply_payment(ref, "ramesh", "", 100)

        assert (matched, owed, paid, remaining) == (2, 200, 100, 100)
        assert fake_db.paths_under("users/u1/udhaar") == ["users/u1/udhaar/new"]


class TestProcessTransactions:
    """Voice records transactions; it never creates inventory items or suppliers.

    Items and suppliers are set up once through the manual forms, which capture
    price, cost price, unit, category, mobile and GST. A spoken command carries
    none of that, so letting voice create a record produced a half-formed one
    that every later screen had to defend against. These tests pin the boundary:
    voice moves stock and money for things that already exist, and refuses the
    rest with a message instead of writing.
    """

    UID = "u1"

    def _refs(self, fake_db):
        user = fake_db.collection("users").document(self.UID)
        return {
            "uid": self.UID,
            "db": fake_db,
            "user_stock_ref": user.collection("stock"),
            "user_udhaar_ref": user.collection("udhaar"),
            "user_orders_ref": user.collection("orders"),
        }

    def _run(self, fake_db, txn):
        return process_transactions([txn], **self._refs(fake_db))

    def _seed_item(self, fake_db, item="rice", quantity=10, price=50):
        fake_db.seed(
            f"users/{self.UID}/stock/{item}",
            {"item": item, "quantity": quantity, "price": price, "unit": "pcs"},
        )

    def _txn(self, **overrides):
        base = {
            "target": "stock",
            "operation": "add",
            "item": "",
            "qty": 0,
            "unit": "",
            "amount": 0,
            "rate": 0,
            "customer_name": "",
            "customer_modifier": "",
            "supplier_name": "",
            "is_credit": False,
        }
        base.update(overrides)
        return base

    # --- creation is refused -------------------------------------------

    def test_restock_of_unknown_item_errors_and_creates_nothing(self):
        """The old behaviour silently created a stock doc with no price or unit."""
        fake_db = FakeFirestore()
        results, errors = self._run(fake_db, self._txn(item="samosa", qty=100, rate=10))

        assert results == []
        assert len(errors) == 1
        assert "samosa" in errors[0]
        assert fake_db.paths_under(f"users/{self.UID}/stock") == []

    def test_supplier_purchase_of_unknown_item_creates_nothing(self):
        """A supplier purchase is still a restock — it cannot conjure the item."""
        fake_db = FakeFirestore()
        _, errors = self._run(
            fake_db, self._txn(item="samosa", qty=50, supplier_name="ramesh traders")
        )

        assert len(errors) == 1
        assert fake_db.paths_under(f"users/{self.UID}/stock") == []
        assert fake_db.paths_under(f"users/{self.UID}/suppliers_purchases") == []

    def test_supplier_add_errors_and_writes_no_directory_entry(self):
        fake_db = FakeFirestore()
        results, errors = self._run(
            fake_db, self._txn(target="supplier", supplier_name="ramesh traders")
        )

        assert results == []
        assert len(errors) == 1
        assert "Ramesh Traders" in errors[0]
        assert fake_db.paths_under(f"users/{self.UID}/suppliers") == []

    def test_supplier_delete_errors_and_keeps_the_supplier(self):
        """A mis-transcribed name must never delete a directory entry."""
        fake_db = FakeFirestore()
        fake_db.seed(
            f"users/{self.UID}/suppliers/s1",
            {"name": "Ramesh Traders", "name_lower": "ramesh traders"},
        )
        results, errors = self._run(
            fake_db,
            self._txn(target="supplier", operation="clear", supplier_name="ramesh traders"),
        )

        assert results == []
        assert len(errors) == 1
        assert fake_db.paths_under(f"users/{self.UID}/suppliers") == [
            f"users/{self.UID}/suppliers/s1"
        ]

    # --- orders take anything the customer asks for ---------------------

    def test_order_for_an_uncatalogued_item_is_recorded_without_creating_stock(self):
        """Shopkeepers order things they don't stock-track. The order is the
        record; inventory stays a reference the item simply isn't in."""
        fake_db = FakeFirestore()
        _, errors = self._run(
            fake_db,
            self._txn(operation="subtract", item="samosa", qty=2, rate=10, customer_name="sujal"),
        )

        assert errors == []
        assert fake_db.paths_under(f"users/{self.UID}/stock") == [], "voice must not catalogue"
        (path,) = fake_db.paths_under(f"users/{self.UID}/orders")
        order = fake_db.docs[path]
        assert order["item"] == "samosa"
        assert order["quantity"] == 2
        assert order["amount"] == 20
        assert order["price"] == 10

    def test_an_uncatalogued_order_item_is_flagged_in_the_result_row(self):
        """The Stock cell carries the same "!" the Orders page shows."""
        fake_db = FakeFirestore()
        results, _ = self._run(
            fake_db,
            self._txn(operation="subtract", item="samosa", qty=2, rate=10, customer_name="sujal"),
        )

        (row,) = [r for g in results for r in g["rows"]]
        assert row["Stock"] == "!"

    def test_uncatalogued_and_stocked_items_share_one_order(self):
        fake_db = FakeFirestore()
        self._seed_item(fake_db, "rice", quantity=10, price=50)
        _, errors = process_transactions(
            [
                self._txn(operation="subtract", item="rice", qty=2, customer_name="sujal"),
                self._txn(
                    operation="subtract", item="samosa", qty=4, rate=10, customer_name="sujal"
                ),
            ],
            **self._refs(fake_db),
        )

        assert errors == []
        orders = [fake_db.docs[p] for p in fake_db.paths_under(f"users/{self.UID}/orders")]
        assert {o["item"] for o in orders} == {"rice", "samosa"}
        assert len({o["order_id"] for o in orders}) == 1
        assert fake_db.docs[f"users/{self.UID}/stock/rice"]["quantity"] == 8, "stocked item moves"

    def test_credit_sale_of_an_uncatalogued_item_records_udhaar_and_an_order(self):
        fake_db = FakeFirestore()
        _, errors = self._run(
            fake_db,
            self._txn(
                operation="subtract",
                item="samosa",
                qty=2,
                rate=10,
                customer_name="sujal",
                is_credit=True,
            ),
        )

        assert errors == []
        (udhaar,) = [fake_db.docs[p] for p in fake_db.paths_under(f"users/{self.UID}/udhaar")]
        assert udhaar["item"] == "samosa"
        assert udhaar["amount"] == 20
        assert len(fake_db.paths_under(f"users/{self.UID}/orders")) == 1
        assert fake_db.paths_under(f"users/{self.UID}/stock") == []

    def test_an_uncatalogued_item_with_no_price_still_reaches_the_order(self):
        """Nothing to fall back on, so it lands at ₹0 for the shopkeeper to fix
        on the Orders page — losing the line entirely would be worse."""
        fake_db = FakeFirestore()
        _, errors = self._run(
            fake_db, self._txn(operation="subtract", item="samosa", qty=3, customer_name="sujal")
        )

        assert errors == []
        (path,) = fake_db.paths_under(f"users/{self.UID}/orders")
        assert fake_db.docs[path]["quantity"] == 3
        assert fake_db.docs[path]["amount"] == 0

    def test_a_counter_sale_books_to_the_walk_in_order(self):
        """Nobody named, item not catalogued — the old code rejected this
        outright. A sale is an order, so it lands on a card that can be renamed
        and billed instead of being lost to an error."""
        fake_db = FakeFirestore()
        results, errors = self._run(
            fake_db, self._txn(operation="subtract", item="samosa", qty=2, rate=10)
        )

        assert errors == []
        (path,) = fake_db.paths_under(f"users/{self.UID}/orders")
        order = fake_db.docs[path]
        assert order["customer_name"] == WALK_IN_CUSTOMER
        assert order["customer_modifier"] == ""
        assert order["item"] == "samosa"
        assert order["amount"] == 20
        assert fake_db.paths_under(f"users/{self.UID}/stock") == []
        assert [r["Customer"] for g in results for r in g["rows"]] == ["Walk-in"]

    def test_a_counter_sale_of_a_stocked_item_still_moves_stock(self):
        fake_db = FakeFirestore()
        self._seed_item(fake_db, "rice", quantity=10, price=50)
        _, errors = self._run(fake_db, self._txn(operation="subtract", item="rice", qty=3))

        assert errors == []
        assert fake_db.docs[f"users/{self.UID}/stock/rice"]["quantity"] == 7
        (path,) = fake_db.paths_under(f"users/{self.UID}/orders")
        assert fake_db.docs[path]["customer_name"] == WALK_IN_CUSTOMER
        assert fake_db.docs[path]["amount"] == 150, "priced from inventory"

    def test_counter_sale_items_spoken_together_share_one_walk_in_order(self):
        fake_db = FakeFirestore()
        _, errors = process_transactions(
            [
                self._txn(operation="subtract", item="samosa", qty=2, rate=10),
                self._txn(operation="subtract", item="kachori", qty=1, rate=15),
            ],
            **self._refs(fake_db),
        )

        assert errors == []
        orders = [fake_db.docs[p] for p in fake_db.paths_under(f"users/{self.UID}/orders")]
        assert {o["item"] for o in orders} == {"samosa", "kachori"}
        assert len({o["order_id"] for o in orders}) == 1

    def test_a_named_sale_never_books_to_the_walk_in_card(self):
        fake_db = FakeFirestore()
        self._seed_item(fake_db, "rice", quantity=10, price=50)
        self._run(
            fake_db,
            self._txn(operation="subtract", item="rice", qty=1, customer_name="sujal"),
        )

        (path,) = fake_db.paths_under(f"users/{self.UID}/orders")
        assert fake_db.docs[path]["customer_name"] == "sujal"

    def test_credit_with_no_name_becomes_a_walk_in_order_not_a_ledger_debt(self):
        """There is nobody to chase for the money, so it must not reach the
        ledger — but the goods still left the shop, so the order stands."""
        fake_db = FakeFirestore()
        _, errors = self._run(
            fake_db, self._txn(operation="subtract", item="samosa", qty=2, is_credit=True)
        )

        assert errors == []
        assert fake_db.paths_under(f"users/{self.UID}/udhaar") == []
        (path,) = fake_db.paths_under(f"users/{self.UID}/orders")
        assert fake_db.docs[path]["customer_name"] == WALK_IN_CUSTOMER

    def test_a_spoken_item_close_to_a_stocked_one_still_matches_inventory(self):
        """Accepting new items must not stop STT slips resolving to real stock."""
        fake_db = FakeFirestore()
        self._seed_item(fake_db, "rice", quantity=10, price=50)
        _, errors = self._run(
            fake_db, self._txn(operation="subtract", item="ricee", qty=2, customer_name="sujal")
        )

        assert errors == []
        (path,) = fake_db.paths_under(f"users/{self.UID}/orders")
        assert fake_db.docs[path]["item"] == "rice"
        assert fake_db.docs[path]["amount"] == 100, "priced from inventory"
        assert fake_db.docs[f"users/{self.UID}/stock/rice"]["quantity"] == 8

    # --- transactions still work ---------------------------------------

    def test_restock_of_known_item_updates_quantity_and_price(self):
        fake_db = FakeFirestore()
        self._seed_item(fake_db, "rice", quantity=10, price=50)
        results, errors = self._run(fake_db, self._txn(item="rice", qty=5, rate=60))

        assert errors == []
        stock = fake_db.docs[f"users/{self.UID}/stock/rice"]
        assert stock["quantity"] == 15
        assert stock["price"] == 60  # a rate on a plain restock is the sell price
        assert stock["unit"] == "pcs"  # merge, so manual fields survive
        assert results

    def test_sale_of_known_item_reduces_stock_and_records_an_order(self):
        fake_db = FakeFirestore()
        self._seed_item(fake_db, "rice", quantity=10, price=50)
        _, errors = self._run(
            fake_db,
            self._txn(operation="subtract", item="rice", qty=3, customer_name="sujal"),
        )

        assert errors == []
        assert fake_db.docs[f"users/{self.UID}/stock/rice"]["quantity"] == 7
        assert len(fake_db.paths_under(f"users/{self.UID}/orders")) == 1

    def test_a_voice_sale_is_numbered_like_any_other_order(self):
        fake_db = FakeFirestore()
        self._seed_item(fake_db, "rice", quantity=10, price=50)
        self._run(
            fake_db,
            self._txn(operation="subtract", item="rice", qty=3, customer_name="sujal"),
        )

        (path,) = fake_db.paths_under(f"users/{self.UID}/orders")
        assert fake_db.docs[path]["order_no"] == 1
        assert fake_db.docs[f"users/{self.UID}"]["order_seq"] == 1

    def test_a_follow_up_reuses_the_orders_number_instead_of_drawing_a_new_one(self):
        """A follow-up utterance appends to the same order card, so it is the
        same order — and must not consume a second number from the counter."""
        fake_db = FakeFirestore()
        self._seed_item(fake_db, "rice", quantity=10, price=50)
        self._seed_item(fake_db, "dal", quantity=10, price=80)
        fake_db.seed(
            f"users/{self.UID}/orders/existing",
            {"customer_name": "sujal", "customer_modifier": "", "order_id": "o1", "order_no": 4},
        )
        fake_db.seed(f"users/{self.UID}", {"order_seq": 4})

        process_transactions(
            [self._txn(operation="subtract", item="dal", qty=1, customer_name="sujal")],
            recent_customer="sujal",
            recent_order_id="o1",
            **self._refs(fake_db),
        )

        added = [
            fake_db.docs[p]
            for p in fake_db.paths_under(f"users/{self.UID}/orders")
            if fake_db.docs[p].get("item") == "dal"
        ]
        assert [d["order_no"] for d in added] == [4]
        assert fake_db.docs[f"users/{self.UID}"]["order_seq"] == 4, "counter must not advance"

    def test_supplier_purchase_of_known_item_records_purchase_without_directory_entry(self):
        """Decision: an unsaved supplier does not block a delivery, but voice
        still never writes to the suppliers directory."""
        fake_db = FakeFirestore()
        self._seed_item(fake_db, "soap", quantity=100, price=20)
        _, errors = self._run(
            fake_db,
            self._txn(item="soap", qty=300, rate=12, supplier_name="ramesh traders"),
        )

        assert errors == []
        stock = fake_db.docs[f"users/{self.UID}/stock/soap"]
        assert stock["quantity"] == 400
        assert stock["price"] == 20  # cost price must not overwrite the sell price
        assert len(fake_db.paths_under(f"users/{self.UID}/suppliers_purchases")) == 1
        assert fake_db.paths_under(f"users/{self.UID}/suppliers") == []

    def test_supplier_read_still_lists_purchases(self):
        fake_db = FakeFirestore()
        fake_db.seed(
            f"users/{self.UID}/suppliers/s1",
            {"name": "Ramesh Traders", "name_lower": "ramesh traders"},
        )
        fake_db.seed(
            f"users/{self.UID}/suppliers_purchases/p1",
            {
                "supplier_name": "Ramesh Traders",
                "item_name": "soap",
                "quantity": 300,
                "amount": 3600,
                "timestamp": _ts(1),
            },
        )
        results, errors = self._run(
            fake_db,
            self._txn(target="supplier", operation="read", supplier_name="ramesh traders"),
        )

        assert errors == []
        assert len(results) == 1
        assert results[0]["rows"][0]["Item"] == "Soap"
