/**
 * Suppliers page — directory, purchases, modals
 */

import { $, auth, API } from "./config.js";
import { showToast, escapeHtml } from "./ui.js";

let currentSupplierData = { purchases: [], month_total: 0, month_items: 0 };
let currentSupplierSort = 'recent';
let savedSuppliers = [];

// ── Tab Switching ──
document.querySelectorAll('.supplier-tab').forEach(tab => {
  tab.addEventListener('click', () => {
    document.querySelectorAll('.supplier-tab').forEach(t => t.classList.remove('active'));
    tab.classList.add('active');
    const target = tab.dataset.tab;
    document.querySelectorAll('.supplier-tab-content').forEach(c => c.classList.add('hidden'));
    $(`supplier-tab-${target}`).classList.remove('hidden');
  });
});

// ── Load Saved Suppliers (Directory) ──
export async function loadSavedSuppliers() {
  const listEl = $('supplier-directory-list');
  listEl.innerHTML = '<div class="inventory-empty">Loading suppliers…</div>';
  try {
    const token = await auth.currentUser.getIdToken();
    const res = await fetch(`${API}/suppliers/list`, {
      headers: { Authorization: `Bearer ${token}` }
    });
    const data = await res.json();
    savedSuppliers = data.suppliers || [];
    renderSavedSuppliers();
  } catch {
    listEl.innerHTML = '<div class="inventory-empty">Could not load suppliers.</div>';
  }
}

function renderSavedSuppliers() {
  const listEl = $('supplier-directory-list');
  if (savedSuppliers.length === 0) {
    listEl.innerHTML = '<div class="inventory-empty">No suppliers added yet.<br>Tap + to add your first supplier.</div>';
    return;
  }

  let html = '';
  for (const s of savedSuppliers) {
    const mobileLine = s.mobile ? `<div class="supplier-dir-mobile">📱 ${escapeHtml(s.mobile)}</div>` : '';
    const gstLine = s.gst_number ? `<div class="supplier-dir-gst">GST: ${escapeHtml(s.gst_number)}</div>` : '';

    html += `<div class="supplier-dir-card" data-id="${s.id}" data-name="${escapeHtml(s.name)}">
      <div class="supplier-dir-header">
        <div class="supplier-dir-info">
          <div class="supplier-dir-name">${escapeHtml(s.name)}</div>
          ${mobileLine}
          ${gstLine}
        </div>
        <div class="supplier-dir-actions">
          <button class="supplier-add-purchase-btn" data-name="${escapeHtml(s.name)}" aria-label="Add purchase" title="Add purchase">＋</button>
          <button class="supplier-expand-btn" data-id="${s.id}" aria-label="Show purchases">▾</button>
          <button class="supplier-delete-btn" data-id="${s.id}" data-name="${escapeHtml(s.name)}" aria-label="Delete supplier">🗑️</button>
        </div>
      </div>
      <div class="supplier-dir-purchases hidden" id="supplier-purchases-${s.id}">
        <div class="inventory-empty">Loading…</div>
      </div>
    </div>`;
  }
  listEl.innerHTML = html;

  // Wire expand buttons
  listEl.querySelectorAll('.supplier-expand-btn').forEach(btn => {
    btn.addEventListener('click', () => toggleSupplierPurchases(btn.dataset.id));
  });

  // Wire delete buttons (opens custom modal)
  listEl.querySelectorAll('.supplier-delete-btn').forEach(btn => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      openDeleteSupplierModal(btn.dataset.id, btn.dataset.name);
    });
  });

  // Wire add-purchase buttons
  listEl.querySelectorAll('.supplier-add-purchase-btn').forEach(btn => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      openPurchaseModal(btn.dataset.name);
    });
  });
}

async function toggleSupplierPurchases(supplierId) {
  const panel = $(`supplier-purchases-${supplierId}`);
  const card = panel.closest('.supplier-dir-card');
  const btn = card.querySelector('.supplier-expand-btn');

  if (!panel.classList.contains('hidden')) {
    panel.classList.add('hidden');
    card.classList.remove('expanded');
    btn.textContent = '▾';
    return;
  }

  panel.classList.remove('hidden');
  card.classList.add('expanded');
  btn.textContent = '▴';

  // Find supplier name
  const supplierName = card.dataset.name;

  // Filter purchases by this supplier
  const purchases = (currentSupplierData.purchases || []).filter(
    p => (p.supplier_name || '').toLowerCase() === supplierName.toLowerCase()
  );

  if (purchases.length === 0) {
    panel.innerHTML = '<div class="inventory-empty" style="padding:10px 0;">No purchases recorded for this supplier.</div>';
    return;
  }

  let html = '<div class="supplier-purchase-list">';
  for (const p of purchases) {
    const dateStr = p.timestamp ? formatPurchaseDate(p.timestamp) : '';
    const itemDesc = `${p.item_name ? p.item_name.charAt(0).toUpperCase() + p.item_name.slice(1) : ''} × ${p.quantity}`;
    html += `<div class="supplier-purchase-row">
      <span class="supplier-purchase-item">${escapeHtml(itemDesc)}</span>
      <span class="supplier-purchase-amount">₹${(p.amount || 0).toLocaleString('en-IN')}</span>
      <span class="supplier-purchase-date">${dateStr}</span>
    </div>`;
  }
  html += '</div>';
  panel.innerHTML = html;
}

