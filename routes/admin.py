"""Support-panel endpoints — all /admin/* routes.

Deliberately small. Everything a shopkeeper can do to their own data — orders,
ledger, suppliers, inventory, bills, settings, history — is reached by calling
the *ordinary* endpoint with an X-Acting-Uid header (see auth.resolve_uid), so
none of that write logic is duplicated here and none of its invariants can drift
out of step. What lives in this file is only what the ordinary routes cannot
express: finding a shop in the first place, reading the voice diagnostics, and
reading the audit trail.

Every handler is gated by require_admin(), which demands the `admin` custom
claim. Uids arrive as path parameters here rather than as the acting header,
because these routes are *about* a shop rather than acting as one.
"""

from fastapi import APIRouter, Header, HTTPException, Query
from firebase_admin import auth as fb_auth
from firebase_admin import firestore
from google.cloud.firestore_v1.base_query import FieldFilter

from auth import require_admin

router = APIRouter()

# A support session looks at one shop at a time; these caps keep a stray request
# from streaming an entire collection into a serverless function's memory.
MAX_USER_RESULTS = 25
MAX_VOICE_LOGS = 100
MAX_AUDIT_ROWS = 200


def _iso(ts):
    """Firestore timestamps render as ISO strings; anything odd renders as None."""
    try:
        return ts.isoformat() if ts else None
    except AttributeError:
        return None


def _user_summary(user) -> dict:
    """The Firebase Auth side of a shop — how support recognises the caller."""
    return {
        "uid": user.uid,
        "email": user.email or "",
        "phone_number": user.phone_number or "",
        "display_name": user.display_name or "",
        "disabled": bool(user.disabled),
        "is_admin": bool((user.custom_claims or {}).get("admin") is True),
        "created_at": getattr(user.user_metadata, "creation_timestamp", None),
        "last_sign_in": getattr(user.user_metadata, "last_sign_in_timestamp", None),
    }


@router.get("/admin/me")
async def admin_me(authorization: str = Header(None)):
    """Whether the caller may use the panel.

    The browser can read the claim off its own ID token, but the panel asks the
    server anyway: the claim is only meaningful if the server agrees with it,
    and a panel that trusted the client's copy would render a full support UI
    whose every request then 403s.
    """
    decoded = require_admin(authorization)
    return {
        "uid": decoded["uid"],
        "email": decoded.get("email", ""),
        "is_admin": True,
    }


@router.get("/admin/users")
async def admin_find_users(
    q: str = Query("", max_length=200),
    authorization: str = Header(None),
):
    """Find a shop by phone, email or uid — or list recent signups when q is empty.

    Lookup goes through Firebase Auth rather than Firestore because a shopkeeper
    on the phone identifies themselves by the number they signed up with, and
    users/{uid} holds no contact details until they fill in shop settings.
    """
    require_admin(authorization)

    query = (q or "").strip()
    if not query:
        page = fb_auth.list_users(max_results=MAX_USER_RESULTS)
        return {"users": [_user_summary(u) for u in page.users], "query": ""}

    # Try each interpretation of the string; a phone number typed without the
    # country code is the common support case, so +91 is attempted as well.
    candidates = []
    if "@" in query:
        candidates.append(lambda: fb_auth.get_user_by_email(query))
    if query.startswith("+") or query.isdigit():
        digits = query.lstrip("+")
        candidates.append(lambda: fb_auth.get_user_by_phone_number("+" + digits))
        if len(digits) == 10:
            candidates.append(lambda: fb_auth.get_user_by_phone_number("+91" + digits))
    candidates.append(lambda: fb_auth.get_user(query))

    for lookup in candidates:
        try:
            return {"users": [_user_summary(lookup())], "query": query}
        except Exception:
            continue

    return {"users": [], "query": query}


@router.get("/admin/users/{uid}/overview")
async def admin_user_overview(uid: str, authorization: str = Header(None)):
    """One call for the panel header: who this is, and how much of everything they have.

    Counts are read with a projection of a single field so a shop with thousands
    of order lines does not pull every document body across just to be counted.
    """
    from main import db

    require_admin(authorization)

    try:
        account = _user_summary(fb_auth.get_user(uid))
    except Exception:
        raise HTTPException(status_code=404, detail="No such user.")

    user_ref = db.collection("users").document(uid)
    snap = user_ref.get()
    settings = snap.to_dict() if snap.exists else {}
    settings = settings or {}

    counts = {}
    for name in (
        "stock",
        "orders",
        "udhaar",
        "suppliers",
        "suppliers_purchases",
        "bills",
        "history",
        "voice_logs",
    ):
        try:
            counts[name] = sum(1 for _ in user_ref.collection(name).select([]).stream())
        except Exception as e:
            # A missing index or a transient read shouldn't blank the whole
            # header — show the rest and mark this one unknown.
            print(f"⚠️ admin overview count failed for {name}: {e!s}")
            counts[name] = None

    voice_meta = {}
    try:
        meta = user_ref.collection("_meta").document("voice_cooldown").get()
        if meta.exists:
            data = meta.to_dict() or {}
            voice_meta = {
                "daily_count": data.get("daily_count", 0),
                "daily_date": data.get("daily_date", ""),
                "last_request_at": _iso(data.get("last_request_at")),
            }
    except Exception as e:
        print(f"⚠️ admin overview voice meta failed: {e!s}")

    return {
        "account": account,
        "settings": {
            "shop_name": settings.get("shop_name", ""),
            "shop_mobile": settings.get("shop_mobile", ""),
            "shop_address": settings.get("shop_address", ""),
            "upi_id": settings.get("upi_id", ""),
            "order_seq": settings.get("order_seq", 0),
        },
        "counts": counts,
        "voice_usage": voice_meta,
    }


