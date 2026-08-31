"""
Bill generation — POST /orders/{order_id}/bill.

Renders a PDF invoice for a customer order, archives it to Firebase Storage at
users/{uid}/bills/{order_id}.pdf, and returns a non-expiring, read-only download
URL (Firebase download-token style) so the bill can be viewed/shared anytime.

Bills are *disposable*. Both the PDF and its metadata doc are deleted 30 days
after last use (Firestore TTL on expires_at; a GCS lifecycle rule on customTime),
and rebuilt on demand afterwards. That only works because nothing about a bill is
invented at generation time: the number comes from the order's own order_no, and
the download token is derived from uid + order_id, so a regenerated bill is
byte-for-byte the same bill at the same URL. POST .../bill/touch pushes the
deadline out whenever someone actually opens or shares the bill.

Currency is rendered as "Rs." rather than the ₹ glyph: reportlab's bundled fonts
don't include U+20B9, and we avoid committing a binary Unicode TTF. Swap in a
registered TTF (e.g. DejaVuSans) here if a proper ₹ symbol is wanted later.
"""

import hashlib
import hmac
import io
import os
from datetime import datetime, timedelta, timezone
from urllib.parse import quote
from uuid import uuid4

from fastapi import APIRouter, Header, HTTPException
from firebase_admin import firestore
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    HRFlowable,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from auth import get_bucket, verify_token
from routes.orders import (  # reuse the order-side helpers rather than restating them
    _allocate_order_no,
    _bill_blob_path,
    _bill_expiry,
    _bill_no,
    _display_price,
    _load_order_docs,
)

router = APIRouter()

# Brand palette (matches the app theme).
GREEN = colors.HexColor("#22c55e")
DARK_GREEN = colors.HexColor("#15803d")
LIGHT_GREEN = colors.HexColor("#dcfce7")
GREY = colors.HexColor("#6b7280")
BORDER = colors.HexColor("#d1d5db")
IST = timezone(timedelta(hours=5, minutes=30))


def _titlecase(s: str) -> str:
    return (s or "").strip().title()


def _num(n) -> str:
    """Grouped number, no decimals when whole (e.g. 1,000 or 1,250.50)."""
    n = float(n or 0)
    return f"{int(n):,}" if n == int(n) else f"{n:,.2f}"


def _money(n) -> str:
    return f"Rs. {_num(n)}"


def _download_token(uid: str, order_id: str) -> str:
    """The bill's Firebase Storage download token.

    Derived, not random, so regenerating a bill that was deleted by the retention
    sweep rebuilds the *same* URL — a link shared with a customer months ago goes
    dead while the PDF is gone and starts working again the moment it is rebuilt.

    Falls back to a random token when BILL_TOKEN_SECRET is unset: a missing secret
    costs link stability, which is not worth failing a bill over.
    """
    secret = os.getenv("BILL_TOKEN_SECRET")
    if not secret:
        return str(uuid4())
    msg = f"{uid}:{order_id}".encode()
    return hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()


def _pdf_url(bucket_name: str, blob_name: str, token: str) -> str:
    """Non-expiring, read-only Firebase download URL for an archived bill."""
    path = quote(blob_name, safe="")
    return (
        f"https://firebasestorage.googleapis.com/v0/b/{bucket_name}/o/{path}"
        f"?alt=media&token={token}"
    )


def _touch_blob(blob) -> None:
    """Restart the Storage-side retention clock for a bill PDF.

    The bucket's lifecycle rule deletes on daysSinceCustomTime, so stamping
    customTime is what "the shopkeeper used this bill today" means to GCS. Only
    objects that carry customTime are eligible for that rule, which is why
    inventory photos are unaffected by it.
    """
    try:
        blob.custom_time = datetime.now(timezone.utc)
        blob.patch()
    except Exception:
        pass


