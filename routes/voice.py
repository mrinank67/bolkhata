"""
Voice processing endpoint — POST /process_voice
"""

import time
import json
import os
import datetime
import requests

from fastapi import APIRouter, HTTPException, UploadFile, File, Header, BackgroundTasks
from fastapi.responses import JSONResponse
from groq import Groq
from firebase_admin import firestore

from auth import verify_token
from prompts import get_system_prompt
from db_operations import process_transactions
from models import ResolveTransactionRequest
from rate_limiter import (
    check_user_cooldown,
    check_global_rate_limit,
    record_rate_limit_hit,
    GROQ_RPM,
    GROQ_RPD,
    SARVAM_RPM,
)

router = APIRouter()

# Setup Groq & Sarvam (lazy init to avoid import-time errors before load_dotenv)
_groq_client = None
_sarvam_api_key = None


def _get_groq_client():
    global _groq_client
    if _groq_client is None:
        _groq_client = Groq(api_key=os.getenv("GROQ_API_KEY"))
    return _groq_client


def _get_sarvam_key():
    global _sarvam_api_key
    if _sarvam_api_key is None:
        _sarvam_api_key = os.getenv("SARVAM_API_KEY")
    return _sarvam_api_key


def _debug_logs() -> bool:
    """Transcripts/intents are PII — only log them when explicitly enabled.
    Read at call time because this module imports before load_dotenv()."""
    return os.getenv("DEBUG_LOGS", "").lower() in ("1", "true", "yes")


