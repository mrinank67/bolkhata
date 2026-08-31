# API Endpoints Reference

BolKhata uses a clean REST API structure. All endpoints except `/config` and `/pay` require a `Bearer <Firebase_ID_Token>` in the Authorization header.

Adding a new API **path** also requires an explicit `src`/`dest` mapping in `vercel.json` — see [Architecture](architecture.md#conventions-the-code-depends-on).

| Endpoint | Method | Description |
| :--- | :--- | :--- |
| `/config` | `GET` | Fetches client Firebase keys dynamically |
| `/process_voice` | `POST` | Primary entry point. Processes audio binary data and commits intents to Firestore |
| `/voice/resolve` | `POST` | Completes a transaction after the user picks a customer in a disambiguation prompt |
| `/inventory` | `GET` | Lists all active stock names, quantities, prices, optional metadata, and product image URLs |
| `/inventory` | `POST` | Creates a stock item from a `multipart/form-data` submission (name + price required; product photo, cost price, unit, opening stock, and category optional) |
| `/inventory/{item_id}` | `PUT` | Renames an item or updates its price/stock levels in-place |
| `/inventory/{item_id}` | `DELETE` | Removes a single item from the active database |
| `/confirm_clear_inventory` | `POST` | Deletes the entire stock collection (requires UI verification) |
| `/suppliers` | `GET` | Lists wholesale purchase history and compiles monthly totals |
| `/suppliers/purchase` | `POST` | Logs a wholesale transaction, automatically updating related item stock |
| `/suppliers/list` | `GET` | Retrieves saved wholesale vendors |
| `/suppliers/add` | `POST` | Registers a new wholesale vendor in the directory |
| `/suppliers/{supplier_id}` | `PUT` | Edits a saved vendor's name, mobile, and GST (rejects duplicate names) |
| `/suppliers/{supplier_id}` | `DELETE` | Removes a vendor and their purchase history |
| `/ledger/customers` | `GET` | Compiles a list of active udhaar accounts, dues, and itemizations |
| `/ledger/entry` | `POST` | Manually writes credit/udhaar entries directly |
| `/ledger/clear` | `POST` | Settles a customer's dues — full or partial (FIFO), shared with the voice payment flow |
| `/ledger/whatsapp-reminder` | `POST` | Saves a customer's WhatsApp number and reminder schedule |
| `/orders` | `GET` | Lists customer orders grouped by order, with per-order totals and a grand total |
| `/orders` | `POST` | Creates a new order from one or more line items |
| `/orders/{order_id}/items` | `POST` | Appends a line item to an existing order |
| `/orders/{order_id}/bill` | `POST` | Renders a PDF bill, archives it to Storage, and returns a permanent download link |
| `/orders/{order_id}/bill/touch` | `POST` | Marks an archived bill as still in use, restarting its 30-day retention window |
| `/orders/{order_id}/customer` | `PUT` | Re-points a whole order at a different customer — how a walk-in order gets a name before it is billed |
| `/orders/item/{item_id}` | `PUT` | Edits a single order line item (item, quantity, or price) |
| `/orders/item/{item_id}` | `DELETE` | Removes a single line item from an order |
| `/orders/{order_id}` | `DELETE` | Deletes an entire order and all its line items |
| `/pay/create` | `POST` | Mints a signed UPI payment link token for the caller's saved UPI ID |
| `/pay` | `GET` | Public payment page; validates the signed token and renders a UPI deep link |
| `/settings` | `GET` / `PUT` | Reads or updates the shopkeeper's UPI ID (validated VPA format) and shop "Bill From" profile (name, mobile, address) |
| `/history` | `GET` | Pulls the last 50 speech transaction logs and parsing errors |
| `/history` | `DELETE` | Clears the voice processing history |
