"""
Database operations for voice-based transaction processing.

Contains the core business logic for processing parsed transactions
and updating Firestore (stock, udhaar, orders collections).
"""

import datetime

from thefuzz import process
from firebase_admin import firestore
from google.cloud.firestore_v1.base_query import FieldFilter


SUPPLIER_SUFFIXES = {"supplier", "suppliers", "wholesale", "distributor", "distributors", "traders", "supply", "supplies", "vendor", "vendors"}

# Sort fallback for udhaar docs missing a timestamp (must be tz-aware to compare
# with Firestore timestamps)
_EPOCH_MIN = datetime.datetime.min.replace(tzinfo=datetime.timezone.utc)


def _to_number(value, default=0):
    """Coerce LLM-provided values (None, "2.5", "800", 12) to a number."""
    try:
        num = float(value)
    except (TypeError, ValueError):
        return default
    return int(num) if num.is_integer() else num


def _normalize_supplier_name(name: str) -> str:
    words = name.strip().lower().split()
    if len(words) > 1 and words[-1] in SUPPLIER_SUFFIXES:
        words = words[:-1]
    return " ".join(words)


def _match_supplier(raw_name: str, existing_suppliers: list[str]) -> str:
    normalized = _normalize_supplier_name(raw_name)
    if not existing_suppliers:
        return normalized
    # Check if normalized name fuzzy-matches an existing supplier
    best_match, score = process.extractOne(normalized, existing_suppliers)
    if score > 70:
        return best_match
    return normalized


