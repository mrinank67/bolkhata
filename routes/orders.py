"""
Customer order endpoints — all /orders/* routes.

Orders are stored as flat line-item docs under users/{uid}/orders/, each tagged
with an order_id so items spoken/added together group into one order. These
routes are pure CRUD on the order record — they intentionally do NOT touch
inventory stock; inventory reconciliation is deferred to the (future) bill
generation flow so editing an order doesn't trigger unnecessary stock writes.
"""

from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Header
from firebase_admin import firestore
from google.cloud.firestore_v1.base_query import FieldFilter

from auth import verify_token, get_bucket
from models import OrderCreateRequest, OrderItemAddRequest, OrderItemUpdate

router = APIRouter()


def _display_price(data: dict) -> float:
    """Unit price: stored price if present, else derived from amount/quantity."""
    price = data.get("price")
    if price:
        return price
    qty = data.get("quantity") or 0
    amount = data.get("amount") or 0
    return (amount / qty) if qty else 0


def _order_id_for_doc(data: dict) -> str:
    """Grouping key for a line-item doc: its order_id, else the legacy synthetic key."""
    oid = data.get("order_id")
    if oid:
        return oid
    cname = data.get("customer_name", "unknown")
    cmod = data.get("customer_modifier", "")
    ts_obj = data.get("timestamp")
    try:
        day = ts_obj.date().isoformat() if ts_obj else "unknown"
    except AttributeError:
        day = "unknown"
    return f"legacy|{cname}|{cmod}|{day}"


def _mark_bill_stale(user_ref, order_id: str) -> None:
    """Flag an order's bill as out-of-date so the UI re-generates it on next view.
    Best-effort: a missing bill (or any error) is silently ignored."""
    try:
        bill_ref = user_ref.collection("bills").document(order_id)
        if bill_ref.get().exists:
            bill_ref.update({"stale": True})
    except Exception:
        pass


def _attach_bills(user_ref, order_list: list) -> None:
    """Decorate each order with its generated-bill metadata (if any) so the UI can
    offer "Show Bill" (re-open the saved PDF) instead of regenerating every click."""
    try:
        bill_docs = {d.id: (d.to_dict() or {}) for d in user_ref.collection("bills").stream()}
    except Exception:
        return
    if not bill_docs:
        return
    try:
        bucket_name = get_bucket().name
    except Exception:
        return
    for o in order_list:
        bd = bill_docs.get(o["order_id"])
        if not bd or not bd.get("download_token") or not bd.get("storage_path"):
            continue
        path = quote(bd["storage_path"], safe="")
        o["bill"] = {
            "bill_number": f"BK-{int(bd.get('bill_number') or 0):03d}",
            "pdf_url": (
                f"https://firebasestorage.googleapis.com/v0/b/{bucket_name}/o/{path}"
                f"?alt=media&token={bd['download_token']}"
            ),
            "stale": bool(bd.get("stale", False)),
        }


@router.get("/orders")
async def get_orders(authorization: str = Header(None)):
    from main import db

    uid = verify_token(authorization)
    user_ref = db.collection("users").document(uid)
    orders_ref = user_ref.collection("orders")
    docs = orders_ref.order_by("timestamp", direction=firestore.Query.DESCENDING).stream()

    orders = {}
    total_value = 0

    for doc in docs:
        data = doc.to_dict()
        cname = data.get("customer_name", "unknown")
        cmod = data.get("customer_modifier", "")

        ts_obj = data.get("timestamp")
        try:
            ts = ts_obj.isoformat() if ts_obj else None
        except AttributeError:
            ts = None

        # Group by order_id; legacy docs without one fall back to customer + day.
        oid = data.get("order_id")
        if not oid:
            try:
                day = ts_obj.date().isoformat() if ts_obj else "unknown"
            except AttributeError:
                day = "unknown"
            oid = f"legacy|{cname}|{cmod}|{day}"

        amount = data.get("amount", 0) or 0

        if oid not in orders:
            orders[oid] = {
                "order_id": oid,
                "customer_name": cname,
                "customer_modifier": cmod,
                "last_order": ts,
                "total": 0,
                "items": [],
            }

        orders[oid]["items"].append({
            "id": doc.id,
            "item": data.get("item", ""),
            "quantity": data.get("quantity", 0),
            "price": _display_price(data),
            "amount": amount,
        })
        orders[oid]["total"] += amount
        total_value += amount

    order_list = list(orders.values())
    _attach_bills(user_ref, order_list)

    return {
        "orders": order_list,
        "order_count": len(order_list),
        "total_value": total_value,
    }


@router.post("/orders")
async def create_order(req: OrderCreateRequest, authorization: str = Header(None)):
    from main import db

    uid = verify_token(authorization)

    if not req.customer_name.strip():
        raise HTTPException(status_code=400, detail="Customer name is required.")
    if not req.items:
        raise HTTPException(status_code=400, detail="At least one item is required.")

    orders_ref = db.collection("users").document(uid).collection("orders")
    order_id = orders_ref.document().id  # one shared id for all items in this order

    cname = req.customer_name.strip().lower()
    cmod = (req.customer_modifier or "").strip().lower()

    for it in req.items:
        item = it.item.strip().lower()
        if not item:
            continue
        orders_ref.add({
            "customer_name": cname,
            "customer_modifier": cmod,
            "item": item,
            "quantity": it.quantity,
            "amount": round(it.price * it.quantity, 2),
            "price": it.price,
            "order_id": order_id,
            "timestamp": firestore.SERVER_TIMESTAMP,
        })

    return {
        "status": "success",
        "message": f"Order created for {req.customer_name}.",
        "order_id": order_id,
    }


