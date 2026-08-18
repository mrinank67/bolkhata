"""
Inventory endpoints — GET/POST/PUT/DELETE /inventory, POST /confirm_clear_inventory
"""

import re
from typing import Optional
from urllib.parse import quote
from uuid import uuid4

from fastapi import APIRouter, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from firebase_admin import firestore

from auth import get_bucket, verify_token
from image_utils import ImageRejected, process_item_image
from models import MAX_AMOUNT, MAX_QTY, InventoryItemUpdate
from rate_limiter import (
    IMAGE_UPLOAD_RPD,
    IMAGE_UPLOAD_RPM,
    IMAGE_USER_LIMIT,
    check_global_rate_limit,
    check_user_limit,
)

router = APIRouter()

# Our own policy cap, deliberately not inherited from a hosting platform, so it
# survives the move off Vercel unchanged. Justified by heap bounds, mobile data
# cost, and per-request storage cost — all of which hold on any host.
MAX_UPLOAD_BYTES = 3 * 1024 * 1024

# A pack unit is a counting unit, not a conversion factor: a "dozen" item holds
# a quantity of dozens and a price per dozen, never 12 pieces at price/12.
ALLOWED_UNITS = {"", "pcs", "dozen", "box", "pack"}
MAX_CATEGORY_LEN = 50

# Fields carried across a rename. The doc id IS the item name, so renaming means
# delete + recreate; anything missing from this list is silently destroyed.
_CARRY_ON_RENAME = (
    "cost_price",
    "unit",
    "category",
    "image_id",
    "image_path",
    "image_thumb_path",
    "image_token",
)

_RESERVED_ID = re.compile(r"^__.*__$")
_ILLEGAL_ID_CHARS = re.compile(r"[/\x00-\x1f\x7f]")


def _normalize_item_id(raw: str) -> str:
    """Item name -> Firestore doc id (the doc id IS the lowercased name here).

    Firestore doc ids may not contain '/', may not be '.' or '..', and may not
    match __.*__. The create path is where user text first becomes a document
    path, so it is validated here rather than raising a 500 deep in the SDK.
    """
    name = (raw or "").strip().lower()
    if not name or len(name) > 100:
        raise HTTPException(status_code=400, detail="Item name must be 1–100 characters.")
    if _ILLEGAL_ID_CHARS.search(name) or name in (".", "..") or _RESERVED_ID.match(name):
        raise HTTPException(status_code=400, detail="Item name contains invalid characters.")
    return name