def process_transactions(
    transactions: list,
    uid: str,
    db,
    user_stock_ref,
    user_udhaar_ref,
    user_orders_ref,
) -> tuple:
    """
    Process a list of parsed transactions and update the database.

    Returns:
        (result_list, errors) — structured result groups and error messages.
    """
    # Fetch dynamic inventory to enrich fuzzy matching
    stock_docs = list(user_stock_ref.stream())
    all_fuzzy_candidates = [doc.id for doc in stock_docs]

    # Fetch existing supplier names for fuzzy matching (from both directory and purchases)
    directory_docs = db.collection("users").document(uid).collection("suppliers").stream()
    directory_names = {doc.to_dict().get("name", "").strip().lower() for doc in directory_docs if doc.to_dict().get("name", "").strip()}
    purchases_docs = db.collection("users").document(uid).collection("suppliers_purchases").stream()
    purchase_names = {doc.to_dict().get("supplier_name", "").strip().lower() for doc in purchases_docs if doc.to_dict().get("supplier_name", "").strip()}
    existing_suppliers = list(directory_names | purchase_names)

    # Structured result groups keyed by action type
    result_groups = {}
    errors = []

    def get_group(action_key, title, icon, columns):
        if action_key not in result_groups:
            result_groups[action_key] = {
                "action": action_key,
                "title": title,
                "icon": icon,
                "columns": columns,
                "rows": [],
            }
        return result_groups[action_key]

    for txn in transactions:
        if not isinstance(txn, dict):
            continue
        # The LLM may emit null for any field, so .get() defaults aren't enough
        target = txn.get("target")
        operation = txn.get("operation")
        raw_item = str(txn.get("item") or "")
        raw_qty = txn.get("qty", 1)
        customer_name = (txn.get("customer_name") or "").lower()
        customer_modifier = (txn.get("customer_modifier") or "").lower()
        is_credit = bool(txn.get("is_credit"))
        raw_supplier = (txn.get("supplier_name") or "").strip()
        supplier_name = _match_supplier(raw_supplier, existing_suppliers) if raw_supplier else ""
        txn_amount = _to_number(txn.get("amount"))
        txn_rate = _to_number(txn.get("rate"))
        txn_unit = txn.get("unit") or ""

        # --- Disambiguate duplicate customer names ---
        # _resolved is set by /voice/resolve after the user picks a customer;
        # without it, picking the no-modifier option would re-trigger this prompt forever.
        if (
            customer_name
            and not customer_modifier
            and not txn.get("_resolved")
            and operation in ("subtract", "payment", "clear", "send_reminder")
        ):
            # Distinct customers are identified by modifier only — entries for the
            # same person can have mixed whatsapp_number values ("" on new entries).
            seen = {}
            for doc in user_udhaar_ref.where(filter=FieldFilter("customer_name", "==", customer_name)).stream():
                data = doc.to_dict()
                mod = (data.get("customer_modifier") or "").lower()
                phone = data.get("whatsapp_number", "")
                if mod not in seen:
                    seen[mod] = {"modifier": mod, "phone": phone}
                elif phone and not seen[mod]["phone"]:
                    seen[mod]["phone"] = phone
            if len(seen) > 1:
                title_name = customer_name.capitalize()
                group_key = f"disambig_{customer_name}"
                group = get_group(group_key, f"Which {title_name}?", "👥", ["Customer", "Phone"])
                options = []
                for opt in seen.values():
                    label = f"{title_name} ({opt['modifier']})" if opt["modifier"] else title_name
                    phone_display = opt["phone"] if opt["phone"] else "No number"
                    group["rows"].append({"Customer": label, "Phone": phone_display})
                    options.append(opt)
                group["requires_disambiguation"] = True
                group["disambiguation_options"] = options
                group["pending_transaction"] = txn
                continue

        # --- Handle Order Inquiries ---
        if target == "ledger" and operation == "read" and not is_credit:
            if not customer_name:
                errors.append("Kiske orders dekhne hain? (Please specify a name).")
                continue

            group_key = f"order_inquiry_{customer_name}_{customer_modifier}"
            title_name = f"{customer_name.capitalize()} ({customer_modifier})" if customer_modifier else customer_name.capitalize()
            group = get_group(group_key, f"{title_name}'s Orders", "🛍️", ["Item", "Qty", "Amount"])

            local_orders_ref = db.collection("users").document(uid).collection("orders")
            docs = local_orders_ref.where(filter=FieldFilter("customer_name", "==", customer_name)).stream()

            orders_found = False
            for doc in docs:
                data = doc.to_dict()
                if customer_modifier and customer_modifier.lower() != data.get("customer_modifier", "").lower():
                    continue
                orders_found = True
                item_name = data.get("item", "unknown")
                qty = data.get("quantity", 0)
                amt = data.get("amount", 0)
                group["rows"].append({
                    "Item": item_name.capitalize(),
                    "Qty": qty,
                    "Amount": f"₹{amt:,.0f}" if amt else "-",
                })

            if not orders_found:
                group["empty_message"] = f"No orders found for {title_name}."
            continue

        # --- Handle Full Inventory ---
        if target == "stock" and operation == "read" and raw_item == "ALL":
            group = get_group(
                "full_inventory", "Full Inventory", "📦", ["#", "Item", "Stock"]
            )
            all_docs = user_stock_ref.stream()
            idx = 1
            for doc in all_docs:
                data = doc.to_dict()
                group["rows"].append(
                    {
                        "#": idx,
                        "Item": doc.id.capitalize(),
                        "Stock": data.get("quantity", 0),
                    }
                )
                idx += 1
            if not group["rows"]:
                group["empty_message"] = "Inventory is empty. No items added yet."
            continue

        # --- Handle Clear Entire Inventory (requires confirmation) ---
        if target == "stock" and operation == "clear" and raw_item == "ALL":
            all_docs = list(user_stock_ref.stream())

            if all_docs:
                item_count = len(all_docs)
                group = get_group(
                    "clear_inventory", "⚠️ Confirm Inventory Deletion", "🗑️", ["Action", "Items"]
                )
                group["rows"].append(
                    {"Action": "Delete ALL inventory", "Items": f"{item_count} items"}
                )
                group["requires_confirmation"] = True
                group["confirmation_message"] = f"Are you sure you want to delete all {item_count} items from your inventory? This action cannot be undone."
            else:
                group = get_group(
                    "clear_inventory", "Inventory Cleared", "🗑️", ["Action", "Status"]
                )
                group["empty_message"] = "Inventory is already empty."
            continue

        # --- Handle Ledger Inquiries ---
        if target == "ledger" and operation == "read" and is_credit:
            if not customer_name:
                errors.append("Kiska khaata dekhna hai? (Please specify a name).")
                continue

            group_key = f"ledger_inquiry_{customer_name}_{customer_modifier}"
            title_name = f"{customer_name.capitalize()} ({customer_modifier})" if customer_modifier else customer_name.capitalize()
            group = get_group(
                group_key,
                f"{title_name}'s Ledger",
                "📒",
                ["Item", "Qty Owed", "Amount Owed"],
            )

            docs = user_udhaar_ref.where(
                filter=FieldFilter("customer_name", "==", customer_name)
            ).stream()
            dues_map = {}
            amount_map = {}
            for doc in docs:
                data = doc.to_dict()
                if customer_modifier and customer_modifier.lower() != data.get("customer_modifier", "").lower():
                    continue
                item_name = data.get("item", "unknown")
                dues_map[item_name] = dues_map.get(item_name, 0) + data.get("quantity", 0)
                amount_map[item_name] = amount_map.get(item_name, 0) + data.get("amount", 0)

            if not dues_map:
                group["empty_message"] = (
                    f"{title_name} ka khaata clear hai. No dues!"
                )
            else:
                total_amount = 0
                for item, qty in dues_map.items():
                    amt = amount_map.get(item, 0)
                    total_amount += amt
                    group["rows"].append({
                        "Item": item.capitalize(),
                        "Qty Owed": qty,
                        "Amount Owed": f"₹{amt:,.0f}" if amt else "-",
                    })
                if len(dues_map) > 1 and total_amount:
                    group["rows"].append({
                        "Item": "Total",
                        "Qty Owed": "",
                        "Amount Owed": f"₹{total_amount:,.0f}",
                    })
            continue

        # --- Handle Clearing Ledgers ---
        if target == "ledger" and operation == "clear":
            if not customer_name:
                errors.append("Kiska khaata clear karna hai? (Please specify a name).")
                continue

            group_key = f"clear_ledger_{customer_name}_{customer_modifier}"
            title_name = f"{customer_name.capitalize()} ({customer_modifier})" if customer_modifier else customer_name.capitalize()
            group = get_group(group_key, "Ledger Cleared", "💰", ["Customer", "Entries", "Amount Cleared", "Status"])

            docs = list(
                user_udhaar_ref.where(
                    filter=FieldFilter("customer_name", "==", customer_name)
                ).stream()
            )

            docs_to_delete = []
            cleared_amount = 0
            for doc in docs:
                data = doc.to_dict()
                if customer_modifier and customer_modifier.lower() != data.get("customer_modifier", "").lower():
                    continue
                docs_to_delete.append(doc)
                cleared_amount += data.get("amount", 0)

            if docs_to_delete:
                for doc in docs_to_delete:
                    doc.reference.delete()
                group["rows"].append({
                    "Customer": title_name,
                    "Entries": len(docs_to_delete),
                    "Amount Cleared": f"₹{cleared_amount:,.0f}" if cleared_amount else "-",
                    "Status": "✅ Settled",
                })
            else:
                group["rows"].append({
                    "Customer": title_name,
                    "Entries": 0,
                    "Amount Cleared": "-",
                    "Status": "ℹ️ No dues found",
                })
            continue

        # --- Handle Send Reminder ---
        if target == "ledger" and operation == "send_reminder":
            if not customer_name:
                errors.append("Kisko reminder bhejun? (Please specify a name).")
                continue

            title_name = (
                f"{customer_name.capitalize()} ({customer_modifier})"
                if customer_modifier
                else customer_name.capitalize()
            )
            group_key = f"send_reminder_{customer_name}_{customer_modifier}"

            docs = user_udhaar_ref.where(
                filter=FieldFilter("customer_name", "==", customer_name)
            ).stream()

            total_due = 0
            wa_number = ""
            for doc in docs:
                data = doc.to_dict()
                if customer_modifier and customer_modifier.lower() != data.get("customer_modifier", "").lower():
                    continue
                total_due += data.get("amount", 0)
                if data.get("whatsapp_number"):
                    wa_number = data["whatsapp_number"]

            if total_due <= 0:
                group = get_group(group_key, f"Reminder — {title_name}", "🔔", ["Customer", "Status"])
                group["empty_message"] = f"{title_name} ka koi baaki hisaab nahi hai."
                continue

            # Read UPI ID from user settings
            user_doc = db.collection("users").document(uid).get()
            upi_id = user_doc.to_dict().get("upi_id", "") if user_doc.exists else ""

            group = get_group(group_key, f"Reminder — {title_name}", "🔔", ["Customer", "Due"])
            group["rows"].append({"Customer": title_name, "Due": f"₹{total_due:,.0f}"})
            group["reminder_data"] = {
                "customer_name": title_name,
                "total_due": total_due,
                "whatsapp_number": wa_number,
                "upi_id": upi_id,
            }
            continue

        # --- Handle Payment (full or partial) ---
        if target == "ledger" and operation == "payment":
            if not customer_name:
                errors.append("Kisne payment kiya? (Please specify a name).")
                continue
            if not txn_amount:
                errors.append("Kitna payment hua? (Please specify an amount).")
                continue

            title_name = f"{customer_name.capitalize()} ({customer_modifier})" if customer_modifier else customer_name.capitalize()
            group_key = f"payment_{customer_name}_{customer_modifier}"

            docs = list(user_udhaar_ref.where(
                filter=FieldFilter("customer_name", "==", customer_name)
            ).stream())

            matching = []
            for doc in docs:
                data = doc.to_dict()
                if customer_modifier and customer_modifier.lower() != data.get("customer_modifier", "").lower():
                    continue
                matching.append((doc, data))

            if not matching:
                group = get_group(group_key, f"Payment — {title_name}", "💰", ["Customer", "Status"])
                group["empty_message"] = f"{title_name} ka koi baaki hisaab nahi hai."
                continue

            total_owed = sum(d.get("amount", 0) or 0 for _, d in matching)
            payment = min(txn_amount, total_owed)
            remaining = total_owed - payment

            # Apply payment FIFO (oldest debt first). Fully-paid entries are
            # deleted so the ledger reads as settled; partially-paid entries
            # keep their original timestamp (FIFO order) and record the
            # payment time separately.
            payment_left = payment
            matching.sort(key=lambda x: x[1].get("timestamp") or _EPOCH_MIN)
            for doc, data in matching:
                entry_amount = data.get("amount", 0) or 0
                if entry_amount <= 0:
                    continue
                reduction = min(payment_left, entry_amount)
                if entry_amount - reduction <= 0:
                    doc.reference.delete()
                else:
                    doc.reference.update({
                        "amount": entry_amount - reduction,
                        "last_payment_at": firestore.SERVER_TIMESTAMP,
                    })
                payment_left -= reduction
                if payment_left <= 0:
                    break

            paid_display = f"₹{payment:,.0f}"
            if txn_amount > total_owed:
                paid_display = f"₹{payment:,.0f} (of ₹{txn_amount:,.0f} — only dues recorded)"
            group = get_group(group_key, f"Payment — {title_name}", "💰",
                              ["Customer", "Paid", "Previous Due", "Remaining"])
            group["rows"].append({
                "Customer": title_name,
                "Paid": paid_display,
                "Previous Due": f"₹{total_owed:,.0f}",
                "Remaining": f"₹{remaining:,.0f}" if remaining > 0 else "✅ Settled",
            })
            continue

        # --- Handle amount-only credit entry (no item, e.g. "Suresh pe 800 ka udhaar") ---
        if is_credit and customer_name and txn_amount and not raw_item:
            title_name = f"{customer_name.capitalize()} ({customer_modifier})" if customer_modifier else customer_name.capitalize()
            group = get_group(
                "udhaar_sale",
                "Credit Sale (Udhaar)",
                "📒",
                ["Customer", "Item", "Qty", "Unit", "Amount", "Total Owed", "Stock"],
            )

            # Log each lump credit as its own entry; the running "Total Owed"
            # sums prior matching "general" entries plus this one.
            prior_owed = 0
            for doc in user_udhaar_ref.where(
                filter=FieldFilter("customer_name", "==", customer_name)
            ).where(
                filter=FieldFilter("item", "==", "general")
            ).stream():
                if doc.to_dict().get("customer_modifier", "").lower() == customer_modifier.lower():
                    prior_owed += doc.to_dict().get("amount", 0) or 0

            user_udhaar_ref.add({
                "customer_name": customer_name,
                "customer_modifier": customer_modifier,
                "item": "general",
                "quantity": 0,
                "amount": txn_amount,
                "unit": "",
                "whatsapp_number": "",
                "reminder_schedule": "",
                "reminder_sent": False,
                "due_note": "",
                "timestamp": firestore.SERVER_TIMESTAMP,
            })
            total_owed_amount = prior_owed + txn_amount

            group["rows"].append({
                "Customer": title_name,
                "Item": "General",
                "Qty": "-",
                "Unit": "-",
                "Amount": f"₹{txn_amount:,.0f}",
                "Total Owed": f"₹{total_owed_amount:,.0f}",
                "Stock": "-",
            })
            continue

        # --- Normal Stock Processing ---
        if not raw_item or raw_item == "ALL":
            continue

        raw_item = raw_item.lower()

        # Dynamic Fuzzy Match
        if all_fuzzy_candidates:
            best_match, score = process.extractOne(raw_item, all_fuzzy_candidates)
            if score > 70:
                standard_item = best_match
            else:
                standard_item = raw_item
        else:
            standard_item = raw_item

        stock_doc_ref = user_stock_ref.document(standard_item)
        stock_doc = stock_doc_ref.get()

        if not stock_doc.exists:
            if operation == "add":
                current_qty = 0
                db_price = 0
            else:
                errors.append(f"{standard_item} not found in inventory.")
                continue
        else:
            stock_data = stock_doc.to_dict()
            current_qty = stock_data.get("quantity", 0)
            db_price = stock_data.get("price", 0)

        # Inquiry
        if target == "stock" and operation == "read":
            group = get_group("inquiry", "Stock Check", "🔍", ["Item", "Current Stock"])
            group["rows"].append(
                {"Item": standard_item.capitalize(), "Current Stock": current_qty}
            )
            continue

        # Quantity — keep fractional values ("2.5 kilo") instead of collapsing to 1
        if raw_qty == "ALL" and operation == "subtract":
            qty = current_qty
        else:
            qty = _to_number(raw_qty, default=1)

        # Calculate txn_amount — rate is unambiguous (per-unit), so it always wins.
        # The stored retail price only applies to sales; using it for restocks
        # would fabricate a purchase cost in supplier records.
        if txn_rate > 0 and qty > 0:
            txn_amount = txn_rate * qty
        elif not txn_amount and db_price > 0 and operation == "subtract":
            txn_amount = db_price * qty

        # Calculate new stock
        if operation == "subtract":
            new_qty = max(0, current_qty - qty)
        else:
            new_qty = current_qty + qty
            
        # Update Stock DB
        update_data = {
            "quantity": new_qty, 
            "item": standard_item,
            "updated_at": firestore.SERVER_TIMESTAMP
        }
        if not stock_doc.exists:
            update_data["created_at"] = firestore.SERVER_TIMESTAMP
            
        stock_doc_ref.set(update_data, merge=True)
        title_name = f"{customer_name.capitalize()} ({customer_modifier})" if customer_modifier else customer_name.capitalize() if customer_name else ""

        # Build result row
        if operation == "subtract" and is_credit and customer_name:
            group = get_group(
                "udhaar_sale",
                "Credit Sale (Udhaar)",
                "📒",
                ["Customer", "Item", "Qty", "Unit", "Amount", "Total Owed", "Stock"],
            )

            # Log every credit sale as its own entry (a transaction record),
            # rather than merging into the customer's existing item entry. The
            # running "Total Owed" is computed by summing prior matching entries.
            prior_owed = 0
            for doc in user_udhaar_ref.where(
                filter=FieldFilter("customer_name", "==", customer_name)
            ).where(
                filter=FieldFilter("item", "==", standard_item)
            ).stream():
                if doc.to_dict().get("customer_modifier", "").lower() == customer_modifier.lower():
                    prior_owed += doc.to_dict().get("amount", 0) or 0

            user_udhaar_ref.add({
                "customer_name": customer_name,
                "customer_modifier": customer_modifier,
                "item": standard_item,
                "quantity": qty,
                "amount": txn_amount or 0,
                "unit": txn_unit or "",
                "whatsapp_number": "",
                "reminder_schedule": "",
                "reminder_sent": False,
                "due_note": "",
                "timestamp": firestore.SERVER_TIMESTAMP,
            })
            total_owed_amount = prior_owed + (txn_amount or 0)

            # Dual-write: also log a matching order record (credit sale = goods left the shop)
            try:
                user_orders_ref.add({
                    "customer_name": customer_name,
                    "customer_modifier": customer_modifier,
                    "item": standard_item,
                    "quantity": qty,
                    "amount": txn_amount or 0,
                    "timestamp": firestore.SERVER_TIMESTAMP,
                })
            except Exception as e:
                print(f"⚠️ Order dual-write failed for {customer_name}/{standard_item}: {e}")

            group["rows"].append({
                "Customer": title_name,
                "Item": standard_item.capitalize(),
                "Qty": qty,
                "Unit": txn_unit or "-",
                "Amount": f"₹{txn_amount:,.0f}" if txn_amount else "-",
                "Total Owed": f"₹{total_owed_amount:,.0f}" if total_owed_amount else "-",
                "Stock": new_qty,
            })
        elif operation == "subtract" and customer_name:
            group = get_group(
                "order_sale",
                "Customer Order",
                "🛍️",
                ["Customer", "Item", "Qty", "Amount", "Total Ordered", "Stock"]
            )

            # Log every sale as its own order entry (a transaction record),
            # rather than merging into the customer's existing item entry. The
            # running "Total Ordered" is computed by summing prior matching entries.
            prior_ordered = 0
            for doc in user_orders_ref.where(
                filter=FieldFilter("customer_name", "==", customer_name)
            ).where(
                filter=FieldFilter("item", "==", standard_item)
            ).stream():
                if doc.to_dict().get("customer_modifier", "").lower() == customer_modifier.lower():
                    prior_ordered += doc.to_dict().get("amount", 0) or 0

            user_orders_ref.add(
                {
                    "customer_name": customer_name,
                    "customer_modifier": customer_modifier,
                    "item": standard_item,
                    "quantity": qty,
                    "amount": txn_amount or 0,
                    "timestamp": firestore.SERVER_TIMESTAMP,
                }
            )
            total_order_amount = prior_ordered + (txn_amount or 0)

            group["rows"].append({
                "Customer": title_name,
                "Item": standard_item.capitalize(),
                "Qty": qty,
                "Amount": f"₹{txn_amount:,.0f}" if txn_amount else "-",
                "Total Ordered": f"₹{total_order_amount:,.0f}" if total_order_amount else "-",
                "Stock": new_qty,
            })
        elif operation == "subtract":
            group = get_group(
                "decrease", "Stock Sold", "🛒", ["Item", "Sold", "Amount", "Previous", "Current"]
            )
            group["rows"].append(
                {
                    "Item": standard_item.capitalize(),
                    "Sold": qty,
                    "Amount": f"₹{txn_amount:,.0f}" if txn_amount else "-",
                    "Previous": current_qty,
                    "Current": new_qty,
                }
            )
        else:
            # Stock add — with or without supplier
            if supplier_name:
                group = get_group(
                    "supplier_purchase",
                    "Supplier Purchase",
                    "🏪",
                    ["Supplier", "Item", "Qty", "Unit", "Rate", "Amount", "Stock Now"],
                )
                purchases_ref = db.collection("users").document(uid).collection("suppliers_purchases")
                purchases_ref.add({
                    "supplier_name": supplier_name,
                    "item_name": standard_item,
                    "quantity": qty,
                    "amount": txn_amount or 0,
                    "proof_image_url": "",
                    "timestamp": firestore.SERVER_TIMESTAMP,
                })
                group["rows"].append({
                    "Supplier": supplier_name.title(),
                    "Item": standard_item.capitalize(),
                    "Qty": qty,
                    "Unit": txn_unit or "-",
                    "Rate": f"₹{txn_rate:,.0f}" if txn_rate else "-",
                    "Amount": f"₹{txn_amount:,.0f}" if txn_amount else "-",
                    "Stock Now": new_qty,
                })
            else:
                group = get_group(
                    "increase",
                    "Stock Added",
                    "📦",
                    ["Item", "Added", "Previous", "Current"],
                )
                group["rows"].append(
                    {
                        "Item": standard_item.capitalize(),
                        "Added": qty,
                        "Previous": current_qty,
                        "Current": new_qty,
                    }
                )

    result_list = list(result_groups.values())

    if not result_list and not errors:
        errors.append("Couldn't understand that. Please try speaking again clearly.")

    return result_list, errors