@router.get("/admin/users/{uid}/voice-logs")
async def admin_voice_logs(
    uid: str,
    status: str = Query("", max_length=40),
    limit: int = Query(50, ge=1, le=MAX_VOICE_LOGS),
    authorization: str = Header(None),
):
    """What the pipeline heard, understood and did, newest first.

    The status filter is applied in Python rather than as a Firestore where():
    combining an equality filter with the timestamp ordering needs a composite
    index, and this collection is small and short-lived enough that it is not
    worth one.
    """
    from main import db

    require_admin(authorization)

    logs_ref = db.collection("users").document(uid).collection("voice_logs")
    wanted = (status or "").strip()
    # Over-fetch when filtering so a page isn't mostly empty after the filter.
    fetch = min(MAX_VOICE_LOGS, limit * 4) if wanted else limit

    try:
        docs = (
            logs_ref.order_by("timestamp", direction=firestore.Query.DESCENDING)
            .limit(fetch)
            .stream()
        )
    except Exception as e:
        print(f"⚠️ admin voice log read failed: {e!s}")
        raise HTTPException(status_code=500, detail="Could not read voice logs.")

    entries = []
    for doc in docs:
        data = doc.to_dict() or {}
        if wanted and data.get("status") != wanted:
            continue
        entries.append(
            {
                "id": doc.id,
                "status": data.get("status", ""),
                "transcript": data.get("transcript", ""),
                "intent": data.get("intent", ""),
                "results": data.get("results", []),
                "errors": data.get("errors", []),
                "error_detail": data.get("error_detail", ""),
                "transaction_count": data.get("transaction_count"),
                "stt_ms": data.get("stt_ms"),
                "llm_ms": data.get("llm_ms"),
                "db_ms": data.get("db_ms"),
                "total_ms": data.get("total_ms"),
                "stt_model": data.get("stt_model", ""),
                "llm_model": data.get("llm_model", ""),
                "audio_size": data.get("audio_size"),
                "audio_mime": data.get("audio_mime", ""),
                "recent_customer": data.get("recent_customer", ""),
                "recent_modifier": data.get("recent_modifier", ""),
                "recent_order_id": data.get("recent_order_id", ""),
                "selected_modifier": data.get("selected_modifier", ""),
                "timestamp": _iso(data.get("timestamp")),
                "expires_at": _iso(data.get("expires_at")),
            }
        )
        if len(entries) >= limit:
            break

    return {"voice_logs": entries, "status_filter": wanted}


@router.get("/admin/users/{uid}/bills")
async def admin_bills(uid: str, authorization: str = Header(None)):
    """Bill metadata for a shop.

    There is no user-facing GET /bills — the app reaches a bill through its
    order. Support needs the other direction: which bills exist, which have gone
    stale, and which are about to be swept by the 30-day retention rule.
    """
    from main import db

    require_admin(authorization)

    bills_ref = db.collection("users").document(uid).collection("bills")
    bills = []
    for doc in bills_ref.stream():
        data = doc.to_dict() or {}
        bills.append(
            {
                # The doc id is the order_id — the link back to the order.
                "order_id": doc.id,
                "stale": bool(data.get("stale", False)),
                "storage_path": data.get("storage_path", ""),
                "generated_at": _iso(data.get("generated_at")),
                "expires_at": _iso(data.get("expires_at")),
                # download_token is deliberately omitted: pairing it with
                # storage_path reconstructs a public, never-expiring URL to the
                # customer's bill, and docs/security.md forbids logging those.
            }
        )

    bills.sort(key=lambda b: b["generated_at"] or "", reverse=True)
    return {"bills": bills}


@router.get("/admin/audit")
async def admin_audit(
    acting_uid: str = Query("", max_length=128),
    limit: int = Query(100, ge=1, le=MAX_AUDIT_ROWS),
    authorization: str = Header(None),
):
    """Who acted as which shop, and what they did.

    Written by the middleware in main.py for every request that carried an
    acting header — reads included, because reading a shopkeeper's ledger is
    exactly the access that should leave a trace.
    """
    from main import db

    require_admin(authorization)

    query = db.collection("admin_audit")
    target = (acting_uid or "").strip()
    if target:
        query = query.where(filter=FieldFilter("acting_uid", "==", target))

    try:
        docs = query.order_by("at", direction=firestore.Query.DESCENDING).limit(limit).stream()
        rows = list(docs)
    except Exception as e:
        # Filtering plus ordering needs a composite index that may not exist
        # yet; fall back to ordering only, then filter in Python, so the trail
        # is still readable rather than 500-ing.
        print(f"⚠️ admin audit query fell back to unfiltered read: {e!s}")
        docs = (
            db.collection("admin_audit")
            .order_by("at", direction=firestore.Query.DESCENDING)
            .limit(MAX_AUDIT_ROWS)
            .stream()
        )
        rows = [d for d in docs if not target or (d.to_dict() or {}).get("acting_uid") == target]
        rows = rows[:limit]

    entries = []
    for doc in rows:
        data = doc.to_dict() or {}
        entries.append(
            {
                "id": doc.id,
                "admin_uid": data.get("admin_uid", ""),
                "acting_uid": data.get("acting_uid", ""),
                "method": data.get("method", ""),
                "path": data.get("path", ""),
                "status_code": data.get("status_code"),
                "at": _iso(data.get("at")),
            }
        )

    return {"audit": entries}