async def _read_capped(upload: UploadFile, cap: int) -> bytes:
    """Read an UploadFile in chunks, aborting past `cap`, so a hostile body never
    materializes whole in memory before we've decided to accept it."""
    chunks, total = [], 0
    while True:
        chunk = await upload.read(64 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > cap:
            raise HTTPException(
                status_code=413,
                detail=f"Photo too large. Please use one under {cap // (1024 * 1024)} MB.",
            )
        chunks.append(chunk)
    if total < 100:
        raise HTTPException(status_code=400, detail="Image is empty or corrupt.")
    return b"".join(chunks)


def _image_url(bucket_name: str, path: str, token: str) -> str:
    """Rebuild a Firebase download URL from path + token (same shape as bills)."""
    return (
        f"https://firebasestorage.googleapis.com/v0/b/{bucket_name}/o/{quote(path, safe='')}"
        f"?alt=media&token={token}"
    )


def _put_image(bucket, path: str, data: bytes, token: str) -> None:
    blob = bucket.blob(path)
    blob.metadata = {"firebaseStorageDownloadTokens": token}
    # The path is uuid-keyed and its bytes never change, so it is safe to cache
    # forever. This is the single biggest lever on Firebase egress cost.
    blob.cache_control = "public, max-age=31536000, immutable"
    # Content type and filename are fixed server-side and never echoed from the
    # client, so the object can't be served as markup and a saved file never
    # carries attacker-controlled text.
    blob.content_disposition = 'inline; filename="item.webp"'
    blob.upload_from_string(data, content_type="image/webp")


def _delete_item_images(data: dict) -> None:
    """Best-effort blob cleanup. Orphaned blobs are billed forever and are
    invisible in the app, so this must run on every path that destroys a doc."""
    paths = [p for p in (data.get("image_path"), data.get("image_thumb_path")) if p]
    if not paths:
        return
    try:
        bucket = get_bucket()
        for path in paths:
            try:
                bucket.blob(path).delete()
            except Exception:
                pass  # already gone, or storage hiccup — never block the Firestore write
    except Exception as e:
        print(f"⚠️ Item image cleanup failed: {e}")


def _rate_limited(message: str, retry_after: float) -> JSONResponse:
    return JSONResponse(
        status_code=429,
        content={"status": "rate_limited", "message": message, "retry_after": retry_after},
        headers={"Retry-After": str(int(retry_after) + 1)},
    )


@router.post("/confirm_clear_inventory")
async def confirm_clear_inventory(authorization: str = Header(None)):
    from main import db

    uid = verify_token(authorization)
    user_stock_ref = db.collection("users").document(uid).collection("stock")
    all_docs = list(user_stock_ref.stream())

    if not all_docs:
        return {"status": "success", "message": "Inventory is already empty.", "deleted_count": 0}

    for doc in all_docs:
        doc.reference.delete()

    # Delete every item photo in one prefix pass rather than two blob deletes per
    # doc — on a 300-item catalogue that would be 600 serial round trips and would
    # blow the serverless timeout. Best-effort: never fail the clear over storage.
    try:
        bucket = get_bucket()
        for blob in bucket.list_blobs(prefix=f"users/{uid}/items/"):
            try:
                blob.delete()
            except Exception:
                pass
    except Exception as e:
        print(f"⚠️ Item image cleanup failed during inventory clear: {e}")

    return {
        "status": "success",
        "message": f"✅ Cleared {len(all_docs)} items from inventory.",
        "deleted_count": len(all_docs),
    }


@router.get("/inventory")
async def get_inventory(authorization: str = Header(None)):
    from main import db

    uid = verify_token(authorization)
    user_stock_ref = db.collection("users").document(uid).collection("stock")
    docs = user_stock_ref.stream()

    # Resolve the bucket once and rebuild image URLs from path + token, mirroring
    # routes/orders.py::_attach_bills. Degrades to no images when the bucket isn't
    # configured, rather than 500-ing the whole inventory page.
    try:
        bucket_name = get_bucket().name
    except Exception as e:
        print(f"⚠️ Storage bucket unavailable, serving inventory without images: {e}")
        bucket_name = ""

    inventory = []
    for doc in docs:
        data = doc.to_dict()

        ts_obj = data.get("updated_at") or data.get("created_at")
        try:
            ts = ts_obj.timestamp() * 1000 if ts_obj else 0
        except AttributeError:
            ts = 0

        # Items created by voice or a supplier purchase have none of the fields
        # below, so every one needs a default.
        row = {
            "item": doc.id,
            "quantity": data.get("quantity", 0),
            "price": data.get("price", 0),
            "cost_price": data.get("cost_price", 0),
            "unit": data.get("unit", ""),
            "category": data.get("category", ""),
            "updated_at": ts,
        }

        token, path = data.get("image_token"), data.get("image_path")
        if bucket_name and token and path:
            row["image_url"] = _image_url(bucket_name, path, token)
            row["thumb_url"] = _image_url(bucket_name, data.get("image_thumb_path") or path, token)

        inventory.append(row)
    return {"inventory": inventory}


@router.post("/inventory")
async def create_inventory_item(
    item: str = Form(...),
    price: float = Form(...),
    quantity: int = Form(0),
    cost_price: float = Form(0),
    unit: str = Form(""),
    category: str = Form(""),
    image: Optional[UploadFile] = File(None),
    authorization: str = Header(None),
):
    """Create a stock item, optionally with a product photo (multipart/form-data).

    A multipart body can't use a Pydantic model, so the bounds from
    models.InventoryItemUpdate are re-asserted by hand below — keep them in sync.
    """
    from main import db

    # 1. Auth first: an unauthenticated caller can't even cause a rate-limit write.
    uid = verify_token(authorization)

    # 2. Rate limits before any storage or image work. Only charged when a photo
    #    is actually attached — a photo-less create shouldn't spend the image budget.
    if image is not None:
        allowed, retry_after = check_user_limit(db, uid, IMAGE_USER_LIMIT)
        if not allowed:
            # Cooldown retries are <= 3s; anything longer is the daily cap.
            # Round up so the message agrees with the Retry-After header instead
            # of telling the shopkeeper to "try again in 0s".
            message = (
                "Aaj ki photo limit khatam ho gayi. Kal phir try karein."
                if retry_after > 60
                else f"Thoda ruko! Try again in {int(retry_after) + 1}s."
            )
            return _rate_limited(message, retry_after)

        for config in (IMAGE_UPLOAD_RPM, IMAGE_UPLOAD_RPD):
            allowed, retry_after = check_global_rate_limit(db, config)
            if not allowed:
                print(f"⚠️ {config.name} rate limit hit — retry_after={retry_after}s")
                return _rate_limited(
                    f"Server busy. Please try again in {int(retry_after) + 1} seconds.",
                    retry_after,
                )

    # 3. Field validation (mirrors the bounds in models.py).
    item_id = _normalize_item_id(item)
    if not 0 <= price <= MAX_AMOUNT:
        raise HTTPException(status_code=400, detail="Selling price is out of range.")
    if not 0 <= cost_price <= MAX_AMOUNT:
        raise HTTPException(status_code=400, detail="Cost price is out of range.")
    if not 0 <= quantity <= MAX_QTY:
        raise HTTPException(status_code=400, detail="Opening stock is out of range.")
    unit = (unit or "").strip().lower()
    if unit not in ALLOWED_UNITS:
        raise HTTPException(status_code=400, detail="Unit must be PCS, Dozen, Box or Pack.")
    category = (category or "").strip()[:MAX_CATEGORY_LEN]

    user_stock_ref = db.collection("users").document(uid).collection("stock")

    # 4. Duplicate guard BEFORE spending storage. Voice (db_operations.py) and
    #    supplier purchases (routes/suppliers.py) create stock/{name} docs under
    #    the same id scheme — overwriting one would wipe a live item's quantity.
    #    Re-checked transactionally in step 7 to close the TOCTOU window.
    if user_stock_ref.document(item_id).get().exists:
        raise HTTPException(
            status_code=409,
            detail=f"'{item_id}' is already in your inventory. Tap it to edit.",
        )

    # 5. Sanitize + compress. Nothing the client sent survives this step.
    image_fields, uploaded_paths = {}, {}
    if image is not None:
        raw = await _read_capped(image, MAX_UPLOAD_BYTES)
        try:
            main_bytes, thumb_bytes, _, _ = process_item_image(raw)
        except ImageRejected as e:
            raise HTTPException(status_code=400, detail=str(e))

        # 6. Upload. The path is uuid-keyed, NOT item-name-keyed: the doc id is
        #    the item name, so a rename would otherwise orphan the blob. A uuid
        #    path survives renames and keeps user text out of object paths.
        image_id = uuid4().hex
        token = str(uuid4())
        bucket = get_bucket()
        uploaded_paths = {
            "image_path": f"users/{uid}/items/{image_id}.webp",
            "image_thumb_path": f"users/{uid}/items/{image_id}_thumb.webp",
        }
        _put_image(bucket, uploaded_paths["image_path"], main_bytes, token)
        _put_image(bucket, uploaded_paths["image_thumb_path"], thumb_bytes, token)
        image_fields = {"image_id": image_id, "image_token": token, **uploaded_paths}

    # 7. Create transactionally so a voice "add" racing this request can't be
    #    clobbered. If we lose, delete the blobs we just wrote.
    @firestore.transactional
    def _create(txn):
        doc_ref = user_stock_ref.document(item_id)
        if doc_ref.get(transaction=txn).exists:
            return False
        txn.set(
            doc_ref,
            {
                "item": item_id,
                "quantity": quantity,
                "price": price,
                "cost_price": cost_price,
                "unit": unit,
                "category": category,
                "created_at": firestore.SERVER_TIMESTAMP,
                "updated_at": firestore.SERVER_TIMESTAMP,
                **image_fields,
            },
        )
        return True

    if not _create(db.transaction()):
        _delete_item_images(uploaded_paths)
        raise HTTPException(
            status_code=409,
            detail=f"'{item_id}' is already in your inventory. Tap it to edit.",
        )

    response = {
        "status": "success",
        "message": f"✅ '{item_id}' added to inventory.",
        "item": item_id,
    }
    if image_fields:
        bucket_name = get_bucket().name
        response["image_url"] = _image_url(
            bucket_name, image_fields["image_path"], image_fields["image_token"]
        )
        response["thumb_url"] = _image_url(
            bucket_name, image_fields["image_thumb_path"], image_fields["image_token"]
        )
    return response


@router.put("/inventory/{item_id}")
async def update_inventory_item(
    item_id: str, req: InventoryItemUpdate, authorization: str = Header(None)
):
    """Update an inventory item's name, quantity, and/or price."""
    from main import db

    uid = verify_token(authorization)
    user_stock_ref = db.collection("users").document(uid).collection("stock")
    doc_ref = user_stock_ref.document(item_id)
    doc = doc_ref.get()

    if not doc.exists:
        raise HTTPException(status_code=404, detail="Item not found in inventory.")

    new_name = _normalize_item_id(req.item) if req.item else None
    new_quantity = req.quantity
    new_price = req.price

    # If renaming: delete old doc, create new one
    if new_name and new_name != item_id:
        # Check if target name already exists
        target_doc = user_stock_ref.document(new_name).get()
        if target_doc.exists:
            raise HTTPException(
                status_code=400, detail=f"An item named '{new_name}' already exists."
            )

        old_data = doc.to_dict()
        new_data = {
            "item": new_name,
            "quantity": new_quantity if new_quantity is not None else old_data.get("quantity", 0),
            "price": new_price if new_price is not None else old_data.get("price", 0),
            "created_at": old_data.get("created_at", firestore.SERVER_TIMESTAMP),
            "updated_at": firestore.SERVER_TIMESTAMP,
        }
        # A rename is delete + recreate, so anything not copied here is LOST.
        # Dropping image_path would orphan a blob that keeps costing money and is
        # no longer reachable from any doc. The uuid-keyed path means no blob
        # rewrite is needed — the fields just move to the new doc.
        for key in _CARRY_ON_RENAME:
            if key in old_data:
                new_data[key] = old_data[key]

        user_stock_ref.document(new_name).set(new_data)
        doc_ref.delete()
        return {
            "status": "success",
            "message": f"Item renamed from '{item_id}' to '{new_name}' and updated.",
            "item": new_name,
        }
    else:
        # Update in place
        update_data = {"updated_at": firestore.SERVER_TIMESTAMP}
        if new_quantity is not None:
            update_data["quantity"] = new_quantity
        if new_price is not None:
            update_data["price"] = new_price
        doc_ref.update(update_data)
        return {
            "status": "success",
            "message": f"Item '{item_id}' updated.",
            "item": item_id,
        }


@router.delete("/inventory/{item_id}")
async def delete_inventory_item(item_id: str, authorization: str = Header(None)):
    """Delete a single inventory item."""
    from main import db

    uid = verify_token(authorization)
    user_stock_ref = db.collection("users").document(uid).collection("stock")
    doc_ref = user_stock_ref.document(item_id)
    doc = doc_ref.get()

    if not doc.exists:
        raise HTTPException(status_code=404, detail="Item not found in inventory.")

    data = doc.to_dict() or {}
    doc_ref.delete()
    _delete_item_images(data)
    return {
        "status": "success",
        "message": f"Item '{item_id}' deleted from inventory.",
    }
