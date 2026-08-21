"""The one-time migration onto 30-day bill retention.

It renumbers live orders and permanently deletes every archived bill, so the
things worth pinning are that a dry run really is inert, that numbering follows
order dates rather than whatever order Firestore streams in, and that the wipe
reaches Storage as well as Firestore — an undeleted PDF keeps a public URL.
"""

import datetime
from unittest import mock

import pytest

from scripts.migrate_bills import main, migrate_user
from tests.fakes import FakeFirestore

UID = "u1"
ORDERS = f"users/{UID}/orders"
BILLS = f"users/{UID}/bills"


def _ts(days_ago):
    return datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=days_ago)


def _line(order_id, days_ago, item="rice", **extra):
    return {
        "order_id": order_id,
        "customer_name": "ramesh",
        "customer_modifier": "",
        "item": item,
        "quantity": 1,
        "price": 50,
        "amount": 50,
        "timestamp": _ts(days_ago),
        **extra,
    }


@pytest.fixture
def db():
    return FakeFirestore()


@pytest.fixture
def bucket():
    """Storage double whose bill PDFs live in `.objects`."""
    b = mock.MagicMock()
    b.objects = {}

    def _list_blobs(prefix):
        out = []
        for name in list(b.objects):
            if not name.startswith(prefix):
                continue
            blob = mock.MagicMock()
            blob.name = name
            blob.delete.side_effect = lambda n=name: b.objects.pop(n, None)
            out.append(blob)
        return out

    b.list_blobs.side_effect = _list_blobs
    return b


class TestBackfill:
    def test_numbers_orders_oldest_first(self, db, bucket):
        db.seed(f"{ORDERS}/c", _line("newest", days_ago=1))
        db.seed(f"{ORDERS}/a", _line("oldest", days_ago=30))
        db.seed(f"{ORDERS}/b", _line("middle", days_ago=10))

        migrate_user(db, bucket, UID, apply=True)

        assert db.docs[f"{ORDERS}/a"]["order_no"] == 1
        assert db.docs[f"{ORDERS}/b"]["order_no"] == 2
        assert db.docs[f"{ORDERS}/c"]["order_no"] == 3

    def test_line_items_of_one_order_share_its_number(self, db, bucket):
        db.seed(f"{ORDERS}/a", _line("o1", days_ago=5, item="rice"))
        db.seed(f"{ORDERS}/b", _line("o1", days_ago=5, item="dal"))
        db.seed(f"{ORDERS}/c", _line("o2", days_ago=1))

        migrate_user(db, bucket, UID, apply=True)

        assert db.docs[f"{ORDERS}/a"]["order_no"] == db.docs[f"{ORDERS}/b"]["order_no"] == 1
        assert db.docs[f"{ORDERS}/c"]["order_no"] == 2

    def test_undated_legacy_rows_are_numbered_too(self, db, bucket):
        """Sorting on timestamp alone would silently drop these."""
        db.seed(f"{ORDERS}/a", {"customer_name": "ramesh", "item": "rice", "quantity": 1})
        db.seed(f"{ORDERS}/b", _line("o1", days_ago=1))

        migrate_user(db, bucket, UID, apply=True)

        assert db.docs[f"{ORDERS}/a"]["order_no"] == 1
        assert db.docs[f"{ORDERS}/b"]["order_no"] == 2

    def test_sets_the_counter_and_retires_bill_seq(self, db, bucket):
        db.seed(f"users/{UID}", {"bill_seq": 12, "shop_name": "Sharma Kirana"})
        db.seed(f"{ORDERS}/a", _line("o1", days_ago=2))
        db.seed(f"{ORDERS}/b", _line("o2", days_ago=1))

        migrate_user(db, bucket, UID, apply=True)

        user = db.docs[f"users/{UID}"]
        assert user["order_seq"] == 2
        assert "bill_seq" not in user
        assert user["shop_name"] == "Sharma Kirana", "unrelated settings must survive"


class TestWipe:
    def test_removes_bill_docs_and_pdfs(self, db, bucket):
        db.seed(f"{ORDERS}/a", _line("o1", days_ago=1))
        db.seed(f"{BILLS}/o1", {"bill_number": 3, "download_token": "t"})
        bucket.objects[f"users/{UID}/bills/o1.pdf"] = b"%PDF-"
        bucket.objects["users/u1/inventory/photo.jpg"] = b"jpeg"

        migrate_user(db, bucket, UID, apply=True)

        assert db.paths_under(BILLS) == []
        assert list(bucket.objects) == ["users/u1/inventory/photo.jpg"], (
            "only bill PDFs are in scope"
        )

    def test_orders_themselves_are_untouched(self, db, bucket):
        db.seed(f"{ORDERS}/a", _line("o1", days_ago=1))
        db.seed(f"{BILLS}/o1", {"bill_number": 3})

        migrate_user(db, bucket, UID, apply=True)

        assert db.docs[f"{ORDERS}/a"]["item"] == "rice"