@router.post("/orders/{order_id}/bill")
async def generate_bill(order_id: str, authorization: str = Header(None)):
    from main import db

    uid = verify_token(authorization)
    user_ref = db.collection("users").document(uid)
    orders_ref = user_ref.collection("orders")

    # 1. Load the order's line items.
    docs = _load_order_docs(orders_ref, order_id)
    if not docs:
        raise HTTPException(status_code=404, detail="Order not found.")

    customer_name = ""
    customer_modifier = ""
    order_no = None
    items = []
    total_amount = 0.0
    total_qty = 0
    for doc in docs:
        data = doc.to_dict()
        customer_name = customer_name or data.get("customer_name", "")
        customer_modifier = customer_modifier or data.get("customer_modifier", "")
        order_no = order_no or data.get("order_no")
        qty = data.get("quantity", 0) or 0
        amount = data.get("amount", 0) or 0
        items.append(
            {
                "item": data.get("item", ""),
                "quantity": qty,
                "price": _display_price(data),
                "amount": amount,
            }
        )
        total_amount += amount
        total_qty += qty

    # 2. Shop ("Bill From") details.
    user_doc = user_ref.get()
    udata = user_doc.to_dict() if user_doc.exists else {}
    shop_name = (udata.get("shop_name") or "").strip() or "My Shop"
    shop_mobile = (udata.get("shop_mobile") or "").strip()
    shop_address = (udata.get("shop_address") or "").strip()

    # 3. Bill number — the order's own number, so it is identical every time this
    # bill is rebuilt. Orders written before numbering existed get one now, stamped
    # back onto their line items so it never has to be worked out again.
    if not order_no:
        order_no = _allocate_order_no(db, user_ref)
        for doc in docs:
            try:
                doc.reference.update({"order_no": order_no})
            except Exception:
                pass
    bill_no = _bill_no(order_no)

    # 4. Render the PDF.
    pdf_bytes = _render_bill_pdf(
        bill_no=bill_no,
        shop_name=shop_name,
        shop_mobile=shop_mobile,
        shop_address=shop_address,
        customer_name=customer_name,
        customer_modifier=customer_modifier,
        items=items,
        total_amount=total_amount,
        total_qty=total_qty,
    )

    # 5. Upload, embedding the download token so the permanent link works after
    # overwrite. An existing token is reused rather than re-derived, so a link
    # already shared with a customer survives even if BILL_TOKEN_SECRET changes.
    bill_ref = user_ref.collection("bills").document(order_id)
    existing = bill_ref.get()
    download_token = (
        (existing.to_dict() or {}).get("download_token") if existing.exists else None
    ) or _download_token(uid, order_id)

    bucket = get_bucket()
    blob = bucket.blob(_bill_blob_path(uid, order_id))
    blob.metadata = {"firebaseStorageDownloadTokens": download_token}
    blob.custom_time = datetime.now(timezone.utc)
    blob.upload_from_string(pdf_bytes, content_type="application/pdf")

    # Mark the saved bill current — clears any stale flag set by later order edits
    # — and start its 30-day retention window.
    bill_ref.set(
        {
            "download_token": download_token,
            "storage_path": _bill_blob_path(uid, order_id),
            "stale": False,
            "generated_at": firestore.SERVER_TIMESTAMP,
            "expires_at": _bill_expiry(),
        },
        merge=True,
    )

    # 6. Build the non-expiring, read-only download URL.
    pdf_url = _pdf_url(bucket.name, blob.name, download_token)

    return {
        "status": "success",
        "bill_number": bill_no,
        "order_no": order_no,
        "pdf_url": pdf_url,
    }


@router.post("/orders/{order_id}/bill/touch")
async def touch_bill(order_id: str, authorization: str = Header(None)):
    """Restart a bill's 30-day retention window because someone just used it.

    Deliberately cheap — one Firestore write and one Storage metadata patch, no
    PDF work — because the frontend fires it on every bill open and share.
    """
    from main import db

    uid = verify_token(authorization)
    bill_ref = db.collection("users").document(uid).collection("bills").document(order_id)

    if not bill_ref.get().exists:
        # Already swept, or never generated. Nothing to keep alive; the next
        # "Generate Bill" rebuilds it at the same number and URL.
        return {"status": "success", "touched": False}

    bill_ref.update({"expires_at": _bill_expiry()})
    try:
        _touch_blob(get_bucket().blob(_bill_blob_path(uid, order_id)))
    except Exception:
        pass

    return {"status": "success", "touched": True}


