"""
Database operations for voice-based transaction processing.

Contains the core business logic for processing parsed transactions
and updating Firestore (stock, udhaar, orders collections).
"""

from thefuzz import process
from firebase_admin import firestore
from google.cloud.firestore_v1.base_query import FieldFilter


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
        supplier_name = txn.get("supplier_name", "").strip()
        txn_amount = txn.get("amount", 0)
        txn_unit = txn.get("unit", "")

        # --- Handle Order Inquiries ---
        if target == "ledger" and operation == "read" and not is_credit:
            if not customer_name:
                errors.append("Kiske orders dekhne hain? (Please specify a name).")
                continue
            
            group_key = f"order_inquiry_{customer_name}_{customer_modifier}"
            title_name = f"{customer_name.capitalize()} ({customer_modifier})" if customer_modifier else customer_name.capitalize()
            group = get_group(group_key, f"{title_name}'s Orders", "🛍️", ["Item", "Qty"])

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
                group["rows"].append({"Item": item_name.capitalize(), "Qty": qty})
            
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
                ["Item", "Quantity Owed"],
            )

            docs = user_udhaar_ref.where(
                filter=FieldFilter("customer_name", "==", customer_name)
            ).stream()
            dues_map = {}
            for doc in docs:
                data = doc.to_dict()
                if customer_modifier and customer_modifier.lower() != data.get("customer_modifier", "").lower():
                    continue
                item_name = data.get("item", "unknown")
                dues_map[item_name] = dues_map.get(item_name, 0) + data.get(
                    "quantity", 0
                )

            if not dues_map:
                group["empty_message"] = (
                    f"{title_name} ka khaata clear hai. No dues!"
                )
            else:
                for item, qty in dues_map.items():
                    group["rows"].append(
                        {"Item": item.capitalize(), "Quantity Owed": qty}
                    )
            continue

        # --- Handle Clearing Ledgers ---
        if target == "ledger" and operation == "clear":
            if not customer_name:
                errors.append("Kiska khaata clear karna hai? (Please specify a name).")
                continue

            group_key = f"clear_ledger_{customer_name}_{customer_modifier}"
            title_name = f"{customer_name.capitalize()} ({customer_modifier})" if customer_modifier else customer_name.capitalize()
            group = get_group(group_key, "Ledger Cleared", "💰", ["Customer", "Status"])

            docs = list(
                user_udhaar_ref.where(
                    filter=FieldFilter("customer_name", "==", customer_name)
                ).stream()
            )

            docs_to_delete = []
            for doc in docs:
                data = doc.to_dict()
                if customer_modifier and customer_modifier.lower() != data.get("customer_modifier", "").lower():
                    continue
                docs_to_delete.append(doc)

            if docs_to_delete:
                for doc in docs_to_delete:
                    doc.reference.delete()
                group["rows"].append(
                    {"Customer": title_name, "Status": "✅ Settled"}
                )
            else:
                group["rows"].append(
                    {
                        "Customer": title_name,
                        "Status": "ℹ️ No dues found",
                    }
                )
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
            else:
                errors.append(f"{standard_item} not found in inventory.")
                continue
        else:
            current_qty = stock_doc.to_dict().get("quantity", 0)

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
                ["Item", "Qty", "Previous", "Current", "Customer"],
            )
            
            docs = user_udhaar_ref.where(filter=FieldFilter("customer_name", "==", customer_name)).where(filter=FieldFilter("item", "==", standard_item)).stream()
            existing_doc = None
            for doc in docs:
                if doc.to_dict().get("customer_modifier", "").lower() == customer_modifier.lower():
                    existing_doc = doc
                    break
                    
            if existing_doc:
                existing_qty = existing_doc.to_dict().get("quantity", 0)
                final_qty = existing_qty + qty
                update_fields = {
                    "quantity": final_qty,
                    "timestamp": firestore.SERVER_TIMESTAMP
                }
                if txn_amount:
                    existing_amount = existing_doc.to_dict().get("amount", 0)
                    update_fields["amount"] = existing_amount + txn_amount
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
                
            group["rows"].append(
                {
                    "Item": standard_item.capitalize(),
                    "Qty": final_qty,
                    "Previous": current_qty,
                    "Current": new_qty,
                    "Customer": title_name,
                }
            )
        elif operation == "subtract" and customer_name:
            group = get_group(
                "order_sale",
                "Customer Order",
                "🛍️",
                ["Item", "Qty", "Previous", "Current", "Customer"]
            )
            
            docs = user_orders_ref.where(filter=FieldFilter("customer_name", "==", customer_name)).where(filter=FieldFilter("item", "==", standard_item)).stream()
            existing_doc = None
            for doc in docs:
                if doc.to_dict().get("customer_modifier", "").lower() == customer_modifier.lower():
                    existing_doc = doc
                    break
                    
            if existing_doc:
                existing_qty = existing_doc.to_dict().get("quantity", 0)
                final_qty = existing_qty + qty
                existing_doc.reference.update({
                    "quantity": final_qty,
                    "timestamp": firestore.SERVER_TIMESTAMP
                })
            else:
                user_orders_ref.add(
                    {
                        "customer_name": customer_name,
                        "customer_modifier": customer_modifier,
                        "item": standard_item,
                        "quantity": qty,
                        "timestamp": firestore.SERVER_TIMESTAMP,
                    }
                )
                final_qty = qty
                
            group["rows"].append(
                {
                    "Item": standard_item.capitalize(),
                    "Qty": final_qty,
                    "Previous": current_qty,
                    "Current": new_qty,
                    "Customer": title_name
                }
            )
        elif operation == "subtract":
            group = get_group(
                "decrease", "Stock Sold", "🛒", ["Item", "Sold", "Previous", "Current"]
            )
            group["rows"].append(
                {
                    "Item": standard_item.capitalize(),
                    "Sold": qty,
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
                    ["Item", "Added", "Amount", "Supplier", "Stock Now"],
                )
                # Also record in suppliers_purchases collection
                purchases_ref = db.collection("users").document(uid).collection("suppliers_purchases")
                purchases_ref.add({
                    "supplier_name": supplier_name,
                    "item_name": standard_item,
                    "quantity": qty,
                    "amount": txn_amount or 0,
                    "proof_image_url": "",
                    "timestamp": firestore.SERVER_TIMESTAMP,
                })
                group["rows"].append(
                    {
                        "Item": standard_item.capitalize(),
                        "Added": qty,
                        "Amount": f"₹{txn_amount:,.0f}" if txn_amount else "-",
                        "Supplier": supplier_name.title(),
                        "Stock Now": new_qty,
                    }
                )
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
