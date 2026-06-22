/**
 * Customer Orders page — orders grouped by session, editable line items,
 * placeholder bill generation. Mirrors the Ledger page structure.
 */

import { $, auth, API } from "./config.js";
import { showToast, escapeHtml, capitalize } from "./ui.js";

// WhatsApp glyph — same artwork as the Ledger "Send Reminder" button.
const WA_SVG = '<svg width="18" height="18" viewBox="0 0 24 24" fill="#fff"><path d="M17.472 14.382c-.297-.149-1.758-.867-2.03-.967-.273-.099-.471-.148-.67.15-.197.297-.767.966-.94 1.164-.173.199-.347.223-.644.075-.297-.15-1.255-.463-2.39-1.475-.883-.788-1.48-1.761-1.653-2.059-.173-.297-.018-.458.13-.606.134-.133.298-.347.446-.52.149-.174.198-.298.298-.497.099-.198.05-.371-.025-.52-.075-.149-.669-1.612-.916-2.207-.242-.579-.487-.5-.669-.51-.173-.008-.371-.01-.57-.01-.198 0-.52.074-.792.372-.272.297-1.04 1.016-1.04 2.479 0 1.462 1.065 2.875 1.213 3.074.149.198 2.096 3.2 5.077 4.487.709.306 1.262.489 1.694.625.712.227 1.36.195 1.871.118.571-.085 1.758-.719 2.006-1.413.248-.694.248-1.289.173-1.413-.074-.124-.272-.198-.57-.347m-5.421 7.403h-.004a9.87 9.87 0 01-5.031-1.378l-.361-.214-3.741.982.998-3.648-.235-.374a9.86 9.86 0 01-1.51-5.26c.001-5.45 4.436-9.884 9.888-9.884 2.64 0 5.122 1.03 6.988 2.898a9.825 9.825 0 012.893 6.994c-.003 5.45-4.437 9.884-9.885 9.884m8.413-18.297A11.815 11.815 0 0012.05 0C5.495 0 .16 5.335.157 11.892c0 2.096.547 4.142 1.588 5.945L.057 24l6.305-1.654a11.882 11.882 0 005.683 1.448h.005c6.554 0 11.89-5.335 11.893-11.893a11.821 11.821 0 00-3.48-8.413z"/></svg>';

// Pencil glyph — diagonal with the writing tip at the bottom-left (Feather "edit-2").
const PENCIL_SVG = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 3a2.828 2.828 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5L17 3z"/></svg>';

let currentOrdersData = { orders: [], order_count: 0, total_value: 0 };
let currentOrdersSort = 'recent';
let ordersSearchQuery = '';
let inventoryPrices = {};            // { itemNameLower: price }

// Pending modal state
let orderEditState = null;           // { mode:'edit'|'add', itemId, orderId, customerName, customerModifier }
let orderDeleteState = null;         // { type:'item'|'order', id, label }

const inr = (n) => `₹${(Number(n) || 0).toLocaleString('en-IN')}`;

export async function loadOrders() {
  const listEl = $('orders-list');
  listEl.innerHTML = '<div class="inventory-empty">Loading orders…</div>';
  try {
    const token = await auth.currentUser.getIdToken();
    const [ordersRes, invRes] = await Promise.all([
      fetch(`${API}/orders`, { headers: { Authorization: `Bearer ${token}` } }),
      fetch(`${API}/inventory`, { headers: { Authorization: `Bearer ${token}` } }),
    ]);
    currentOrdersData = await ordersRes.json();

    // Build inventory price map + datalist for price defaults / autocomplete
    inventoryPrices = {};
    let datalistHtml = '';
    try {
      const inv = await invRes.json();
      for (const it of (inv.inventory || [])) {
        const name = (it.item || '').toLowerCase();
        if (!name) continue;
        inventoryPrices[name] = it.price || 0;
        datalistHtml += `<option value="${escapeHtml(capitalize(it.item))}"></option>`;
      }
    } catch { /* inventory is best-effort — defaults just won't prefill */ }
    $('orders-item-datalist').innerHTML = datalistHtml;

    $('orders-count').textContent = currentOrdersData.order_count || 0;
    $('orders-total-value').textContent = inr(currentOrdersData.total_value || 0);
    renderOrders();
  } catch {
    listEl.innerHTML = '<div class="inventory-empty">Could not load orders.</div>';
  }
}

