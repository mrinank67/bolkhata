"""
Pydantic request/response models for BolKhata API.
"""

from typing import Optional

from fastapi import UploadFile
from pydantic import BaseModel, Field

# Shared bounds: quantities up to 1 lakh units, amounts up to ₹1 crore.
# Public names exist because multipart endpoints take Form(...) fields, which get
# no Pydantic validation — routes/inventory.py re-asserts these bounds by hand.
MAX_QTY = 100_000
MAX_AMOUNT = 10_000_000

_MAX_QTY = MAX_QTY
_MAX_AMOUNT = MAX_AMOUNT


class InventoryItemUpdate(BaseModel):
    """PUT /inventory/{id}, bound as a multipart Form model.

    The photo lives in the model rather than in a separate File() parameter on
    purpose: FastAPI only flattens a form model into its fields when it is the
    *sole* body parameter, and a second one would make it expect a form field
    literally named "req". Keeping everything here is also what lets this
    endpoint be validated by these bounds instead of re-asserting them by hand
    the way the multipart create route has to.
    """

    item: Optional[str] = Field(default=None, max_length=100)
    quantity: Optional[int] = Field(default=None, ge=0, le=_MAX_QTY)
    price: Optional[float] = Field(default=None, ge=0, le=_MAX_AMOUNT)
    image: Optional[UploadFile] = None
    # Only meaningful when no replacement photo is attached; an attached image
    # always wins, so "replace" never has to be spelled as remove-then-add.
    remove_image: bool = False


class PurchaseRequest(BaseModel):
    supplier_name: str = Field(max_length=100)
    item_name: str = Field(max_length=100)
    quantity: int = Field(gt=0, le=_MAX_QTY)
    amount: float = Field(ge=0, le=_MAX_AMOUNT)
    proof_image_url: Optional[str] = Field(default="", max_length=500)


class LedgerEntryRequest(BaseModel):
    customer_name: str = Field(max_length=100)
    customer_modifier: Optional[str] = Field(default="", max_length=100)
    item: str = Field(max_length=100)
    quantity: int = Field(ge=0, le=_MAX_QTY)
    unit: Optional[str] = Field(default="", max_length=30)
    amount: Optional[float] = Field(default=0, ge=0, le=_MAX_AMOUNT)
    whatsapp_number: Optional[str] = Field(default="", max_length=16)
    reminder_schedule: Optional[str] = Field(default="", max_length=50)
    due_note: Optional[str] = Field(default="", max_length=300)


class ClearDuesRequest(BaseModel):
    customer_name: str = Field(max_length=100)
    customer_modifier: Optional[str] = Field(default="", max_length=100)
    # Amount the customer paid. >= the total owed settles the account fully;
    # less than the total is a partial clear (applied oldest-debt-first).
    amount: float = Field(gt=0, le=_MAX_AMOUNT)


class WhatsAppReminderRequest(BaseModel):
    customer_name: str = Field(max_length=100)
    customer_modifier: Optional[str] = Field(default="", max_length=100)
    whatsapp_number: str = Field(max_length=16)
    reminder_schedule: Optional[str] = Field(default="", max_length=50)


class OrderItemCreate(BaseModel):
    item: str = Field(max_length=100)
    quantity: int = Field(gt=0, le=_MAX_QTY)
    price: float = Field(ge=0, le=_MAX_AMOUNT)


class OrderCreateRequest(BaseModel):
    customer_name: str = Field(max_length=100)
    customer_modifier: Optional[str] = Field(default="", max_length=100)
    items: list[OrderItemCreate] = Field(default_factory=list)


class OrderItemAddRequest(OrderItemCreate):
    # Customer is used as a fallback when the order_id can't be looked up
    # (e.g. legacy orders that predate the order_id field).
    customer_name: Optional[str] = Field(default="", max_length=100)
    customer_modifier: Optional[str] = Field(default="", max_length=100)


class OrderItemUpdate(BaseModel):
    item: Optional[str] = Field(default=None, max_length=100)
    quantity: Optional[int] = Field(default=None, gt=0, le=_MAX_QTY)
    price: Optional[float] = Field(default=None, ge=0, le=_MAX_AMOUNT)


class UserSettingsRequest(BaseModel):
    upi_id: Optional[str] = Field(default="", max_length=256)
    # Shop ("Bill From") details — rendered on generated bills.
    shop_name: Optional[str] = Field(default="", max_length=100)
    shop_mobile: Optional[str] = Field(default="", max_length=16)
    shop_address: Optional[str] = Field(default="", max_length=300)


class PayLinkRequest(BaseModel):
    # Note: the payee UPI ID (pa) and display name (pn) are intentionally NOT
    # accepted from the client — pa comes from the authenticated user's saved
    # settings and pn is fixed server-side.
    am: float = Field(gt=0, le=10_000_000)
    tn: str = Field(default="", max_length=120)


class SupplierCreateRequest(BaseModel):
    name: str = Field(max_length=100)
    mobile: Optional[str] = Field(default="", max_length=16)
    gst_number: Optional[str] = Field(default="", max_length=20)


class ResolveTransactionRequest(BaseModel):
    transaction: dict
    selected_modifier: str = ""
