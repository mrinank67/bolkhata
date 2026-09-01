"""
History endpoints — GET/DELETE /history
"""

from fastapi import APIRouter, Header
from firebase_admin import firestore

from auth import resolve_uid

router = APIRouter()


@router.get("/history")
async def get_history(
    authorization: str = Header(None),
    x_acting_uid: str = Header(None),
):
    from main import db

    uid = resolve_uid(authorization, x_acting_uid)
    history_ref = db.collection("users").document(uid).collection("history")
    docs = (
        history_ref.order_by("timestamp", direction=firestore.Query.DESCENDING).limit(50).stream()
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


@router.delete("/history")
async def clear_history(
    authorization: str = Header(None),
    x_acting_uid: str = Header(None),
):
    """Clear the user-facing history screen.

    This deliberately does not touch users/{uid}/voice_logs: those are
    TTL-bounded support diagnostics, and a shopkeeper tidying their history
    should not destroy the record needed to diagnose the problem they are about
    to call about. See docs/security.md.
    """
    from main import db

    uid = resolve_uid(authorization, x_acting_uid)
    history_ref = db.collection("users").document(uid).collection("history")
    docs = history_ref.stream()
    for doc in docs:
        doc.reference.delete()
    return {"status": "cleared"}