def _render_bill_pdf(
    *,
    bill_no,
    shop_name,
    shop_mobile,
    shop_address,
    customer_name,
    customer_modifier,
    items,
    total_amount,
    total_qty,
) -> bytes:
    styles = getSampleStyleSheet()
    title = ParagraphStyle(
        "billTitle",
        parent=styles["Title"],
        textColor=DARK_GREEN,
        fontSize=24,
        alignment=0,
        spaceAfter=0,
    )
    powered = ParagraphStyle(
        "powered", parent=styles["Normal"], textColor=GREY, fontSize=9, alignment=2
    )
    label = ParagraphStyle(
        "label",
        parent=styles["Normal"],
        textColor=DARK_GREEN,
        fontSize=8,
        fontName="Helvetica-Bold",
        spaceAfter=4,
    )
    meta = ParagraphStyle("meta", parent=styles["Normal"], fontSize=9, spaceAfter=2)
    party = ParagraphStyle("party", parent=styles["Normal"], fontSize=9, leading=13)
    party_name = ParagraphStyle("partyName", parent=party, fontName="Helvetica-Bold")
    cell = ParagraphStyle("cell", parent=styles["Normal"], fontSize=9, leading=12)
    footer = ParagraphStyle(
        "footer", parent=styles["Normal"], textColor=GREY, fontSize=8, alignment=1
    )

    cw = A4[0] - 30 * mm  # content width inside 15mm margins
    flow = []

    # Header: BILL (left) + Powered by BolKhata (right).
    flow.append(
        Table(
            [[Paragraph("BILL", title), Paragraph("Powered by <b>BolKhata</b>", powered)]],
            colWidths=[cw * 0.5, cw * 0.5],
            style=TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE")]),
        )
    )
    flow.append(Spacer(1, 4))

    bill_date = datetime.now(IST).strftime("%d-%m-%Y")
    flow.append(Paragraph(f"Bill Number&nbsp;&nbsp;<b>{bill_no}</b>", meta))
    flow.append(Paragraph(f"Bill Date&nbsp;&nbsp;<b>{bill_date}</b>", meta))
    flow.append(Spacer(1, 6))
    flow.append(HRFlowable(width="100%", thickness=1.2, color=GREEN, spaceAfter=10))

    # Bill From / Bill To boxes.
    cust_display = _titlecase(customer_name) or "Customer"
    if customer_modifier:
        cust_display += f" ({customer_modifier})"

    from_cell = [Paragraph("Bill From", label), Paragraph(_titlecase(shop_name), party_name)]
    if shop_address:
        from_cell.append(Paragraph(shop_address.replace("\n", "<br/>"), party))
    if shop_mobile:
        from_cell.append(Paragraph(f"Mobile Number: {shop_mobile}", party))

    to_cell = [Paragraph("Bill To", label), Paragraph(cust_display, party_name)]

    party_table = Table([[from_cell, to_cell]], colWidths=[cw * 0.5, cw * 0.5])
    party_table.setStyle(
        TableStyle(
            [
                ("BOX", (0, 0), (0, 0), 0.5, BORDER),
                ("BOX", (1, 0), (1, 0), 0.5, BORDER),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                ("TOPPADDING", (0, 0), (-1, -1), 10),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
            ]
        )
    )
    flow.append(party_table)
    flow.append(Spacer(1, 14))

    # Items table.
    data = [["S.No", "Item Name", "Qty", "Rate (Rs.)", "Total (Rs.)"]]
    for i, it in enumerate(items, 1):
        data.append(
            [
                str(i),
                Paragraph(_titlecase(it["item"]), cell),
                _num(it["quantity"]),
                _num(it["price"]),
                _num(it["amount"]),
            ]
        )
    data.append(
        [
            "",
            Paragraph("<b>TOTAL</b>", cell),
            _num(total_qty),
            "",
            Paragraph(f"<b>{_money(total_amount)}</b>", cell),
        ]
    )

    n = len(data) - 1  # index of the TOTAL row
    items_table = Table(
        data,
        colWidths=[cw * 0.10, cw * 0.45, cw * 0.13, cw * 0.16, cw * 0.16],
    )
    items_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), GREEN),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("ALIGN", (0, 0), (0, -1), "CENTER"),
                ("ALIGN", (2, 0), (-1, -1), "RIGHT"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LINEBELOW", (0, 0), (-1, -2), 0.4, BORDER),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                # TOTAL row emphasis.
                ("BACKGROUND", (0, n), (-1, n), LIGHT_GREEN),
                ("LINEABOVE", (0, n), (-1, n), 1, GREEN),
            ]
        )
    )
    flow.append(items_table)
    flow.append(Spacer(1, 24))

    # Notes (left intentionally blank) + footer.
    flow.append(Paragraph("Notes", label))
    flow.append(HRFlowable(width="100%", thickness=0.4, color=BORDER, spaceBefore=24, spaceAfter=8))
    flow.append(Paragraph("This is a computer-generated bill. Powered by BolKhata.", footer))

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=15 * mm,
        rightMargin=15 * mm,
        topMargin=15 * mm,
        bottomMargin=15 * mm,
        title=f"Bill {bill_no}",
    )
    doc.build(flow)
    return buf.getvalue()
