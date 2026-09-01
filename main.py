"""
BolKhata — Voice-powered inventory & ledger management for Indian Kirana shops.

This is the main entry point. All route logic is in the routes/ package.
"""

import os

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from firebase_admin import firestore

from auth import acting_context, init_firebase
from routes.admin import router as admin_router
from routes.bills import router as bills_router
from routes.history import router as history_router
from routes.inventory import router as inventory_router
from routes.ledger import router as ledger_router
from routes.orders import router as orders_router
from routes.suppliers import router as suppliers_router
from routes.voice import router as voice_router

load_dotenv()

# Initialize Firebase & Firestore
db = init_firebase()

# Create FastAPI app
app = FastAPI()

# CORS: the deployed frontend is same-origin (no CORS needed), so only local
# dev origins are allowed by default. Auth uses Bearer headers (not cookies),
# so allow_credentials is unnecessary. Extra origins (e.g. a custom domain
# serving the frontend separately) go in ALLOWED_ORIGINS, comma-separated.
_extra_origins = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_extra_origins,
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include all routers
app.include_router(voice_router)
app.include_router(inventory_router)
app.include_router(history_router)
app.include_router(suppliers_router)
app.include_router(ledger_router)
app.include_router(orders_router)
app.include_router(bills_router)
app.include_router(admin_router)


@app.middleware("http")
async def audit_admin_impersonation(request: Request, call_next):
    """Record every request an admin made while acting as another shop.

    Here rather than inside resolve_uid() because the interesting facts — which
    endpoint, and whether it actually succeeded — only exist once the response
    does. resolve_uid() leaves the two uids in a ContextVar on its way past.

    Reads are audited as well as writes: opening a shopkeeper's ledger is
    precisely the access that should leave a trace.
    """
    # A fresh slot per request, which resolve_uid() fills in if — and only if —
    # an admin actually acted as someone. See the note on acting_context: it has
    # to be a mutable object, because context does not propagate back up out of
    # the child task that runs the endpoint.
    identity: dict = {}
    acting_context.set(identity)

    response = await call_next(request)

    if not identity:
        # No acting header, or the caller was refused before resolve_uid()
        # reached the point of filling it in.
        return response

    try:
        db.collection("admin_audit").add(
            {
                "admin_uid": identity["admin_uid"],
                "acting_uid": identity["acting_uid"],
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "at": firestore.SERVER_TIMESTAMP,
            }
        )
    except Exception as e:
        # Fail-open, like the rate limiter: an audit write must never block
        # someone repairing a live shop. It is loud in the function logs instead.
        print(
            f"⚠️ admin audit write failed "
            f"({identity['admin_uid']} -> {identity['acting_uid']}): {e!s}"
        )

    return response


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
