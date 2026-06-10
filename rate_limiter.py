"""
Rate Limiter for external API calls (Groq, Sarvam).

Uses Firestore as shared state so rate limits work correctly
across Vercel serverless invocations.

To upgrade plans, simply change the values in GROQ_LIMITS / SARVAM_LIMITS below.
"""

import time
import datetime
from dataclasses import dataclass
from typing import Optional, Tuple


# ═══════════════════════════════════════════════════════════════
# CONFIGURABLE LIMITS — Change these when upgrading API plans
# ═══════════════════════════════════════════════════════════════

@dataclass
class RateLimitConfig:
    """Rate limit configuration for a single API."""
    name: str               # Display name (e.g. "Groq LLM")
    max_requests: int       # Max requests allowed in the window
    window_seconds: int     # Sliding window size in seconds
    firestore_key: str      # Key used in the Firestore rate_limits document


# ── Groq Free Tier ──
# Actual limits: 30 RPM, 14,400 RPD
# We use 80% to leave headroom
GROQ_RPM = RateLimitConfig(
    name="Groq LLM",
    max_requests=24,         # 80% of 30 RPM
    window_seconds=60,
    firestore_key="groq_rpm",
)

GROQ_RPD = RateLimitConfig(
    name="Groq LLM (daily)",
    max_requests=11500,      # 80% of 14,400 RPD
    window_seconds=86400,
    firestore_key="groq_rpd",
)

# ── Sarvam Starter Plan ──
# Actual limits: 60 RPM for real-time STT
# We use 80% to leave headroom
SARVAM_RPM = RateLimitConfig(
    name="Sarvam STT",
    max_requests=48,         # 80% of 60 RPM
    window_seconds=60,
    firestore_key="sarvam_rpm",
)

# ── Per-user cooldown ──
# Minimum seconds between voice requests for a single user.
# Set low (2s) for hectic shop environments where speed matters.
USER_COOLDOWN_SECONDS = 2

# ── Per-user daily cap ──
# Stops a single account from exhausting the shared Groq/Sarvam quota
# (open signup + global limits = one abuser can lock out everyone).
# A busy shop doing a sale every 2 minutes for 12 hours is ~360 requests.
USER_DAILY_LIMIT = 400


# ═══════════════════════════════════════════════════════════════
# RATE LIMIT LOGIC (Firestore-backed sliding window)
# ═══════════════════════════════════════════════════════════════

# Firestore document path for global rate limits
_RATE_LIMITS_COLLECTION = "_system"
_RATE_LIMITS_DOC = "rate_limits"


def check_global_rate_limit(
    db, config: RateLimitConfig
) -> Tuple[bool, float]:
    """
    Check and update the global sliding-window rate limit for an API.

    For short windows (< 3600s): uses timestamp-based sliding window.
    For long windows (daily): uses a simple counter with date key
    to avoid storing thousands of timestamps in Firestore.

    Uses a Firestore transaction to atomically read/prune/append.

    Returns:
        (allowed, retry_after)
        - allowed=True, retry_after=0  → request may proceed
        - allowed=False, retry_after=N → request blocked, retry in N seconds
    """
    doc_ref = db.collection(_RATE_LIMITS_COLLECTION).document(_RATE_LIMITS_DOC)
    now = time.time()

    # Daily limits use a counter approach (avoids huge timestamp arrays)
    if config.window_seconds >= 3600:
        return _check_daily_rate_limit(db, doc_ref, config, now)

    # Short windows use sliding-window timestamps
    window_start = now - config.window_seconds

    @_firestore_transactional
    def _check_and_update(transaction, doc_ref):
        snapshot = doc_ref.get(transaction=transaction)
        data = snapshot.to_dict() if snapshot.exists else {}

        # Get existing timestamps for this API, prune expired ones
        timestamps = data.get(config.firestore_key, [])
        active = [ts for ts in timestamps if ts > window_start]

        if len(active) >= config.max_requests:
            # Find earliest timestamp to calculate when a slot opens
            oldest = min(active)
            retry_after = round((oldest + config.window_seconds) - now, 1)
            return False, max(retry_after, 0.5)

        # Allow: append current timestamp
        active.append(now)
        transaction.set(doc_ref, {config.firestore_key: active}, merge=True)
        return True, 0.0

    try:
        return _check_and_update(db.transaction(), doc_ref)
    except Exception as e:
        # If Firestore transaction fails, allow the request through
        # (fail-open to avoid blocking users due to infra issues)
        print(f"⚠️ Rate limit check failed for {config.name}: {e}")
        return True, 0.0


