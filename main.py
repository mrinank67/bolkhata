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
import datetime

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
            cred_path = "poc-inventory-management-98303-0fc65b9f53ce.json"
            if not os.path.exists(cred_path):
                raise Exception(
                    "Firebase Credentials not found! Add FIREBASE_SERVICE_ACCOUNT env var or the JSON file."
                )
            cred = credentials.Certificate(cred_path)

        firebase_admin.initialize_app(cred)
    return firestore.client()


db = init_firebase()

@app.get("/config")
async def get_config():
    return {
        "apiKey": os.getenv("FIREBASE_API_KEY"),
        "authDomain": os.getenv("FIREBASE_AUTH_DOMAIN"),
        "projectId": os.getenv("FIREBASE_PROJECT_ID"),
        "storageBucket": os.getenv("FIREBASE_STORAGE_BUCKET"),
        "messagingSenderId": os.getenv("FIREBASE_MESSAGING_SENDER_ID"),
        "appId": os.getenv("FIREBASE_APP_ID"),
        "measurementId": os.getenv("FIREBASE_MEASUREMENT_ID"),
    }


@app.post("/process_voice")
async def process_voice(
    background_tasks: BackgroundTasks,
    audio: UploadFile = File(...),
    authorization: str = Header(None),
):
    start_total = time.time()

    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Unauthorized")

    token = authorization.split("Bearer ")[1]
    try:
        t0 = time.time()
        decoded_token = auth.verify_id_token(token)
        uid = decoded_token["uid"]
        print(f"⏱️ Token Verify: {time.time() - t0:.2f}s")
    except Exception as e:  # noqa: F841
        raise HTTPException(status_code=401, detail="Invalid Authentication Token")

    user_stock_ref = db.collection("users").document(uid).collection("stock")
    user_udhaar_ref = db.collection("users").document(uid).collection("udhaar")
    user_orders_ref = db.collection("users").document(uid).collection("orders")

    # Fetch recent customer context from the last 5 minutes
    recent_customer = ""
    recent_modifier = ""
    try:
        last_orders = list(user_orders_ref.order_by("timestamp", direction=firestore.Query.DESCENDING).limit(1).stream())
        last_order_time = last_orders[0].to_dict().get("timestamp") if last_orders else None
        
        last_udhaars = list(user_udhaar_ref.order_by("timestamp", direction=firestore.Query.DESCENDING).limit(1).stream())
        last_udhaar_time = last_udhaars[0].to_dict().get("timestamp") if last_udhaars else None

        latest_doc = None
        if last_order_time and last_udhaar_time:
            latest_doc = last_orders[0] if last_order_time > last_udhaar_time else last_udhaars[0]
        elif last_order_time:
            latest_doc = last_orders[0]
        elif last_udhaar_time:
            latest_doc = last_udhaars[0]
            
        if latest_doc:
            data = latest_doc.to_dict()
            ts = data.get("timestamp")
            now = datetime.datetime.now(datetime.timezone.utc)
            if ts and (now - ts).total_seconds() < 300: # 5 minutes
                recent_customer = data.get("customer_name", "")
                recent_modifier = data.get("customer_modifier", "")
    except Exception as e:
        print("Error fetching recent context:", e)

    # --- STEP 1: Speech-to-Text via Groq (Whisper) ---
    t1 = time.time()
    try:
        audio_bytes = await audio.read()

        if len(audio_bytes) < 100:
            return {
                "status": "error",
                "message": "Audio too short. Please hold the button while speaking.",
            }

        transcription = groq_client.audio.transcriptions.create(
            file=(audio.filename, audio_bytes, audio.content_type),
            model="whisper-large-v3",
            prompt="The user is speaking Hindi or Hinglish regarding shop inventory.",
            response_format="json",
            language="hi",
            temperature=0.0,
        )
        hindi_text = transcription.text
        print(f"⏱️ STT (Groq Whisper): {time.time() - t1:.2f}s")
        print(f"Heard: {hindi_text}")

    except Exception as e:
        print(f"❌ GROQ STT ERROR: {str(e)}")
        raise HTTPException(status_code=500, detail=f"STT Error: {str(e)}")

    if not hindi_text.strip():
        return {"status": "error", "message": "Could not hear anything clearly."}

    # --- STEP 2: Intent Extraction via Groq (Llama 3) ---
    t2 = time.time()
    try:
        recent_context_msg = ""
        if recent_customer:
            recent_context_msg = f"\n                    RECENT CONTEXT: The user just made a transaction for a customer named '{recent_customer}' (modifier: '{recent_modifier}'). If the user says something like 'aur 2 item de do' (give 2 more) WITHOUT explicitly saying a name, you MUST use '{recent_customer}' as the customer_name and '{recent_modifier}' as the customer_modifier."

        chat_completion = groq_client.chat.completions.create(
            messages=[
                {
                    "role": "system",
                    "content": f"""
                    You are an AI for an Indian Kirana shop. Map the user's speech into strict JSON transactions.
                    Recent Context: {recent_context_msg}
                    
                    Map intents using this schema:
                    - 'target': "stock" (inventory/sales) OR "ledger" (udhaar/accounts/orders).
                    - 'operation': "add" (restock shop inventory), "subtract" (sell/give). NOTE: "khate me add karo" means selling on credit, so use "subtract"!), "read" (check/inquiry), OR "clear" (settle/delete).
                    - 'item': English product name ONLY (strip units like 'packet', 'kilo'). Use "ALL" for full inventory. "" if not applicable.
                    - 'qty': Integer (Parse Hindi numbers). Use 0 for read/clear operations.
                    - 'customer_name': English name of the person (e.g. "ramesh"). Apply to all items in the utterance. Use Context if implied. "" if cash sale.
                    - 'customer_modifier': Any location or descriptor (e.g. "delhi wale"). "" if none.
                    - 'is_credit': boolean (true ONLY if "udhaar", "khata", or a specific account is implied).
                    
                    Return ONLY valid JSON matching this exact structure:
                    {{
                      "hinglish_text": "do maggi ramesh delhi ke khate me",
                      "transactions": [
                        {{"target": "stock", "operation": "subtract", "item": "maggi", "qty": 2, "customer_name": "ramesh", "customer_modifier": "delhi", "is_credit": true}}
                      ]
                    }}
                    """
                },
                {"role": "user", "content": f"Text to process: '{hindi_text}'"},
            ],
            model="llama-3.1-8b-instant",
            response_format={"type": "json_object"},
            temperature=0.0,
        )

        json_str = chat_completion.choices[0].message.content
        intent = json.loads(json_str)
        print(f"⏱️ LLM (Groq Llama3): {time.time() - t2:.2f}s")
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

    # Fetch dynamic inventory to enrich fuzzy matching
    stock_docs = list(user_stock_ref.stream())
    all_fuzzy_candidates = [doc.id for doc in stock_docs]

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
                "rows": [],
            }
        return result_groups[action_key]

    for txn in transactions:
        target = txn.get("target")
        operation = txn.get("operation")
        raw_item = txn.get("item", "")
        raw_qty = txn.get("qty", 1)
        customer_name = txn.get("customer_name", "").lower()
        customer_modifier = txn.get("customer_modifier", "").lower()
        is_credit = txn.get("is_credit", False)

        # --- Handle Order Inquiries ---
        if target == "ledger" and operation == "read" and not is_credit:
            if not customer_name:
                errors.append("Kiske orders dekhne hain? (Please specify a name).")
                continue
            
            group_key = f"order_inquiry_{customer_name}_{customer_modifier}"
            title_name = f"{customer_name.capitalize()} ({customer_modifier})" if customer_modifier else customer_name.capitalize()
            group = get_group(group_key, f"{title_name}'s Orders", "🛍️", ["Item", "Qty", "Context"])

            user_orders_ref = db.collection("users").document(uid).collection("orders")
            docs = user_orders_ref.where(filter=FieldFilter("customer_name", "==", customer_name)).stream()
            
            orders_found = False
            for doc in docs:
                data = doc.to_dict()
                if customer_modifier and customer_modifier.lower() != data.get("customer_modifier", "").lower():
                    continue
                orders_found = True
                item_name = data.get("item", "unknown")
                qty = data.get("quantity", 0)
                ctx = data.get("customer_modifier", "")
                group["rows"].append({"Item": item_name.capitalize(), "Qty": qty, "Context": ctx if ctx else "-"})
            
            if not orders_found:
                group["empty_message"] = f"No orders found for {title_name}."
            continue

        # --- Handle Full Inventory ---
        if target == "stock" and operation == "read" and raw_item == "ALL":
            group = get_group(
                "full_inventory", "Full Inventory", "📦", ["#", "Item", "Stock"]
            )
            all_docs = user_stock_ref.stream()
            idx = 1
            for doc in all_docs:
                data = doc.to_dict()
                group["rows"].append(
                    {
                        "#": idx,
                        "Item": doc.id.capitalize(),
                        "Stock": data.get("quantity", 0),
                    }
                )
                idx += 1
            if not group["rows"]:
                group["empty_message"] = "Inventory is empty. No items added yet."
            continue

        # --- Handle Clear Entire Inventory (requires confirmation) ---
        if target == "stock" and operation == "clear" and raw_item == "ALL":
            all_docs = list(user_stock_ref.stream())

            if all_docs:
                item_count = len(all_docs)
                group = get_group(
                    "clear_inventory", "⚠️ Confirm Inventory Deletion", "🗑️", ["Action", "Items"]
                )
                group["rows"].append(
                    {"Action": "Delete ALL inventory", "Items": f"{item_count} items"}
                )
                group["requires_confirmation"] = True
                group["confirmation_message"] = f"Are you sure you want to delete all {item_count} items from your inventory? This action cannot be undone."
            else:
                group = get_group(
                    "clear_inventory", "Inventory Cleared", "🗑️", ["Action", "Status"]
                )
                group["empty_message"] = "Inventory is already empty."
            continue

        # --- Handle Ledger Inquiries ---
        if target == "ledger" and operation == "read" and is_credit:
            if not customer_name:
                errors.append("Kiska khaata dekhna hai? (Please specify a name).")
                continue

            group_key = f"ledger_inquiry_{customer_name}_{customer_modifier}"
            title_name = f"{customer_name.capitalize()} ({customer_modifier})" if customer_modifier else customer_name.capitalize()
            group = get_group(
                group_key,
                f"{title_name}'s Ledger",
                "📒",
                ["Item", "Quantity Owed"],
            )

            docs = user_udhaar_ref.where(
                filter=FieldFilter("customer_name", "==", customer_name)
            ).stream()
            dues_map = {}
            for doc in docs:
                data = doc.to_dict()
                if customer_modifier and customer_modifier.lower() != data.get("customer_modifier", "").lower():
                    continue
                item_name = data.get("item", "unknown")
                dues_map[item_name] = dues_map.get(item_name, 0) + data.get(
                    "quantity", 0
                )

            if not dues_map:
                group["empty_message"] = (
                    f"{title_name} ka khaata clear hai. No dues!"
                )
            else:
                for item, qty in dues_map.items():
                    group["rows"].append(
                        {"Item": item.capitalize(), "Quantity Owed": qty}
                    )
            continue

        # --- Handle Clearing Ledgers ---
        if target == "ledger" and operation == "clear":
            if not customer_name:
                errors.append("Kiska khaata clear karna hai? (Please specify a name).")
                continue

            group_key = f"clear_ledger_{customer_name}_{customer_modifier}"
            title_name = f"{customer_name.capitalize()} ({customer_modifier})" if customer_modifier else customer_name.capitalize()
            group = get_group(group_key, "Ledger Cleared", "💰", ["Customer", "Status"])

            docs = list(
                user_udhaar_ref.where(
                    filter=FieldFilter("customer_name", "==", customer_name)
                ).stream()
            )

            docs_to_delete = []
            for doc in docs:
                data = doc.to_dict()
                if customer_modifier and customer_modifier.lower() != data.get("customer_modifier", "").lower():
                    continue
                docs_to_delete.append(doc)

            if docs_to_delete:
                for doc in docs_to_delete:
                    doc.reference.delete()
                group["rows"].append(
                    {"Customer": title_name, "Status": "✅ Settled"}
                )
            else:
                group["rows"].append(
                    {
                        "Customer": title_name,
                        "Status": "ℹ️ No dues found",
                    }
                )
            continue

        # --- Normal Stock Processing ---
        if not raw_item or raw_item == "ALL":
            continue

        raw_item = raw_item.lower()

        # Dynamic Fuzzy Match
        if all_fuzzy_candidates:
            best_match, score = process.extractOne(raw_item, all_fuzzy_candidates)
            if score > 70:
                standard_item = best_match
            else:
                standard_item = raw_item
        else:
            standard_item = raw_item

        stock_doc_ref = user_stock_ref.document(standard_item)
        stock_doc = stock_doc_ref.get()

        if not stock_doc.exists:
            if operation == "add":
                current_qty = 0
            else:
                errors.append(f"{standard_item} not found in inventory.")
                continue
        else:
            current_qty = stock_doc.to_dict().get("quantity", 0)

        # Inquiry
        if target == "stock" and operation == "read":
            group = get_group("inquiry", "Stock Check", "🔍", ["Item", "Current Stock"])
            group["rows"].append(
                {"Item": standard_item.capitalize(), "Current Stock": current_qty}
            )
            continue

        # Quantity
        if raw_qty == "ALL" and operation == "subtract":
            qty = current_qty
        else:
            try:
                qty = int(raw_qty)
            except ValueError:
                qty = 1

        # Calculate new stock
        if operation == "subtract":
            new_qty = max(0, current_qty - qty)
        else:
            new_qty = current_qty + qty
            
        # Update Stock DB
        update_data = {
            "quantity": new_qty, 
            "item": standard_item,
            "updated_at": firestore.SERVER_TIMESTAMP
        }
        if not stock_doc.exists:
            update_data["created_at"] = firestore.SERVER_TIMESTAMP
            
        stock_doc_ref.set(update_data, merge=True)
        title_name = f"{customer_name.capitalize()} ({customer_modifier})" if customer_modifier else customer_name.capitalize() if customer_name else ""

        # Build result row
        if operation == "subtract" and is_credit and customer_name:
            group = get_group(
                "udhaar_sale",
                "Credit Sale (Udhaar)",
                "📒",
                ["Item", "Qty", "Previous", "Current", "Customer"],
            )
            
            docs = user_udhaar_ref.where(filter=FieldFilter("customer_name", "==", customer_name)).where(filter=FieldFilter("item", "==", standard_item)).stream()
            existing_doc = None
            for doc in docs:
                if doc.to_dict().get("customer_modifier", "").lower() == customer_modifier.lower():
                    existing_doc = doc
                    break
                    
            if existing_doc:
                existing_qty = existing_doc.to_dict().get("quantity", 0)
                final_qty = existing_qty + qty
                existing_doc.reference.update({
                    "quantity": final_qty,
                    "timestamp": firestore.SERVER_TIMESTAMP
                })
            else:
                user_udhaar_ref.add(
                    {
                        "customer_name": customer_name,
                        "customer_modifier": customer_modifier,
                        "item": standard_item,
                        "quantity": qty,
                        "timestamp": firestore.SERVER_TIMESTAMP,
                    }
                )
                final_qty = qty
                
            group["rows"].append(
                {
                    "Item": standard_item.capitalize(),
                    "Qty": final_qty,
                    "Previous": current_qty,
                    "Current": new_qty,
                    "Customer": title_name,
                }
            )
        elif operation == "subtract" and customer_name:
            group = get_group(
                "order_sale",
                "Customer Order",
                "🛍️",
                ["Item", "Qty", "Previous", "Current", "Customer", "Context"]
            )
            
            docs = user_orders_ref.where(filter=FieldFilter("customer_name", "==", customer_name)).where(filter=FieldFilter("item", "==", standard_item)).stream()
            existing_doc = None
            for doc in docs:
                if doc.to_dict().get("customer_modifier", "").lower() == customer_modifier.lower():
                    existing_doc = doc
                    break
                    
            if existing_doc:
                existing_qty = existing_doc.to_dict().get("quantity", 0)
                final_qty = existing_qty + qty
                existing_doc.reference.update({
                    "quantity": final_qty,
                    "timestamp": firestore.SERVER_TIMESTAMP
                })
            else:
                user_orders_ref.add(
                    {
                        "customer_name": customer_name,
                        "customer_modifier": customer_modifier,
                        "item": standard_item,
                        "quantity": qty,
                        "timestamp": firestore.SERVER_TIMESTAMP,
                    }
                )
                final_qty = qty
                
            group["rows"].append(
                {
                    "Item": standard_item.capitalize(),
                    "Qty": final_qty,
                    "Previous": current_qty,
                    "Current": new_qty,
                    "Customer": customer_name.capitalize(),
                    "Context": customer_modifier if customer_modifier else "-"
                }
            )
        elif operation == "subtract":
            group = get_group(
                "decrease", "Stock Sold", "🛒", ["Item", "Sold", "Previous", "Current"]
            )
            group["rows"].append(
                {
                    "Item": standard_item.capitalize(),
                    "Sold": qty,
                    "Previous": current_qty,
                    "Current": new_qty,
                }
            )
        else:
            group = get_group(
                "increase",
                "Stock Added",
                "📦",
                ["Item", "Added", "Previous", "Current"],
            )
            group["rows"].append(
                {
                    "Item": standard_item.capitalize(),
                    "Added": qty,
                    "Previous": current_qty,
                    "Current": new_qty,
                }
            )

    result_list = list(result_groups.values())

    if not result_list and not errors:
        errors.append("Couldn't understand that. Please try speaking again clearly.")

    # Save to history in background (non-blocking)
    if result_list or errors:

        def write_history():
            user_history_ref = (
                db.collection("users").document(uid).collection("history")
            )
            user_history_ref.add(
                {
                    "results": result_list,
                    "errors": errors,
                    "timestamp": firestore.SERVER_TIMESTAMP,
                }
            )

        background_tasks.add_task(write_history)

    print(f"⏱️ Firestore DB Ops: {time.time() - t3:.2f}s")
    print(f"⏱️ TOTAL VOICE PROCESS: {time.time() - start_total:.2f}s")

    return {
        "status": "success",
        "results": result_list,
        "errors": errors,
        "raw_text": hinglish_text,
        "understood_intent": intent,
    }