@router.post("/orders/{order_id}/items")
async def add_order_item(order_id: str, req: OrderItemAddRequest, authorization: str = Header(None)):
    from main import db

    uid = verify_token(authorization)
    orders_ref = db.collection("users").document(uid).collection("orders")

    item = req.item.strip().lower()
    if not item:
        raise HTTPException(status_code=400, detail="Item is required.")

    # Authoritative customer comes from an existing doc in this order; fall back to
    # the request body (e.g. legacy orders that have no queryable order_id field).
    cname = (req.customer_name or "").strip().lower()
    cmod = (req.customer_modifier or "").strip().lower()
    existing = list(orders_ref.where(filter=FieldFilter("order_id", "==", order_id)).limit(1).stream())
    if existing:
        data = existing[0].to_dict()
        cname = data.get("customer_name", cname)
        cmod = data.get("customer_modifier", cmod)
    elif not cname:
        raise HTTPException(status_code=404, detail="Order not found.")

    orders_ref.add({
        "customer_name": cname,
        "customer_modifier": cmod,
        "item": item,
        "quantity": req.quantity,
        "amount": round(req.price * req.quantity, 2),
        "price": req.price,
        "order_id": order_id,
        "timestamp": firestore.SERVER_TIMESTAMP,
    })

    _mark_bill_stale(orders_ref.parent, order_id)
    return {"status": "success", "message": "Item added to order."}


@router.put("/orders/item/{item_id}")
async def update_order_item(item_id: str, req: OrderItemUpdate, authorization: str = Header(None)):
    from main import db

    uid = verify_token(authorization)
    orders_ref = db.collection("users").document(uid).collection("orders")
    doc_ref = orders_ref.document(item_id)
    doc = doc_ref.get()

    if not doc.exists:
        raise HTTPException(status_code=404, detail="Order item not found.")

    data = doc.to_dict()

    # Effective new values (fall back to existing ones for fields not supplied).
    old_item = data.get("item", "")
    old_qty = data.get("quantity", 0) or 0
    old_price = _display_price(data)
    new_item = req.item.strip().lower() if req.item is not None else old_item
    new_qty = req.quantity if req.quantity is not None else old_qty
    new_price = req.price if req.price is not None else old_price

    # Only write (and invalidate the bill) when something the bill shows actually
    # changed — re-saving identical values is a no-op, so the saved bill stays fresh.
    changed = (
        new_item != old_item
        or (new_qty or 0) != (old_qty or 0)
        or round(new_price or 0, 2) != round(old_price or 0, 2)
    )

    # Do NOT touch `timestamp` here. Orders without an order_id group by
    # customer + day (see get_orders), so re-dating an edited item would move it
    # into another day's order — merging two separate orders for the same person.
    if changed:
        doc_ref.update({
            "item": new_item,
            "quantity": new_qty,
            "price": new_price,
            "amount": round((new_price or 0) * (new_qty or 0), 2),
        })
        _mark_bill_stale(orders_ref.parent, _order_id_for_doc(data))

    return {"status": "success", "message": "Order item updated."}


@router.delete("/orders/item/{item_id}")
async def delete_order_item(item_id: str, authorization: str = Header(None)):
    from main import db

    uid = verify_token(authorization)
    orders_ref = db.collection("users").document(uid).collection("orders")
    doc_ref = orders_ref.document(item_id)

    snap = doc_ref.get()
    if not snap.exists:
        raise HTTPException(status_code=404, detail="Order item not found.")

    order_id = _order_id_for_doc(snap.to_dict())
    doc_ref.delete()
    _mark_bill_stale(orders_ref.parent, order_id)
    return {"status": "success", "message": "Order item removed."}


@router.delete("/orders/{order_id}")
async def delete_order(order_id: str, authorization: str = Header(None)):
    from main import db

    uid = verify_token(authorization)
    orders_ref = db.collection("users").document(uid).collection("orders")

    if order_id.startswith("legacy|"):
        # Synthetic key for pre-order_id docs: legacy|{cname}|{cmod}|{day}.
        # Those docs have no order_id field, so match them by customer + day.
        parts = order_id.split("|")
        cname = parts[1] if len(parts) > 1 else ""
        cmod = parts[2] if len(parts) > 2 else ""
        day = parts[3] if len(parts) > 3 else ""
        docs = []
        for doc in orders_ref.where(filter=FieldFilter("customer_name", "==", cname)).stream():
            d = doc.to_dict()
            if (d.get("customer_modifier", "") or "") != cmod:
                continue
            ts = d.get("timestamp")
            try:
                doc_day = ts.date().isoformat() if ts else "unknown"
            except AttributeError:
                doc_day = "unknown"
            if (not d.get("order_id") and doc_day == day) or d.get("order_id") == order_id:
                docs.append(doc)
    else:
        docs = list(orders_ref.where(filter=FieldFilter("order_id", "==", order_id)).stream())

    if not docs:
        raise HTTPException(status_code=404, detail="Order not found.")

    for doc in docs:
        doc.reference.delete()

    # The order is gone, so its saved bill metadata is no longer meaningful.
    try:
        orders_ref.parent.collection("bills").document(order_id).delete()
    except Exception:
        pass

    return {"status": "success", "message": "Order deleted.", "deleted_items": len(docs)}
