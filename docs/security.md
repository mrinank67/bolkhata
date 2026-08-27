# Security & Rate Limiting

## Tenant Isolation

* **Siloed Databases:** All subcollections (stock, udhaar, orders, bills, history, suppliers, suppliers_purchases) and all Storage objects (bills, item photos) are uniquely locked under their authenticated Firebase uid path, preventing cross-shop data leaks. The uid always comes from the verified token, never from the request.
* **Firebase Authentication:** Phone/OTP, Google, and Email sign-in keep every shopkeeper's ledger strictly private and isolated.
* **Deny-All Firestore Rules:** `firestore.rules` blocks all direct client access; only the backend's Admin SDK touches data. Deploy with `npx firebase-tools deploy --only firestore:rules`.
* **Per-User File Storage:** Generated bill PDFs (`users/{uid}/bills/`) and product photos (`users/{uid}/items/`) are exposed only through unguessable download tokens — never via a public listing. Deleting an item or clearing inventory also deletes its photos, deleting an order deletes its bill PDF, and renaming an item carries its photo across; an orphaned blob would otherwise be billed indefinitely.

## Upload & Payment Hardening

* **Hardened Image Uploads:** Product photos are identified by magic bytes (JPEG, PNG, and WebP only — the client's `Content-Type` and filename are ignored entirely), capped at 3 MB before any decoding, and rejected above 40 megapixels so a small file cannot expand into a decompression bomb. Every accepted image is then **fully re-encoded from decoded pixels**, which strips EXIF/GPS metadata (these are photos taken inside a named shop) and discards any appended polyglot payload — no client-supplied bytes ever reach Storage. Storage paths are server-generated UUIDs, so a hostile filename cannot escape the caller's own directory.
* **Signed Payment Links:** `/pay` tokens are signed with `PAY_LINK_SECRET` (required; the app refuses to mint links without it) and expire after 24 hours. The payee UPI ID is always read from the authenticated shopkeeper's settings, never from the request.

## Rate Limits

Each feature holds its own per-user budget, so uploading photos can never consume a shopkeeper's voice quota.

* **User Cooldown:** One voice request per 2 seconds per user to prevent audio button spamming.
* **Per-User Daily Cap:** 400 voice requests per user per day, so a single account cannot exhaust the shared quota.
* **Image Uploads:** A 3-second per-user cooldown and 100 uploads per user per day, plus global ceilings of 60/minute and 2,000/day to bound Firebase Storage growth. Signup is open, so the global cap is the layer that holds even against mass account creation. Creating an item without a photo doesn't spend this budget.
* **Sarvam STT Global Limits:** Firestore-backed sliding window to stay within plan quotas.
* **Groq RPM/RPD Limits:** Monitored to gracefully handle Groq Cloud rate-limit policies and return a user-friendly wait message ("Thoda ruko!").

The limiter is fail-open: if its Firestore transaction fails, the request proceeds rather than blocking users.

## Input & Output Handling

* **Input Validation:** All write endpoints enforce bounds (non-negative quantities/amounts, length caps, UPI VPA format) via Pydantic models. Multipart endpoints get no Pydantic validation, so `POST /inventory` re-asserts the same bounds by hand and additionally validates the item name as a Firestore document ID.
* **Hardened Frontend:** All user-derived strings are HTML-escaped before rendering; CORS is restricted to local development origins plus an optional `ALLOWED_ORIGINS` allowlist.
* **Privacy:** Voice transcripts and parsed intents are only logged when `DEBUG_LOGS=1`; production logs contain timings only.
* **Download Tokens:** Firebase download-token URLs (bills and item photos) are public-but-unguessable, bypass `storage.rules` entirely, and never expire — so they must never be logged, and deleting a record must delete its blobs.

## Automated Security Checks

Secret scanning over the full git history, a tracked-credential-file check, and CodeQL's `security-extended` suite run on every push and pull request — see [Development & Deployment](development.md#automated-quality-checks).