# Helper to verify token and extract uid
def verify_token(authorization: str):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Unauthorized")
    token = authorization.split("Bearer ")[1]
    try:
        decoded = auth.verify_id_token(token)
        return decoded["uid"]
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid Authentication Token")



# Confirmed clear inventory endpoint
@app.post("/confirm_clear_inventory")
async def confirm_clear_inventory(authorization: str = Header(None)):
    uid = verify_token(authorization)
    user_stock_ref = db.collection("users").document(uid).collection("stock")
    all_docs = list(user_stock_ref.stream())

    if not all_docs:
        return {"status": "success", "message": "Inventory is already empty.", "deleted_count": 0}

    for doc in all_docs:
        doc.reference.delete()

    return {"status": "success", "message": f"✅ Cleared {len(all_docs)} items from inventory.", "deleted_count": len(all_docs)}


@app.get("/inventory")
async def get_inventory(authorization: str = Header(None)):
    uid = verify_token(authorization)
    user_stock_ref = db.collection("users").document(uid).collection("stock")
    docs = user_stock_ref.stream()
    inventory = []
    for doc in docs:
        data = doc.to_dict()
        
        ts_obj = data.get("updated_at") or data.get("created_at")
        try:
            ts = ts_obj.timestamp() * 1000 if ts_obj else 0
        except AttributeError:
            ts = 0
            
        inventory.append({
            "item": doc.id,
            "quantity": data.get("quantity", 0),
            "updated_at": ts,
        })
    return {"inventory": inventory}


@app.get("/history")
async def get_history(authorization: str = Header(None)):
    uid = verify_token(authorization)
    history_ref = db.collection("users").document(uid).collection("history")
    docs = (
        history_ref.order_by("timestamp", direction=firestore.Query.DESCENDING)
        .limit(50)
        .stream()
    )

    entries = []
    for doc in docs:
        data = doc.to_dict()
        ts = data.get("timestamp")
        entries.append(
            {
                "id": doc.id,
                "results": data.get("results", []),
                "errors": data.get("errors", []),
                "timestamp": ts.isoformat() if ts else None,
            }
        )
    return {"history": entries}


@app.delete("/history")
async def clear_history(authorization: str = Header(None)):
    uid = verify_token(authorization)
    history_ref = db.collection("users").document(uid).collection("history")
    docs = history_ref.stream()
    for doc in docs:
        doc.reference.delete()
    return {"status": "cleared"}
