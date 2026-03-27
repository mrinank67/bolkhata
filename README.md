Markdown
# 🗣️ Voice Bahi-Khata (Smart Inventory PoC)

A lightning-fast, voice-first inventory management system designed to replace traditional pen-and-paper ledgers (*bahi-khatas*) in rural Indian shops (*kiranas*). 

This Proof of Concept (PoC) prioritizes extreme low latency, ensuring the AI can process regional Hindi/Hinglish voice commands and update the database just as fast as a shopkeeper can write it down.

## 🚀 Features
* **Zero-Latency Voice Capture:** Uses a "Press and Hold" walkie-talkie UI to capture audio cleanly in extremely noisy shop environments.
* **Dialect & Hinglish Support:** Powered by Groq and Whisper-Large-V3 (locked to Hindi) to accurately transcribe heavily accented speech without hallucinating.
* **Intelligent Intent Extraction:** Uses Llama 3 (via Groq) to instantly translate Devanagari to English, map local slang (e.g., "Colgate" -> "toothpaste"), and understand complex multi-item orders in a single breath.
* **Udhaar (Credit) Management:** Automatically routes items to a customer's credit ledger if a name is spoken (e.g., *"Do lux Ramesh ke khaate mein likh do"*), while preventing "context bleed" to cash items.
* **Smart Edge Cases:** Handles relative quantities (*"saari maggi bech di"*) and stock inquiries (*"maggi kitni bachi hai?"*) out of the box.

## 🛠️ Tech Stack
* **Frontend:** Vanilla HTML, CSS, JavaScript (MediaRecorder API)
* **Backend:** FastAPI (Python), SQLite
* **AI Engine:** Groq API 
  * STT: `whisper-large-v3`
  * NLP/Intent: `llama-3.3-70b-versatile`
* **Matching Logic:** `thefuzz` (Fuzzy string matching for brand standardization)

## 💻 Local Setup Instructions

**1. Clone the repository**
```bash
git clone [https://github.com/YOUR_USERNAME/voice-bahi-khata.git](https://github.com/YOUR_USERNAME/voice-bahi-khata.git)
cd voice-bahi-khata
2. Set up a Virtual Environment

Bash
python -m venv venv
# On Windows:
venv\Scripts\activate
# On Mac/Linux:
source venv/bin/activate
3. Install Dependencies

Bash
pip install -r requirements.txt
pip install python-dotenv uvicorn
4. Set up Environment Variables
Create a .env file in the root directory and add your Groq API key:

Plaintext
GROQ_API_KEY=gsk_your_api_key_here
5. Run the Server

Bash
uvicorn main:app --reload
Once running, open http://localhost:8000/index.html (or serve the HTML file via a local live server) and hold the microphone button to start testing!

🎙️ Example Voice Commands to Test
Standard Sale: "Do colgate aur ek maggi de do."

Restock: "Paanch naye lux aaye hain."

Credit (Udhaar): "Ek sabun Suresh ke khaate mein likh do."

Mixed Cash & Credit: "Ek maggi de do, aur do lux Ramesh ke khaate mein likh do."

Wipe Stock: "Saari maggi bech di."

Inquiry: "Bhai, toothpaste kitna bacha hai dekhna."

☁️ Deployment Notes (Vercel)
This PoC is configured to be deployable on Vercel via the included vercel.json file.

⚠️ Important SQLite Warning: Vercel is a serverless platform. The codebase currently routes the SQLite database to /tmp/inventory.db to prevent read-only crash errors. While the voice processing and AI routing will work perfectly in the cloud, the /tmp database will reset every time the Vercel server spins down.
