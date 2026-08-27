# BolKhata — Smart Voice-First Inventory & Ledger

![FastAPI](https://img.shields.io/badge/FastAPI-009688?style=flat&logo=fastapi&logoColor=white)
![Sarvam AI](https://img.shields.io/badge/Sarvam_AI-saaras:v3-orange?style=flat)
![Groq](https://img.shields.io/badge/Groq-GPT_OSS_20B-purple?style=flat)
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
* **Groq GPT-OSS Intent Parsing:** Extracts item names, quantities (including fractions like 2.5 kilo), unit types, transactional amounts, per-unit rates, customer/supplier names, and credit modifiers from spoken sentences.
* **Contextual Auto-Fill:** Automatically remembers the current active customer context for 5 minutes. If a shopkeeper says "do packet aur de do" right after making a sale, the app automatically credits the correct customer.
* **Customer Disambiguation:** When two customers share a name (e.g. two Sureshes), the app prompts the shopkeeper to pick the right one before applying credit sales, payments, settlements, or reminders.
* **Transactions Only — Setup Is Manual:** Voice records what happens day to day (sales, credit, payments, restocks, supplier purchases, queries). It never creates an inventory item or a supplier. Those are catalogued once through the Add Item and Add Supplier forms, which capture the details speech cannot carry — selling price, cost price, unit, category, photo, mobile, GST. Speaking about an item that isn't catalogued returns "*item* inventory mein nahi hai. Pehle app mein add karein." instead of creating a half-filled record, so every voice transaction resolves against a complete catalogue.

### 2. Real-Time Smart Inventory

* **Fuzzy Match Engine:** Uses dynamic fuzzy matching (thefuzz) to automatically resolve spoken slang, variations, or typos (e.g., "magi" -> "Maggi") to standard stored item IDs.
* **Add Item with Photo:** A floating + button on the Inventory page opens a form to catalogue a product in seconds — take a photo, type the name and selling price, save. Cost price, unit (PCS/Box/Pack), opening stock, and category are optional; the photo is optional too. This is the **only** way an item enters the catalogue, and items created here are immediately available to both voice and manual billing.
* **In-App Camera:** Product photos are captured through a live preview *inside the page*, not by handing off to the device camera app — so nothing is written to the phone's gallery or storage. Existing photos can still be picked from the gallery.
* **Visual Stock Grid:** A responsive dashboard displaying inventory tiles color-coded by stock level (out-of-stock, low stock, healthy stock), each showing its product thumbnail with a neutral placeholder for items saved without a photo.
* **Two-Stage Image Compression:** Photos are downscaled in the browser before upload (a ~20x reduction — a 3.5 MB phone photo uploads as ~170 KB) and then re-encoded server-side into a 1024px WebP plus a 256px grid thumbnail, roughly 140 KB of storage per item. Both are served with immutable cache headers so repeat views of the inventory grid cost no Firebase egress.
* **Manual Overrides:** Edit, rename, or delete items and adjust stock quantities directly from the visual dashboard. Renaming an item keeps its photo, and deleting one reclaims its storage.

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
* **Supplier Directory:** Add, edit, and save primary wholesale vendors, mobile numbers, and GST details in a clean directory (duplicate names are rejected on rename). The directory is managed from the Suppliers page only — voice cannot register or remove a vendor, since a spoken name carries no mobile or GST and a mis-transcribed one must never delete an entry. Purchases are still recorded by voice, including from a vendor not yet saved.
* **Invoice Uploads:** Keep digital receipts of wholesale purchases for easier auditing (simulated image upload placeholder).

### 5. Customer Orders & Bill Generation

* **Order Management Page:** A dedicated Orders panel groups line items into per-session orders (by `order_id`). Each card expands to show item-by-item quantity, unit price, and amount, with running per-order totals and a summary of total order count and value.
* **Voice or Manual Entry:** Build orders by speaking naturally or via the + button. Add, edit, or remove individual line items and delete whole orders inline — no page reloads.
* **Inventory-Aware Pricing:** New order line items auto-fill their unit price from live inventory, with an item-name autocomplete sourced from current stock.
* **Stock-Safe Edits:** Order edits intentionally never mutate inventory stock — stock reconciliation is deferred to billing — so editing an order never triggers stray stock writes.
* **One-Tap PDF Bills:** Generate a branded A4 PDF invoice for any order, complete with an itemized table, quantity/rate/total columns, and a grand total. Every order carries a running order number the moment it is created, and its bill takes the same number (`BK-001`, `BK-002`, …) — so the number is a property of the sale, not of when a PDF happened to be printed.
* **Self-Cleaning Bill Archive:** Bills are kept for 30 days after they were last generated, opened, shared, or edited, then deleted — opening one restarts the clock. Nothing is lost: a bill rebuilt months later comes back with the same number at the same link. See [Bill Retention](docs/bill-retention.md) for the mechanics and the one-time project setup it needs.
* **Shop Profile ("Bill From"):** Account Settings captures the shop name, mobile, and address that print on every bill — alongside the UPI ID used for payment reminders.
* **Permanent Shareable Links:** Each bill is archived to Firebase Storage and served through a non-expiring, unguessable download token, so it can be reopened or re-shared anytime; regenerating a bill keeps the same number and link, even if the archived copy was cleaned up in between.
* **Send Bill on WhatsApp:** One tap formats a Hinglish message with the bill link and opens it in WhatsApp for the customer's saved number.

### 6. Multi-Tenant Security & Reliability

* **Firebase Authentication:** Phone/OTP, Google, and Email sign-in keep every shopkeeper's ledger strictly private and isolated.
* **Deny-All Firestore Rules:** Clients never talk to Firestore directly; all access flows through the authenticated API.
* **Layered Rate Limiting:** Per-user cooldowns, per-user daily caps, and global sliding-window limits protect the Sarvam and Groq quotas from abuse.

Full details in [Security & Rate Limiting](docs/security.md).

---

## Example Voice Commands

Speak naturally in Hindi or Hinglish, and BolKhata will instantly map the correct transaction. Every command below acts on an item or supplier that already exists — catalogue those once through the Add Item and Add Supplier forms, then run the shop by voice.

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
| **Supplier Purchase Inquiry** | *"Parle distributor se kitna maal liya?"* | Lists recent purchases and the total spent with that supplier |
| **Clear Inventory** | *"Saara stock delete kar do."* | Requires button confirmation before clearing stock |
| **Uncatalogued Item** | *"Sau samosa add karo."* (samosa not in inventory) | Returns an error asking you to add the item in the app first — voice never creates items |

---

## Built With

A vanilla HTML/CSS/JS progressive web app (no build step) talking to a FastAPI
backend on Vercel, with Firebase for auth, data, and file storage. Speech goes
through Sarvam AI for transcription and Groq's GPT-OSS 20B for intent
extraction; bills are rendered server-side with ReportLab.

Every push and pull request runs 460 automated tests, Ruff and ESLint, secret
scanning, and CodeQL static analysis before anything can reach production.

---

## Documentation

| Document | Covers |
| :--- | :--- |
| [Architecture](docs/architecture.md) | Tech stack, module map, the voice pipeline, the Firestore data model, and the conventions the code depends on |
| [API Reference](docs/api-reference.md) | Every REST endpoint, its method, and what it does |
| [Security & Rate Limiting](docs/security.md) | Tenant isolation, upload hardening, signed links, and every rate-limit budget |
| [Development & Deployment](docs/development.md) | Local setup, environment variables, running the test suite, CI, and deploying |
| [Bill Retention](docs/bill-retention.md) | The 30-day bill lifecycle, its one-time Firestore/Storage setup, and the data migration |

---

## License

This project is licensed under the MIT License.
