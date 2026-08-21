"""One-time migration to the 30-day bill retention scheme.

Bills used to draw their number from a per-shop counter at generation time and
keep it only inside the bill document, so deleting an old bill would have made a
regenerated one come back with a *different* number. Numbers now live on the
order itself (order_no), which makes a bill fully rebuildable and therefore safe
to delete after 30 days of disuse.

This script moves an existing database onto that scheme, per shop:

  1. Numbers every existing order, oldest first, starting at 1.
  2. Sets users/{uid}.order_seq to the highest number handed out, and drops the
     retired bill_seq field.
  3. Deletes every users/{uid}/bills/* document.
  4. Deletes every users/{uid}/bills/*.pdf blob in Storage.

Steps 3 and 4 are a deliberate reset: the old PDFs carry numbers from the old
counter, which no longer agree with the new order numbers. Every bill is rebuilt
on demand, at its new number, the next time someone opens it.

    python scripts/migrate_bills.py              # dry run — reports, changes nothing
    python scripts/migrate_bills.py --apply      # performs the migration

The dry run is the default on purpose: the wipe is irreversible, and renumbering
means an invoice a customer already holds may now name a different order.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from firebase_admin import firestore

from auth import get_bucket, init_firebase
from routes.orders import _order_id_for_doc

# Firestore caps a batch at 500 operations; stop short of it so a batch is never
# rejected for being one write over.
BATCH_LIMIT = 450


def _sort_key(data: dict):
    """Chronological order, with undated docs first so they still get numbered.

    Sorting on the timestamp alone would drop them: they are the oldest records
    in the database and the ones most likely to be missing fields.
    """
    ts = data.get("timestamp")
    return (1, ts) if ts is not None else (0, 0)


def plan_orders(orders_ref) -> tuple[list, int]:
    """Work out which line items get which order number.

    Returns (assignments, order_count) where each assignment is
    (doc_reference, order_no). Grouping uses the same helper the API uses, so an
    order numbered here is exactly the order that shows up as one card in the UI.
    """
    docs = [(doc.reference, doc.to_dict() or {}) for doc in orders_ref.stream()]
    docs.sort(key=lambda pair: _sort_key(pair[1]))

    numbers: dict[str, int] = {}
    assignments = []
    for ref, data in docs:
        oid = _order_id_for_doc(data)
        if oid not in numbers:
            numbers[oid] = len(numbers) + 1
        assignments.append((ref, numbers[oid]))
    return assignments, len(numbers)


def migrate_user(db, bucket, uid: str, *, apply: bool) -> dict:
    """Migrate one shop. Returns a stats dict; writes nothing unless apply."""
    user_ref = db.collection("users").document(uid)
    assignments, order_count = plan_orders(user_ref.collection("orders"))
    bill_refs = [doc.reference for doc in user_ref.collection("bills").stream()]

    blobs = []
    if bucket is not None:
        try:
            blobs = list(bucket.list_blobs(prefix=f"users/{uid}/bills/"))
        except Exception as e:
            print(f"  ! could not list PDFs for {uid}: {e}")

    stats = {
        "items": len(assignments),
        "orders": order_count,
        "bill_docs": len(bill_refs),
        "blobs": len(blobs),
    }
    if not apply:
        return stats

    batch = db.batch()
    pending = 0

    def flush():
        nonlocal batch, pending
        if pending:
            batch.commit()
            batch = db.batch()
            pending = 0

    for ref, order_no in assignments:
        batch.set(ref, {"order_no": order_no}, merge=True)
        pending += 1
        if pending >= BATCH_LIMIT:
            flush()

    for ref in bill_refs:
        batch.delete(ref)
        pending += 1
        if pending >= BATCH_LIMIT:
            flush()

    # Last, so a run that dies partway leaves the counter behind the numbers
    # already written rather than ahead of them — a re-run then re-assigns the
    # same numbers instead of skipping past them.
    batch.set(
        user_ref,
        {"order_seq": order_count, "bill_seq": firestore.DELETE_FIELD},
        merge=True,
    )
    pending += 1
    flush()

    for blob in blobs:
        try:
            blob.delete()
        except Exception as e:
            print(f"  ! could not delete {getattr(blob, 'name', blob)}: {e}")

    return stats


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--apply",
        action="store_true",
        help="perform the migration (without this, only report what would change)",
    )
    parser.add_argument("--uid", help="migrate a single shop instead of all of them")
    args = parser.parse_args(argv)

    db = init_firebase()
    try:
        bucket = get_bucket()
    except Exception as e:
        print(f"! Storage unavailable ({e}) — PDFs will not be deleted.")
        bucket = None

    if args.uid:
        uids = [args.uid]
    else:
        uids = [ref.id for ref in db.collection("users").list_documents()]

    mode = "APPLYING" if args.apply else "DRY RUN — nothing will be written"
    print(f"{mode}. {len(uids)} shop(s).\n")

    totals = {"items": 0, "orders": 0, "bill_docs": 0, "blobs": 0}
    for uid in uids:
        stats = migrate_user(db, bucket, uid, apply=args.apply)
        for key in totals:
            totals[key] += stats[key]
        numbered = "numbered" if args.apply else "would number"
        removed = "removed" if args.apply else "would remove"
        print(
            f"{uid}: {numbered} {stats['orders']} order(s) #1–#{stats['orders']} "
            f"across {stats['items']} line item(s); "
            f"{removed} {stats['bill_docs']} bill doc(s) and {stats['blobs']} PDF(s)"
        )

    print(
        f"\nTotal: {totals['orders']} orders, {totals['items']} line items, "
        f"{totals['bill_docs']} bill docs, {totals['blobs']} PDFs."
    )
    if not args.apply:
        print("Re-run with --apply to perform this migration.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