@router.post("/process_voice")
async def process_voice(
    background_tasks: BackgroundTasks,
    audio: UploadFile = File(...),
    authorization: str = Header(None),
):
    from main import db

    start_total = time.time()

    uid = verify_token(authorization)

    # ── Rate Limit Checks (before any external API calls) ──
    # 1. Per-user cooldown + daily cap
    allowed, retry_after = check_user_cooldown(db, uid)
    if not allowed:
        if retry_after > 3600:
            message = "Aaj ki voice limit khatam ho gayi. Please try again tomorrow."
        else:
            message = f"Thoda ruko! Try again in {retry_after:.0f}s."
        return JSONResponse(
            status_code=429,
            content={
                "status": "rate_limited",
                "message": message,
                "retry_after": retry_after,
            },
            headers={"Retry-After": str(int(retry_after) + 1)},
        )

    # 2. Global Sarvam STT rate limit
    allowed, retry_after = check_global_rate_limit(db, SARVAM_RPM)
    if not allowed:
        print(f"⚠️ Sarvam STT rate limit hit — retry_after={retry_after}s")
        return JSONResponse(
            status_code=429,
            content={
                "status": "rate_limited",
                "message": f"Server busy. Please try again in {retry_after:.0f} seconds.",
                "retry_after": retry_after,
            },
            headers={"Retry-After": str(int(retry_after) + 1)},
        )

    # 3. Global Groq LLM rate limits (RPM + daily)
    allowed, retry_after = check_global_rate_limit(db, GROQ_RPM)
    if not allowed:
        print(f"⚠️ Groq RPM rate limit hit — retry_after={retry_after}s")
        return JSONResponse(
            status_code=429,
            content={
                "status": "rate_limited",
                "message": f"Server busy. Please try again in {retry_after:.0f} seconds.",
                "retry_after": retry_after,
            },
            headers={"Retry-After": str(int(retry_after) + 1)},
        )

    allowed, retry_after = check_global_rate_limit(db, GROQ_RPD)
    if not allowed:
        print(f"⚠️ Groq daily rate limit hit — retry_after={retry_after}s")
        return JSONResponse(
            status_code=429,
            content={
                "status": "rate_limited",
                "message": "Daily limit reached. Please try again tomorrow.",
                "retry_after": retry_after,
            },
            headers={"Retry-After": str(int(retry_after) + 1)},
        )

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

        # Push-to-talk clips are well under 1 MB; cap before spending Sarvam quota
        if len(audio_bytes) > 2 * 1024 * 1024:
            return {
                "status": "error",
                "message": "Audio too long. Please keep messages under 30 seconds.",
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
            'api-subscription-key': _get_sarvam_key()
        }

        response = requests.post(url, headers=headers, data=data, files=files)

        # Retry once on 429 from Sarvam
        if response.status_code == 429:
            record_rate_limit_hit(db, SARVAM_RPM)
            print("⚠️ Sarvam 429 — retrying after 2s...")
            time.sleep(2)
            # Re-read audio bytes for retry (file pointer already consumed)
            files_retry = {
                'file': (audio.filename, audio_bytes, audio.content_type)
            }
            response = requests.post(url, headers=headers, data=data, files=files_retry)
            if response.status_code == 429:
                return JSONResponse(
                    status_code=429,
                    content={
                        "status": "rate_limited",
                        "message": "Voice service is busy. Please try again in a few seconds.",
                        "retry_after": 5,
                    },
                    headers={"Retry-After": "5"},
                )

        response.raise_for_status()
        
        result = response.json()
        hindi_text = result.get('transcript', result.get('text', ''))
        
        print(f"⏱️ STT (Sarvam): {time.time() - t1:.2f}s")
        if _debug_logs():
            print(f"Heard: {hindi_text}")

    except Exception as e:
        print(f"❌ SARVAM STT ERROR: {str(e)}")
        if hasattr(e, 'response') and e.response is not None:
            print(f"Response: {e.response.text}")
        # Don't leak internal error details to the client
        raise HTTPException(status_code=500, detail="Speech recognition failed. Please try again.")

    if not hindi_text.strip():
        return {"status": "error", "message": "Could not hear anything clearly."}

    # --- STEP 2: Intent Extraction via Groq LLM ---
    t2 = time.time()
    try:
        recent_context_msg = ""
        if recent_customer:
            recent_context_msg = f"\nRECENT CONTEXT: The user just made a transaction for a customer named '{recent_customer}' (modifier: '{recent_modifier}'). If the user says something like 'aur 2 item de do' (give 2 more) WITHOUT explicitly saying a name, you MUST use '{recent_customer}' as the customer_name and '{recent_modifier}' as the customer_modifier."

        system_prompt = get_system_prompt(recent_context_msg)

        try:
            chat_completion = _get_groq_client().chat.completions.create(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"Text to process: '{hindi_text}'"},
                ],
                model="llama-3.1-8b-instant",
                response_format={"type": "json_object"},
                temperature=0.0,
            )
        except Exception as groq_err:
            # Retry once on Groq 429 (rate limit from their side)
            err_status = getattr(groq_err, 'status_code', None)
            if err_status == 429:
                record_rate_limit_hit(db, GROQ_RPM)
                print("⚠️ Groq 429 — retrying after 3s...")
                time.sleep(3)
                chat_completion = _get_groq_client().chat.completions.create(
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": f"Text to process: '{hindi_text}'"},
                    ],
                    model="llama-3.1-8b-instant",
                    response_format={"type": "json_object"},
                    temperature=0.0,
                )
            else:
                raise groq_err

        json_str = chat_completion.choices[0].message.content
        intent = json.loads(json_str)
        print(f"⏱️ LLM (Groq Llama3): {time.time() - t2:.2f}s")
        if _debug_logs():
            print(f"Understood Intent: {intent}")

    except Exception as e:
        print(f"❌ GROQ LLM ERROR: {str(e)}")
        # If it's still a 429 after retry, return proper 429 to client
        err_status = getattr(e, 'status_code', None)
        if err_status == 429:
            return JSONResponse(
                status_code=429,
                content={
                    "status": "rate_limited",
                    "message": "AI service is busy. Please try again in a few seconds.",
                    "retry_after": 5,
                },
                headers={"Retry-After": "5"},
            )
        raise HTTPException(status_code=500, detail="Failed to understand the intent.")

    # --- STEP 3: Standardization & Database Loop ---
    t3 = time.time()
    # Handle LLM returning either a flat object or a transactions array
    # (or "transactions": null)
    transactions = intent.get("transactions") or []
    if not transactions and "action" in intent:
        # LLM returned a single flat transaction instead of an array
        transactions = [intent]

    result_list, errors = process_transactions(
        transactions=transactions,
        uid=uid,
        db=db,
        user_stock_ref=user_stock_ref,
        user_udhaar_ref=user_udhaar_ref,
        user_orders_ref=user_orders_ref,
    )

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
        "raw_text": hindi_text,
        "understood_intent": intent,
    }


@router.post("/voice/resolve")
async def resolve_transaction(
    req: ResolveTransactionRequest,
    background_tasks: BackgroundTasks,
    authorization: str = Header(None),
):
    from main import db

    uid = verify_token(authorization)
    txn = req.transaction
    txn["customer_modifier"] = req.selected_modifier
    # Mark as user-resolved so processing doesn't re-prompt when the chosen
    # customer has no modifier (empty modifier would otherwise loop forever)
    txn["_resolved"] = True

    user_stock_ref = db.collection("users").document(uid).collection("stock")
    user_udhaar_ref = db.collection("users").document(uid).collection("udhaar")
    user_orders_ref = db.collection("users").document(uid).collection("orders")

    result_list, errors = process_transactions(
        transactions=[txn],
        uid=uid,
        db=db,
        user_stock_ref=user_stock_ref,
        user_udhaar_ref=user_udhaar_ref,
        user_orders_ref=user_orders_ref,
    )

    # Save to history in background, same as /process_voice
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

    return {"status": "success", "results": result_list, "errors": errors}
