"""
Pydantic request/response models for BolKhata API.
"""

from pydantic import BaseModel
from typing import Optional


class InventoryItemUpdate(BaseModel):
    item: Optional[str] = None
    quantity: Optional[int] = None
    price: Optional[float] = None


class PurchaseRequest(BaseModel):
    supplier_name: str
    item_name: str
    quantity: int
    amount: float
    proof_image_url: Optional[str] = ""


class LedgerEntryRequest(BaseModel):
    customer_name: str
    customer_modifier: Optional[str] = ""
    item: str
    quantity: int
    unit: Optional[str] = ""
    amount: Optional[float] = 0
    whatsapp_number: Optional[str] = ""
    reminder_schedule: Optional[str] = ""
    due_note: Optional[str] = ""


class LedgerEntryUpdate(BaseModel):
    customer_name: Optional[str] = None
    customer_modifier: Optional[str] = None
    item: Optional[str] = None
    quantity: Optional[int] = None
    unit: Optional[str] = None
    amount: Optional[float] = None
    whatsapp_number: Optional[str] = None
    reminder_schedule: Optional[str] = None
    due_note: Optional[str] = None


class WhatsAppReminderRequest(BaseModel):
    customer_name: str
    customer_modifier: Optional[str] = ""
    whatsapp_number: str
    reminder_schedule: str


class SupplierCreateRequest(BaseModel):
    name: str
    mobile: Optional[str] = ""
    gst_number: Optional[str] = ""
