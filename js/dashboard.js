/**
 * Dashboard — Live inventory grid, edit/delete modals, sorting
 */

import { $, auth, API } from "./config.js";
import { showToast } from "./ui.js";

let currentInventory = [];
let currentSort = 'name-asc';

export async function loadDashboardInventory() {
  const inventoryGrid = $("inventory-grid");
  inventoryGrid.innerHTML = '<div class="inventory-empty">Loading inventory…</div>';
  try {
    const token = await auth.currentUser.getIdToken();
    const res = await fetch(`${API}/inventory`, {
      headers: { Authorization: `Bearer ${token}` }
    });
    const data = await res.json();
    currentInventory = data.inventory || [];
    renderDashboardInventory();
  } catch {
    inventoryGrid.innerHTML = '<div class="inventory-empty">Could not load inventory.</div>';
  }
}

function renderDashboardInventory() {
  const inventoryGrid = $("inventory-grid");
  if (currentInventory.length === 0) {
    inventoryGrid.innerHTML = '<div class="inventory-empty">No items in inventory yet.<br>Use Voice to add stock.</div>';
    return;
  }

  let items = [...currentInventory];
  if (currentSort === 'name-asc') {
    items.sort((a, b) => (a.item || '').localeCompare(b.item || ''));
  } else if (currentSort === 'name-desc') {
    items.sort((a, b) => (b.item || '').localeCompare(a.item || ''));
  } else if (currentSort === 'stock-asc') {
    items.sort((a, b) => (a.quantity || 0) - (b.quantity || 0));
  } else if (currentSort === 'stock-desc') {
    items.sort((a, b) => (b.quantity || 0) - (a.quantity || 0));
  } else if (currentSort === 'recent') {
    items.sort((a, b) => (b.updated_at || 0) - (a.updated_at || 0));
  }

  let html = '';
  for (const item of items) {
    const qty = item.quantity ?? 0;
    const price = item.price ?? 0;
    let qtyClass = '';
    if (qty === 0) qtyClass = 'out-of-stock';
    else if (qty <= 5) qtyClass = 'low-stock';

    const priceHtml = price > 0
      ? `<div class="inventory-tile-price">Total: ₹${(qty * price).toLocaleString('en-IN')} <span style="font-weight: 500; font-size: 0.85em; opacity: 0.8;">(₹${price.toLocaleString('en-IN')}/item)</span></div>`
      : '';

    html += `<div class="inventory-tile" data-item-id="${item.item}" data-item-qty="${qty}" data-item-price="${price}">
      <div class="inventory-tile-name">${item.item}</div>
      <div class="inventory-tile-qty ${qtyClass}">${qty}</div>
      ${priceHtml}
      <div class="inventory-tile-edit-hint">Tap to edit</div>
    </div>`;
  }
  html += `<div class="inventory-total">${items.length} item${items.length !== 1 ? 's' : ''} in stock</div>`;
  inventoryGrid.innerHTML = html;

  // Wire up tile click → open edit modal
  inventoryGrid.querySelectorAll('.inventory-tile').forEach(tile => {
    tile.addEventListener('click', () => {
      openInventoryEditModal(
        tile.dataset.itemId,
        parseInt(tile.dataset.itemQty) || 0,
        parseFloat(tile.dataset.itemPrice) || 0
      );
    });
  });
}

// ── Inventory Edit Modal ──
function openInventoryEditModal(itemId, qty, price) {
  $("inventory-edit-original-id").value = itemId;
  $("inventory-edit-name").value = itemId;
  $("inventory-edit-qty").value = qty;
  $("inventory-edit-price").value = price || '';
  $("inventory-edit-modal").classList.add("open");
  setTimeout(() => $("inventory-edit-name").focus(), 100);
}

$("inventory-edit-cancel").addEventListener("click", () => {
  $("inventory-edit-modal").classList.remove("open");
});

$("inventory-edit-save").addEventListener("click", async () => {
  const originalId = $("inventory-edit-original-id").value;
  const newName = $("inventory-edit-name").value.trim();
  const newQty = parseInt($("inventory-edit-qty").value);
  const newPrice = parseFloat($("inventory-edit-price").value) || 0;

  if (!newName) {
    showToast('❌ Item name is required.');
    return;
  }
  if (isNaN(newQty) || newQty < 0) {
    showToast('❌ Quantity must be 0 or greater.');
    return;
  }

  const btn = $("inventory-edit-save");
  btn.innerHTML = '<div class="spinner"></div>';
  btn.disabled = true;

  try {
    const token = await auth.currentUser.getIdToken();
    const res = await fetch(`${API}/inventory/${encodeURIComponent(originalId)}`, {
      method: 'PUT',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${token}`
      },
      body: JSON.stringify({
        item: newName,
        quantity: newQty,
        price: newPrice
      })
    });
    const data = await res.json();
    if (res.ok && data.status === 'success') {
      showToast('✅ ' + data.message);
      $("inventory-edit-modal").classList.remove("open");
      loadDashboardInventory();
    } else {
      showToast('❌ ' + (data.detail || data.message || 'Failed to update.'));
    }
  } catch {
    showToast('❌ Could not connect to server.');
  } finally {
    btn.textContent = 'Save';
    btn.disabled = false;
  }
});

$("inventory-edit-delete").addEventListener("click", async () => {
  const originalId = $("inventory-edit-original-id").value;
  const btn = $("inventory-edit-delete");

  // Show custom confirmation modal
  $("inventory-delete-message").textContent = `Delete "${originalId}" from inventory? This cannot be undone.`;
  $("inventory-delete-modal").classList.add("open");

  const confirmed = await new Promise(resolve => {
    const confirmBtn = $("inventory-delete-confirm");
    const cancelBtn = $("inventory-delete-cancel");

    const cleanup = () => {
      confirmBtn.removeEventListener("click", onConfirm);
      cancelBtn.removeEventListener("click", onCancel);
      $("inventory-delete-modal").classList.remove("open");
    };

    const onConfirm = () => { cleanup(); resolve(true); };
    const onCancel = () => { cleanup(); resolve(false); };

    confirmBtn.addEventListener("click", onConfirm);
    cancelBtn.addEventListener("click", onCancel);
  });

  if (!confirmed) return;

  btn.innerHTML = '<div class="spinner"></div>';
  btn.disabled = true;

  try {
    const token = await auth.currentUser.getIdToken();
    const res = await fetch(`${API}/inventory/${encodeURIComponent(originalId)}`, {
      method: 'DELETE',
      headers: { Authorization: `Bearer ${token}` }
    });
    const data = await res.json();
    if (res.ok && data.status === 'success') {
      showToast('🗑️ ' + data.message);
      $("inventory-edit-modal").classList.remove("open");
      loadDashboardInventory();
    } else {
      showToast('❌ ' + (data.detail || 'Failed to delete.'));
    }
  } catch {
    showToast('❌ Could not connect to server.');
  } finally {
    btn.textContent = '🗑️ Delete';
    btn.disabled = false;
  }
});

$("inventory-sort").addEventListener("change", (e) => {
  currentSort = e.target.value;
  renderDashboardInventory();
});

$("dashboard-refresh-btn").addEventListener("click", () => {
  const btn = $("dashboard-refresh-btn");
  btn.classList.add("spinning");
  loadDashboardInventory().finally(() => {
    setTimeout(() => btn.classList.remove("spinning"), 800);
  });
});