class TestDryRun:
    def test_writes_nothing(self, db, bucket):
        db.seed(f"{ORDERS}/a", _line("o1", days_ago=1))
        db.seed(f"{BILLS}/o1", {"bill_number": 3})
        bucket.objects[f"users/{UID}/bills/o1.pdf"] = b"%PDF-"
        before = {path: dict(data) for path, data in db.docs.items()}

        stats = migrate_user(db, bucket, UID, apply=False)

        assert db.docs == before
        assert list(bucket.objects) == [f"users/{UID}/bills/o1.pdf"]
        assert stats == {"items": 1, "orders": 1, "bill_docs": 1, "blobs": 1}

    def test_is_the_default_from_the_command_line(self, db, bucket, capsys):
        db.seed(f"{ORDERS}/a", _line("o1", days_ago=1))

        with (
            mock.patch("scripts.migrate_bills.init_firebase", return_value=db),
            mock.patch("scripts.migrate_bills.get_bucket", return_value=bucket),
        ):
            assert main([]) == 0

        assert "order_no" not in db.docs[f"{ORDERS}/a"]
        assert "DRY RUN" in capsys.readouterr().out


class TestCommandLine:
    def test_apply_refuses_when_storage_is_unavailable(self, db, capsys):
        """Wiping bill docs without their PDFs strands every blob: still publicly
        readable, no longer referenced, and impossible to find afterwards."""
        db.seed(f"{ORDERS}/a", _line("o1", days_ago=1))
        db.seed(f"{BILLS}/o1", {"bill_number": 3})

        with (
            mock.patch("scripts.migrate_bills.init_firebase", return_value=db),
            mock.patch(
                "scripts.migrate_bills.get_bucket", side_effect=Exception("no bucket configured")
            ),
        ):
            assert main(["--apply"]) == 1

        assert f"{BILLS}/o1" in db.docs, "nothing may be deleted on the refusal path"
        assert "order_no" not in db.docs[f"{ORDERS}/a"]
        assert "Refusing to --apply" in capsys.readouterr().out

    def test_dry_run_still_reports_when_storage_is_unavailable(self, db, capsys):
        db.seed(f"{ORDERS}/a", _line("o1", days_ago=1))

        with (
            mock.patch("scripts.migrate_bills.init_firebase", return_value=db),
            mock.patch("scripts.migrate_bills.get_bucket", side_effect=Exception("no bucket")),
        ):
            assert main([]) == 0

        assert "DRY RUN" in capsys.readouterr().out

    def test_apply_migrates_every_shop(self, db, bucket):
        db.seed(f"{ORDERS}/a", _line("o1", days_ago=1))
        db.seed("users/u2/orders/a", _line("o9", days_ago=1))

        with (
            mock.patch("scripts.migrate_bills.init_firebase", return_value=db),
            mock.patch("scripts.migrate_bills.get_bucket", return_value=bucket),
        ):
            assert main(["--apply"]) == 0

        assert db.docs[f"{ORDERS}/a"]["order_no"] == 1
        assert db.docs["users/u2/orders/a"]["order_no"] == 1

    def test_finds_shops_with_no_user_document(self, db, bucket):
        """A voice-only shop has orders but never a users/{uid} doc of its own."""
        db.seed("users/voice-only/orders/a", _line("o1", days_ago=1))

        with (
            mock.patch("scripts.migrate_bills.init_firebase", return_value=db),
            mock.patch("scripts.migrate_bills.get_bucket", return_value=bucket),
        ):
            main(["--apply"])

        assert db.docs["users/voice-only/orders/a"]["order_no"] == 1

    def test_uid_limits_the_run_to_one_shop(self, db, bucket):
        db.seed(f"{ORDERS}/a", _line("o1", days_ago=1))
        db.seed("users/u2/orders/a", _line("o9", days_ago=1))

        with (
            mock.patch("scripts.migrate_bills.init_firebase", return_value=db),
            mock.patch("scripts.migrate_bills.get_bucket", return_value=bucket),
        ):
            main(["--apply", "--uid", UID])

        assert db.docs[f"{ORDERS}/a"]["order_no"] == 1
        assert "order_no" not in db.docs["users/u2/orders/a"]
