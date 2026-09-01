/**
 * panels.js — the shop's records, viewed and edited on their behalf.
 *
 * Every call here is an ordinary BolKhata endpoint with an acting header, so
 * the server-side rules apply unchanged: bills are marked stale when an order
 * line moves, order edits never touch stock, purchase corrections do, order
 * numbers stay put. Nothing is reimplemented client-side.
 */

import {
  $,
  adminFetch,
  empty,
  esc,
  getActingUid,
  money,
  renderError,
  toast,
  when
} from "./core.js";

const UNITS = ["", "pcs", "dozen", "box", "pack"];

function unitSelect(selected) {
  return `<select data-field="unit">${UNITS.map(
    u => `<option value="${u}" ${u === (selected || "") ? "selected" : ""}>${u || "—"}</option>`
  ).join("")}</select>`;
}

/** Run a mutation, report it, and refresh the panel it came from. */
async function mutate(action, reload, successMessage) {
  try {
    await action();
    toast(successMessage, "good");
    await reload();
  } catch (err) {
    toast(err.message, "error");
  }
}

// ─────────────────────────── Orders ───────────────────────────

export async function loadOrders() {
  const el = $("admin-panel-orders");
  el.innerHTML = `<div class="admin-empty">Loading…</div>`;
  try {
    const data = await adminFetch("/orders");
    const orders = data.orders || [];
    if (!orders.length) return empty(el, "This shop has no orders.");

    el.innerHTML = `
      <div class="admin-panel-note">
        Editing a line here marks the order's saved bill stale, exactly as it would
        in the shopkeeper's own app. Stock is deliberately untouched.
      </div>
      ${orders.map(buildOrder).join("")}`;
    wireOrders(el);
  } catch (err) {
    renderError(el, err);
  }
}

function buildOrder(order) {
  const items = (order.items || [])
    .map(
      it => `
      <tr data-item-id="${esc(it.id)}">
        <td><input data-field="item" value="${esc(it.item)}" /></td>
        <td><input data-field="quantity" type="number" min="1" value="${esc(it.quantity)}" /></td>
        <td>${unitSelect(it.unit)}</td>
        <td><input data-field="price" type="number" min="0" step="0.01" value="${esc(it.price)}" /></td>
        <td class="admin-amount">${esc(money(it.amount))}</td>
        <td class="admin-row-actions">
          <button class="btn-small" data-act="save-item" type="button">Save</button>
          <button class="btn-small btn-danger-ghost" data-act="delete-item" type="button">Delete</button>
        </td>
      </tr>`
    )
    .join("");

  const name = order.customer_name || "(counter sale)";

  // /orders decorates an order with `bill` only while a usable PDF exists —
  // past its retention window the field is dropped, because the link would 404.
  const bill = order.bill
    ? `<a class="admin-bill-link" href="${esc(order.bill.pdf_url)}" target="_blank" rel="noopener">
         ${esc(order.bill.bill_number)}</a>
       ${order.bill.stale ? '<span class="admin-badge warn">Stale</span>' : ""}`
    : "";

  return `
    <article class="admin-card" data-order-id="${esc(order.order_id)}">
      <header class="admin-card-head">
        <div>
          <strong>#${esc(order.order_no ?? "—")}</strong>
          <span class="admin-customer">${esc(name)}${
            order.customer_modifier ? ` (${esc(order.customer_modifier)})` : ""
          }</span>
          ${bill}
        </div>
        <div class="admin-meta">${esc(when(order.last_order))} · ${esc(money(order.total))}</div>
      </header>
      <table class="admin-table">
        <thead><tr><th>Item</th><th>Qty</th><th>Unit</th><th>Price</th><th>Amount</th><th></th></tr></thead>
        <tbody>${items}</tbody>
      </table>
      <footer class="admin-card-foot">
        <input data-field="customer_name" placeholder="Re-point to customer…" value="${esc(order.customer_name || "")}" />
        <input data-field="customer_modifier" placeholder="Modifier" value="${esc(order.customer_modifier || "")}" />
        <button class="btn-small" data-act="save-customer" type="button">Change customer</button>
        <button class="btn-small" data-act="bill" type="button">Generate bill</button>
        <button class="btn-small btn-danger-ghost" data-act="delete-order" type="button">Delete order</button>
      </footer>
    </article>`;
}

function rowValues(row) {
  const read = f => row.querySelector(`[data-field="${f}"]`).value;
  return {
    item: read("item").trim(),
    quantity: Number(read("quantity")),
    unit: read("unit"),
    price: Number(read("price"))
  };
}

