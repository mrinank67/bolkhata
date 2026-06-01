"""
Firebase initialization and authentication helpers.
"""

import os
import json
import time

import firebase_admin
from firebase_admin import credentials, firestore, auth
from fastapi import HTTPException


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
            cred_path = "bolkhata-prod-firebase-adminsdk-fbsvc-842a3ee7ed.json"
            if not os.path.exists(cred_path):
                raise Exception(
                    "Firebase Credentials not found! Add FIREBASE_SERVICE_ACCOUNT env var or the JSON file."
                )
            cred = credentials.Certificate(cred_path)

        firebase_admin.initialize_app(cred)
    return firestore.client()


def verify_token(authorization: str) -> str:
    """Verify a Firebase ID token and return the user's UID."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Unauthorized")
    token = authorization.split("Bearer ")[1]
    try:
        t0 = time.time()
        decoded = auth.verify_id_token(token)
        print(f"⏱️ Token Verify: {time.time() - t0:.2f}s")
        return decoded["uid"]
    except Exception as e:
        if isinstance(e, HTTPException):
            raise e
        raise HTTPException(status_code=401, detail="Invalid Authentication Token")