// ── Delete Supplier (custom modal) ──
let pendingDeleteSupplierId = null;

function openDeleteSupplierModal(id, name) {
  pendingDeleteSupplierId = id;
  $('supplier-delete-message').textContent = `Are you sure you want to delete "${name}"? This cannot be undone.`;
  $('supplier-delete-modal').classList.add('open');
}

$('supplier-delete-cancel').addEventListener('click', () => {
  pendingDeleteSupplierId = null;
  $('supplier-delete-modal').classList.remove('open');
});

$('supplier-delete-confirm').addEventListener('click', async () => {
  if (!pendingDeleteSupplierId) return;
  const id = pendingDeleteSupplierId;
  const btn = $('supplier-delete-confirm');
  btn.innerHTML = '<div class="spinner"></div>';
  btn.disabled = true;

  try {
    const token = await auth.currentUser.getIdToken();
    const res = await fetch(`${API}/suppliers/${id}`, {
      method: 'DELETE',
      headers: { Authorization: `Bearer ${token}` }
    });
    const data = await res.json();
    if (data.status === 'success') {
      showToast('✅ ' + data.message);
      loadSavedSuppliers();
      loadSuppliers();  // refresh summary cards + purchase history
    } else {
      showToast('❌ ' + (data.detail || 'Failed to delete.'));
    }
  } catch {
    showToast('❌ Could not connect to server.');
  } finally {
    btn.textContent = 'Delete';
    btn.disabled = false;
    pendingDeleteSupplierId = null;
    $('supplier-delete-modal').classList.remove('open');
  }
});

// ── Add Supplier Modal ──
$('supplier-add-btn').addEventListener('click', () => {
  $('supplier-add-modal').classList.add('open');
  $('supplier-modal-name').value = '';
  $('supplier-modal-mobile').value = '';
  $('supplier-modal-gst').value = '';
  setTimeout(() => $('supplier-modal-name').focus(), 100);
});

$('supplier-modal-cancel').addEventListener('click', () => {
  $('supplier-add-modal').classList.remove('open');
});

$('supplier-modal-save').addEventListener('click', async () => {
  const name = $('supplier-modal-name').value.trim();
  const mobile = $('supplier-modal-mobile').value.trim();
  const gst = $('supplier-modal-gst').value.trim();

  if (!name) {
    showToast('❌ Supplier name is required.');
    return;
  }

  const btn = $('supplier-modal-save');
  btn.innerHTML = '<div class="spinner"></div>';
  btn.disabled = true;

  try {
    const token = await auth.currentUser.getIdToken();
    const res = await fetch(`${API}/suppliers/add`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${token}`
      },
      body: JSON.stringify({ name, mobile: mobile ? `+91${mobile}` : '', gst_number: gst })
    });
    const data = await res.json();
    if (data.status === 'success') {
      showToast('✅ ' + data.message);
      $('supplier-add-modal').classList.remove('open');
      loadSavedSuppliers();
    } else {
      showToast('❌ ' + (data.detail || 'Failed to add supplier.'));
    }
  } catch {
    showToast('❌ Could not connect to server.');
  } finally {
    btn.textContent = 'Add Supplier';
    btn.disabled = false;
  }
});

// ── Manual Purchase Modal ──
function openPurchaseModal(supplierName) {
  $('purchase-modal-supplier').value = supplierName || '';
  $('purchase-modal-item').value = '';
  $('purchase-modal-qty').value = '';
  $('purchase-modal-amount').value = '';
  $('supplier-purchase-modal').classList.add('open');
  setTimeout(() => $('purchase-modal-item').focus(), 100);
}

$('purchase-modal-cancel').addEventListener('click', () => {
  $('supplier-purchase-modal').classList.remove('open');
});

$('purchase-modal-save').addEventListener('click', async () => {
  const supplier = $('purchase-modal-supplier').value.trim();
  const item = $('purchase-modal-item').value.trim();
  const qty = parseInt($('purchase-modal-qty').value) || 0;
  const amount = parseFloat($('purchase-modal-amount').value) || 0;

  if (!supplier || !item || qty <= 0) {
    showToast('❌ Please fill in supplier, item, and quantity.');
    return;
  }

  const btn = $('purchase-modal-save');
  btn.innerHTML = '<div class="spinner"></div>';
  btn.disabled = true;

  try {
    const token = await auth.currentUser.getIdToken();
    const res = await fetch(`${API}/suppliers/purchase`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${token}`
      },
      body: JSON.stringify({
        supplier_name: supplier,
        item_name: item,
        quantity: qty,
        amount: amount,
        proof_image_url: ''
      })
    });
    const data = await res.json();
    if (data.status === 'success') {
      showToast('✅ ' + data.message);
      $('supplier-purchase-modal').classList.remove('open');
      loadSuppliers();
    } else {
      showToast('❌ ' + (data.detail || 'Failed to add purchase.'));
    }
  } catch {
    showToast('❌ Could not connect to server.');
  } finally {
    btn.textContent = 'Add Purchase';
    btn.disabled = false;
  }
});