function wireOrders(root) {
  root.addEventListener("click", async e => {
    const btn = e.target.closest("button[data-act]");
    if (!btn) return;

    const card = btn.closest("[data-order-id]");
    const orderId = card.dataset.orderId;
    const row = btn.closest("tr[data-item-id]");

    if (btn.dataset.act === "save-item") {
      await mutate(
        () =>
          adminFetch(`/orders/item/${encodeURIComponent(row.dataset.itemId)}`, {
            method: "PUT",
            body: JSON.stringify(rowValues(row))
          }),
        loadOrders,
        "Line updated. The saved bill is now stale."
      );
    } else if (btn.dataset.act === "delete-item") {
      if (!confirm("Delete this line?")) return;
      await mutate(
        () =>
          adminFetch(`/orders/item/${encodeURIComponent(row.dataset.itemId)}`, {
            method: "DELETE"
          }),
        loadOrders,
        "Line deleted."
      );
    } else if (btn.dataset.act === "save-customer") {
      const name = card.querySelector('[data-field="customer_name"]').value.trim();
      const modifier = card.querySelector('[data-field="customer_modifier"]').value.trim();
      await mutate(
        () =>
          adminFetch(`/orders/${encodeURIComponent(orderId)}/customer`, {
            method: "PUT",
            body: JSON.stringify({ customer_name: name, customer_modifier: modifier })
          }),
        loadOrders,
        "Order re-pointed."
      );
    } else if (btn.dataset.act === "bill") {
      try {
        const bill = await adminFetch(`/orders/${encodeURIComponent(orderId)}/bill`, {
          method: "POST"
        });
        toast(`Bill ${bill.bill_number} ready.`, "good");
        window.open(bill.pdf_url, "_blank", "noopener");
      } catch (err) {
        toast(err.message, "error");
      }
    } else if (btn.dataset.act === "delete-order") {
      if (!confirm("Delete the whole order and its bill?")) return;
      await mutate(
        () => adminFetch(`/orders/${encodeURIComponent(orderId)}`, { method: "DELETE" }),
        loadOrders,
        "Order deleted."
      );
    }
  });
}

// ─────────────────────────── Ledger ───────────────────────────

export async function loadLedger() {
  const el = $("admin-panel-ledger");
  el.innerHTML = `<div class="admin-empty">Loading…</div>`;
  try {
    const data = await adminFetch("/ledger/customers");
    const customers = data.customers || [];
    if (!customers.length) return empty(el, "This shop has no outstanding credit.");

    el.innerHTML = `
      <div class="admin-panel-note">
        Total outstanding <strong>${esc(money(data.total_due))}</strong> across
        ${customers.length} customer(s). Deleting a line removes the debt; it does
        not record a payment.
      </div>
      ${customers.map(buildLedgerCustomer).join("")}`;
    wireLedger(el);
  } catch (err) {
    renderError(el, err);
  }
}

function buildLedgerCustomer(c) {
  const rows = (c.items || [])
    .map(
      it => `
      <tr data-entry-id="${esc(it.id)}">
        <td><input data-field="item" value="${esc(it.item)}" /></td>
        <td><input data-field="quantity" type="number" min="0" value="${esc(it.quantity)}" /></td>
        <td><input data-field="amount" type="number" min="0" step="0.01" value="${esc(it.amount)}" /></td>
        <td class="admin-meta">${esc(when(it.timestamp))}</td>
        <td class="admin-row-actions">
          <button class="btn-small" data-act="save-entry" type="button">Save</button>
          <button class="btn-small btn-danger-ghost" data-act="delete-entry" type="button">Delete</button>
        </td>
      </tr>`
    )
    .join("");

  return `
    <article class="admin-card">
      <header class="admin-card-head">
        <div><strong>${esc(c.customer_name)}</strong>${
          c.customer_modifier ? ` <span class="admin-customer">(${esc(c.customer_modifier)})</span>` : ""
        }</div>
        <div class="admin-meta">owes ${esc(money(c.total_due))}</div>
      </header>
      <table class="admin-table">
        <thead><tr><th>Item</th><th>Qty</th><th>Amount</th><th>When</th><th></th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </article>`;
}

function wireLedger(root) {
  root.addEventListener("click", async e => {
    const btn = e.target.closest("button[data-act]");
    if (!btn) return;
    const row = btn.closest("tr[data-entry-id]");
    const id = row.dataset.entryId;

    if (btn.dataset.act === "save-entry") {
      const read = f => row.querySelector(`[data-field="${f}"]`).value;
      await mutate(
        () =>
          adminFetch(`/ledger/entry/${encodeURIComponent(id)}`, {
            method: "PUT",
            body: JSON.stringify({
              item: read("item").trim(),
              quantity: Number(read("quantity")),
              amount: Number(read("amount"))
            })
          }),
        loadLedger,
        "Ledger entry corrected."
      );
    } else if (btn.dataset.act === "delete-entry") {
      if (!confirm("Remove this debt? This is not a payment.")) return;
      await mutate(
        () => adminFetch(`/ledger/entry/${encodeURIComponent(id)}`, { method: "DELETE" }),
        loadLedger,
        "Ledger entry removed."
      );
    }
  });
}

