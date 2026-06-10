"""
Database operations for voice-based transaction processing.

Contains the core business logic for processing parsed transactions
and updating Firestore (stock, udhaar, orders collections).
"""

from thefuzz import process
from firebase_admin import firestore
from google.cloud.firestore_v1.base_query import FieldFilter


SUPPLIER_SUFFIXES = {"supplier", "suppliers", "wholesale", "distributor", "distributors", "traders", "supply", "supplies", "vendor", "vendors"}


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
    hindi_text: str,
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
    hinglish_text = hindi_text

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
        target = txn.get("target")
        operation = txn.get("operation")
        raw_item = txn.get("item", "")
        raw_qty = txn.get("qty", 1)
        customer_name = txn.get("customer_name", "").lower()
        customer_modifier = txn.get("customer_modifier", "").lower()
        is_credit = txn.get("is_credit", False)
        supplier_name = _match_supplier(txn.get("supplier_name", ""), existing_suppliers) if txn.get("supplier_name", "").strip() else ""
        txn_amount = txn.get("amount", 0)
        txn_rate = txn.get("rate", 0)
        txn_unit = txn.get("unit", "")

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

        # Quantity
        if raw_qty == "ALL" and operation == "subtract":
            qty = current_qty
        else:
            try:
                qty = int(raw_qty)
            except ValueError:
                qty = 1

        # Calculate txn_amount if not provided
        if not txn_amount:
            if txn_rate > 0:
                txn_amount = txn_rate * qty
            elif db_price > 0:
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

            docs = user_udhaar_ref.where(filter=FieldFilter("customer_name", "==", customer_name)).where(filter=FieldFilter("item", "==", standard_item)).stream()
            existing_doc = None
            for doc in docs:
                if doc.to_dict().get("customer_modifier", "").lower() == customer_modifier.lower():
                    existing_doc = doc
                    break

            if existing_doc:
                existing_qty = existing_doc.to_dict().get("quantity", 0)
                existing_amount = existing_doc.to_dict().get("amount", 0)
                final_qty = existing_qty + qty
                total_owed_amount = existing_amount + (txn_amount or 0)
                update_fields = {
                    "quantity": final_qty,
                    "timestamp": firestore.SERVER_TIMESTAMP
                }
                if txn_amount:
                    update_fields["amount"] = total_owed_amount
                if txn_unit:
                    update_fields["unit"] = txn_unit
                existing_doc.reference.update(update_fields)
            else:
                udhaar_data = {
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
                }
                user_udhaar_ref.add(udhaar_data)
                final_qty = qty
                total_owed_amount = txn_amount or 0

            group["rows"].append({
                "Customer": title_name,
                "Item": standard_item.capitalize(),
                "Qty": final_qty,
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

            docs = user_orders_ref.where(filter=FieldFilter("customer_name", "==", customer_name)).where(filter=FieldFilter("item", "==", standard_item)).stream()
            existing_doc = None
            for doc in docs:
                if doc.to_dict().get("customer_modifier", "").lower() == customer_modifier.lower():
                    existing_doc = doc
                    break

            if existing_doc:
                existing_data = existing_doc.to_dict()
                existing_qty = existing_data.get("quantity", 0)
                final_qty = existing_qty + qty
                total_order_amount = existing_data.get("amount", 0) + (txn_amount or 0)
                update_fields = {
                    "quantity": final_qty,
                    "timestamp": firestore.SERVER_TIMESTAMP
                }
                if txn_amount:
                    update_fields["amount"] = total_order_amount
                existing_doc.reference.update(update_fields)
            else:
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
                final_qty = qty
                total_order_amount = txn_amount or 0

            group["rows"].append({
                "Customer": title_name,
                "Item": standard_item.capitalize(),
                "Qty": final_qty,
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