function renderOrders() {
  const listEl = $('orders-list');
  let orders = [...(currentOrdersData.orders || [])];

  if (ordersSearchQuery) {
    const q = ordersSearchQuery.toLowerCase();
    orders = orders.filter(o =>
      (o.customer_name || '').toLowerCase().includes(q) ||
      (o.customer_modifier || '').toLowerCase().includes(q)
    );
  }

  if (orders.length === 0) {
    listEl.innerHTML = '<div class="inventory-empty">No orders found.<br>Use Voice or the + button to add an order.</div>';
    return;
  }

  if (currentOrdersSort === 'name-asc') {
    orders.sort((a, b) => (a.customer_name || '').localeCompare(b.customer_name || ''));
  } else if (currentOrdersSort === 'amount-desc') {
    orders.sort((a, b) => (b.total || 0) - (a.total || 0));
  }
  // 'recent' already sorted by the API

  let html = '';
  for (const o of orders) {
    const displayName = o.customer_modifier
      ? `${capitalize(o.customer_name)} (${o.customer_modifier})`
      : capitalize(o.customer_name);
    const lastOrder = o.last_order ? `Last order ${formatOrderDate(o.last_order)}` : '';

    let itemsHtml = '';
    for (const item of (o.items || [])) {
      const qty = item.quantity || 0;
      const price = item.price || 0;
      itemsHtml += `<div class="order-item-row" data-id="${escapeHtml(item.id)}" data-item="${escapeHtml(item.item)}" data-qty="${qty}" data-price="${price}">
        <div class="order-item-info">
          <div class="order-item-name">${escapeHtml(capitalize(item.item))}</div>
          <div class="order-item-meta">${qty} × ${inr(price)} = ${inr(item.amount)}</div>
        </div>
        <div class="order-item-actions">
          <button class="order-item-edit" title="Edit">${PENCIL_SVG}</button>
          <button class="order-item-remove" title="Remove" data-label="${escapeHtml(capitalize(item.item))}">✕</button>
        </div>
      </div>`;
    }

    html += `<div class="ledger-customer-card order-card"
        data-order-id="${escapeHtml(o.order_id)}"
        data-customer="${escapeHtml(o.customer_name)}"
        data-modifier="${escapeHtml(o.customer_modifier || '')}">
      <div class="ledger-card-header" onclick="this.parentElement.classList.toggle('expanded')">
        <div class="ledger-card-info">
          <div class="ledger-card-name">${escapeHtml(displayName)}</div>
          <div class="ledger-card-subtitle">${lastOrder}</div>
        </div>
        <div class="ledger-card-right">
          <div class="ledger-card-amount due">${inr(o.total || 0)}</div>
        </div>
      </div>
      <div class="ledger-card-details">
        <div class="order-items-list">${itemsHtml}</div>
        <div class="order-item-actions-row">
          <button class="btn btn-outline order-add-item-btn">+ Add item</button>
          <button class="btn btn-outline order-delete-order-btn">🗑️ Delete order</button>
        </div>
        <div class="order-bill-actions">
          <button class="btn btn-primary order-generate-bill-btn">🧾 Generate Bill</button>
          <button class="btn btn-whatsapp order-send-bill-btn">${WA_SVG}Send Bill on WhatsApp</button>
        </div>
      </div>
    </div>`;
  }
  listEl.innerHTML = html;
  wireOrderCards(listEl);
}

function wireOrderCards(listEl) {
  // Edit a line item
  listEl.querySelectorAll('.order-item-edit').forEach(btn => {
    btn.addEventListener('click', () => {
      const row = btn.closest('.order-item-row');
      const card = btn.closest('.order-card');
      openEditModal({
        mode: 'edit',
        itemId: row.dataset.id,
        orderId: card.dataset.orderId,
        customerName: card.dataset.customer,
        customerModifier: card.dataset.modifier,
        item: row.dataset.item,
        qty: row.dataset.qty,
        price: row.dataset.price,
      });
    });
  });

  // Remove a line item
  listEl.querySelectorAll('.order-item-remove').forEach(btn => {
    btn.addEventListener('click', () => {
      const row = btn.closest('.order-item-row');
      openDeleteModal({ type: 'item', id: row.dataset.id, label: `Remove "${btn.dataset.label}" from this order?` });
    });
  });

  // Add an item to an existing order
  listEl.querySelectorAll('.order-add-item-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const card = btn.closest('.order-card');
      openEditModal({
        mode: 'add',
        orderId: card.dataset.orderId,
        customerName: card.dataset.customer,
        customerModifier: card.dataset.modifier,
      });
    });
  });

  // Delete whole order
  listEl.querySelectorAll('.order-delete-order-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const card = btn.closest('.order-card');
      const name = capitalize(card.dataset.customer);
      openDeleteModal({ type: 'order', id: card.dataset.orderId, label: `Delete the entire order for ${name}?` });
    });
  });

  // Placeholder bill buttons
  listEl.querySelectorAll('.order-generate-bill-btn').forEach(btn => {
    btn.addEventListener('click', () => showToast('🧾 Bill generation coming soon.'));
  });
  listEl.querySelectorAll('.order-send-bill-btn').forEach(btn => {
    btn.addEventListener('click', () => showToast('📲 Send bill on WhatsApp coming soon.'));
  });
}

