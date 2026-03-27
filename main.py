from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
import sqlite3
import json
from thefuzz import process
from groq import Groq
import os
from dotenv import load_dotenv

load_dotenv()

# Setup Groq 

api_key = os.getenv("GROQ_API_KEY")
groq_client = Groq(api_key=api_key)

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def init_db():
    conn = sqlite3.connect('inventory.db')
    c = conn.cursor()
    # Existing stock table
    c.execute('''CREATE TABLE IF NOT EXISTS stock (item TEXT PRIMARY KEY, quantity INTEGER)''')
    
    # Udhaar (Credit) Ledger Table
    c.execute('''CREATE TABLE IF NOT EXISTS udhaar (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, 
                    customer_name TEXT, 
                    item TEXT, 
                    quantity INTEGER,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                )''')
                
    c.execute("INSERT OR IGNORE INTO stock (item, quantity) VALUES ('toothpaste', 50)")
    c.execute("INSERT OR IGNORE INTO stock (item, quantity) VALUES ('soap', 100)")
    c.execute("INSERT OR IGNORE INTO stock (item, quantity) VALUES ('noodles', 40)")
    conn.commit()
    return conn

conn = init_db()

brand_to_item_map = {
    "colgate": "toothpaste",
    "pepsodent": "toothpaste",
    "dant kanti": "toothpaste",
    "lux": "soap",
    "lifebuoy": "soap",
    "dettol": "soap",
    "maggi": "noodles",
    "yippee": "noodles"
}
standard_items = list(brand_to_item_map.keys())

@app.post("/process_voice")
async def process_voice(audio: UploadFile = File(...)):
    
    # --- STEP 1: Speech-to-Text via Groq (Whisper) ---
    try:
        audio_bytes = await audio.read()
        
        if len(audio_bytes) < 100:
            return {"status": "error", "message": "Audio too short. Please hold the button while speaking."}
            
        transcription = groq_client.audio.transcriptions.create(
          file=(audio.filename, audio_bytes, audio.content_type), 
          model="whisper-large-v3",
          prompt="The user is speaking Hindi or Hinglish regarding shop inventory.",
          response_format="json",
          language="hi",
          temperature=0.0
        )
        hindi_text = transcription.text
        print(f"Heard: {hindi_text}") 
        
    except Exception as e:
        print(f"❌ GROQ STT ERROR: {str(e)}")
        raise HTTPException(status_code=500, detail=f"STT Error: {str(e)}")

    if not hindi_text.strip():
        return {"status": "error", "message": "Could not hear anything clearly."}
    
# --- STEP 2: Intent Extraction via Groq (Llama 3) ---
    try:
        chat_completion = groq_client.chat.completions.create(
            messages=[
                {
                    "role": "system",
                    "content": """
                    You are an AI for a rural Indian shop. Extract a list of "transactions" from the user's speech.
                    
                    Rules for 'action':
                    - "decrease": Selling, giving, or reducing stock ("bech diya", "de do", "nikal do").
                    - "increase": Buying, receiving, or adding stock ("aaya hai", "kharida", "laaye").
                    - "inquiry": Asking about stock levels ("kitna bacha hai").
                    
                    Rules for 'raw_item' & 'quantity':
                    - 'raw_item' MUST be transliterated into English characters (e.g., "maggi").
                    - 'quantity': Use "ALL" if they say "saari/sab", 0 for inquiries, or an integer.
                    
                    NEW RULE: 'customer_name' (Udhaar / Credit)
                    - If the user explicitly mentions giving an item on credit, in an account, or to a specific person (e.g., "Ramesh ke khaate mein likh do"), extract that person's name. Transliterate to English (e.g., "ramesh").
                    - CRITICAL ANTI-BLEED RULE: The 'customer_name' ONLY applies to the specific item it is spoken with. Do NOT apply the name to other items in the sentence unless explicitly told to. If an item is a regular cash sale (just "de do"), its 'customer_name' MUST be null.
                    
                    Output Format:
                    You MUST return ONLY valid JSON in this exact structure:
                    {
                      "hinglish_text": "translated text here",
                      "transactions": [
                        {"action": "decrease", "raw_item": "maggi", "quantity": 1, "customer_name": null},
                        {"action": "decrease", "raw_item": "soap", "quantity": 2, "customer_name": "ramesh"}
                      ]
                    }
                    """
                },
                {
                    "role": "user",
                    "content": f"Text to process: '{hindi_text}'"
                }
            ],
            model="llama-3.3-70b-versatile",
            response_format={"type": "json_object"},
            temperature=0.0
        )
        
        json_str = chat_completion.choices[0].message.content
        intent = json.loads(json_str)
        print(f"Understood Intent: {intent}")
        
    except Exception as e:
        print(f"❌ GROQ LLM ERROR: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to understand the intent.")

    # --- STEP 3: Standardization & Database Loop ---
    transactions = intent.get("transactions", [])
    hinglish_text = intent.get("hinglish_text", hindi_text)
    
    results = [] 

    for txn in transactions:
        raw_item = txn.get("raw_item", "").lower()
        action = txn.get("action")
        raw_qty = txn.get("quantity", 1)
        customer_name = txn.get("customer_name") # Check if it's an Udhaar transaction
        
        # Fuzzy Match
        best_match, score = process.extractOne(raw_item, standard_items)
        standard_item = brand_to_item_map[best_match] if score > 70 else raw_item
        
        c = conn.cursor()
        c.execute("SELECT quantity FROM stock WHERE item=?", (standard_item,))
        row = c.fetchone()
        
        if not row:
            results.append(f"❌ {standard_item} not found in inventory.")
            continue
            
        current_qty = row[0]
        
        # Edge Case: Inquiry
        if action == "inquiry":
            results.append(f"ℹ️ {standard_item} stock is currently at {current_qty}.")
            continue

        # Edge Case: "ALL"
        if raw_qty == "ALL" and action == "decrease":
            qty = current_qty
        else:
            try:
                qty = int(raw_qty)
            except ValueError:
                qty = 1 
        
        # Calculate new stock
        if action == "decrease":
            new_qty = max(0, current_qty - qty)
        else:
            new_qty = current_qty + qty
            
        # Update Stock DB
        c.execute("UPDATE stock SET quantity=? WHERE item=?", (new_qty, standard_item))
        
        # NEW: Handle Udhaar Logging
        if action == "decrease" and customer_name:
            c.execute("INSERT INTO udhaar (customer_name, item, quantity) VALUES (?, ?, ?)", 
                      (customer_name, standard_item, qty))
            results.append(f"📒 Wrote {qty} {standard_item} in {customer_name.capitalize()}'s account. (Stock: {new_qty})")
        elif action == "decrease":
            results.append(f"✅ Sold {qty} {standard_item}. (Stock: {new_qty})")
        else:
            results.append(f"📦 Added {qty} {standard_item}. (Stock: {new_qty})")
    
    conn.commit()
    
    final_message = "\n".join(results)
    if not final_message:
        final_message = "No clear actions understood."

    return {
        "status": "success",
        "message": final_message,
        "raw_text": hinglish_text, 
        "understood_intent": intent
    }