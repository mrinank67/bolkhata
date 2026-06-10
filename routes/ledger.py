"""
Customer ledger endpoints — all /ledger/* routes
"""

import os
from fastapi import APIRouter, HTTPException, Header, Query
from fastapi.responses import HTMLResponse
from firebase_admin import firestore
from google.cloud.firestore_v1.base_query import FieldFilter
from itsdangerous import URLSafeTimedSerializer, SignatureExpired, BadSignature

from auth import verify_token
from models import LedgerEntryRequest, LedgerEntryUpdate, WhatsAppReminderRequest, UserSettingsRequest, PayLinkRequest

# Lazy-initialized: this module is imported before main.py runs load_dotenv(),
# and a hardcoded fallback secret would let anyone forge payment links.
_pay_serializer = None


def _get_pay_serializer() -> URLSafeTimedSerializer:
    global _pay_serializer
    if _pay_serializer is None:
        secret = os.getenv("PAY_LINK_SECRET")
        if not secret:
            raise HTTPException(
                status_code=503,
                detail="Payment links are not configured (PAY_LINK_SECRET is missing).",
            )
        _pay_serializer = URLSafeTimedSerializer(secret)
    return _pay_serializer


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


PAY_LINK_MAX_AGE = 24 * 60 * 60  # 24 hours


@router.post("/pay/create")
async def create_pay_link(req: PayLinkRequest, authorization: str = Header(None)):
    from main import db

    uid = verify_token(authorization)

    # The payee UPI ID comes from the caller's saved settings, never from the
    # request body — otherwise any account could mint official-looking
    # BolKhata payment pages pointing at an arbitrary UPI ID.
    user_doc = db.collection("users").document(uid).get()
    upi_id = (user_doc.to_dict() or {}).get("upi_id", "").strip() if user_doc.exists else ""
    if not upi_id:
        raise HTTPException(status_code=400, detail="Set your UPI ID in Account Settings first.")

    # pn (payee display name) is also fixed server-side so a crafted request
    # can't impersonate another brand on the payment page
    token = _get_pay_serializer().dumps({"pa": upi_id, "pn": "BolKhata", "am": req.am, "tn": req.tn})
    return {"token": token}


@router.get("/pay", response_class=HTMLResponse)
async def pay_page(token: str = Query(..., description="Signed payment token")):
    from html import escape
    from urllib.parse import quote

    try:
        data = _get_pay_serializer().loads(token, max_age=PAY_LINK_MAX_AGE)
    except SignatureExpired:
        return _pay_error_page("This payment link has expired. Please ask the sender for a new link.")
    except BadSignature:
        return _pay_error_page("This payment link is invalid.")
    except HTTPException:
        return _pay_error_page("Payment links are temporarily unavailable.")

    upi_id = escape(str(data["pa"]))
    payee = escape(str(data["pn"]))
    amount = escape(str(data["am"]))
    note = escape(str(data.get("tn", "")))
    upi_uri = escape(
        "upi://pay?pa={pa}&pn={pn}&am={am}&cu=INR&tn={tn}".format(
            pa=quote(str(data["pa"]), safe=""),
            pn=quote(str(data["pn"]), safe=""),
            am=quote(str(data["am"]), safe=""),
            tn=quote(str(data.get("tn", "")), safe=""),
        )
    )

    return f"""<!DOCTYPE html>
<html lang="hi">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Pay ₹{amount} — {payee}</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#0f0f13;color:#e8e8e8;min-height:100vh;display:flex;align-items:center;justify-content:center;padding:20px}}
.card{{background:#1a1a24;border-radius:16px;padding:32px 24px;max-width:380px;width:100%;text-align:center;box-shadow:0 8px 32px rgba(0,0,0,0.4)}}
.logo{{font-size:1.6rem;font-weight:800;margin-bottom:4px}}
.subtitle{{font-size:0.8rem;color:#888;margin-bottom:24px}}
.amount{{font-size:2.8rem;font-weight:800;color:#4fc3f7;margin:16px 0 8px}}
.to{{font-size:0.85rem;color:#aaa;margin-bottom:4px}}
.upi-id{{font-size:0.9rem;color:#ccc;font-family:monospace;margin-bottom:6px}}
.note{{font-size:0.8rem;color:#777;margin-bottom:28px;font-style:italic}}
.pay-btn{{display:block;width:100%;padding:16px;background:linear-gradient(135deg,#4fc3f7,#2196f3);color:#fff;font-size:1.1rem;font-weight:700;border:none;border-radius:12px;cursor:pointer;text-decoration:none;transition:box-shadow 0.2s}}
.pay-btn:hover{{box-shadow:0 6px 20px rgba(33,150,243,0.4)}}
.footer{{margin-top:20px;font-size:0.7rem;color:#555}}
</style>
</head>
<body>
<div class="card">
  <div class="logo">BolKhata</div>
  <div class="subtitle">Payment Request</div>
  <div class="amount">₹{amount}</div>
  <div class="to">Pay to</div>
  <div class="upi-id">{upi_id}</div>
  <div class="note">{note if note else ''}</div>
  <a class="pay-btn" href="{upi_uri}">Pay Now</a>
  <div class="footer">Powered by BolKhata</div>
</div>
</body>
</html>"""


def _pay_error_page(message: str) -> str:
    from html import escape
    msg = escape(message)
    return f"""<!DOCTYPE html>
<html lang="hi">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Link Expired — BolKhata</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#0f0f13;color:#e8e8e8;min-height:100vh;display:flex;align-items:center;justify-content:center;padding:20px}}
.card{{background:#1a1a24;border-radius:16px;padding:32px 24px;max-width:380px;width:100%;text-align:center;box-shadow:0 8px 32px rgba(0,0,0,0.4)}}
.logo{{font-size:1.6rem;font-weight:800;margin-bottom:4px}}
.msg{{font-size:1rem;color:#ef5350;margin:24px 0}}
.footer{{margin-top:20px;font-size:0.7rem;color:#555}}
</style>
</head>
<body>
<div class="card">
  <div class="logo">BolKhata</div>
  <div class="msg">{msg}</div>
  <div class="footer">Powered by BolKhata</div>
</div>
</body>
</html>"""


@router.get("/settings")
async def get_settings(authorization: str = Header(None)):
    from main import db

    uid = verify_token(authorization)
    doc = db.collection("users").document(uid).get()
    data = doc.to_dict() if doc.exists else {}
    return {"upi_id": data.get("upi_id", "")}


@router.put("/settings")
async def update_settings(req: UserSettingsRequest, authorization: str = Header(None)):
    import re
    from main import db

    uid = verify_token(authorization)
    upi_id = (req.upi_id or "").strip()
    # VPA format: handle@psp (e.g. 98765@ybl, shopname@okhdfcbank)
    if upi_id and not re.fullmatch(r"[A-Za-z0-9._\-]{2,256}@[A-Za-z]{2,64}", upi_id):
        raise HTTPException(status_code=400, detail="Invalid UPI ID. Expected format: name@bank")
    db.collection("users").document(uid).set(
        {"upi_id": upi_id}, merge=True
    )
    return {"status": "success"}