function formatOrderDate(isoStr) {
  if (!isoStr) return '';
  const d = new Date(isoStr);
  const now = new Date();
  if (d.toDateString() === now.toDateString()) return 'today';
  const yesterday = new Date(now); yesterday.setDate(now.getDate() - 1);
  if (d.toDateString() === yesterday.toDateString()) return 'yesterday';
  return d.toLocaleDateString('en-IN', { day: 'numeric', month: 'short' });
}

// ═══════ Edit / Add Item modal ═══════
function openEditModal(state) {
  orderEditState = state;
  $('order-edit-title').textContent = state.mode === 'add' ? 'Add Item' : 'Edit Item';
  $('order-edit-item').value = state.item ? capitalize(state.item) : '';
  $('order-edit-qty').value = state.qty || '';
  $('order-edit-price').value = (state.price !== undefined && state.price !== '') ? state.price : '';
  $('order-edit-save').textContent = state.mode === 'add' ? 'Add' : 'Save';
  $('order-edit-modal').classList.add('open');
  setTimeout(() => $('order-edit-item').focus(), 100);
}

// Default the price from inventory when the item name is set/changed.
function prefillPriceFromInventory(itemInput, priceInput) {
  const name = (itemInput.value || '').trim().toLowerCase();
  if (name in inventoryPrices) priceInput.value = inventoryPrices[name];
}

$('order-edit-item').addEventListener('change', () => {
  prefillPriceFromInventory($('order-edit-item'), $('order-edit-price'));
});

$('order-edit-cancel').addEventListener('click', () => {
  $('order-edit-modal').classList.remove('open');
  orderEditState = null;
});

