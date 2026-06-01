"""
Customer ledger endpoints — all /ledger/* routes
"""

from fastapi import APIRouter, HTTPException, Header
from firebase_admin import firestore
from google.cloud.firestore_v1.base_query import FieldFilter

from auth import verify_token
from models import LedgerEntryRequest, LedgerEntryUpdate, WhatsAppReminderRequest

router = APIRouter()


@router.get("/ledger/customers")
async def get_ledger_customers(authorization: str = Header(None)):
    from main import db

    uid = verify_token(authorization)
    udhaar_ref = db.collection("users").document(uid).collection("udhaar")
    docs = udhaar_ref.order_by("timestamp", direction=firestore.Query.DESCENDING).stream()

    customers = {}
    total_due = 0

    for doc in docs:
        data = doc.to_dict()
        cname = data.get("customer_name", "unknown")
        cmod = data.get("customer_modifier", "")
        key = f"{cname}|{cmod}"

        amount = data.get("amount", 0)
        qty = data.get("quantity", 0)

        ts_obj = data.get("timestamp")
        try:
            ts = ts_obj.isoformat() if ts_obj else None
        except AttributeError:
            ts = None

        if key not in customers:
            customers[key] = {
                "customer_name": cname,
                "customer_modifier": cmod,
                "total_due": 0,
                "whatsapp_number": data.get("whatsapp_number", ""),
                "reminder_schedule": data.get("reminder_schedule", ""),
                "reminder_sent": data.get("reminder_sent", False),
                "due_note": data.get("due_note", ""),
                "last_entry": ts,
                "items": [],
            }

        entry_data = {
            "id": doc.id,
            "item": data.get("item", ""),
            "quantity": qty,
            "unit": data.get("unit", ""),
            "amount": amount,
            "timestamp": ts,
        }
        customers[key]["items"].append(entry_data)
        customers[key]["total_due"] += amount

        # Update whatsapp/reminder if this entry has it
        if data.get("whatsapp_number"):
            customers[key]["whatsapp_number"] = data.get("whatsapp_number", "")
        if data.get("reminder_schedule"):
            customers[key]["reminder_schedule"] = data.get("reminder_schedule", "")

        total_due += amount

    customer_list = list(customers.values())

    return {
        "customers": customer_list,
        "total_due": total_due,
        "customer_count": len(customer_list),
    }


@router.post("/ledger/entry")
async def add_ledger_entry(req: LedgerEntryRequest, authorization: str = Header(None)):
    from main import db

    uid = verify_token(authorization)

    if not req.customer_name.strip() or not req.item.strip():
        raise HTTPException(status_code=400, detail="Customer name and item are required.")

    udhaar_ref = db.collection("users").document(uid).collection("udhaar")
    entry_data = {
        "customer_name": req.customer_name.strip().lower(),
        "customer_modifier": req.customer_modifier.strip().lower() if req.customer_modifier else "",
        "item": req.item.strip().lower(),
        "quantity": req.quantity,
        "unit": req.unit or "",
        "amount": req.amount or 0,
        "whatsapp_number": req.whatsapp_number or "",
        "reminder_schedule": req.reminder_schedule or "",
        "reminder_sent": False,
        "due_note": req.due_note or "",
        "timestamp": firestore.SERVER_TIMESTAMP,
    }
    udhaar_ref.add(entry_data)

    return {
        "status": "success",
        "message": f"Added {req.item} x{req.quantity} to {req.customer_name}'s ledger.",
    }


@router.put("/ledger/entry/{entry_id}")
async def update_ledger_entry(entry_id: str, req: LedgerEntryUpdate, authorization: str = Header(None)):
    from main import db

    uid = verify_token(authorization)
    udhaar_ref = db.collection("users").document(uid).collection("udhaar")
    doc_ref = udhaar_ref.document(entry_id)
    doc = doc_ref.get()

    if not doc.exists:
        raise HTTPException(status_code=404, detail="Entry not found.")

    update_data = {}
    for field, value in req.dict(exclude_unset=True).items():
        if value is not None:
            update_data[field] = value

    if update_data:
        update_data["timestamp"] = firestore.SERVER_TIMESTAMP
        doc_ref.update(update_data)

    return {"status": "success", "message": "Entry updated."}


@router.post("/ledger/whatsapp-reminder")
async def schedule_whatsapp_reminder(req: WhatsAppReminderRequest, authorization: str = Header(None)):
    from main import db

    uid = verify_token(authorization)

    # Save reminder schedule to all entries for this customer
    udhaar_ref = db.collection("users").document(uid).collection("udhaar")
    docs = udhaar_ref.where(
        filter=FieldFilter("customer_name", "==", req.customer_name.lower())
    ).stream()

    updated = 0
    for doc in docs:
        data = doc.to_dict()
        if req.customer_modifier and data.get("customer_modifier", "").lower() != req.customer_modifier.lower():
            continue
        doc.reference.update({
            "whatsapp_number": req.whatsapp_number,
            "reminder_schedule": req.reminder_schedule,
            "reminder_sent": False,
        })
        updated += 1

    # Placeholder — no actual WhatsApp API integration
    return {
        "status": "success",
        "message": f"WhatsApp reminder scheduled for {req.customer_name}. (Placeholder — integration pending)",
        "updated_entries": updated,
    }
