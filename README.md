# BolKhata — Smart Voice-First Inventory & Ledger

![FastAPI](https://img.shields.io/badge/FastAPI-009688?style=flat&logo=fastapi&logoColor=white)
![Sarvam AI](https://img.shields.io/badge/Sarvam_AI-saaras:v3-orange?style=flat)
![Groq](https://img.shields.io/badge/Groq-Llama_3.1-purple?style=flat)
![Firebase](https://img.shields.io/badge/Firebase-FFCA28?style=flat&logo=firebase&logoColor=black)
![PWA](https://img.shields.io/badge/PWA-Ready-blue?style=flat&logo=progressive-web-apps&logoColor=white)
![Vercel](https://img.shields.io/badge/Vercel-Hosted-black?style=flat&logo=vercel&logoColor=white)

BolKhata is a lightning-fast, voice-first inventory management and customer ledger system designed to replace traditional pen-and-paper ledgers (bahi-khatas) for kirana shopkeepers and small business owners.

By leveraging extreme low-latency processing, BolKhata allows shopkeepers to speak naturally in Hindi, Hinglish, or English to record sales, manage inventory, track wholesale supplier purchases, and sync customer credit accounts—all within a fraction of a second.

---

## Core Features

### 1. Voice-First Kirana Operations

* **Zero-Latency Push-to-Talk:** A responsive walkie-talkie UI optimized for mobile devices and noisy shop environments. Desktop users can press and hold the Spacebar to speak.
* **Sarvam AI Transcription & Translation:** Translates and transcribes Hindi, Hinglish, and other regional languages natively via Sarvam AI's saaras:v3 Speech-to-Text engine.
* **Groq Llama 3 Intent Parsing:** Extracts item names, quantities, unit types, transactional amounts, customer/supplier names, and credit modifiers from spoken sentences.
* **Contextual Auto-Fill:** Automatically remembers the current active customer context for 5 minutes. If a shopkeeper says "do packet aur de do" right after making a sale, the app automatically credits the correct customer.

### 2. Real-Time Smart Inventory

* **Fuzzy Match Engine:** Uses dynamic fuzzy matching (thefuzz) to automatically resolve spoken slang, variations, or typos (e.g., "magi" -> "Maggi") to standard stored item IDs.
* **Visual Stock Grid:** A responsive dashboard displaying inventory tiles color-coded by stock level (Red for out-of-stock, Orange for low stock, Green for healthy stock).
* **Manual Overrides:** Edit, rename, or delete items and adjust stock quantities directly from the visual dashboard.

### 3. Customer Credit Ledger (Udhaar Panel)

* **Debt Itemization:** Drill down into specific customer cards to see item-by-item credit logs, unit sizes, unit prices, due dates, and custom notes.
* **Manual Balance Adjustments:** Add, edit, or settle manual credit entries in seconds.
* **WhatsApp Payment Reminders:** A scheduler that formats custom debt breakdown messages and copies/shares them directly to the customer's WhatsApp (simulated integration).

### 4. Supplier & Wholesale Purchasing

* **Automatic Restocking:** Logging supplier purchases (e.g., "Asha Wholesale se 10 packet Surf Excel mangwaya") automatically increments stock counts in your live inventory.
* **Supplier Directory:** Manage and save primary wholesale vendors, mobile numbers, and GST details in a clean directory.
* **Invoice Uploads:** Keep digital receipts of wholesale purchases for easier auditing (simulated image upload placeholder).

### 5. Multi-Tenant Security & Reliability

* **Firebase Authentication:** Multi-factor authentication (Phone/OTP, Google, Email) keeps every shopkeeper’s shop ledger strictly private and isolated.
* **API Cooldown & Global Rate Limiting:** State-of-the-art backend rate limiting prevents API overuse and unexpected billing on Sarvam and Groq services.

---

## Example Voice Commands

Speak naturally in Hindi or Hinglish, and BolKhata will instantly map the correct transaction:

| Transaction Type | Example Spoken Hindi Command | Extracted Intent |
| :--- | :--- | :--- |
| **Standard Cash Sale** | *"Do Colgate aur ek Maggi de do."* | Sells 2 Colgate & 1 Maggi (Decreases stock) |
| **Credit Sale (Udhaar)** | *"Suresh ke khate me ek Lux sabun likh do."* | Credits 1 Lux to Suresh (Decreases stock, logs to ledger) |
| **Contextual Sale** | *"Ramesh Delhi wale ke khate me 50 rupey ki 2 Maggi likho."* | Accounts for local modifiers (Delhi wale Ramesh) & custom pricing |
| **Supplier Purchase** | *"Parle distributor se 10 packet Parle-G 120 rupey me liya."* | Increases Parle-G stock by 10, logs ₹120 purchase from Parle |
| **Checking Stock** | *"Toothpaste kitna bacha hai dekhna?"* | Performs instant fuzzy search and prints current stock |
| **Order History Inquiry** | *"Nehru apartment wale Sharma ji ke orders dikhao."* | Filters and lists all orders for that specific customer |
| **Settle Credit** | *"Ramesh ka khata clear kar do."* | Wipes all credit dues for Ramesh |
| **Clear Inventory** | *"Saara stock delete kar do."* | Requires physical button verification before clearing stock |

---

## Tech Stack & Architecture

* **Frontend:** Modular Single Page App (SPA) built using native HTML5, CSS3, and JavaScript.
* **PWA Engine:** Service Worker (sw.js) and Web App Manifest (manifest.json) for offline asset caching, standalone launcher capability, and responsive layout scaling.
* **Backend:** FastAPI (Python) optimized for extremely low routing overhead.
* **Database & Auth:** Google Firebase (Firestore Database, Firebase Authentication).
* **Language Engines:** Sarvam AI (Speech-to-Text & Native Translation), Groq Cloud (Llama 3.1 LLM for structure extraction).

---

## API Endpoints Reference

BolKhata uses a clean REST API structure. All transactional and write-based endpoints require a `Bearer <Firebase_ID_Token>` in the authorization header.

| Endpoint | Method | Description |
| :--- | :--- | :--- |
| `/config` | `GET` | Fetches client Firebase keys dynamically |
| `/process_voice` | `POST` | Primary entry point. Processes audio binary data and commits intents to Firestore |
| `/inventory` | `GET` | Lists all active stock active names, quantities, and prices |
| `/inventory/{item_id}` | `PUT` | Renames an item or updates its price/stock levels in-place |
| `/inventory/{item_id}` | `DELETE` | Removes a single item from the active database |
| `/confirm_clear_inventory` | `POST` | Deletes the entire stock collection (requires UI verification) |
| `/suppliers` | `GET` | Lists wholesale purchase history and compiles monthly totals |
| `/suppliers/purchase` | `POST` | Logs a wholesale transaction, automatically updating related item stock |
| `/suppliers/list` | `GET` | Retrieves saved wholesale vendors |
| `/suppliers/add` | `POST` | Registers a new wholesale vendor in the directory |
| `/ledger/customers` | `GET` | Compiles a list of active udhaar accounts, dues, and itemizations |
| `/ledger/entry` | `POST` | Manually writes credit/udhaar entries directly |
| `/ledger/entry/{id}` | `PUT` | Edits due notes, phone numbers, or quantities of ledger items |
| `/history` | `GET` | Pulls the last 50 speech transaction logs and parsing errors |

---

## Security & Rate Limiting

* **Siloed Databases:** All subcollections (stock, udhaar, history, suppliers, suppliers_purchases) are uniquely locked under their authenticated Firebase uid path, preventing cross-shop data leaks.
* **Rate Limits Imposed:**
  * **User Cooldown:** 1 request per 3 seconds per user to prevent audio button spamming.
  * **Sarvam STT Global Limits:** Monitored on Firestore to prevent billing overcharges.
  * **Groq RPM/RPD Limits:** Monitored to gracefully handle Groq Cloud rate-limit policies and return a user-friendly wait message ("Thoda ruko!").

---

## License

This project is licensed under the MIT License.