// ────────────────────────── Suppliers ──────────────────────────

export async function loadSuppliers() {
  const el = $("admin-panel-suppliers");
  el.innerHTML = `<div class="admin-empty">Loading…</div>`;
  try {
    const [purchases, directory] = await Promise.all([
      adminFetch("/suppliers"),
      adminFetch("/suppliers/list")
    ]);

    const rows = (purchases.purchases || [])
      .map(
        p => `
        <tr data-purchase-id="${esc(p.id)}">
          <td><input data-field="supplier_name" value="${esc(p.supplier_name)}" /></td>
          <td><input data-field="item_name" value="${esc(p.item_name)}" /></td>
          <td><input data-field="quantity" type="number" min="1" value="${esc(p.quantity)}" /></td>
          <td><input data-field="amount" type="number" min="0" step="0.01" value="${esc(p.amount)}" /></td>
          <td class="admin-row-actions">
            <button class="btn-small" data-act="save-purchase" type="button">Save</button>
            <button class="btn-small btn-danger-ghost" data-act="delete-purchase" type="button">Delete</button>
          </td>
        </tr>`
      )
      .join("");

    const dir = (directory.suppliers || [])
      .map(s => `<li>${esc(s.name)}${s.mobile ? ` · ${esc(s.mobile)}` : ""}</li>`)
      .join("");

    el.innerHTML = `
      <div class="admin-panel-note">
        Correcting a purchase moves stock by the difference, and deleting one takes
        its quantity back out — the same way recording it put stock in.
      </div>
      <article class="admin-card">
        <header class="admin-card-head"><strong>Purchases</strong>
          <div class="admin-meta">this month ${esc(money(purchases.month_total))}</div>
        </header>
        ${
          rows
            ? `<table class="admin-table">
                 <thead><tr><th>Supplier</th><th>Item</th><th>Qty</th><th>Amount</th><th></th></tr></thead>
                 <tbody>${rows}</tbody>
               </table>`
            : `<div class="admin-empty">No purchases recorded.</div>`
        }
      </article>
      <article class="admin-card">
        <header class="admin-card-head"><strong>Directory</strong></header>
        ${dir ? `<ul class="admin-plain-list">${dir}</ul>` : `<div class="admin-empty">No saved suppliers.</div>`}
      </article>`;

    wireSuppliers(el);
  } catch (err) {
    renderError(el, err);
  }
}

function wireSuppliers(root) {
  root.addEventListener("click", async e => {
    const btn = e.target.closest("button[data-act]");
    if (!btn) return;
    const row = btn.closest("tr[data-purchase-id]");
    const id = row.dataset.purchaseId;

    if (btn.dataset.act === "save-purchase") {
      const read = f => row.querySelector(`[data-field="${f}"]`).value;
      await mutate(
        () =>
          adminFetch(`/suppliers/purchase/${encodeURIComponent(id)}`, {
            method: "PUT",
            body: JSON.stringify({
              supplier_name: read("supplier_name").trim(),
              item_name: read("item_name").trim(),
              quantity: Number(read("quantity")),
              amount: Number(read("amount"))
            })
          }),
        loadSuppliers,
        "Purchase corrected and stock adjusted."
      );
    } else if (btn.dataset.act === "delete-purchase") {
      if (!confirm("Delete this purchase and take its quantity back out of stock?")) return;
      await mutate(
        () => adminFetch(`/suppliers/purchase/${encodeURIComponent(id)}`, { method: "DELETE" }),
        loadSuppliers,
        "Purchase deleted and stock adjusted."
      );
    }
  });
}

// ────────────────────────── Inventory ──────────────────────────

