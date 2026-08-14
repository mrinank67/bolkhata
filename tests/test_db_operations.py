"""Core ledger arithmetic and supplier-name resolution.

apply_payment() is the highest-consequence pure-ish function in the codebase:
it decides how much of a shopkeeper's outstanding credit gets written off, and
it is shared by both the voice "payment" flow and the manual Clear Dues button.
A bug here silently destroys real debt records.
"""

import datetime

import pytest

from db_operations import (
    _format_purchase_date,
    _match_supplier,
    _normalize_supplier_name,
    _to_number,
    apply_payment,
)

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
        matched, owed, paid, remaining = apply_payment(ref, "ramesh", "tailor", 100)

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
