"""
Supplier endpoints — all /suppliers/* routes
"""

import datetime

from fastapi import APIRouter, HTTPException, Header
from firebase_admin import firestore
from google.cloud.firestore_v1.base_query import FieldFilter

from auth import verify_token
from db_operations import _normalize_supplier_name
from models import PurchaseRequest, SupplierCreateRequest

router = APIRouter()


@router.get("/suppliers")
async def get_suppliers(authorization: str = Header(None)):
    from main import db

    uid = verify_token(authorization)
    purchases_ref = db.collection("users").document(uid).collection("suppliers_purchases")
    docs = purchases_ref.order_by("timestamp", direction=firestore.Query.DESCENDING).stream()

    # Directory names are authoritative for how a supplier is displayed. Voice
    # and manual entry store names with different case/suffixes, so group and
    # display everything by a normalized key (lowercase, suffix stripped) — this
    # is what keeps "Ramesh Traders" (manual) and "ramesh" (voice) as one card.
    dir_ref = db.collection("users").document(uid).collection("suppliers")
    dir_display = {}
    for d in dir_ref.stream():
        nm = (d.to_dict().get("name") or "").strip()
        if nm:
            dir_display.setdefault(_normalize_supplier_name(nm), nm)

    purchases = []
    now = datetime.datetime.now(datetime.timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    month_total = 0
    month_items = 0
    # key -> canonical display name (directory wins; otherwise the longest variant seen)
    canonical = dict(dir_display)

    for doc in docs:
        data = doc.to_dict()
        ts_obj = data.get("timestamp")
        try:
            ts = ts_obj.timestamp() * 1000 if ts_obj else 0
        except AttributeError:
            ts = 0

        amount = data.get("amount", 0)
        raw_name = (data.get("supplier_name") or "").strip()
        key = _normalize_supplier_name(raw_name)
        entry = {
            "id": doc.id,
            "supplier_name": raw_name,
            "supplier_key": key,
            "item_name": data.get("item_name", ""),
            "quantity": data.get("quantity", 0),
            "amount": amount,
            "proof_image_url": data.get("proof_image_url", ""),
            "timestamp": ts,
        }
        purchases.append(entry)

        if key not in dir_display:
            cur = canonical.get(key)
            if cur is None or len(raw_name) > len(cur):
                canonical[key] = raw_name

        # Monthly stats
        if ts_obj and ts_obj >= month_start:
            month_total += amount
            month_items += 1

    # Resolve each purchase to its canonical display name and aggregate totals on
    # the normalized key so case/suffix variants collapse into one supplier.
    supplier_totals = {}
    for entry in purchases:
        key = entry["supplier_key"]
        disp = canonical.get(key) or entry["supplier_name"]
        entry["supplier_name"] = disp
        bucket = supplier_totals.setdefault(key, {"name": disp, "total": 0, "items": []})
        bucket["total"] += entry["amount"]
        bucket["items"].append(entry["item_name"])

    return {
        "purchases": purchases,
        "month_total": month_total,
        "month_items": month_items,
        "supplier_totals": supplier_totals,
    }


@router.post("/suppliers/purchase")
async def add_supplier_purchase(req: PurchaseRequest, authorization: str = Header(None)):
    from main import db

    uid = verify_token(authorization)

    if not req.supplier_name.strip() or not req.item_name.strip():
        raise HTTPException(status_code=400, detail="Supplier name and item name are required.")
    if req.quantity <= 0:
        raise HTTPException(status_code=400, detail="Quantity must be greater than 0.")

    purchases_ref = db.collection("users").document(uid).collection("suppliers_purchases")
    purchase_data = {
        "supplier_name": req.supplier_name.strip(),
        "item_name": req.item_name.strip().lower(),
        "quantity": req.quantity,
        "amount": req.amount,
        "proof_image_url": req.proof_image_url or "",
        "timestamp": firestore.SERVER_TIMESTAMP,
    }
    purchases_ref.add(purchase_data)

    # Auto-update stock inventory
    stock_ref = db.collection("users").document(uid).collection("stock")
    item_key = req.item_name.strip().lower()
    stock_doc_ref = stock_ref.document(item_key)
    stock_doc = stock_doc_ref.get()

    if stock_doc.exists:
        current_qty = stock_doc.to_dict().get("quantity", 0)
        stock_doc_ref.update({
            "quantity": current_qty + req.quantity,
            "updated_at": firestore.SERVER_TIMESTAMP,
        })
    else:
        stock_doc_ref.set({
            "item": item_key,
            "quantity": req.quantity,
            "created_at": firestore.SERVER_TIMESTAMP,
            "updated_at": firestore.SERVER_TIMESTAMP,
        })

    return {
        "status": "success",
        "message": f"Added {req.quantity}x {req.item_name} from {req.supplier_name}. Stock updated.",
    }


@router.get("/suppliers/list")
async def list_saved_suppliers(authorization: str = Header(None)):
    """List all saved suppliers in the user's directory."""
    from main import db

    uid = verify_token(authorization)
    suppliers_ref = db.collection("users").document(uid).collection("suppliers")
    docs = suppliers_ref.order_by("created_at", direction=firestore.Query.DESCENDING).stream()

    suppliers = []
    for doc in docs:
        data = doc.to_dict()
        ts_obj = data.get("created_at")
        try:
            ts = ts_obj.timestamp() * 1000 if ts_obj else 0
        except AttributeError:
            ts = 0
        suppliers.append({
            "id": doc.id,
            "name": data.get("name", ""),
            "mobile": data.get("mobile", ""),
            "gst_number": data.get("gst_number", ""),
            "created_at": ts,
        })
    return {"suppliers": suppliers}


@router.post("/suppliers/add")
async def add_supplier(req: SupplierCreateRequest, authorization: str = Header(None)):
    """Add a new supplier to the user's directory."""
    from main import db

    uid = verify_token(authorization)

    if not req.name.strip():
        raise HTTPException(status_code=400, detail="Supplier name is required.")

    suppliers_ref = db.collection("users").document(uid).collection("suppliers")

    # Check for duplicate name
    existing = suppliers_ref.where(
        filter=FieldFilter("name_lower", "==", req.name.strip().lower())
    ).limit(1).stream()
    if any(True for _ in existing):
        raise HTTPException(status_code=400, detail=f"Supplier '{req.name.strip()}' already exists.")

    supplier_data = {
        "name": req.name.strip(),
        "name_lower": req.name.strip().lower(),
        "mobile": req.mobile.strip() if req.mobile else "",
        "gst_number": req.gst_number.strip() if req.gst_number else "",
        "created_at": firestore.SERVER_TIMESTAMP,
    }
    doc_ref = suppliers_ref.add(supplier_data)

    return {
        "status": "success",
        "message": f"Supplier '{req.name.strip()}' added.",
        "id": doc_ref[1].id,
    }


@router.put("/suppliers/{supplier_id}")
async def update_supplier(supplier_id: str, req: SupplierCreateRequest, authorization: str = Header(None)):
    """Edit a saved supplier's name, mobile, and GST number."""
    from main import db

    uid = verify_token(authorization)

    if not req.name.strip():
        raise HTTPException(status_code=400, detail="Supplier name is required.")

    suppliers_ref = db.collection("users").document(uid).collection("suppliers")
    doc_ref = suppliers_ref.document(supplier_id)
    if not doc_ref.get().exists:
        raise HTTPException(status_code=404, detail="Supplier not found.")

    new_name = req.name.strip()
    new_name_lower = new_name.lower()

    # Reject a rename that collides with a *different* existing supplier
    existing = suppliers_ref.where(
        filter=FieldFilter("name_lower", "==", new_name_lower)
    ).limit(2).stream()
    if any(e.id != supplier_id for e in existing):
        raise HTTPException(status_code=400, detail=f"Supplier '{new_name}' already exists.")

    doc_ref.update({
        "name": new_name,
        "name_lower": new_name_lower,
        "mobile": req.mobile.strip() if req.mobile else "",
        "gst_number": req.gst_number.strip() if req.gst_number else "",
        "updated_at": firestore.SERVER_TIMESTAMP,
    })

    return {
        "status": "success",
        "message": f"Supplier '{new_name}' updated.",
    }


@router.delete("/suppliers/{supplier_id}")
async def delete_supplier(supplier_id: str, authorization: str = Header(None)):
    """Delete a supplier from the user's directory."""
    from main import db

    uid = verify_token(authorization)
    doc_ref = db.collection("users").document(uid).collection("suppliers").document(supplier_id)
    doc = doc_ref.get()

    if not doc.exists:
        raise HTTPException(status_code=404, detail="Supplier not found.")

    supplier_name = doc.to_dict().get("name", "")
    doc_ref.delete()

    # Also delete all purchases from this supplier. Voice and manual entry store
    # the name with different case/suffixes, so match on the normalized key — an
    # exact string match would leave voice-recorded purchases orphaned.
    key = _normalize_supplier_name(supplier_name)
    purchases_ref = db.collection("users").document(uid).collection("suppliers_purchases")
    deleted_count = 0
    for pdoc in purchases_ref.stream():
        pname = (pdoc.to_dict().get("supplier_name") or "").strip()
        if pname and _normalize_supplier_name(pname) == key:
            pdoc.reference.delete()
            deleted_count += 1

    return {
        "status": "success",
        "message": f"Supplier '{supplier_name}' and {deleted_count} purchase(s) deleted.",
    }