export async function loadInventory() {
  const el = $("admin-panel-inventory");
  el.innerHTML = `<div class="admin-empty">Loading…</div>`;
  try {
    const data = await adminFetch("/inventory");
    const items = data.inventory || [];
    if (!items.length) return empty(el, "This shop has no inventory items.");

    const rows = items
      .map(
        it => `
        <tr data-item-id="${esc(it.item)}">
          <td>${esc(it.item)}</td>
          <td><input data-field="quantity" type="number" min="0" value="${esc(it.quantity ?? 0)}" /></td>
          <td>${unitSelect(it.unit)}</td>
          <td><input data-field="price" type="number" min="0" step="0.01" value="${esc(it.price ?? 0)}" /></td>
          <td class="admin-row-actions">
            <button class="btn-small" data-act="save-stock" type="button">Save</button>
          </td>
        </tr>`
      )
      .join("");

    el.innerHTML = `
      <article class="admin-card">
        <header class="admin-card-head"><strong>${items.length} item(s)</strong></header>
        <table class="admin-table">
          <thead><tr><th>Item</th><th>Qty</th><th>Unit</th><th>Price</th><th></th></tr></thead>
          <tbody>${rows}</tbody>
        </table>
      </article>`;
    wireInventory(el);
  } catch (err) {
    renderError(el, err);
  }
}

function wireInventory(root) {
  root.addEventListener("click", async e => {
    const btn = e.target.closest('button[data-act="save-stock"]');
    if (!btn) return;
    const row = btn.closest("tr[data-item-id]");
    const read = f => row.querySelector(`[data-field="${f}"]`).value;

    // The update route is multipart, not JSON — it shares a form model with the
    // shopkeeper's edit sheet so a photo can ride along. FormData also means the
    // browser sets its own Content-Type boundary, hence no header here.
    const form = new FormData();
    form.append("quantity", read("quantity"));
    form.append("price", read("price"));
    form.append("unit", read("unit"));

    await mutate(
      () =>
        adminFetch(`/inventory/${encodeURIComponent(row.dataset.itemId)}`, {
          method: "PUT",
          body: form
        }),
      loadInventory,
      "Item updated."
    );
  });
}

// ──────────────────────────── Bills ────────────────────────────

export async function loadBills() {
  const el = $("admin-panel-bills");
  el.innerHTML = `<div class="admin-empty">Loading…</div>`;
  try {
    const data = await adminFetch(
      `/admin/users/${encodeURIComponent(getActingUid())}/bills`,
      { acting: false }
    );
    const bills = data.bills || [];
    if (!bills.length) {
      return empty(el, "No bills stored. They are rebuilt on demand from the order.");
    }

    el.innerHTML = `
      <div class="admin-panel-note">
        Bills are disposable — swept after 30 days of disuse and rebuilt at the same
        number and URL from the order. "Stale" means the order changed since the PDF
        was written; regenerate it from the Orders tab.
      </div>
      <article class="admin-card">
        <table class="admin-table">
          <thead><tr><th>Order</th><th>State</th><th>Generated</th><th>Expires</th></tr></thead>
          <tbody>
            ${bills
              .map(
                b => `<tr>
                  <td><code>${esc(b.order_id)}</code></td>
                  <td>${b.stale ? '<span class="admin-badge warn">Stale</span>' : '<span class="admin-badge good">Current</span>'}</td>
                  <td class="admin-meta">${esc(when(b.generated_at))}</td>
                  <td class="admin-meta">${esc(when(b.expires_at))}</td>
                </tr>`
              )
              .join("")}
          </tbody>
        </table>
      </article>`;
  } catch (err) {
    renderError(el, err);
  }
}

// ─────────────────────────── Settings ───────────────────────────

export async function loadSettings() {
  const el = $("admin-panel-settings");
  el.innerHTML = `<div class="admin-empty">Loading…</div>`;
  try {
    const s = await adminFetch("/settings");
    el.innerHTML = `
      <article class="admin-card">
        <header class="admin-card-head"><strong>Shop details</strong>
          <div class="admin-meta">printed on every bill</div>
        </header>
        <div class="admin-form">
          <label>Shop name<input data-field="shop_name" value="${esc(s.shop_name)}" /></label>
          <label>Mobile<input data-field="shop_mobile" value="${esc(s.shop_mobile)}" /></label>
          <label>Address<input data-field="shop_address" value="${esc(s.shop_address)}" /></label>
          <label>UPI ID<input data-field="upi_id" value="${esc(s.upi_id)}" placeholder="name@bank" /></label>
          <button class="btn-small" data-act="save-settings" type="button">Save</button>
        </div>
      </article>`;

    el.querySelector('[data-act="save-settings"]').addEventListener("click", async () => {
      const read = f => el.querySelector(`[data-field="${f}"]`).value.trim();
      await mutate(
        () =>
          adminFetch("/settings", {
            method: "PUT",
            body: JSON.stringify({
              shop_name: read("shop_name"),
              shop_mobile: read("shop_mobile"),
              shop_address: read("shop_address"),
              upi_id: read("upi_id")
            })
          }),
        loadSettings,
        "Settings saved."
      );
    });
  } catch (err) {
    renderError(el, err);
  }
}
