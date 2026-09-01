"""
Firebase initialization and authentication helpers.
"""

import contextvars
import json
import os
import time

import firebase_admin
from fastapi import HTTPException
from firebase_admin import auth, credentials, firestore, storage

# Carries "an admin acted as this shop" from resolve_uid() up to the audit
# middleware in main.py, which only learns the path and status once the response
# exists.
#
# A ContextVar because a serverless worker handles requests concurrently and a
# module global would attribute one admin's action to another. It holds a
# *mutable dict*, filled in by resolve_uid() and read back by the middleware,
# rather than the identity itself: Starlette runs the endpoint in a child task,
# so a ContextVar.set() inside the handler is invisible to the middleware that
# called it. The dict object is shared across that boundary, so mutating it is.
acting_context: contextvars.ContextVar[dict | None] = contextvars.ContextVar(
    "acting_context", default=None
)


def init_firebase():
    """Initialize Firebase Admin SDK and return a Firestore client."""
    if not firebase_admin._apps:
        # Check for env variable first (used in Vercel)
        firebase_json_env = os.getenv("FIREBASE_SERVICE_ACCOUNT")
        if firebase_json_env:
            cred_dict = json.loads(firebase_json_env)
            cred = credentials.Certificate(cred_dict)
        else:
            # Fallback to local JSON (for local development)
            cred_path = "bolkhata-app-firebase-adminsdk-fbsvc-c2ffa955ec.json"
            if not os.path.exists(cred_path):
                raise Exception(
                    "Firebase Credentials not found! Add FIREBASE_SERVICE_ACCOUNT env var or the JSON file."
                )
            cred = credentials.Certificate(cred_path)

        # storageBucket lets storage.bucket() resolve the default bucket for bill PDFs.
        firebase_admin.initialize_app(cred, {"storageBucket": os.getenv("FIREBASE_STORAGE_BUCKET")})
    return firestore.client()


def get_bucket():
    """Return the default Firebase Storage bucket (Admin SDK; bypasses Storage rules)."""
    return storage.bucket()


def verify_token_full(authorization: str) -> dict:
    """Verify a Firebase ID token and return the whole decoded token.

    verify_token() is the uid-only form every route handler uses. This exists
    because the admin seam needs a second claim off the same token, and
    verifying twice would double the work on every admin request.
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Unauthorized")
    token = authorization.split("Bearer ")[1]
    try:
        t0 = time.time()
        decoded = auth.verify_id_token(token)
        print(f"⏱️ Token Verify: {time.time() - t0:.2f}s")
        return decoded
    except Exception as e:
        if isinstance(e, HTTPException):
            raise e
        raise HTTPException(status_code=401, detail="Invalid Authentication Token")


def verify_token(authorization: str) -> str:
    """Verify a Firebase ID token and return the user's UID."""
    return verify_token_full(authorization)["uid"]


def is_admin(decoded: dict) -> bool:
    """True when the token carries the admin custom claim.

    Identity comparison against True, not truthiness: the claim is set by
    scripts/grant_admin.py as a real boolean, and a stray "false" string would
    otherwise grant support access to every shop in the country.
    """
    return decoded.get("admin") is True


def require_admin(authorization: str) -> dict:
    """The decoded token of an admin caller, or 403. Guards the /admin/* routes."""
    decoded = verify_token_full(authorization)
    if not is_admin(decoded):
        raise HTTPException(status_code=403, detail="Forbidden")
    return decoded


def resolve_uid(authorization: str, acting_uid: str | None = None) -> str:
    """The shop this request operates on.

    Without an X-Acting-Uid header this is exactly verify_token(): the caller's
    own uid, which is what every ordinary shopkeeper request gets. With one, the
    caller must carry the admin claim — a non-admin who sends the header is
    refused rather than quietly falling back to their own uid, because a silent
    fallback would let a support bug look like a successful edit.
    """
    acting_uid = (acting_uid or "").strip()
    if not acting_uid:
        return verify_token(authorization)

    decoded = verify_token_full(authorization)
    if not is_admin(decoded):
        raise HTTPException(status_code=403, detail="Forbidden")

    # Stashed for the audit middleware, which only learns the method, path and
    # status code once the response exists. Absent outside a request (a direct
    # unit-test call), in which case there is nothing to audit.
    slot = acting_context.get()
    if slot is not None:
        slot["admin_uid"] = decoded["uid"]
        slot["acting_uid"] = acting_uid

    return acting_uid
