"""
Inventory endpoints — GET/PUT/DELETE /inventory, POST /confirm_clear_inventory
"""

from fastapi import APIRouter, HTTPException, Header
from firebase_admin import firestore

from auth import verify_token
from models import InventoryItemUpdate

router = APIRouter()


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

    return {"status": "success", "message": f"✅ Cleared {len(all_docs)} items from inventory.", "deleted_count": len(all_docs)}


@router.get("/inventory")
async def get_inventory(authorization: str = Header(None)):
    from main import db

    uid = verify_token(authorization)
    user_stock_ref = db.collection("users").document(uid).collection("stock")
    docs = user_stock_ref.stream()
    inventory = []
    for doc in docs:
        data = doc.to_dict()
        
        ts_obj = data.get("updated_at") or data.get("created_at")
        try:
            ts = ts_obj.timestamp() * 1000 if ts_obj else 0
        except AttributeError:
            ts = 0
            
        inventory.append({
            "item": doc.id,
            "quantity": data.get("quantity", 0),
            "price": data.get("price", 0),
            "updated_at": ts,
        })
    return {"inventory": inventory}


@router.put("/inventory/{item_id}")
async def update_inventory_item(item_id: str, req: InventoryItemUpdate, authorization: str = Header(None)):
    """Update an inventory item's name, quantity, and/or price."""
    from main import db

    uid = verify_token(authorization)
    user_stock_ref = db.collection("users").document(uid).collection("stock")
    doc_ref = user_stock_ref.document(item_id)
    doc = doc_ref.get()

    if not doc.exists:
        raise HTTPException(status_code=404, detail="Item not found in inventory.")

    new_name = req.item.strip().lower() if req.item else None
    new_quantity = req.quantity
    new_price = req.price

    # If renaming: delete old doc, create new one
    if new_name and new_name != item_id:
        # Check if target name already exists
        target_doc = user_stock_ref.document(new_name).get()
        if target_doc.exists:
            raise HTTPException(status_code=400, detail=f"An item named '{new_name}' already exists.")

        old_data = doc.to_dict()
        new_data = {
            "item": new_name,
            "quantity": new_quantity if new_quantity is not None else old_data.get("quantity", 0),
            "price": new_price if new_price is not None else old_data.get("price", 0),
            "created_at": old_data.get("created_at", firestore.SERVER_TIMESTAMP),
            "updated_at": firestore.SERVER_TIMESTAMP,
        }
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

    doc_ref.delete()
    return {
        "status": "success",
        "message": f"Item '{item_id}' deleted from inventory.",
    }
