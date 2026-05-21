from fastapi import FastAPI, HTTPException, UploadFile, File, Header, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
import json
import time
from thefuzz import process
import os
import requests
from groq import Groq
from dotenv import load_dotenv
import firebase_admin
from firebase_admin import credentials, firestore, auth
from google.cloud.firestore_v1.base_query import FieldFilter
import datetime

load_dotenv()

# Setup Groq & Sarvam
api_key = os.getenv("GROQ_API_KEY")
groq_client = Groq(api_key=api_key)
sarvam_api_key = os.getenv("SARVAM_API_KEY")

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
            cred_path = "bolkhata-prod-firebase-adminsdk-fbsvc-842a3ee7ed.json"
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

    uid = verify_token(authorization)

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

    # --- STEP 1: Speech-to-Text via Sarvam AI ---
    t1 = time.time()
    try:
        audio_bytes = await audio.read()

        if len(audio_bytes) < 100:
            return {
                "status": "error",
                "message": "Audio too short. Please hold the button while speaking.",
            }

        url = "https://api.sarvam.ai/speech-to-text"
        files = {
            'file': (audio.filename, audio_bytes, audio.content_type)
        }
        data = {
            'model': 'saaras:v3',
            'language_code': 'unknown',
            'mode': 'translate',
            'with_diarization': 'false'
        }
        headers = {
            'api-subscription-key': sarvam_api_key
        }

        response = requests.post(url, headers=headers, data=data, files=files)
        response.raise_for_status()
        
        result = response.json()
        hindi_text = result.get('transcript', result.get('text', ''))
        
        print(f"⏱️ STT (Sarvam): {time.time() - t1:.2f}s")
        print(f"Heard: {hindi_text}")

    except Exception as e:
        print(f"❌ SARVAM STT ERROR: {str(e)}")
        if hasattr(e, 'response') and e.response is not None:
            print(f"Response: {e.response.text}")
        raise HTTPException(status_code=500, detail=f"STT Error: {str(e)}")

    if not hindi_text.strip():
        return {"status": "error", "message": "Could not hear anything clearly."}

    # --- STEP 2: Intent Extraction via Sarvam AI LLM ---
    t2 = time.time()
    try:
        recent_context_msg = ""
        if recent_customer:
            recent_context_msg = f"\n                    RECENT CONTEXT: The user just made a transaction for a customer named '{recent_customer}' (modifier: '{recent_modifier}'). If the user says something like 'aur 2 item de do' (give 2 more) WITHOUT explicitly saying a name, you MUST use '{recent_customer}' as the customer_name and '{recent_modifier}' as the customer_modifier."

        system_prompt = f"""
                    You are an AI for an Indian Kirana shop. 
                    Map the user's English speech (which has been translated from their native language) into strict JSON transactions.
                    Recent Context: {recent_context_msg}
                    
                    Map intents using this schema:
                    - 'target': "stock" (for ANY item sales, restocks, or supplier purchases) OR "ledger" (ONLY for checking/clearing accounts or order history).
                    - 'operation': MUST be from the shop's perspective. "add" (restock shop inventory, receive from supplier), "subtract" (sell/give. NOTE: "order me likho" or "khate me add karo" BOTH mean giving items to a customer, so MUST use "subtract"), "read" (check/inquiry), OR "clear" (settle/delete).
                    - 'item': Format as English name (e.g. "soap"). Strip units like 'packet', 'kilo'. Use "ALL" for full inventory. "" if not applicable.
                    - 'qty': Integer. Use 0 for read/clear operations.
                    - 'unit': The unit of measurement (e.g. "packet", "kilo", "bars", "pieces", "box"). "" if not mentioned.
                    - 'amount': Number — the total price in Rupees if mentioned (e.g. "500 rupay ka" -> 500, "1200 mein" -> 1200). 0 if no price mentioned.
                    - 'customer_name': English name of the customer/person buying (e.g. "ramesh"). Apply to all items in the utterance. Use Context if implied. "" if cash sale.
                    - 'customer_modifier': Any location or descriptor (e.g. "delhi wale"). "" if none.
                    - 'supplier_name': English name of the supplier/vendor from whom items are being PURCHASED/RECEIVED (e.g. "asha wholesale", "sharma distributor"). ONLY use when the shop is BUYING or RECEIVING stock. "" if not a supplier purchase.
                    - 'is_credit': boolean (true ONLY if "udhaar" or "khata" is explicitly mentioned. MUST be FALSE if they say "order").
                    
                    SUPPLIER DETECTION RULES:
                    - If user says "X se Y aaya/liya/mangwaya" (received/bought from X), X is the supplier_name and operation is "add".
                    - Keywords for supplier: "se aaya", "se liya", "se mangwaya", "supplier", "wholesale", "distributor".
                    - supplier_name should NEVER be the same as customer_name. Suppliers GIVE stock TO the shop. Customers GET stock FROM the shop.
                    
                    IMPORTANT: Return ONLY valid JSON matching this exact structure. DO NOT include trailing commas, and DO NOT include any conversational text or markdown formatting. Output raw JSON only:
                    {{
                      "hinglish_text": "Asha wholesale se 120 clutcher aaya 7800 ka",
                      "transactions": [
                        {{"target": "stock", "operation": "add", "item": "clutcher", "qty": 120, "unit": "", "amount": 7800, "customer_name": "", "customer_modifier": "", "supplier_name": "asha wholesale", "is_credit": false}}
                      ]
                    }}
                    
                    More examples:
                    - "do maggi ramesh delhi ke khate me" -> subtract, item=maggi, qty=2, customer_name=ramesh, customer_modifier=delhi, is_credit=true
                    - "ramesh ko 12 packet maggi diya 480 rupay udhaar" -> subtract, item=maggi, qty=12, unit=packet, amount=480, customer_name=ramesh, is_credit=true
                    - "khan beauty supply se 36 curly extension aur 24 darjan kacher aaya 6250 mein" -> TWO transactions, both operation=add, supplier_name=khan beauty supply
                    - "meera traders se 40 darjan farande aaye aur 200 hair pins, total 4400" -> TWO transactions, operation=add, supplier_name=meera traders
                    """

        chat_completion = groq_client.chat.completions.create(
            messages=[
                {"role": "system", "content": system_prompt},
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
        supplier_name = txn.get("supplier_name", "").strip()
        txn_amount = txn.get("amount", 0)
        txn_unit = txn.get("unit", "")

        # --- Handle Order Inquiries ---
        if target == "ledger" and operation == "read" and not is_credit:
            if not customer_name:
                errors.append("Kiske orders dekhne hain? (Please specify a name).")
                continue
            
            group_key = f"order_inquiry_{customer_name}_{customer_modifier}"
            title_name = f"{customer_name.capitalize()} ({customer_modifier})" if customer_modifier else customer_name.capitalize()
            group = get_group(group_key, f"{title_name}'s Orders", "🛍️", ["Item", "Qty"])

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
                group["rows"].append({"Item": item_name.capitalize(), "Qty": qty})
            
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
                update_fields = {
                    "quantity": final_qty,
                    "timestamp": firestore.SERVER_TIMESTAMP
                }
                if txn_amount:
                    existing_amount = existing_doc.to_dict().get("amount", 0)
                    update_fields["amount"] = existing_amount + txn_amount
                if txn_unit:
                    update_fields["unit"] = txn_unit
                existing_doc.reference.update(update_fields)
            else:
                udhaar_data = {
                    "customer_name": customer_name,
                    "customer_modifier": customer_modifier,
                    "item": standard_item,
                    "quantity": qty,
                    "amount": txn_amount or 0,
                    "unit": txn_unit or "",
                    "whatsapp_number": "",
                    "reminder_schedule": "",
                    "reminder_sent": False,
                    "due_note": "",
                    "timestamp": firestore.SERVER_TIMESTAMP,
                }
                user_udhaar_ref.add(udhaar_data)
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
                ["Item", "Qty", "Previous", "Current", "Customer"]
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
                    "Customer": title_name
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
            # Stock add — with or without supplier
            if supplier_name:
                group = get_group(
                    "supplier_purchase",
                    "Supplier Purchase",
                    "🏪",
                    ["Item", "Added", "Amount", "Supplier", "Stock Now"],
                )
                # Also record in suppliers_purchases collection
                purchases_ref = db.collection("users").document(uid).collection("suppliers_purchases")
                purchases_ref.add({
                    "supplier_name": supplier_name,
                    "item_name": standard_item,
                    "quantity": qty,
                    "amount": txn_amount or 0,
                    "proof_image_url": "",
                    "timestamp": firestore.SERVER_TIMESTAMP,
                })
                group["rows"].append(
                    {
                        "Item": standard_item.capitalize(),
                        "Added": qty,
                        "Amount": f"₹{txn_amount:,.0f}" if txn_amount else "-",
                        "Supplier": supplier_name.title(),
                        "Stock Now": new_qty,
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
        t0 = time.time()
        decoded = auth.verify_id_token(token)
        print(f"⏱️ Token Verify: {time.time() - t0:.2f}s")
        allowed_users_env = os.getenv("ALLOWED_PREVIEW_USERS", "")
        # If the environment variable isn't set (e.g. in Production), allow everyone
        if allowed_users_env:
            ALLOWED_PREVIEW_USERS = [u.strip() for u in allowed_users_env.split(",") if u.strip()]
            
            email = decoded.get("email", "")
            phone = decoded.get("phone_number", "")
            
            if email not in ALLOWED_PREVIEW_USERS and phone not in ALLOWED_PREVIEW_USERS:
                raise HTTPException(status_code=403, detail="Access Denied to Preview Branch")
            
        return decoded["uid"]
    except Exception as e:
        if isinstance(e, HTTPException):
            raise e
        raise HTTPException(status_code=401, detail="Invalid Authentication Token")


@app.get("/verify_access")
async def verify_access(authorization: str = Header(None)):
    verify_token(authorization)
    return {"status": "success", "allowed": True}

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


# ═══════ PYDANTIC MODELS ═══════

class PurchaseRequest(BaseModel):
    supplier_name: str
    item_name: str
    quantity: int
    amount: float
    proof_image_url: Optional[str] = ""


class LedgerEntryRequest(BaseModel):
    customer_name: str
    customer_modifier: Optional[str] = ""
    item: str
    quantity: int
    unit: Optional[str] = ""
    amount: Optional[float] = 0
    whatsapp_number: Optional[str] = ""
    reminder_schedule: Optional[str] = ""
    due_note: Optional[str] = ""


class LedgerEntryUpdate(BaseModel):
    customer_name: Optional[str] = None
    customer_modifier: Optional[str] = None
    item: Optional[str] = None
    quantity: Optional[int] = None
    unit: Optional[str] = None
    amount: Optional[float] = None
    whatsapp_number: Optional[str] = None
    reminder_schedule: Optional[str] = None
    due_note: Optional[str] = None


class WhatsAppReminderRequest(BaseModel):
    customer_name: str
    customer_modifier: Optional[str] = ""
    whatsapp_number: str
    reminder_schedule: str


# ═══════ SUPPLIERS ENDPOINTS ═══════

@app.get("/suppliers")
async def get_suppliers(authorization: str = Header(None)):
    uid = verify_token(authorization)
    purchases_ref = db.collection("users").document(uid).collection("suppliers_purchases")
    docs = purchases_ref.order_by("timestamp", direction=firestore.Query.DESCENDING).stream()

    purchases = []
    supplier_totals = {}
    now = datetime.datetime.now(datetime.timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    month_total = 0
    month_items = 0

    for doc in docs:
        data = doc.to_dict()
        ts_obj = data.get("timestamp")
        try:
            ts = ts_obj.timestamp() * 1000 if ts_obj else 0
        except AttributeError:
            ts = 0

        amount = data.get("amount", 0)
        entry = {
            "id": doc.id,
            "supplier_name": data.get("supplier_name", ""),
            "item_name": data.get("item_name", ""),
            "quantity": data.get("quantity", 0),
            "amount": amount,
            "proof_image_url": data.get("proof_image_url", ""),
            "timestamp": ts,
        }
        purchases.append(entry)

        # Aggregate by supplier
        sname = data.get("supplier_name", "Unknown")
        if sname not in supplier_totals:
            supplier_totals[sname] = {"total": 0, "items": []}
        supplier_totals[sname]["total"] += amount
        supplier_totals[sname]["items"].append(data.get("item_name", ""))

        # Monthly stats
        if ts_obj and ts_obj >= month_start:
            month_total += amount
            month_items += 1

    return {
        "purchases": purchases,
        "month_total": month_total,
        "month_items": month_items,
        "supplier_totals": supplier_totals,
    }


@app.post("/suppliers/purchase")
async def add_supplier_purchase(req: PurchaseRequest, authorization: str = Header(None)):
    uid = verify_token(authorization)

    if not req.supplier_name.strip() or not req.item_name.strip():
        raise HTTPException(status_code=400, detail="Supplier name and item name are required.")
    if req.quantity <= 0:
        raise HTTPException(status_code=400, detail="Quantity must be greater than 0.")

    purchases_ref = db.collection("users").document(uid).collection("suppliers_purchases")
    purchase_data = {
        "supplier_name": req.supplier_name.strip(),
        "item_name": req.item_name.strip().lower(),
        "quantity": req.quantity,
        "amount": req.amount,
        "proof_image_url": req.proof_image_url or "",
        "timestamp": firestore.SERVER_TIMESTAMP,
    }
    purchases_ref.add(purchase_data)

    # Auto-update stock inventory
    stock_ref = db.collection("users").document(uid).collection("stock")
    item_key = req.item_name.strip().lower()
    stock_doc_ref = stock_ref.document(item_key)
    stock_doc = stock_doc_ref.get()

    if stock_doc.exists:
        current_qty = stock_doc.to_dict().get("quantity", 0)
        stock_doc_ref.update({
            "quantity": current_qty + req.quantity,
            "updated_at": firestore.SERVER_TIMESTAMP,
        })
    else:
        stock_doc_ref.set({
            "item": item_key,
            "quantity": req.quantity,
            "created_at": firestore.SERVER_TIMESTAMP,
            "updated_at": firestore.SERVER_TIMESTAMP,
        })

    return {
        "status": "success",
        "message": f"Added {req.quantity}x {req.item_name} from {req.supplier_name}. Stock updated.",
    }


# ═══════ CUSTOMER LEDGER ENDPOINTS (uses udhaar collection) ═══════

@app.get("/ledger/customers")
async def get_ledger_customers(authorization: str = Header(None)):
    uid = verify_token(authorization)
    udhaar_ref = db.collection("users").document(uid).collection("udhaar")
    docs = udhaar_ref.order_by("timestamp", direction=firestore.Query.DESCENDING).stream()

    customers = {}
    total_due = 0

    for doc in docs:
        data = doc.to_dict()
        cname = data.get("customer_name", "unknown")
        cmod = data.get("customer_modifier", "")
        key = f"{cname}|{cmod}"

        amount = data.get("amount", 0)
        qty = data.get("quantity", 0)

        ts_obj = data.get("timestamp")
        try:
            ts = ts_obj.isoformat() if ts_obj else None
        except AttributeError:
            ts = None

        if key not in customers:
            customers[key] = {
                "customer_name": cname,
                "customer_modifier": cmod,
                "total_due": 0,
                "whatsapp_number": data.get("whatsapp_number", ""),
                "reminder_schedule": data.get("reminder_schedule", ""),
                "reminder_sent": data.get("reminder_sent", False),
                "due_note": data.get("due_note", ""),
                "last_entry": ts,
                "items": [],
            }

        entry_data = {
            "id": doc.id,
            "item": data.get("item", ""),
            "quantity": qty,
            "unit": data.get("unit", ""),
            "amount": amount,
            "timestamp": ts,
        }
        customers[key]["items"].append(entry_data)
        customers[key]["total_due"] += amount

        # Update whatsapp/reminder if this entry has it
        if data.get("whatsapp_number"):
            customers[key]["whatsapp_number"] = data.get("whatsapp_number", "")
        if data.get("reminder_schedule"):
            customers[key]["reminder_schedule"] = data.get("reminder_schedule", "")

        total_due += amount

    customer_list = list(customers.values())

    return {
        "customers": customer_list,
        "total_due": total_due,
        "customer_count": len(customer_list),
    }


@app.post("/ledger/entry")
async def add_ledger_entry(req: LedgerEntryRequest, authorization: str = Header(None)):
    uid = verify_token(authorization)

    if not req.customer_name.strip() or not req.item.strip():
        raise HTTPException(status_code=400, detail="Customer name and item are required.")

    udhaar_ref = db.collection("users").document(uid).collection("udhaar")
    entry_data = {
        "customer_name": req.customer_name.strip().lower(),
        "customer_modifier": req.customer_modifier.strip().lower() if req.customer_modifier else "",
        "item": req.item.strip().lower(),
        "quantity": req.quantity,
        "unit": req.unit or "",
        "amount": req.amount or 0,
        "whatsapp_number": req.whatsapp_number or "",
        "reminder_schedule": req.reminder_schedule or "",
        "reminder_sent": False,
        "due_note": req.due_note or "",
        "timestamp": firestore.SERVER_TIMESTAMP,
    }
    udhaar_ref.add(entry_data)

    return {
        "status": "success",
        "message": f"Added {req.item} x{req.quantity} to {req.customer_name}'s ledger.",
    }


@app.put("/ledger/entry/{entry_id}")
async def update_ledger_entry(entry_id: str, req: LedgerEntryUpdate, authorization: str = Header(None)):
    uid = verify_token(authorization)
    udhaar_ref = db.collection("users").document(uid).collection("udhaar")
    doc_ref = udhaar_ref.document(entry_id)
    doc = doc_ref.get()

    if not doc.exists:
        raise HTTPException(status_code=404, detail="Entry not found.")

    update_data = {}
    for field, value in req.dict(exclude_unset=True).items():
        if value is not None:
            update_data[field] = value

    if update_data:
        update_data["timestamp"] = firestore.SERVER_TIMESTAMP
        doc_ref.update(update_data)

    return {"status": "success", "message": "Entry updated."}


@app.post("/ledger/whatsapp-reminder")
async def schedule_whatsapp_reminder(req: WhatsAppReminderRequest, authorization: str = Header(None)):
    uid = verify_token(authorization)

    # Save reminder schedule to all entries for this customer
    udhaar_ref = db.collection("users").document(uid).collection("udhaar")
    docs = udhaar_ref.where(
        filter=FieldFilter("customer_name", "==", req.customer_name.lower())
    ).stream()

    updated = 0
    for doc in docs:
        data = doc.to_dict()
        if req.customer_modifier and data.get("customer_modifier", "").lower() != req.customer_modifier.lower():
            continue
        doc.reference.update({
            "whatsapp_number": req.whatsapp_number,
            "reminder_schedule": req.reminder_schedule,
            "reminder_sent": False,
        })
        updated += 1

    # Placeholder — no actual WhatsApp API integration
    return {
        "status": "success",
        "message": f"WhatsApp reminder scheduled for {req.customer_name}. (Placeholder — integration pending)",
        "updated_entries": updated,
    }

