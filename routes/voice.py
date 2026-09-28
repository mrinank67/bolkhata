"""
Voice processing endpoint — POST /process_voice
"""

import datetime
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor

import requests
from fastapi import APIRouter, BackgroundTasks, File, Header, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from firebase_admin import firestore
from groq import Groq

from audio_split import (
    MAX_AUDIO_SECONDS,
    MAX_CHUNK_SECONDS,
    MIN_AUDIO_SECONDS,
    decode_pcm,
    pcm_seconds,
    split_pcm,
    to_wav,
)
from auth import verify_token
from db_operations import process_transactions
from models import ResolveTransactionRequest
from prompts import get_system_prompt, get_translation_prompt
from rate_limiter import (
    GROQ_RPD,
    GROQ_RPM,
    SARVAM_RPM,
    check_global_rate_limit,
    check_user_cooldown,
    record_rate_limit_hit,
)
from voice_log import emit_voice_log, ms, write_voice_log

router = APIRouter()

# Setup Groq & Sarvam (lazy init to avoid import-time errors before load_dotenv)
_groq_client = None
_sarvam_api_key = None

# Intent-extraction model. gpt-oss-20b is a reasoning model; the shop floor cares
# about latency far more than deliberation, so reasoning effort is pinned low.
GROQ_MODEL = "openai/gpt-oss-20b"
GROQ_REASONING_EFFORT = "low"

# Sarvam speech-to-text model. Named here rather than inline in the request so
# the voice log records which model produced a transcript — the first thing
# worth knowing when transcription quality changes after an upgrade.
STT_MODEL = "saaras:v3"
# codemix, not translate: speech comes back untranslated — each language in its
# own script, English words (brands, units) in Latin, numbers as digits. The
# translation is a separate Groq pass (get_translation_prompt), because Sarvam's
# translate mode rendered "12 rupees each" and "12 rupees for the lot" alike and
# a per-unit price landed as the line total.
STT_MODE = "codemix"
SARVAM_URL = "https://api.sarvam.ai/speech-to-text"


class SarvamBusy(Exception):
    """Sarvam still answered 429 after the one retry."""


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


def _needs_translation(text: str) -> bool:
    """True when the transcript has any non-Latin letters.

    codemix writes every Indian language in its own script, so a transcript of
    plain ASCII letters is already English and the translation call is skipped.
    """
    return any(c.isalpha() and not c.isascii() for c in text)


def _groq_json(db, system_prompt: str, user_content: str) -> str:
    """One JSON-mode call to GROQ_MODEL, retried once on a Groq 429.

    Returns the raw message content; raises Groq's error if the retry fails too.
    """
    kwargs = {
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        "model": GROQ_MODEL,
        "response_format": {"type": "json_object"},
        "temperature": 0.0,
        "reasoning_effort": GROQ_REASONING_EFFORT,
    }
    try:
        completion = _get_groq_client().chat.completions.create(**kwargs)
    except Exception as groq_err:
        # Retry once on Groq 429 (rate limit from their side)
        if getattr(groq_err, "status_code", None) != 429:
            raise
        record_rate_limit_hit(db, GROQ_RPM)
        print("⚠️ Groq 429 — retrying after 3s...")
        time.sleep(3)
        completion = _get_groq_client().chat.completions.create(**kwargs)
    return completion.choices[0].message.content


def _translate(db, transcript: str) -> str:
    """The transcript in English, from the contextual translation pass.

    Charges its own Groq request to the global quotas. Raises on anything short
    of a usable translation — including a refused quota — so the caller can fall
    back to the untranslated transcript.
    """
    for config in (GROQ_RPM, GROQ_RPD):
        allowed, retry_after = check_global_rate_limit(db, config)
        if not allowed:
            raise RuntimeError(f"skipped: {config.firestore_key} quota, retry_after={retry_after}")

    raw = _groq_json(db, get_translation_prompt(), transcript)
    english = json.loads(raw).get("english")
    if not isinstance(english, str) or not english.strip():
        raise ValueError(f"no translation in model output: {raw[:200]!r}")
    return english.strip()


def _transcribe_clip(db, clip: tuple) -> dict:
    """One Sarvam REST call for one (filename, bytes, mime) clip of <= 30 s.

    Retries once on 429 and raises SarvamBusy if the retry is refused too.
    """
    data = {
        "model": STT_MODEL,
        "language_code": "unknown",
        "mode": STT_MODE,
        "with_diarization": "false",
    }
    headers = {"api-subscription-key": _get_sarvam_key()}

    response = requests.post(SARVAM_URL, headers=headers, data=data, files={"file": clip})
    if response.status_code == 429:
        record_rate_limit_hit(db, SARVAM_RPM)
        print("⚠️ Sarvam 429 — retrying after 2s...")
        time.sleep(2)
        response = requests.post(SARVAM_URL, headers=headers, data=data, files={"file": clip})
        if response.status_code == 429:
            raise SarvamBusy()

    response.raise_for_status()
    return response.json()


