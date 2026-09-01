# Bill Retention (30 days)

Generated bills used to accumulate forever — a Firestore doc and a Storage PDF
per bill, with `GET /orders` streaming the whole `bills` collection on every page
load. They now expire 30 days after they were last generated, opened, shared, or
edited, and are rebuilt on demand afterwards.

## Why a Rebuilt Bill Is Safe

This only works because a rebuilt bill is identical to the original:

* **Number** — `BK-{order_no:03d}`, taken from the order's own `order_no`, which is allocated when the order is created and never changes. (It used to be drawn from a `bill_seq` counter at generation time and stored only in the bill doc, so deleting the doc would have changed the number.)
* **URL** — the Storage download token is `HMAC-SHA256(BILL_TOKEN_SECRET, "{uid}:{order_id}")`, so a link already shared with a customer goes dead while the PDF is gone and starts working again the moment it is rebuilt.

This is why `BILL_TOKEN_SECRET` matters: without it, tokens fall back to random
values and a rebuilt bill lands at a new URL.

## Pushing Out the Clock

`POST /orders/{order_id}/bill/touch` restarts the window. The frontend fires it
on every bill open and WhatsApp share, and any edit to the order does the same.

## One-Time Infrastructure Setup

Deletion is done by infrastructure, not app code. **Both of these are required —
without them nothing is ever actually deleted.**

```bash
# Firestore: delete bill docs once expires_at passes
gcloud firestore fields ttls update expires_at --collection-group=bills --enable-ttl

# Storage: delete bill PDFs 31 days after their customTime
gcloud storage buckets update gs://<FIREBASE_STORAGE_BUCKET> \
  --lifecycle-file=storage.lifecycle.json
```

## The Same Mechanism Guards the Voice Logs

`users/{uid}/voice_logs` holds transcripts and parsed intents for the support
console (see [architecture](architecture.md)). They carry an `expires_at` 30 days
out for the same reason bills do — but a **TTL policy is per collection group**,
so the rule above does not cover them. This is required too, or transcripts
accumulate forever:

```bash
gcloud firestore fields ttls update expires_at --collection-group=voice_logs --enable-ttl
```

Nothing else is needed: no audio is stored, so there is no Storage half.

`daysSinceCustomTime` matches **only** objects that carry a `customTime`, which
bill generation and `/bill/touch` set and nothing else does — so item photos in
the same bucket are unaffected. The lifecycle window is 31 days against
Firestore's 30 so the metadata doc goes first; readers also skip already-expired
bills themselves, because the TTL sweep can lag its deadline by up to 24 hours.

## Migrating an Existing Database

`scripts/migrate_bills.py` numbers every existing order oldest-first, sets
`order_seq`, drops the retired `bill_seq`, and deletes the old bill docs and PDFs
(their numbers came from the old counter and no longer agree with the new order
numbers). It is a dry run unless given `--apply`.

```bash
python scripts/migrate_bills.py           # report what would change
python scripts/migrate_bills.py --apply   # do it
```
