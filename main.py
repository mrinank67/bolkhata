from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
import json
from thefuzz import process
from groq import Groq
import os
from dotenv import load_dotenv
import firebase_admin
from firebase_admin import credentials, firestore
from google.cloud.firestore_v1.base_query import FieldFilter

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

# Initialize Firebase Admin
def init_firebase():
    if not firebase_admin._apps:
        # Check for env variable first (used in Vercel)
        firebase_json_env = os.getenv("FIREBASE_SERVICE_ACCOUNT")
        if firebase_json_env:
            cred_dict = json.loads(firebase_json_env)
            cred = credentials.Certificate(cred_dict)
        else:
            # Fallback to local JSON (for local development)
            cred_path = "poc-inventory-management-98303-firebase-adminsdk-fbsvc-f8868789db.json"
            if not os.path.exists(cred_path):
                raise Exception("Firebase Credentials not found! Add FIREBASE_SERVICE_ACCOUNT env var or the JSON file.")
            cred = credentials.Certificate(cred_path)
            
        firebase_admin.initialize_app(cred)
    return firestore.client()

db = init_firebase()

def init_default_stock():
    # Only seed the default values if they don't exist
    # This allows future customization since we won't overwrite existing db values.
    stock_ref = db.collection('stock')
    
    defaults = {
        'toothpaste': 50,
        'soap': 100,
        'noodles': 40
    }
    
    for item, qty in defaults.items():
        doc = stock_ref.document(item).get()
        if not doc.exists:
            stock_ref.document(item).set({'quantity': qty, 'item': item})

init_default_stock()

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
                    
                    Allowed Actions:
                    1. "decrease": Selling or reducing stock ("bech diya", "de do").
                    2. "increase": Buying or adding stock ("aaya hai", "kharida").
                    3. "inquiry": Checking physical stock of an item ("kitna bacha hai").
                    4. "ledger_inquiry": Checking a customer's account/debt ("khata dikhao", "hisab", "udhaar").
                    5. "clear_ledger": Settling debt or wiping an account clean ("khata clear kar do", "udhaar chuka diya", "paise de diye").
                    
                    Extraction Rules:
                    - 'raw_item': Transliterate to English (e.g., "maggi"). If the action is a ledger inquiry or clear ledger, set to "".
                    - 'quantity': Integer. Use "ALL" if they say "saari/sab". Use 0 for inquiries or clearing ledgers.
                    - 'customer_name': Extract the name in English (e.g., "ramesh") ONLY if they mention an account, credit, or a specific person. Otherwise, set to "".
                    - 'hinglish_text': Translate the raw Devanagari input into the Latin alphabet.
                    
                    You MUST return ONLY valid JSON. Do not include any text outside the JSON block.
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
        action = txn.get("action")
        customer_name = txn.get("customer_name")
        raw_qty = txn.get("quantity", 1)

        # --- Handle Ledger Inquiries ---
        if action == "ledger_inquiry":
            if not customer_name:
                results.append("❌ Kiska khaata dekhna hai? (Please specify a name).")
                continue
                
            docs = db.collection('udhaar').where(filter=FieldFilter('customer_name', '==', customer_name)).stream()
            
            dues_map = {}
            for doc in docs:
                data = doc.to_dict()
                item_name = data.get('item', 'unknown')
                dues_map[item_name] = dues_map.get(item_name, 0) + data.get('quantity', 0)
                
            if not dues_map:
                results.append(f"✅ {customer_name.capitalize()} ka khaata clear hai. (No dues)")
            else:
                dues = ", ".join([f"{qty} {item}" for item, qty in dues_map.items()])
                results.append(f"📒 {customer_name.capitalize()} owes: {dues}.")
            continue

        # --- NEW: Handle Clearing Ledgers ---
        if action == "clear_ledger":
            if not customer_name:
                results.append("❌ Kiska khaata clear karna hai? (Please specify a name).")
                continue
                
            docs = list(db.collection('udhaar').where(filter=FieldFilter('customer_name', '==', customer_name)).stream())
            
            if docs:
                # Delete all rows belonging to this customer
                for doc in docs:
                    doc.reference.delete()
                results.append(f"💰 {customer_name.capitalize()} ka udhaar clear ho gaya! (Account settled).")
            else:
                results.append(f"ℹ️ {customer_name.capitalize()} ke naam par koi udhaar nahi tha. (No dues found).")
            continue

        # --- Normal Stock Processing ---
        raw_item = txn.get("raw_item")
        if not raw_item:
            continue
            
        raw_item = raw_item.lower()
        
        # Fuzzy Match
        best_match, score = process.extractOne(raw_item, standard_items)
        standard_item = brand_to_item_map[best_match] if score > 70 else raw_item
        
        stock_doc_ref = db.collection('stock').document(standard_item)
        stock_doc = stock_doc_ref.get()
        
        if not stock_doc.exists:
            results.append(f"❌ {standard_item} not found in inventory.")
            continue
            
        current_qty = stock_doc.to_dict().get('quantity', 0)
        
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
        stock_doc_ref.set({'quantity': new_qty}, merge=True)
        
        # NEW: Handle Udhaar Logging
        if action == "decrease" and customer_name:
            db.collection('udhaar').add({
                'customer_name': customer_name,
                'item': standard_item,
                'quantity': qty,
                'timestamp': firestore.SERVER_TIMESTAMP
            })
            results.append(f"📒 Wrote {qty} {standard_item} in {customer_name.capitalize()}'s account. (Stock: {new_qty})")
        elif action == "decrease":
            results.append(f"✅ Sold {qty} {standard_item}. (Stock: {new_qty})")
        else:
            results.append(f"📦 Added {qty} {standard_item}. (Stock: {new_qty})")
    
    final_message = "\n".join(results)
    if not final_message:
        final_message = "No clear actions understood."

    return {
        "status": "success",
        "message": final_message,
        "raw_text": hinglish_text, 
        "understood_intent": intent
    }