def _prepare_clips(audio_bytes: bytes, filename, mime) -> tuple[list[tuple], float | None]:
    """The clips to send to Sarvam, and the decoded duration when it is known.

    A clip under Sarvam's limit goes up exactly as uploaded. A longer one is cut
    at pauses into WAV chunks (see audio_split.py). If the audio cannot be
    decoded, it also goes up as uploaded: Sarvam may still read it, and if not,
    that failure is logged exactly as it was before the splitting existed.
    """
    original = [(filename, audio_bytes, mime)]
    try:
        pcm = decode_pcm(audio_bytes)
    except Exception as e:
        print(f"⚠️ Could not decode audio for splitting: {type(e).__name__}: {e!s}")
        return original, None

    seconds = pcm_seconds(pcm)
    if seconds <= MAX_CHUNK_SECONDS:
        return original, seconds

    clips = [
        (f"chunk{i}.wav", to_wav(chunk), "audio/wav") for i, chunk in enumerate(split_pcm(pcm))
    ]
    return clips, seconds


@router.post("/process_voice")
async def process_voice(
    background_tasks: BackgroundTasks,
    audio: UploadFile = File(...),
    authorization: str = Header(None),
):
    from main import db

    start_total = time.time()

    uid = verify_token(authorization)

    # Diagnostic context accumulated as the request learns things, so the log
    # written at any exit point carries everything known by that point. A dict
    # rather than locals because the earliest exits (rate limits) happen before
    # the transcript, the intent or even the audio size exist.
    ctx: dict = {"stt_model": STT_MODEL, "stt_mode": STT_MODE, "llm_model": GROQ_MODEL}

    def _log(status: str, **fields):
        emit_voice_log(
            db,
            background_tasks,
            uid,
            status,
            total_ms=ms(start_total, time.time()),
            **{**ctx, **fields},
        )

    def _log_now(status: str, **fields):
        """The same entry, written before the handler raises.

        A background task does not survive an exception — see write_voice_log().
        """
        write_voice_log(
            db,
            uid,
            status,
            total_ms=ms(start_total, time.time()),
            **{**ctx, **fields},
        )

    # ── Rate Limit Checks (before any external API calls) ──
    # 1. Per-user cooldown + daily cap
    allowed, retry_after = check_user_cooldown(db, uid)
    if not allowed:
        # Cooldown retries are <= 2s; anything longer is the daily cap
        if retry_after > 10:
            message = "Aaj ki voice limit khatam ho gayi. Please try again tomorrow."
        else:
            message = f"Thoda ruko! Try again in {retry_after:.0f}s."
        _log("rate_limited", error_detail=f"user cooldown/daily cap, retry_after={retry_after}")
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
        _log("rate_limited", error_detail=f"global sarvam rpm, retry_after={retry_after}")
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
        _log("rate_limited", error_detail=f"global groq rpm, retry_after={retry_after}")
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
        _log("rate_limited", error_detail=f"global groq rpd, retry_after={retry_after}")
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

    # Fetch recent customer context from the last 2 minutes
    recent_customer = ""
    recent_modifier = ""
    recent_order_id = ""
    try:
        last_orders = list(
            user_orders_ref.order_by("timestamp", direction=firestore.Query.DESCENDING)
            .limit(1)
            .stream()
        )
        last_order_time = last_orders[0].to_dict().get("timestamp") if last_orders else None

        last_udhaars = list(
            user_udhaar_ref.order_by("timestamp", direction=firestore.Query.DESCENDING)
            .limit(1)
            .stream()
        )
        last_udhaar_time = last_udhaars[0].to_dict().get("timestamp") if last_udhaars else None

        latest_doc = None
        if last_order_time and last_udhaar_time:
            latest_doc = last_orders[0] if last_order_time > last_udhaar_time else last_udhaars[0]
        elif last_order_time:
            latest_doc = last_orders[0]
        elif last_udhaar_time:
            latest_doc = last_udhaars[0]

        now = datetime.datetime.now(datetime.timezone.utc)
        if latest_doc:
            data = latest_doc.to_dict()
            ts = data.get("timestamp")
            if ts and (now - ts).total_seconds() < 120:  # 2 minutes
                # A counter sale's order has no customer on it, so this stays
                # empty: nothing carries over from one. Two counter sales a
                # minute apart are almost always two people in the queue, and
                # neither the LLM's follow-up context nor the order-reuse below
                # should tie them together.
                recent_customer = data.get("customer_name", "")
                recent_modifier = data.get("customer_modifier", "")

        # Carry the recent order's id forward so a follow-up command for the same
        # customer appends to that order instead of starting a new one. Guarded so
        # it only applies when the last order is itself within the window and
        # belongs to the recent customer (an amount-only credit writes no order,
        # leaving a stale last order that must not be reused).
        if (
            recent_customer
            and last_orders
            and last_order_time
            and (now - last_order_time).total_seconds() < 120
        ):
            o = last_orders[0].to_dict()
            if o.get("customer_name", "") == recent_customer and (
                o.get("customer_modifier", "") or ""
            ) == (recent_modifier or ""):
                recent_order_id = o.get("order_id", "")
    except Exception as e:
        print("Error fetching recent context:", e)

    # The context injected into the prompt is the first thing to check when an
    # utterance lands on the wrong customer, so it is logged whatever happens next.
    ctx["recent_customer"] = recent_customer or None
    ctx["recent_modifier"] = recent_modifier or None
    ctx["recent_order_id"] = recent_order_id or None

    # --- STEP 1: Speech-to-Text via Sarvam AI ---
    t1 = time.time()
    try:
        audio_bytes = await audio.read()
        ctx["audio_size"] = len(audio_bytes)
        ctx["audio_mime"] = audio.content_type
        ctx["audio_filename"] = audio.filename

        if len(audio_bytes) < 100:
            _log("audio_too_short")
            return {
                "status": "error",
                "message": "Audio too short. Please hold the button while speaking.",
            }

        # A 2-minute clip is ~2 MB at the browser's default Opus bitrate; cap
        # before spending any effort on it
        if len(audio_bytes) > 2 * 1024 * 1024:
            _log("audio_too_long")
            return {
                "status": "error",
                "message": "Audio too long. Please keep messages under 2 minutes.",
            }

        clips, seconds = _prepare_clips(audio_bytes, audio.filename, audio.content_type)
        ctx["stt_chunks"] = len(clips)
        if seconds is not None:
            ctx["audio_seconds"] = round(seconds, 1)
            if seconds < MIN_AUDIO_SECONDS:
                _log("audio_too_short")
                return {
                    "status": "error",
                    "message": "Audio too short. Please hold the button while speaking.",
                }
            if seconds > MAX_AUDIO_SECONDS:
                _log("audio_too_long")
                return {
                    "status": "error",
                    "message": "Audio too long. Please keep messages under 2 minutes.",
                }

        if len(clips) > 1:
            # The Sarvam slot taken above pays for the first chunk; every further
            # chunk is another request against the same per-minute quota.
            allowed, retry_after = check_global_rate_limit(db, SARVAM_RPM, cost=len(clips) - 1)
            if not allowed:
                print(f"⚠️ Sarvam STT rate limit hit (chunked) — retry_after={retry_after}s")
                _log(
                    "rate_limited",
                    error_detail=f"global sarvam rpm ({len(clips)} chunks), retry_after={retry_after}",
                )
                return JSONResponse(
                    status_code=429,
                    content={
                        "status": "rate_limited",
                        "message": f"Server busy. Please try again in {retry_after:.0f} seconds.",
                        "retry_after": retry_after,
                    },
                    headers={"Retry-After": str(int(retry_after) + 1)},
                )

        if len(clips) == 1:
            results = [_transcribe_clip(db, clips[0])]
        else:
            # In parallel, so a 1-minute clip costs about the latency of a
            # 30-second one. map() keeps the chunks in spoken order.
            with ThreadPoolExecutor(max_workers=len(clips)) as pool:
                results = list(pool.map(lambda clip: _transcribe_clip(db, clip), clips))

        parts = (r.get("transcript", r.get("text", "")) or "" for r in results)
        hindi_text = " ".join(p.strip() for p in parts if p.strip())

        ctx["stt_ms"] = ms(t1, time.time())
        ctx["transcript"] = hindi_text
        ctx["stt_language"] = results[0].get("language_code")
        print(f"⏱️ STT (Sarvam, {len(clips)} chunk(s)): {time.time() - t1:.2f}s")
        if _debug_logs():
            print(f"Heard: {hindi_text}")

    except SarvamBusy:
        _log(
            "rate_limited",
            stt_ms=ms(t1, time.time()),
            error_detail="sarvam 429 after one retry",
        )
        return JSONResponse(
            status_code=429,
            content={
                "status": "rate_limited",
                "message": "Voice service is busy. Please try again in a few seconds.",
                "retry_after": 5,
            },
            headers={"Retry-After": "5"},
        )

    except Exception as e:
        print(f"❌ SARVAM STT ERROR: {e!s}")
        error_detail = f"{type(e).__name__}: {e!s}"
        if getattr(e, "response", None) is not None:
            print(f"Response: {e.response.text}")
            # Sarvam's body is the only place a 400 says *why* it refused the
            # audio; without it every rejection reads as a bare "Bad Request".
            error_detail += f" | {e.response.text}"
        # The client is told nothing beyond "it failed"; the log is where the
        # actual reason lives, which is the whole point of writing one.
        _log_now("stt_error", stt_ms=ms(t1, time.time()), error_detail=error_detail)
        # Don't leak internal error details to the client
        raise HTTPException(status_code=500, detail="Speech recognition failed. Please try again.")

    if not hindi_text.strip():
        _log("stt_empty")
        return {"status": "error", "message": "Could not hear anything clearly."}

    # --- STEP 2: Contextual translation via Groq ---
    # Fail-open: if the translation is refused or unusable, the intent model
    # gets the untranslated transcript. It reads Indian languages well enough
    # that this beats failing a request whose STT has already been paid for.
    english_text = hindi_text
    if _needs_translation(hindi_text):
        t_tr = time.time()
        try:
            english_text = _translate(db, hindi_text)
            ctx["translation"] = english_text
            if _debug_logs():
                print(f"Translated: {english_text}")
        except Exception as e:
            print(f"⚠️ Translation failed, using the raw transcript: {type(e).__name__}: {e!s}")
            ctx["translation_error"] = f"{type(e).__name__}: {e!s}"
        ctx["translate_ms"] = ms(t_tr, time.time())
        print(f"⏱️ Translation (Groq {GROQ_MODEL}): {time.time() - t_tr:.2f}s")

    # --- STEP 3: Intent Extraction via Groq LLM ---
    t2 = time.time()
    try:
        recent_context_msg = ""
        if recent_customer:
            recent_context_msg = f"\nRECENT CONTEXT: The user just made a transaction for a customer named '{recent_customer}' (modifier: '{recent_modifier}'). If the user says something like 'aur 2 item de do' (give 2 more) WITHOUT explicitly saying a name, you MUST use '{recent_customer}' as the customer_name and '{recent_modifier}' as the customer_modifier."

        system_prompt = get_system_prompt(recent_context_msg)

        json_str = _groq_json(db, system_prompt, f"Text to process: '{english_text}'")
        intent = json.loads(json_str)
        ctx["llm_ms"] = ms(t2, time.time())
        # The raw string, not the parsed dict: when the LLM emits something the
        # schema does not describe, the exact text is what explains the outcome.
        ctx["intent"] = json_str
        print(f"⏱️ LLM (Groq {GROQ_MODEL}): {time.time() - t2:.2f}s")
        if _debug_logs():
            print(f"Understood Intent: {intent}")

    except Exception as e:
        print(f"❌ GROQ LLM ERROR: {e!s}")
        # If it's still a 429 after retry, return proper 429 to client
        err_status = getattr(e, "status_code", None)
        if err_status == 429:
            _log(
                "rate_limited",
                llm_ms=ms(t2, time.time()),
                error_detail="groq 429 after one retry",
            )
            return JSONResponse(
                status_code=429,
                content={
                    "status": "rate_limited",
                    "message": "AI service is busy. Please try again in a few seconds.",
                    "retry_after": 5,
                },
                headers={"Retry-After": "5"},
            )
        _log_now("llm_error", llm_ms=ms(t2, time.time()), error_detail=f"{type(e).__name__}: {e!s}")
        raise HTTPException(status_code=500, detail="Failed to understand the intent.")

    # --- STEP 4: Standardization & Database Loop ---
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
        recent_customer=recent_customer,
        recent_modifier=recent_modifier,
        recent_order_id=recent_order_id,
    )

    # Save to history in background (non-blocking)
    if result_list or errors:

        def write_history():
            user_history_ref = db.collection("users").document(uid).collection("history")
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

    # Logged even when the pipeline succeeded but produced nothing: "I said it
    # and nothing happened" is a complaint, and an empty result list with a
    # readable transcript and intent is what explains it.
    _log(
        "ok",
        db_ms=ms(t3, time.time()),
        transaction_count=len(transactions),
        results=result_list,
        errors=errors,
    )

    return {
        "status": "success",
        "results": result_list,
        "errors": errors,
        "raw_text": hindi_text,
        "translated_text": ctx.get("translation"),
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
            user_history_ref = db.collection("users").document(uid).collection("history")
            user_history_ref.add(
                {
                    "results": result_list,
                    "errors": errors,
                    "timestamp": firestore.SERVER_TIMESTAMP,
                }
            )

        background_tasks.add_task(write_history)

    # The disambiguation round-trip is its own log entry: it is the second half
    # of an earlier /process_voice request, and without it the log would show an
    # ambiguous utterance that apparently never resolved.
    emit_voice_log(
        db,
        background_tasks,
        uid,
        "resolve",
        intent=json.dumps({"transactions": [txn]}, default=str),
        selected_modifier=req.selected_modifier or None,
        results=result_list,
        errors=errors,
    )

    return {"status": "success", "results": result_list, "errors": errors}
