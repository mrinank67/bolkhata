from fastapi import FastAPI, HTTPException, UploadFile, File, Header, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
import json
import time
from thefuzz import process
from groq import Groq
import os
from dotenv import load_dotenv
import firebase_admin
from firebase_admin import credentials, firestore, auth
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

@app.get("/config")
async def get_config():
    return {
        "apiKey": os.getenv("FIREBASE_API_KEY"),
        "authDomain": os.getenv("FIREBASE_AUTH_DOMAIN"),
        "projectId": os.getenv("FIREBASE_PROJECT_ID"),
        "storageBucket": os.getenv("FIREBASE_STORAGE_BUCKET"),
        "messagingSenderId": os.getenv("FIREBASE_MESSAGING_SENDER_ID"),
        "appId": os.getenv("FIREBASE_APP_ID"),
        "measurementId": os.getenv("FIREBASE_MEASUREMENT_ID")
    }

@app.post("/process_voice")
async def process_voice(background_tasks: BackgroundTasks, audio: UploadFile = File(...), authorization: str = Header(None)):
    start_total = time.time()
    
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Unauthorized")
        
    token = authorization.split("Bearer ")[1]
    try:
        t0 = time.time()
        decoded_token = auth.verify_id_token(token)
        uid = decoded_token['uid']
        print(f"⏱️ Token Verify: {time.time()-t0:.2f}s")
    except Exception as e:
        raise HTTPException(status_code=401, detail="Invalid Authentication Token")

    user_stock_ref = db.collection('users').document(uid).collection('stock')
    user_udhaar_ref = db.collection('users').document(uid).collection('udhaar')

    # --- STEP 1: Speech-to-Text via Groq (Whisper) ---
    t1 = time.time()
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
        print(f"⏱️ STT (Groq Whisper): {time.time()-t1:.2f}s")
        print(f"Heard: {hindi_text}") 
        
    except Exception as e:
        print(f"❌ GROQ STT ERROR: {str(e)}")
        raise HTTPException(status_code=500, detail=f"STT Error: {str(e)}")

    if not hindi_text.strip():
        return {"status": "error", "message": "Could not hear anything clearly."}
    