$('order-edit-save').addEventListener('click', async () => {
  if (!orderEditState) return;
  const item = $('order-edit-item').value.trim();
  const qty = parseInt($('order-edit-qty').value) || 0;
  const price = parseFloat($('order-edit-price').value) || 0;

  if (!item || qty <= 0) {
    showToast('❌ Please fill item and quantity.');
    return;
  }

  const btn = $('order-edit-save');
  const label = btn.textContent;
  btn.innerHTML = '<div class="spinner"></div>';
  btn.disabled = true;

  try {
    const token = await auth.currentUser.getIdToken();
    let res;
    if (orderEditState.mode === 'add') {
      res = await fetch(`${API}/orders/${encodeURIComponent(orderEditState.orderId)}/items`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
        body: JSON.stringify({
          item, quantity: qty, price,
          customer_name: orderEditState.customerName,
          customer_modifier: orderEditState.customerModifier,
        }),
      });
    } else {
      res = await fetch(`${API}/orders/item/${encodeURIComponent(orderEditState.itemId)}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
        body: JSON.stringify({ item, quantity: qty, price }),
      });
    }
    const data = await res.json();
    if (data.status === 'success') {
      showToast('✅ ' + data.message);
      $('order-edit-modal').classList.remove('open');
      orderEditState = null;
      loadOrders();
    } else {
      showToast('❌ ' + (data.detail || 'Failed to save item.'));
    }
  } catch {
    showToast('❌ Could not connect to server.');
  } finally {
    btn.textContent = label;
    btn.disabled = false;
  }
});

// ═══════ Delete confirm modal (item or whole order) ═══════
function openDeleteModal(state) {
  orderDeleteState = state;
  $('order-delete-message').textContent = state.label;
  $('order-delete-modal').classList.add('open');
}

$('order-delete-cancel').addEventListener('click', () => {
  $('order-delete-modal').classList.remove('open');
  orderDeleteState = null;
});

$('order-delete-confirm').addEventListener('click', async () => {
  if (!orderDeleteState) return;
  const { type, id } = orderDeleteState;
  const btn = $('order-delete-confirm');
  btn.innerHTML = '<div class="spinner"></div>';
  btn.disabled = true;

  try {
    const token = await auth.currentUser.getIdToken();
    const url = type === 'order'
      ? `${API}/orders/${encodeURIComponent(id)}`
      : `${API}/orders/item/${encodeURIComponent(id)}`;
    const res = await fetch(url, { method: 'DELETE', headers: { Authorization: `Bearer ${token}` } });
    const data = await res.json();
    if (data.status === 'success') {
      showToast('✅ ' + data.message);
      loadOrders();
    } else {
      showToast('❌ ' + (data.detail || 'Failed to delete.'));
    }
  } catch {
    showToast('❌ Could not connect to server.');
  } finally {
    btn.textContent = 'Delete';
    btn.disabled = false;
    orderDeleteState = null;
    $('order-delete-modal').classList.remove('open');
  }
});

// ═══════ New Order modal (dynamic item rows) ═══════
function addOrderItemRow(prefill = {}) {
  const container = $('order-items-container');
  const row = document.createElement('div');
  row.className = 'order-new-item-row';
  row.innerHTML = `
    <input type="text" class="order-new-item" list="orders-item-datalist" placeholder="Item" value="${escapeHtml(prefill.item || '')}" />
    <input type="number" class="order-new-qty" placeholder="Qty" min="1" value="${prefill.qty || ''}" />
    <input type="number" class="order-new-price" placeholder="₹" min="0" value="${prefill.price ?? ''}" />
    <button type="button" class="order-new-remove" title="Remove">✕</button>`;
  container.appendChild(row);

  const itemInput = row.querySelector('.order-new-item');
  const priceInput = row.querySelector('.order-new-price');
  itemInput.addEventListener('change', () => prefillPriceFromInventory(itemInput, priceInput));
  row.querySelector('.order-new-remove').addEventListener('click', () => row.remove());
}

$('orders-add-btn').addEventListener('click', () => {
  $('order-entry-customer').value = '';
  $('order-entry-modifier').value = '';
  $('order-items-container').innerHTML = '';
  addOrderItemRow();
  $('order-add-modal').classList.add('open');
  setTimeout(() => $('order-entry-customer').focus(), 100);
});

$('order-add-row-btn').addEventListener('click', () => addOrderItemRow());

$('order-modal-cancel').addEventListener('click', () => {
  $('order-add-modal').classList.remove('open');
});

$('order-modal-save').addEventListener('click', async () => {
  const customer = $('order-entry-customer').value.trim();
  const modifier = $('order-entry-modifier').value.trim();

  const items = [];
  $('order-items-container').querySelectorAll('.order-new-item-row').forEach(row => {
    const item = row.querySelector('.order-new-item').value.trim();
    const qty = parseInt(row.querySelector('.order-new-qty').value) || 0;
    const price = parseFloat(row.querySelector('.order-new-price').value) || 0;
    if (item && qty > 0) items.push({ item, quantity: qty, price });
  });

  if (!customer) { showToast('❌ Please enter a customer name.'); return; }
  if (items.length === 0) { showToast('❌ Add at least one item with a quantity.'); return; }

  const btn = $('order-modal-save');
  btn.innerHTML = '<div class="spinner"></div>';
  btn.disabled = true;

  try {
    const token = await auth.currentUser.getIdToken();
    const res = await fetch(`${API}/orders`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
      body: JSON.stringify({ customer_name: customer, customer_modifier: modifier, items }),
    });
    const data = await res.json();
    if (data.status === 'success') {
      showToast('✅ ' + data.message);
      $('order-add-modal').classList.remove('open');
      loadOrders();
    } else {
      showToast('❌ ' + (data.detail || 'Failed to create order.'));
    }
  } catch {
    showToast('❌ Could not connect to server.');
  } finally {
    btn.textContent = 'Create Order';
    btn.disabled = false;
  }
});

// Search & sort
$('orders-search').addEventListener('input', (e) => {
  ordersSearchQuery = e.target.value;
  renderOrders();
});

$('orders-sort').addEventListener('change', (e) => {
  currentOrdersSort = e.target.value;
  renderOrders();
});