def _check_daily_rate_limit(
    db, doc_ref, config: RateLimitConfig, now: float
) -> Tuple[bool, float]:
    """Counter-based daily rate limit — stores count + date string."""
    today = datetime.date.today().isoformat()  # e.g. "2026-05-31"
    count_key = f"{config.firestore_key}_count"
    date_key = f"{config.firestore_key}_date"

    @_firestore_transactional
    def _check_daily(transaction, doc_ref):
        snapshot = doc_ref.get(transaction=transaction)
        data = snapshot.to_dict() if snapshot.exists else {}

        stored_date = data.get(date_key, "")
        current_count = data.get(count_key, 0)

        # Reset counter if it's a new day
        if stored_date != today:
            current_count = 0

        if current_count >= config.max_requests:
            # Calculate seconds until midnight
            now_dt = datetime.datetime.now()
            midnight = datetime.datetime.combine(
                now_dt.date() + datetime.timedelta(days=1),
                datetime.time.min
            )
            retry_after = (midnight - now_dt).total_seconds()
            return False, round(retry_after, 0)

        # Allow: increment counter
        transaction.set(
            doc_ref,
            {count_key: current_count + 1, date_key: today},
            merge=True,
        )
        return True, 0.0

    try:
        return _check_daily(db.transaction(), doc_ref)
    except Exception as e:
        print(f"⚠️ Daily rate limit check failed for {config.name}: {e}")
        return True, 0.0



def check_user_cooldown(
    db, uid: str, cooldown_seconds: float = USER_COOLDOWN_SECONDS
) -> Tuple[bool, float]:
    """
    Check per-user cooldown and daily request cap.

    Returns:
        (allowed, retry_after)
    """
    doc_ref = (
        db.collection("users")
        .document(uid)
        .collection("_meta")
        .document("voice_cooldown")
    )
    now = time.time()
    today = datetime.date.today().isoformat()

    try:
        doc = doc_ref.get()
        data = doc.to_dict() if doc.exists else {}

        last_request = data.get("last_request_at", 0)
        elapsed = now - last_request
        if elapsed < cooldown_seconds:
            retry_after = round(cooldown_seconds - elapsed, 1)
            return False, max(retry_after, 0.1)

        # Daily cap — counter resets when the date changes
        daily_count = data.get("daily_count", 0) if data.get("daily_date") == today else 0
        if daily_count >= USER_DAILY_LIMIT:
            now_dt = datetime.datetime.now()
            midnight = datetime.datetime.combine(
                now_dt.date() + datetime.timedelta(days=1), datetime.time.min
            )
            return False, round((midnight - now_dt).total_seconds(), 0)

        doc_ref.set({
            "last_request_at": now,
            "daily_count": daily_count + 1,
            "daily_date": today,
        })
        return True, 0.0
    except Exception as e:
        print(f"⚠️ User cooldown check failed: {e}")
        return True, 0.0


def record_rate_limit_hit(db, config: RateLimitConfig):
    """
    Record that a 429 was received from an external API.
    This can be used for monitoring/alerting.
    """
    try:
        doc_ref = db.collection(_RATE_LIMITS_COLLECTION).document("rate_limit_events")
        now = datetime.datetime.now(datetime.timezone.utc)
        doc_ref.set(
            {
                f"last_429_{config.firestore_key}": now.isoformat(),
                f"count_429_{config.firestore_key}": _firestore_increment(1),
            },
            merge=True,
        )
    except Exception:
        pass  # Non-critical, don't block on monitoring failures


# ═══════════════════════════════════════════════════════════════
# FIRESTORE HELPERS
# ═══════════════════════════════════════════════════════════════

def _firestore_transactional(func):
    """Decorator to run a function inside a Firestore transaction."""
    from google.cloud.firestore_v1 import transactional
    return transactional(func)


def _firestore_increment(value):
    """Firestore increment sentinel."""
    from google.cloud.firestore_v1 import Increment
    return Increment(value)
