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

# Indian shops — show purchase dates in IST so "Today"/"Yesterday" line up with
# the shopkeeper's day, not the server's UTC clock.
_IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))


def _format_purchase_date(ts) -> str:
    """Format a Firestore timestamp as a short relative date for voice replies."""
    if not ts:
        return "-"
    try:
        d = ts.astimezone(_IST)
    except (AttributeError, ValueError, OSError):
        return "-"
    today = datetime.datetime.now(_IST).date()
    if d.date() == today:
        return "Today"
    if d.date() == today - datetime.timedelta(days=1):
        return "Yesterday"
    return f"{d.day} {d.strftime('%b')}"


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


def _match_supplier(raw_name: str, supplier_display_map: dict) -> str:
    """Resolve a spoken supplier name to its canonical display name.

    `supplier_display_map` maps normalized keys -> display names (directory
    entries are authoritative; past purchases fill in the rest). Returns the
    fuzzy-matched display name (score > 70) or the normalized spoken name when
    the supplier is new — keeping voice and manual entries on one identity.
    """
    normalized = _normalize_supplier_name(raw_name)
    if not supplier_display_map:
        return normalized
    best_key, score = process.extractOne(normalized, list(supplier_display_map.keys()))
    if score > 70:
        return supplier_display_map[best_key]
    return normalized


def apply_payment(user_udhaar_ref, customer_name: str, customer_modifier: str, amount) -> tuple:
    """Apply a payment of `amount` against a customer's udhaar dues.

    Used by both the voice "payment" operation and the manual "Clear Dues"
    button so the two stay in lockstep.

    - Full settle (amount >= total owed): every matching entry is deleted,
      including unpriced item rows, so the customer drops off the ledger.
    - Partial: oldest debts are reduced first (FIFO); fully-paid entries are
      deleted, partially-paid entries keep their original timestamp and record
      the payment time in `last_payment_at`.

    Returns (matched_count, total_owed, paid, remaining). matched_count == 0
    means the customer had no dues at all.
    """
    customer_name = (customer_name or "").lower()
    customer_modifier = (customer_modifier or "").lower()
    amount = _to_number(amount)

    matching = []
    for doc in user_udhaar_ref.where(
        filter=FieldFilter("customer_name", "==", customer_name)
    ).stream():
        data = doc.to_dict()
        if customer_modifier and customer_modifier != (data.get("customer_modifier") or "").lower():
            continue
        matching.append((doc, data))

    if not matching:
        return 0, 0, 0, 0

    total_owed = sum((d.get("amount", 0) or 0) for _, d in matching)
    paid = min(amount, total_owed)

    # Full settle (also covers the all-unpriced case where total_owed == 0)
    if paid >= total_owed:
        for doc, _ in matching:
            doc.reference.delete()
        return len(matching), total_owed, total_owed, 0

    # Partial: reduce oldest debts first.
    payment_left = paid
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

    return len(matching), total_owed, paid, total_owed - paid


