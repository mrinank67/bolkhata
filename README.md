# BolKhata — Smart Voice Inventory & Ledger [EXPERIMENTAL]

![FastAPI](https://img.shields.io/badge/FastAPI-009688?style=flat&logo=fastapi&logoColor=white)
![Sarvam](https://img.shields.io/badge/Sarvam_AI-STT-FF4500)
![Groq](https://img.shields.io/badge/Groq-LLM-purple)
![Vercel](https://img.shields.io/badge/Vercel-000?style=flat&logo=vercel&logoColor=white)
![Firebase](https://img.shields.io/badge/Firebase-FFCA28?style=flat&logo=firebase&logoColor=black)

A lightning-fast, voice-first inventory management system designed to replace traditional pen-and-paper ledgers (*bahi-khatas*) for shopkeepers and small businesses.

BolKhata prioritizes extreme low latency, ensuring the AI can process Hindi/Hinglish voice commands, display structured transaction receipts, and securely update the cloud database — just as fast as a shopkeeper can write it down.

## Key Features

- **Zero-Latency Voice Capture:** Press-and-hold walkie-talkie UI with spacebar desktop support — designed for noisy shop environments.
- **All-India Language Support:** Powered by Sarvam AI (`saaras:v3`) to automatically detect, translate, and transcribe any of the 22 supported Indian languages natively.
- **Intelligent Intent Extraction:** Groq Llama 3 (`llama-3.1-8b-instant`) takes the translated English text and flawlessly maps voice intents to strict JSON, extracting contextual customer modifiers (e.g., "Delhi wale Ramesh" vs "Park wale Ramesh"), and understanding complex multi-item orders in a single breath.
- **Dynamic Fuzzy Matching:** No hardcoded aliases! The system instantly fetches your live inventory and fuzzy-matches spoken slang or errors to your exact stored items, automatically adapting as you add new stock.
- **Interactive Customer Ledger (Udhaar):** A robust credit tracking panel unified with our udhaar records. Features manual entries, editing/updating, expandable customer cards with total due balances, detailed itemization (units, amounts, due dates), and a custom WhatsApp payment reminder scheduler (placeholder).
- **Suppliers & Purchase Syncing:** Dedicated screen to log wholesale stock purchases from suppliers. Supports tracking unit sizes, purchase prices, dates, and an invoice/proof of purchase image upload (placeholder). Recording supplier purchases automatically increments and updates active inventory stock levels.
- **Enhanced Voice Modifiers:** Upgraded LLM prompts capable of detecting supplier purchase intents, supplier names, specific units (e.g., packets, kg, pieces), and absolute transactional amounts directly from Hinglish utterances.
- **Multi-Tenant Security:** Firebase Auth (Phone/OTP, Google, Email) ensures every shopkeeper's inventory and history is strictly siloed and private.
- **Multi-Page App & Navigation Drawer:** Left-sliding navigation drawer with dedicated pages for Voice Commands, Live Inventory, Transaction History, Suppliers, and Customer Ledger.
- **Live Inventory Dashboard:** Visual, responsive grid tiles displaying real-time stock levels, automatically color-coded for low or out-of-stock items, fetched from a dedicated backend endpoint.
- **Smart History:** Cleanly formatted tables for every transaction, keeping track of the last 50 transactions for easy auditing.
- **Progressive Web App:** Installable on mobile and desktop with offline shell caching, home screen icon, and standalone app mode — no app store required.

## Example Voice Commands

| Intent | Example |
| -------- | --------- |
| **Standard Sale** | *"Do colgate aur ek maggi de do."* |
| **Contextual Sale** | *"Nehru apartment wale Sharma ji ko 2 maggi bech di."* |
| **Restock** | *"Paanch naye lux aaye hain."* |
| **Credit (Udhaar)** | *"Ek sabun Suresh ke khaate mein likh do."* |
| **Credit with Details** | *"Ramesh Delhi ke khate me 2 packet maggi 50 rupey ki likh do."* |
| **Supplier Purchase** | *"Parle distributor se 10 packet Parle-G 120 rupey me khareede."* |
| **Mixed Cash & Credit** | *"Ek maggi de do, aur do lux Ramesh ke khaate mein likh do."* |
| **Check Stock** | *"Bhai, toothpaste kitna bacha hai dekhna."* |
| **Order History Inquiry** | *"Nehru apartment wale Sharma ji ke orders dikhao."* |
| **Full Ledger** | *"Saara stock dikhao."* |
| **Settle Credit** | *"Ramesh ka khata clear kar do."* |
| **Wipe Inventory** | *"Saara stock delete kar do."* |

## Tech Stack

| Layer | Technology |
| ------- | ---------- |
| **Frontend** | Vanilla HTML, CSS, JS (modular SPA, responsive layout) |
| **Backend** | FastAPI (Python) on Vercel serverless |
| **Database & Auth** | Firebase Firestore & Firebase Authentication (`udhaar` and `suppliers_purchases` subcollections) |
| **Speech-to-Text** | Sarvam AI — `saaras:v3` |
| **Intent Extraction** | Groq — `llama-3.1-8b-instant` |
| **Fuzzy Matching** | `thefuzz` (Dynamic live-inventory matching) |
| **PWA** | Service Worker + Web App Manifest |

## PWA Installation

You can install BolKhata directly to your home screen for an app-like experience:

- **Android Chrome:** Tap the "Add to Home Screen" banner at the bottom, or open the browser menu (⋮) and select "Add to Home screen".
- **iOS Safari:** Tap the Share button at the bottom of the screen, scroll down, and select "Add to Home Screen".
- **Desktop (Chrome/Edge):** Click the install icon (monitor with a downward arrow) in the right side of the address bar.

## Security

- API endpoints are protected by Firebase ID Token verification.
- The Firebase service account JSON is excluded from Git via `.gitignore`.
- The reCAPTCHA badge is hidden via CSS but remains functional for phone auth.

## License

This project is licensed under the MIT License.