# --- STEP 2: Intent Extraction via Groq (Llama 3) ---
    t2 = time.time()
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
                    3. "inquiry": Checking physical stock of a SPECIFIC item ("kitna bacha hai", "soap kitna hai").
                    4. "full_inventory": Checking the ENTIRE inventory / all stock at once ("saara stock dikhao", "poora inventory", "sab kuch dikhao", "kya kya hai dukaan mein").
                    5. "ledger_inquiry": Checking a customer's account/debt ("khata dikhao", "hisab", "udhaar").
                    6. "clear_ledger": Settling debt or wiping an account clean ("khata clear kar do", "udhaar chuka diya", "paise de diye").
                    7. "clear_inventory": Deleting ALL stock completely ("saara stock delete kar do", "inventory clear karo", "sab hata do").
                    
                    Extraction Rules:
                    - 'raw_item': Transliterate to English (e.g., "maggi"). If the action is full_inventory, clear_inventory, ledger inquiry, or clear ledger, set to "".
                    - 'quantity': Integer. Use "ALL" if they say "saari/sab". Use 0 for inquiries, full_inventory, clear_inventory, or clearing ledgers.
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
            model="llama-3.1-8b-instant",
            response_format={"type": "json_object"},
            temperature=0.0
        )
        
        json_str = chat_completion.choices[0].message.content
        intent = json.loads(json_str)
        print(f"⏱️ LLM (Groq Llama3): {time.time()-t2:.2f}s")
        print(f"Understood Intent: {intent}")
        
    except Exception as e:
        print(f"❌ GROQ LLM ERROR: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to understand the intent.")

    # --- STEP 3: Standardization & Database Loop ---
    t3 = time.time()
    # Handle LLM returning either a flat object or a transactions array
    transactions = intent.get("transactions", [])
    if not transactions and "action" in intent:
        # LLM returned a single flat transaction instead of an array
        transactions = [intent]
    hinglish_text = intent.get("hinglish_text", hindi_text)
    
    # Structured result groups keyed by action type
    result_groups = {}
    errors = []

    def get_group(action_key, title, icon, columns):
        if action_key not in result_groups:
            result_groups[action_key] = {
                "action": action_key,
                "title": title,
                "icon": icon,
                "columns": columns,
                "rows": []
            }
        return result_groups[action_key]

    for txn in transactions:
        action = txn.get("action")
        customer_name = txn.get("customer_name")
        raw_qty = txn.get("quantity", 1)

        # --- Handle Full Inventory ---
        if action == "full_inventory":
            group = get_group("full_inventory", "Full Inventory", "📦",
                              ["#", "Item", "Stock"])
            all_docs = user_stock_ref.stream()
            idx = 1
            for doc in all_docs:
                data = doc.to_dict()
                group["rows"].append({
                    "#": idx,
                    "Item": doc.id.capitalize(),
                    "Stock": data.get('quantity', 0)
                })
                idx += 1
            if not group["rows"]:
                group["empty_message"] = "Inventory is empty. No items added yet."
            continue

        # --- Handle Clear Entire Inventory ---
        if action == "clear_inventory":
            group = get_group("clear_inventory", "Inventory Cleared", "🗑️",
                              ["Action", "Status"])
            all_docs = list(user_stock_ref.stream())
            
            if all_docs:
                for doc in all_docs:
                    doc.reference.delete()
                group["rows"].append({
                    "Action": "Delete all items",
                    "Status": "✅ Cleared"
                })
            else:
                group["empty_message"] = "Inventory is already empty."
            continue

        # --- Handle Ledger Inquiries ---
        if action == "ledger_inquiry":
            if not customer_name:
                errors.append("Kiska khaata dekhna hai? (Please specify a name).")
                continue

            group_key = f"ledger_inquiry_{customer_name}"
            group = get_group(group_key, f"{customer_name.capitalize()}'s Ledger", "📒",
                              ["Item", "Quantity Owed"])

            docs = user_udhaar_ref.where(filter=FieldFilter('customer_name', '==', customer_name)).stream()
            dues_map = {}
            for doc in docs:
                data = doc.to_dict()
                item_name = data.get('item', 'unknown')
                dues_map[item_name] = dues_map.get(item_name, 0) + data.get('quantity', 0)

            if not dues_map:
                group["empty_message"] = f"{customer_name.capitalize()} ka khaata clear hai. No dues!"
            else:
                for item, qty in dues_map.items():
                    group["rows"].append({"Item": item.capitalize(), "Quantity Owed": qty})
            continue

        # --- Handle Clearing Ledgers ---
        if action == "clear_ledger":
            if not customer_name:
                errors.append("Kiska khaata clear karna hai? (Please specify a name).")
                continue

            group_key = f"clear_ledger_{customer_name}"
            group = get_group(group_key, "Ledger Cleared", "💰",
                              ["Customer", "Status"])

            docs = list(user_udhaar_ref.where(filter=FieldFilter('customer_name', '==', customer_name)).stream())

            if docs:
                for doc in docs:
                    doc.reference.delete()
                group["rows"].append({
                    "Customer": customer_name.capitalize(),
                    "Status": "✅ Settled"
                })
            else:
                group["rows"].append({
                    "Customer": customer_name.capitalize(),
                    "Status": "ℹ️ No dues found"
                })
            continue

        # --- Normal Stock Processing ---
        raw_item = txn.get("raw_item")
        if not raw_item:
            continue

        raw_item = raw_item.lower()

        # Fuzzy Match
        best_match, score = process.extractOne(raw_item, standard_items)
        standard_item = brand_to_item_map[best_match] if score > 70 else raw_item

        stock_doc_ref = user_stock_ref.document(standard_item)
        stock_doc = stock_doc_ref.get()

        if not stock_doc.exists:
            if action == "increase":
                current_qty = 0
            else:
                errors.append(f"{standard_item} not found in inventory.")
                continue
        else:
            current_qty = stock_doc.to_dict().get('quantity', 0)

        # Inquiry
        if action == "inquiry":
            group = get_group("inquiry", "Stock Check", "🔍",
                              ["Item", "Current Stock"])
            group["rows"].append({
                "Item": standard_item.capitalize(),
                "Current Stock": current_qty
            })
            continue

        # Quantity
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
        stock_doc_ref.set({'quantity': new_qty, 'item': standard_item}, merge=True)

        # Build result row
        if action == "decrease" and customer_name:
            group = get_group("udhaar_sale", "Credit Sale (Udhaar)", "📒",
                              ["Item", "Qty", "Previous", "Current", "Customer"])
            user_udhaar_ref.add({
                'customer_name': customer_name,
                'item': standard_item,
                'quantity': qty,
                'timestamp': firestore.SERVER_TIMESTAMP
            })
            group["rows"].append({
                "Item": standard_item.capitalize(),
                "Qty": qty,
                "Previous": current_qty,
                "Current": new_qty,
                "Customer": customer_name.capitalize()
            })
        elif action == "decrease":
            group = get_group("decrease", "Stock Sold", "🛒",
                              ["Item", "Sold", "Previous", "Current"])
            group["rows"].append({
                "Item": standard_item.capitalize(),
                "Sold": qty,
                "Previous": current_qty,
                "Current": new_qty
            })
        else:
            group = get_group("increase", "Stock Added", "📦",
                              ["Item", "Added", "Previous", "Current"])
            group["rows"].append({
                "Item": standard_item.capitalize(),
                "Added": qty,
                "Previous": current_qty,
                "Current": new_qty
            })

    result_list = list(result_groups.values())

    if not result_list and not errors:
        errors.append("Couldn't understand that. Please try speaking again clearly.")

    # Save to history in background (non-blocking)
    if result_list or errors:
        def write_history():
            user_history_ref = db.collection('users').document(uid).collection('history')
            user_history_ref.add({
                'results': result_list,
                'errors': errors,
                'timestamp': firestore.SERVER_TIMESTAMP
            })
        background_tasks.add_task(write_history)

    print(f"⏱️ Firestore DB Ops: {time.time()-t3:.2f}s")
    print(f"⏱️ TOTAL VOICE PROCESS: {time.time()-start_total:.2f}s")

    return {
        "status": "success",
        "results": result_list,
        "errors": errors,
        "raw_text": hinglish_text,
        "understood_intent": intent
    }


# Helper to verify token and extract uid
def verify_token(authorization: str):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Unauthorized")
    token = authorization.split("Bearer ")[1]
    try:
        decoded = auth.verify_id_token(token)
        return decoded['uid']
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid Authentication Token")


@app.get("/history")
async def get_history(authorization: str = Header(None)):
    uid = verify_token(authorization)
    history_ref = db.collection('users').document(uid).collection('history')
    docs = history_ref.order_by('timestamp', direction=firestore.Query.DESCENDING).limit(50).stream()

    entries = []
    for doc in docs:
        data = doc.to_dict()
        ts = data.get('timestamp')
        entries.append({
            "id": doc.id,
            "results": data.get('results', []),
            "errors": data.get('errors', []),
            "timestamp": ts.isoformat() if ts else None
        })
    return {"history": entries}


@app.delete("/history")
async def clear_history(authorization: str = Header(None)):
    uid = verify_token(authorization)
    history_ref = db.collection('users').document(uid).collection('history')
    docs = history_ref.stream()
    for doc in docs:
        doc.reference.delete()
    return {"status": "cleared"}