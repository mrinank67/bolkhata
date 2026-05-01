# BolKhata — Smart Voice Inventory & Ledger

![FastAPI](https://img.shields.io/badge/FastAPI-009688?style=flat&logo=fastapi&logoColor=white)
![Groq](https://img.shields.io/badge/Groq-LLM-purple)
![Whisper](https://img.shields.io/badge/Whisper-STT-412991?logo=openai&logoColor=white)
![Vercel](https://img.shields.io/badge/Vercel-000?style=flat&logo=vercel&logoColor=white)
![Firebase](https://img.shields.io/badge/Firebase-FFCA28?style=flat&logo=firebase&logoColor=black)

A lightning-fast, voice-first inventory management system designed to replace traditional pen-and-paper ledgers (*bahi-khatas*) for shopkeepers and small businesses.

BolKhata prioritizes extreme low latency, ensuring the AI can process Hindi/Hinglish voice commands, display structured transaction receipts, and securely update the cloud database — just as fast as a shopkeeper can write it down.

## Key Features

- **Zero-Latency Voice Capture:** Press-and-hold walkie-talkie UI with spacebar desktop support — designed for noisy shop environments.
- **Dialect & Hinglish Support:** Powered by Groq Whisper-Large-V3 to accurately transcribe heavily accented Hindi/Hinglish speech without hallucinating.
- **Intelligent Intent Extraction:** Llama 3 (`llama-3.1-8b-instant`) instantly translates Devanagari to English, extracts contextual customer modifiers (e.g., "Delhi wale Ramesh" vs "Park wale Ramesh"), and understands complex multi-item orders in a single breath.
- **Dynamic Fuzzy Matching:** No hardcoded aliases! The system instantly fetches your live inventory and fuzzy-matches spoken slang or errors to your exact stored items, automatically adapting as you add new stock.
- **Customer Order & Udhaar Tracking:** Seamlessly separates general orders (*"Ramesh Delhi ko bech diya"*) from credit ledgers (*"Ramesh Delhi ke khate me likh do"*). Query specific customer histories using their exact differentiating context.
- **Multi-Tenant Security:** Firebase Auth (Phone/OTP, Google, Email) ensures every shopkeeper's inventory and history is strictly siloed and private.
- **Smart Dashboard & History:** Cleanly formatted tables for every transaction, with a sliding drawer keeping track of the last 50 transactions for easy auditing.
- **Progressive Web App:** Installable on mobile and desktop with offline shell caching, home screen icon, and standalone app mode — no app store required.

## Example Voice Commands

| Intent | Example |
|--------|---------|
| **Standard Sale** | *"Do colgate aur ek maggi de do."* |
| **Contextual Sale** | *"Nehru apartment wale Sharma ji ko 2 maggi bech di."* |
| **Restock** | *"Paanch naye lux aaye hain."* |
| **Credit (Udhaar)** | *"Ek sabun Suresh ke khaate mein likh do."* |
| **Mixed Cash & Credit** | *"Ek maggi de do, aur do lux Ramesh ke khaate mein likh do."* |
| **Check Stock** | *"Bhai, toothpaste kitna bacha hai dekhna."* |
| **Order History Inquiry** | *"Nehru apartment wale Sharma ji ke orders dikhao."* |
| **Full Ledger** | *"Saara stock dikhao."* |
| **Settle Credit** | *"Ramesh ka khata clear kar do."* |
| **Wipe Inventory** | *"Saara stock delete kar do."* |

## Tech Stack

| Layer | Technology |
|-------|-----------|
| **Frontend** | Vanilla HTML, CSS, JS (modular architecture) |
| **Backend** | FastAPI (Python) on Vercel serverless |
| **Database & Auth** | Firebase Firestore & Firebase Authentication |
| **Speech-to-Text** | Groq — `whisper-large-v3` |
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
