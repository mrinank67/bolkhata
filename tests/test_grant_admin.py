"""scripts/grant_admin.py — the account line it prints must not spill PII.

This is a control, not cosmetics. The script prints to a terminal whose
scrollback gets pasted into support tickets, and CodeQL flags the unmasked form
as clear-text logging of sensitive data. It is also the kind of thing that gets
"helpfully" un-masked by someone debugging a match, so it is asserted here.
"""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from grant_admin import _describe, _mask_email, _mask_phone  # noqa: E402


def _user(uid="uid-123", email="", phone=""):
    return SimpleNamespace(uid=uid, email=email, phone_number=phone)


class TestMaskEmail:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("shopkeeper@example.com", "s***@example.com"),
            ("a@b.co", "a***@b.co"),
        ],
    )
    def test_keeps_only_the_first_character_and_the_domain(self, raw, expected):
        assert _mask_email(raw) == expected

    @pytest.mark.parametrize("raw", ["", "not-an-email", "@nolocal.com"])
    def test_anything_unparseable_masks_completely(self, raw):
        """Rather than fall through and print the raw value."""
        assert _mask_email(raw) == "***"

    def test_the_local_part_never_survives(self):
        assert "shopkeeper" not in _mask_email("shopkeeper@example.com")


class TestMaskPhone:
    def test_keeps_the_last_four_digits(self):
        assert _mask_phone("+919876543210") == "***3210"

    def test_the_country_code_and_body_never_survive(self):
        masked = _mask_phone("+919876543210")
        assert "98765" not in masked
        assert "+91" not in masked

    @pytest.mark.parametrize("raw", ["", "12", "1234"])
    def test_a_number_too_short_to_mask_is_hidden_entirely(self, raw):
        """Keeping "the last four" of a four-digit string would print all of it."""
        assert _mask_phone(raw) == "***"


class TestDescribe:
    def test_the_uid_is_shown_in_full(self):
        """It is opaque, and it is what the operator acts on."""
        assert "uid-123" in _describe(_user())

    def test_neither_the_email_nor_the_phone_appears_verbatim(self):
        line = _describe(_user(email="shopkeeper@example.com", phone="+919876543210"))
        assert "shopkeeper@example.com" not in line
        assert "+919876543210" not in line
        assert "9876543210" not in line

    def test_enough_survives_to_recognise_the_account(self):
        line = _describe(_user(email="shopkeeper@example.com", phone="+919876543210"))
        assert "s***@example.com" in line
        assert "***3210" in line

    def test_absent_fields_are_omitted_rather_than_masked_to_noise(self):
        assert _describe(_user()) == "uid-123"