def process_transactions(
    transactions: list,
    uid: str,
    db,
    user_stock_ref,
    user_udhaar_ref,
    user_orders_ref,
    recent_customer: str = "",
    recent_modifier: str = "",
    recent_order_id: str = "",
) -> tuple:
    """
    Process a list of parsed transactions and update the database.

    When recent_order_id is supplied, items sold to recent_customer in this call
    append to that existing order (the "add to the same order within a timeframe"
    feature) instead of starting a new order card.

    Returns:
        (result_list, errors) — structured result groups and error messages.
    """
    # Fetch dynamic inventory to enrich fuzzy matching
    stock_docs = list(user_stock_ref.stream())
    all_fuzzy_candidates = [doc.id for doc in stock_docs]

    # Build a normalized-key -> display-name map of known suppliers so spoken
    # names canonicalize to one identity. Directory entries win for display;
    # past purchases fill in suppliers not yet saved to the directory.
    suppliers_ref = db.collection("users").document(uid).collection("suppliers")
    purchases_collection_ref = db.collection("users").document(uid).collection("suppliers_purchases")
    supplier_display_map = {}
    supplier_dir_keys = set()
    for doc in suppliers_ref.stream():
        name = (doc.to_dict().get("name") or "").strip()
        if name:
            key = _normalize_supplier_name(name)
            supplier_display_map.setdefault(key, name)
            supplier_dir_keys.add(key)
    for doc in purchases_collection_ref.stream():
        name = (doc.to_dict().get("supplier_name") or "").strip()
        if name:
            supplier_display_map.setdefault(_normalize_supplier_name(name), name)

    # Structured result groups keyed by action type
    result_groups = {}
    errors = []

    # One order "session" id per customer per voice call, so all items spoken in
    # a single command for the same customer group into one order on the Orders page.
    order_session_ids = {}

    # Seed the recent customer's session with their last order's id so a follow-up
    # command within the recent-context window appends to that same order card
    # rather than creating a new one.
    if recent_order_id and recent_customer:
        recent_ckey = f"{recent_customer.lower()}|{(recent_modifier or '').lower()}"
        order_session_ids[recent_ckey] = recent_order_id

    def _order_id_for(ckey):
        if ckey not in order_session_ids:
            order_session_ids[ckey] = user_orders_ref.document().id  # pre-generated shared id
        return order_session_ids[ckey]

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
        supplier_name = _match_supplier(raw_supplier, supplier_display_map) if raw_supplier else ""
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

        # --- Handle Supplier Directory & Purchase Queries ---
        if target == "supplier":
            if not raw_supplier:
                errors.append("Kaunsa supplier? (Please specify a supplier name).")
                continue

            # Add a supplier to the directory (registration only — no goods received).
            # Dedupe on the raw normalized key (not fuzzy) so a genuinely new
            # supplier isn't collapsed into a similarly-named existing one.
            if operation == "add":
                key = _normalize_supplier_name(raw_supplier)
                display = raw_supplier.strip().title()
                group = get_group("supplier_add", "Supplier Added", "🏪", ["Supplier", "Status"])
                if key in supplier_dir_keys:
                    group["rows"].append({
                        "Supplier": supplier_display_map.get(key, display),
                        "Status": "ℹ️ Already in your suppliers",
                    })
                else:
                    suppliers_ref.add({
                        "name": display,
                        "name_lower": display.lower(),
                        "mobile": "",
                        "gst_number": "",
                        "created_at": firestore.SERVER_TIMESTAMP,
                    })
                    supplier_dir_keys.add(key)
                    supplier_display_map.setdefault(key, display)
                    group["rows"].append({"Supplier": display, "Status": "✅ Added"})
                continue

            # Remove a supplier from the directory. Purchase history is kept —
            # deleting financial records by voice is too risky to do silently.
            if operation == "clear":
                key = _normalize_supplier_name(supplier_name)
                group = get_group("supplier_delete", "Supplier Removed", "🏪", ["Supplier", "Status"])
                removed_name = ""
                for doc in suppliers_ref.stream():
                    dname = (doc.to_dict().get("name") or "").strip()
                    if dname and _normalize_supplier_name(dname) == key:
                        doc.reference.delete()
                        removed_name = dname
                if removed_name:
                    group["rows"].append({
                        "Supplier": removed_name,
                        "Status": "✅ Removed (purchase history kept)",
                    })
                else:
                    not_found = supplier_display_map.get(key) or raw_supplier.strip().title()
                    group["empty_message"] = f"{not_found} aapke suppliers mein nahi mila."
                continue

            # Query purchases from a supplier ("Ramesh traders se kitna maal liya")
            if operation == "read":
                key = _normalize_supplier_name(supplier_name)
                display = supplier_display_map.get(key) or raw_supplier.strip().title()
                group_key = f"supplier_purchases_{key}"
                group = get_group(group_key, f"{display} — Purchases", "🏪", ["Item", "Qty", "Amount", "When"])

                matching = []
                for doc in purchases_collection_ref.stream():
                    data = doc.to_dict()
                    pname = (data.get("supplier_name") or "").strip()
                    if pname and _normalize_supplier_name(pname) == key:
                        matching.append(data)

                if not matching:
                    group["empty_message"] = f"{display} se koi purchase record nahi mila."
                    continue

                matching.sort(key=lambda d: d.get("timestamp") or _EPOCH_MIN, reverse=True)
                total_amount = sum((d.get("amount", 0) or 0) for d in matching)
                for data in matching[:10]:
                    amt = data.get("amount", 0) or 0
                    group["rows"].append({
                        "Item": (data.get("item_name") or "—").capitalize(),
                        "Qty": data.get("quantity", 0),
                        "Amount": f"₹{amt:,.0f}" if amt else "-",
                        "When": _format_purchase_date(data.get("timestamp")),
                    })
                if len(matching) > 1:
                    label = "Total" if len(matching) <= 10 else f"Total ({len(matching)} purchases)"
                    group["rows"].append({
                        "Item": label, "Qty": "", "Amount": f"₹{total_amount:,.0f}", "When": "",
                    })
                continue

            # Unrecognized supplier operation — nothing to do
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

            matched, total_owed, payment, remaining = apply_payment(
                user_udhaar_ref, customer_name, customer_modifier, txn_amount
            )

            if not matched:
                group = get_group(group_key, f"Payment — {title_name}", "💰", ["Customer", "Status"])
                group["empty_message"] = f"{title_name} ka koi baaki hisaab nahi hai."
                continue

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
                    "price": (txn_amount / qty) if (txn_amount and qty) else (db_price or 0),
                    "order_id": _order_id_for(f"{customer_name}|{customer_modifier}"),
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
                    "price": (txn_amount / qty) if (txn_amount and qty) else (db_price or 0),
                    "order_id": _order_id_for(f"{customer_name}|{customer_modifier}"),
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
