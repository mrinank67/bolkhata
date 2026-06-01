"""
BolKhata — Voice-powered inventory & ledger management for Indian Kirana shops.

This is the main entry point. All route logic is in the routes/ package.
"""

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

from auth import init_firebase
from routes.voice import router as voice_router
from routes.inventory import router as inventory_router
from routes.history import router as history_router
from routes.suppliers import router as suppliers_router
from routes.ledger import router as ledger_router

load_dotenv()

# Initialize Firebase & Firestore
db = init_firebase()

# Create FastAPI app
app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include all routers
app.include_router(voice_router)
app.include_router(inventory_router)
app.include_router(history_router)
app.include_router(suppliers_router)
app.include_router(ledger_router)


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
