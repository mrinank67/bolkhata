# BolKhata — Smart Voice-First Inventory & Ledger

![FastAPI](https://img.shields.io/badge/FastAPI-009688?style=flat&logo=fastapi&logoColor=white)
![Sarvam AI](https://img.shields.io/badge/Sarvam_AI-saaras:v3-orange?style=flat)
![Groq](https://img.shields.io/badge/Groq-Llama_3.1-purple?style=flat)
![Firebase](https://img.shields.io/badge/Firebase-FFCA28?style=flat&logo=firebase&logoColor=black)
![PWA](https://img.shields.io/badge/PWA-Ready-blue?style=flat&logo=progressive-web-apps&logoColor=white)
![Vercel](https://img.shields.io/badge/Vercel-Hosted-black?style=flat&logo=vercel&logoColor=white)

BolKhata is a lightning-fast, voice-first inventory management and customer ledger system designed to replace traditional pen-and-paper ledgers (bahi-khatas) for kirana shopkeepers and small business owners.

By leveraging extreme low-latency processing, BolKhata allows shopkeepers to speak naturally in Hindi, Hinglish, or English to record sales, manage inventory, track wholesale supplier purchases, sync customer credit accounts, build customer orders, and generate shareable PDF bills — all within a fraction of a second.

---

## Core Features

### 1. Voice-First Kirana Operations

* **Zero-Latency Push-to-Talk:** A responsive walkie-talkie UI optimized for mobile devices and noisy shop environments. Desktop users can press and hold the Spacebar to speak.
* **Sarvam AI Transcription & Translation:** Translates and transcribes Hindi, Hinglish, and other regional languages natively via Sarvam AI's saaras:v3 Speech-to-Text engine.
* **Groq Llama 3 Intent Parsing:** Extracts item names, quantities (including fractions like 2.5 kilo), unit types, transactional amounts, per-unit rates, customer/supplier names, and credit modifiers from spoken sentences.
* **Contextual Auto-Fill:** Automatically remembers the current active customer context for 5 minutes. If a shopkeeper says "do packet aur de do" right after making a sale, the app automatically credits the correct customer.
* **Customer Disambiguation:** When two customers share a name (e.g. two Sureshes), the app prompts the shopkeeper to pick the right one before applying credit sales, payments, settlements, or reminders.

### 2. Real-Time Smart Inventory

* **Fuzzy Match Engine:** Uses dynamic fuzzy matching (thefuzz) to automatically resolve spoken slang, variations, or typos (e.g., "magi" -> "Maggi") to standard stored item IDs.
* **Visual Stock Grid:** A responsive dashboard displaying inventory tiles color-coded by stock level (out-of-stock, low stock, healthy stock).
* **Manual Overrides:** Edit, rename, or delete items and adjust stock quantities directly from the visual dashboard.

### 3. Customer Credit Ledger (Udhaar Panel)

* **Debt Itemization:** Drill down into specific customer cards to see item-by-item credit logs, unit sizes, unit prices, due dates, and custom notes.
* **Amount-Only Credit:** Record lump-sum dues without naming an item ("Suresh pe 800 ka udhaar").
* **Voice Payments:** Record full or partial payments ("Suresh ne 400 diye") — payments are applied to the oldest dues first, and fully settled entries are cleared automatically.
* **Manual Balance Adjustments:** Add, edit, or settle manual credit entries in seconds.
* **WhatsApp Payment Reminders:** Formats a debt summary message with a signed UPI payment link and opens it directly in WhatsApp for the customer's saved number.
* **UPI Payment Links:** Each reminder includes a tamper-proof payment page (signed token, 24-hour expiry) with a one-tap "Pay Now" UPI deep link to the shopkeeper's configured UPI ID.

### 4. Supplier & Wholesale Purchasing

* **Automatic Restocking:** Logging supplier purchases (e.g., "Asha Wholesale se 10 packet Surf Excel mangwaya") automatically increments stock counts in your live inventory.
* **Supplier Name Normalization:** Spoken supplier names are normalized (suffixes like "wholesale", "traders" stripped) and fuzzy-matched against the existing directory to avoid duplicates.
* **Supplier Directory:** Add, edit, and save primary wholesale vendors, mobile numbers, and GST details in a clean directory (duplicate names are rejected on rename).
* **Invoice Uploads:** Keep digital receipts of wholesale purchases for easier auditing (simulated image upload placeholder).

### 5. Customer Orders & Bill Generation

* **Order Management Page:** A dedicated Orders panel groups line items into per-session orders (by `order_id`). Each card expands to show item-by-item quantity, unit price, and amount, with running per-order totals and a summary of total order count and value.
* **Voice or Manual Entry:** Build orders by speaking naturally or via the + button. Add, edit, or remove individual line items and delete whole orders inline — no page reloads.
* **Inventory-Aware Pricing:** New order line items auto-fill their unit price from live inventory, with an item-name autocomplete sourced from current stock.
* **Stock-Safe Edits:** Order edits intentionally never mutate inventory stock — stock reconciliation is deferred to billing — so editing an order never triggers stray stock writes.
* **One-Tap PDF Bills:** Generate a branded A4 PDF invoice for any order, complete with an itemized table, quantity/rate/total columns, and a grand total. Bills carry a stable, sequential bill number (`BK-001`, `BK-002`, …) that stays the same across regenerations.
* **Shop Profile ("Bill From"):** Account Settings captures the shop name, mobile, and address that print on every bill — alongside the UPI ID used for payment reminders.
* **Permanent Shareable Links:** Each bill is archived to Firebase Storage and served through a non-expiring, unguessable download token, so it can be reopened or re-shared anytime; regenerating a bill keeps the same number and link.
* **Send Bill on WhatsApp:** One tap formats a Hinglish message with the bill link and opens it in WhatsApp for the customer's saved number.

### 6. Multi-Tenant Security & Reliability

* **Firebase Authentication:** Phone/OTP, Google, and Email sign-in keep every shopkeeper's ledger strictly private and isolated.
* **Deny-All Firestore Rules:** Clients never talk to Firestore directly; all access flows through the authenticated API.
* **Layered Rate Limiting:** Per-user cooldowns, per-user daily caps, and global sliding-window limits protect the Sarvam and Groq quotas from abuse.

---

## Example Voice Commands

Speak naturally in Hindi or Hinglish, and BolKhata will instantly map the correct transaction:

| Transaction Type | Example Spoken Hindi Command | Extracted Intent |
| :--- | :--- | :--- |
| **Standard Cash Sale** | *"Do Colgate aur ek Maggi de do."* | Sells 2 Colgate & 1 Maggi (decreases stock) |
| **Credit Sale (Udhaar)** | *"Suresh ke khate me ek Lux sabun likh do."* | Credits 1 Lux to Suresh (decreases stock, logs to ledger) |
| **Contextual Sale** | *"Ramesh Delhi wale ke khate me 50 rupey ki 2 Maggi likho."* | Accounts for local modifiers (Delhi wale Ramesh) & custom pricing |
| **Amount-Only Credit** | *"Suresh pe 800 rupey ka udhaar likho."* | Adds an 800 rupee lump-sum due to Suresh's ledger |
| **Payment Received** | *"Suresh ne 400 rupey diye."* | Records a 400 rupee payment, settling oldest dues first |
| **Supplier Purchase** | *"Parle distributor se 10 packet Parle-G 120 rupey me liya."* | Increases Parle-G stock by 10, logs a 120 rupee purchase from Parle |
| **Customer Order** | *"Raj ko do Maggi pandrah rupey wali de do."* | Logs a non-credit sale to Raj — appears as an order on the Orders page, ready to bill |
| **Checking Stock** | *"Toothpaste kitna bacha hai dekhna?"* | Performs instant fuzzy search and prints current stock |
| **Order History Inquiry** | *"Nehru apartment wale Sharma ji ke orders dikhao."* | Filters and lists all orders for that specific customer |
| **Send Reminder** | *"Suresh ko payment ka reminder bhejo."* | Builds a WhatsApp reminder with a UPI payment link |
| **Settle Credit** | *"Ramesh ka khata clear kar do."* | Wipes all credit dues for Ramesh |
| **Clear Inventory** | *"Saara stock delete kar do."* | Requires button confirmation before clearing stock |

---

## Tech Stack & Architecture

* **Frontend:** Modular Single Page App (SPA) built using native HTML5, CSS3, and JavaScript. No build step.
* **PWA Engine:** Service Worker (sw.js) and Web App Manifest (manifest.json) for offline asset caching, standalone launcher capability, and responsive layout scaling.
* **Backend:** FastAPI (Python) optimized for extremely low routing overhead. Deployed as a Vercel Python serverless function.
* **Database & Auth:** Google Firebase (Firestore Database, Firebase Authentication, Firebase Storage for archived bill PDFs).
* **Bill Rendering:** Server-side A4 PDF invoices generated with ReportLab and uploaded to Firebase Storage with a permanent download token.
* **Language Engines:** Sarvam AI (Speech-to-Text & Native Translation), Groq Cloud (Llama 3.1 LLM for structure extraction).

---

## API Endpoints Reference

BolKhata uses a clean REST API structure. All endpoints except `/config` and `/pay` require a `Bearer <Firebase_ID_Token>` in the Authorization header.

| Endpoint | Method | Description |
| :--- | :--- | :--- |
| `/config` | `GET` | Fetches client Firebase keys dynamically |
| `/process_voice` | `POST` | Primary entry point. Processes audio binary data and commits intents to Firestore |
| `/voice/resolve` | `POST` | Completes a transaction after the user picks a customer in a disambiguation prompt |
| `/inventory` | `GET` | Lists all active stock names, quantities, and prices |
| `/inventory/{item_id}` | `PUT` | Renames an item or updates its price/stock levels in-place |
| `/inventory/{item_id}` | `DELETE` | Removes a single item from the active database |
| `/confirm_clear_inventory` | `POST` | Deletes the entire stock collection (requires UI verification) |
| `/suppliers` | `GET` | Lists wholesale purchase history and compiles monthly totals |
| `/suppliers/purchase` | `POST` | Logs a wholesale transaction, automatically updating related item stock |
| `/suppliers/list` | `GET` | Retrieves saved wholesale vendors |
| `/suppliers/add` | `POST` | Registers a new wholesale vendor in the directory |
| `/suppliers/{supplier_id}` | `PUT` | Edits a saved vendor's name, mobile, and GST (rejects duplicate names) |
| `/suppliers/{supplier_id}` | `DELETE` | Removes a vendor and their purchase history |
| `/ledger/customers` | `GET` | Compiles a list of active udhaar accounts, dues, and itemizations |
| `/ledger/entry` | `POST` | Manually writes credit/udhaar entries directly |
| `/ledger/clear` | `POST` | Settles a customer's dues — full or partial (FIFO), shared with the voice payment flow |
| `/ledger/whatsapp-reminder` | `POST` | Saves a customer's WhatsApp number and reminder schedule |
| `/orders` | `GET` | Lists customer orders grouped by order, with per-order totals and a grand total |
| `/orders` | `POST` | Creates a new order from one or more line items |
| `/orders/{order_id}/items` | `POST` | Appends a line item to an existing order |
| `/orders/{order_id}/bill` | `POST` | Renders a PDF bill, archives it to Storage, and returns a permanent download link |
| `/orders/item/{item_id}` | `PUT` | Edits a single order line item (item, quantity, or price) |
| `/orders/item/{item_id}` | `DELETE` | Removes a single line item from an order |
| `/orders/{order_id}` | `DELETE` | Deletes an entire order and all its line items |
| `/pay/create` | `POST` | Mints a signed UPI payment link token for the caller's saved UPI ID |
| `/pay` | `GET` | Public payment page; validates the signed token and renders a UPI deep link |
| `/settings` | `GET` / `PUT` | Reads or updates the shopkeeper's UPI ID (validated VPA format) and shop "Bill From" profile (name, mobile, address) |
| `/history` | `GET` | Pulls the last 50 speech transaction logs and parsing errors |
| `/history` | `DELETE` | Clears the voice processing history |

---

## Security & Rate Limiting

* **Siloed Databases:** All subcollections (stock, udhaar, orders, bills, history, suppliers, suppliers_purchases) are uniquely locked under their authenticated Firebase uid path, preventing cross-shop data leaks.
* **Deny-All Firestore Rules:** `firestore.rules` blocks all direct client access; only the backend's Admin SDK touches data. Deploy with `npx firebase-tools deploy --only firestore:rules`.
* **Per-User Bill Storage:** Generated bill PDFs are written to Firebase Storage under `users/{uid}/bills/` and exposed only through unguessable, per-bill download tokens — never via a public listing.
* **Signed Payment Links:** `/pay` tokens are signed with `PAY_LINK_SECRET` (required; the app refuses to mint links without it) and expire after 24 hours. The payee UPI ID is always read from the authenticated shopkeeper's settings, never from the request.
* **Rate Limits Imposed:**
  * **User Cooldown:** One voice request per 2 seconds per user to prevent audio button spamming.
  * **Per-User Daily Cap:** 400 voice requests per user per day, so a single account cannot exhaust the shared quota.
  * **Sarvam STT Global Limits:** Firestore-backed sliding window to stay within plan quotas.
  * **Groq RPM/RPD Limits:** Monitored to gracefully handle Groq Cloud rate-limit policies and return a user-friendly wait message ("Thoda ruko!").
* **Input Validation:** All write endpoints enforce bounds (non-negative quantities/amounts, length caps, UPI VPA format) via Pydantic models.
* **Hardened Frontend:** All user-derived strings are HTML-escaped before rendering; CORS is restricted to local development origins plus an optional `ALLOWED_ORIGINS` allowlist.
* **Privacy:** Voice transcripts and parsed intents are only logged when `DEBUG_LOGS=1`; production logs contain timings only.

---

## License

This project is licensed under the MIT License.
