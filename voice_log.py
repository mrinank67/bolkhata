"""Diagnostic record of one voice request — users/{uid}/voice_logs/{auto_id}.

Support's only window into the voice pipeline. Without it a shopkeeper saying
"it heard the wrong thing" is unanswerable: the transcript and the parsed intent
used to live in memory for one request and then vanish, and the Vercel function
logs that carried the timings are ephemeral and not queryable.

Two properties this file exists to guarantee:

1. **Every outcome is recorded, not just the successes.** A request that was rate
   limited, produced silence, or died inside Sarvam is exactly the one being
   complained about. routes/voice.py calls emit() before each early return and
   each raise.
2. **The write never breaks the shopkeeper's request.** It runs in a
   BackgroundTask and swallows its own errors — the same fail-open posture
   rate_limiter.py takes, for the same reason: diagnostics must not cost sales.

Audio is deliberately not stored, here or anywhere. Retention is 30 days via a
Firestore TTL policy on expires_at; see docs/bill-retention.md for the mechanism
and docs/security.md for what this means for PII.
"""

from datetime import datetime, timedelta, timezone

from firebase_admin import firestore

# Matches the bill retention window so there is one number to reason about.
VOICE_LOG_RETENTION_DAYS = 30

# Firestore rejects documents over 1 MiB, and a runaway LLM response would take
# the whole log write down with it — losing the record of the very request that
# misbehaved. Truncate instead, and say so in the stored value.
MAX_TRANSCRIPT_CHARS = 4000
MAX_INTENT_CHARS = 20000
MAX_ERROR_CHARS = 2000


def _clip(value: str, limit: int) -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text
    return text[:limit] + f"… [truncated, {len(text)} chars]"


def voice_log_expiry() -> datetime:
    return datetime.now(timezone.utc) + timedelta(days=VOICE_LOG_RETENTION_DAYS)


def build_voice_log(status: str, **fields) -> dict:
    """The document body, separated from the write so tests can assert on it."""
    entry = {
        "status": status,
        "timestamp": firestore.SERVER_TIMESTAMP,
        "expires_at": voice_log_expiry(),
    }

    if "transcript" in fields:
        entry["transcript"] = _clip(fields.pop("transcript"), MAX_TRANSCRIPT_CHARS)
    if "intent" in fields:
        # Stored as text: the intent is free-form LLM output, and Firestore
        # rejects nested nulls and over-deep maps that a dict write would hit.
        intent = fields.pop("intent")
        entry["intent"] = _clip(
            intent if isinstance(intent, str) else repr(intent), MAX_INTENT_CHARS
        )
    if "error_detail" in fields:
        entry["error_detail"] = _clip(fields.pop("error_detail"), MAX_ERROR_CHARS)

    # Everything else (timings, models, audio metadata, injected context) is
    # small and fixed-shape; drop keys with no value so the doc stays readable.
    for key, value in fields.items():
        if value is not None:
            entry[key] = value

    return entry


def write_voice_log(db, uid: str, status: str, **fields) -> None:
    """Write the log immediately. Never raises.

    Used by the paths that raise an HTTPException. A background task attached to
    the request is silently dropped when the handler raises — FastAPI builds the
    error response from the exception handler, which carries no background tasks
    — so the STT and LLM crashes, the two entries support most needs, would
    never be written if they were deferred like the rest. The extra few
    milliseconds are irrelevant on a request that is already failing.
    """
    try:
        db.collection("users").document(uid).collection("voice_logs").add(
            build_voice_log(status, **fields)
        )
    except Exception as e:
        # Fail-open: a diagnostics failure must not surface to the shopkeeper.
        print(f"⚠️ voice_log write failed ({status}): {e!s}")


def emit_voice_log(db, background_tasks, uid: str, status: str, **fields) -> None:
    """Schedule the log write after the response. Never raises.

    For paths that return normally. Use write_voice_log() from a path that
    raises — see the note there.
    """
    try:
        background_tasks.add_task(write_voice_log, db, uid, status, **fields)
    except Exception as e:
        print(f"⚠️ voice_log could not be scheduled ({status}): {e!s}")


def ms(started_at: float, now: float) -> int:
    """Elapsed milliseconds, rounded — the stored twin of the ⏱️ prints."""
    return int(round((now - started_at) * 1000))