// ── Purchase History (existing, now in Purchases tab) ──
export async function loadSuppliers() {
  const supplierList = $('supplier-list');
  supplierList.innerHTML = '<div class="inventory-empty">Loading purchases…</div>';
  try {
    const token = await auth.currentUser.getIdToken();
    const res = await fetch(`${API}/suppliers`, {
      headers: { Authorization: `Bearer ${token}` }
    });
    const data = await res.json();
    currentSupplierData = data;
    $('supplier-month-total').textContent = `₹${(data.month_total || 0).toLocaleString('en-IN')}`;
    $('supplier-month-items').textContent = data.month_items || 0;
    renderSupplierList();
  } catch {
    supplierList.innerHTML = '<div class="inventory-empty">Could not load purchases.</div>';
  }
}

function renderSupplierList() {
  const supplierList = $('supplier-list');
  let purchases = [...(currentSupplierData.purchases || [])];

  if (purchases.length === 0) {
    supplierList.innerHTML = '<div class="inventory-empty">No purchases recorded yet.<br>Use voice to add purchases.</div>';
    return;
  }

  // Sort
  if (currentSupplierSort === 'name-asc') {
    purchases.sort((a, b) => (a.supplier_name || '').localeCompare(b.supplier_name || ''));
  } else if (currentSupplierSort === 'name-desc') {
    purchases.sort((a, b) => (b.supplier_name || '').localeCompare(a.supplier_name || ''));
  } else if (currentSupplierSort === 'amount-desc') {
    purchases.sort((a, b) => (b.amount || 0) - (a.amount || 0));
  } else {
    purchases.sort((a, b) => (b.timestamp || 0) - (a.timestamp || 0));
  }

  let html = '';
  for (const p of purchases) {
    const dateStr = p.timestamp ? formatPurchaseDate(p.timestamp) : '';
    const itemDesc = `${p.item_name ? p.item_name.charAt(0).toUpperCase() + p.item_name.slice(1) : ''} x ${p.quantity}`;
    html += `<div class="supplier-card">
      <div class="supplier-card-info">
        <div class="supplier-card-name">${escapeHtml(p.supplier_name)}</div>
        <div class="supplier-card-items">${escapeHtml(itemDesc)}</div>
      </div>
      <div class="supplier-card-right">
        <div class="supplier-card-amount">₹${(p.amount || 0).toLocaleString('en-IN')}</div>
        <div class="supplier-card-date">${dateStr}</div>
      </div>
    </div>`;
  }
  supplierList.innerHTML = html;
}

function formatPurchaseDate(tsMs) {
  const d = new Date(tsMs);
  const now = new Date();
  if (d.toDateString() === now.toDateString()) return 'Today';
  const yesterday = new Date(now); yesterday.setDate(now.getDate() - 1);
  if (d.toDateString() === yesterday.toDateString()) return 'Yesterday';
  return d.toLocaleDateString('en-IN', { day: 'numeric', month: 'short' });
}

$('supplier-sort').addEventListener('change', (e) => {
  currentSupplierSort = e.target.value;
  renderSupplierList();
});

$('supplier-refresh-btn').addEventListener('click', () => {
  const btn = $('supplier-refresh-btn');
  btn.classList.add('spinning');
  Promise.all([loadSuppliers(), loadSavedSuppliers()]).finally(() => {
    setTimeout(() => btn.classList.remove('spinning'), 800);
  });
});
