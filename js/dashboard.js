/**
 * Dashboard — Live inventory grid, edit/delete modals, sorting
 */

import { $, auth, API } from "./config.js";
import { showToast, escapeHtml } from "./ui.js";
import {
  isCameraSupported, startCamera, stopCamera, isCameraRunning,
  captureFrame, cameraErrorMessage
} from "./camera.js";
import { compressImage } from "./image-compress.js";

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
    inventoryGrid.innerHTML = '<div class="inventory-empty">No items in inventory yet.<br>Tap + to add one, or use Voice.</div>';
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

    // loading="lazy" matters here — a 300-item grid would otherwise fire 300
    // parallel requests on page load.
    const thumb = item.thumb_url
      ? `<img class="inventory-tile-thumb" src="${escapeHtml(item.thumb_url)}" alt="" loading="lazy" decoding="async" />`
      : '<div class="inventory-tile-thumb-empty">📦</div>';

    html += `<div class="inventory-tile" data-item-id="${escapeHtml(item.item)}" data-item-qty="${qty}" data-item-price="${price}">
      ${thumb}
      <div class="inventory-tile-name">${escapeHtml(item.item)}</div>
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

// ── Add Inventory Item Modal ──
// The captured/picked photo lives only in this Blob and the object URL used to
// preview it. Nothing is written to the device: no downloads, no localStorage,
// no IndexedDB, and the in-app camera avoids the OS camera app entirely.
const MAX_PICK_BYTES = 12 * 1024 * 1024;   // reject absurd files before decoding
let addImageBlob = null;
let addPreviewUrl = null;

function setPickerState(state) {
  // state: "empty" | "live" | "preview"
  $("inventory-add-placeholder").classList.toggle("hidden", state !== "empty");
  $("inventory-add-video").classList.toggle("hidden", state !== "live");
  $("inventory-add-preview").classList.toggle("hidden", state !== "preview");
  $("inventory-add-pick-actions").classList.toggle("hidden", state !== "empty");
  $("inventory-add-shoot-actions").classList.toggle("hidden", state !== "live");
  $("inventory-add-retake-actions").classList.toggle("hidden", state !== "preview");
}

function clearAddPhoto() {
  addImageBlob = null;
  if (addPreviewUrl) {
    URL.revokeObjectURL(addPreviewUrl);   // release the in-memory blob
    addPreviewUrl = null;
  }
  $("inventory-add-preview").removeAttribute("src");
}

function showAddPreview(blob) {
  clearAddPhoto();
  addImageBlob = blob;
  addPreviewUrl = URL.createObjectURL(blob);
  $("inventory-add-preview").src = addPreviewUrl;
  setPickerState("preview");
}

function closeAddModal() {
  stopCamera($("inventory-add-video"));
  clearAddPhoto();
  $("inventory-add-modal").classList.remove("open");
}

function openInventoryAddModal() {
  ["inventory-add-name", "inventory-add-price", "inventory-add-cost",
   "inventory-add-qty", "inventory-add-category"].forEach(id => { $(id).value = ""; });
  $("inventory-add-unit").value = "pcs";
  clearAddPhoto();
  setPickerState("empty");
  $("inventory-add-modal").classList.add("open");
  setTimeout(() => $("inventory-add-name").focus(), 100);
}

$("inventory-add-btn").addEventListener("click", openInventoryAddModal);
$("inventory-add-cancel").addEventListener("click", closeAddModal);

$("inventory-add-camera-btn").addEventListener("click", async () => {
  if (!isCameraSupported()) {
    showToast("📷 Camera needs a secure (https) connection. Use Gallery instead.");
    return;
  }
  try {
    setPickerState("live");
    await startCamera($("inventory-add-video"));
  } catch (err) {
    setPickerState("empty");
    showToast("❌ " + cameraErrorMessage(err));
  }
});

$("inventory-add-shutter").addEventListener("click", async () => {
  try {
    const blob = await captureFrame($("inventory-add-video"));
    stopCamera($("inventory-add-video"));
    showAddPreview(blob);
  } catch {
    showToast("❌ Could not capture the photo. Try again.");
  }
});

$("inventory-add-cancel-shot").addEventListener("click", () => {
  stopCamera($("inventory-add-video"));
  setPickerState(addImageBlob ? "preview" : "empty");
});

$("inventory-add-retake").addEventListener("click", () => {
  clearAddPhoto();
  $("inventory-add-camera-btn").click();
});

$("inventory-add-remove-photo").addEventListener("click", () => {
  clearAddPhoto();
  setPickerState("empty");
});

$("inventory-add-gallery-btn").addEventListener("click", () => {
  stopCamera($("inventory-add-video"));
  $("inventory-add-file").click();
});

$("inventory-add-file").addEventListener("change", async e => {
  const file = e.target.files && e.target.files[0];
  e.target.value = "";                    // allow re-picking the same file
  if (!file) return;
  if (file.size > MAX_PICK_BYTES) {
    showToast("❌ That photo is too large.");
    return;
  }
  try {
    showAddPreview(await compressImage(file));
  } catch {
    showToast("❌ Could not read that photo. Try a JPG, PNG or WebP.");
  }
});

// Never leave the camera running when the tab is backgrounded — the hardware
// indicator staying lit looks like the app is still watching.
document.addEventListener("visibilitychange", () => {
  if (document.hidden && isCameraRunning()) {
    stopCamera($("inventory-add-video"));
    setPickerState(addImageBlob ? "preview" : "empty");
  }
});

$("inventory-add-save").addEventListener("click", async () => {
  const name = $("inventory-add-name").value.trim();
  const price = parseFloat($("inventory-add-price").value);

  if (!name) {
    showToast('❌ Item name is required.');
    return;
  }
  if (isNaN(price) || price < 0) {
    showToast('❌ Selling price is required.');
    return;
  }

  const btn = $("inventory-add-save");
  btn.innerHTML = '<div class="spinner"></div>';
  btn.disabled = true;
  let cooldownMs = 0;

  try {
    const token = await auth.currentUser.getIdToken();
    const form = new FormData();
    form.append("item", name);
    form.append("price", String(price));
    form.append("cost_price", String(parseFloat($("inventory-add-cost").value) || 0));
    form.append("unit", $("inventory-add-unit").value);
    form.append("quantity", String(parseInt($("inventory-add-qty").value) || 0));
    form.append("category", $("inventory-add-category").value.trim());
    // The filename is cosmetic — the server ignores it and writes to a uuid path.
    if (addImageBlob) form.append("image", addImageBlob, "item.webp");

    // No Content-Type header: the browser must set the multipart boundary itself.
    const res = await fetch(`${API}/inventory`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${token}` },
      body: form
    });

    if (res.status === 429) {
      const err = await res.json();
      const retryAfter = err.retry_after || 3;
      cooldownMs = Math.ceil(retryAfter) * 1000;
      showToast('⏳ ' + (err.message || `Too many uploads. Retry in ${Math.ceil(retryAfter)}s.`),
                Math.max(cooldownMs, 3000));
      return;
    }

    const data = await res.json();
    if (res.ok && data.status === 'success') {
      showToast('✅ ' + data.message);
      closeAddModal();
      loadDashboardInventory();
    } else {
      showToast('❌ ' + (data.detail || data.message || 'Could not add item.'));
    }
  } catch {
    showToast('❌ Could not connect to server.');
  } finally {
    btn.textContent = 'Save';
    // Mirror the server-side cooldown so a rate-limited user can't just retry
    // instantly, the way applyRecordCooldown() does for voice.
    if (cooldownMs > 0) {
      setTimeout(() => { btn.disabled = false; }, cooldownMs);
    } else {
      btn.disabled = false;
    }
  }
});

$("dashboard-refresh-btn").addEventListener("click", () => {
  const btn = $("dashboard-refresh-btn");
  btn.classList.add("spinning");
  loadDashboardInventory().finally(() => {
    setTimeout(() => btn.classList.remove("spinning"), 800);
  });
});
