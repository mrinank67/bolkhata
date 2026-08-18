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

// Pack units: quantity counts packs and price is per pack, so a dozen item is
// "5 dozen @ ₹100" — never 60 pieces at ₹8.33. "" and "pcs" are the plain
// per-piece case and keep the original wording ("₹100/item", bare qty).
const UNIT_ONE = { dozen: 'dozen', box: 'box', pack: 'pack' };
const UNIT_MANY = { dozen: 'dozen', box: 'boxes', pack: 'packs' };

function packUnit(unit) {
  const u = (unit || '').toLowerCase();
  return UNIT_ONE[u] ? u : '';
}

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

    // The stock value is qty × price in both cases: for a pack unit that is
    // packs × price-per-pack, so no ÷12 anywhere.
    const unit = packUnit(item.unit);
    const priceHtml = price > 0
      ? `<div class="inventory-tile-price">Total: ₹${(qty * price).toLocaleString('en-IN')} <span style="font-weight: 500; font-size: 0.85em; opacity: 0.8;">(₹${price.toLocaleString('en-IN')}/${unit ? UNIT_ONE[unit] : 'item'})</span></div>`
      : '';
    const unitChip = unit ? `<span class="inventory-tile-unit">${UNIT_MANY[unit]}</span>` : '';

    // loading="lazy" matters here — a 300-item grid would otherwise fire 300
    // parallel requests on page load.
    const thumb = item.thumb_url
      ? `<img class="inventory-tile-thumb" src="${escapeHtml(item.thumb_url)}" alt="" loading="lazy" decoding="async" />`
      : '<div class="inventory-tile-thumb-empty">📦</div>';

    html += `<div class="inventory-tile" data-item-id="${escapeHtml(item.item)}">
      ${thumb}
      <div class="inventory-tile-name">${escapeHtml(item.item)}</div>
      <div class="inventory-tile-qty ${qtyClass}">${qty}${unitChip}</div>
      ${priceHtml}
      <div class="inventory-tile-edit-hint">Tap to edit</div>
    </div>`;
  }
  html += `<div class="inventory-total">${items.length} item${items.length !== 1 ? 's' : ''} in stock</div>`;
  inventoryGrid.innerHTML = html;

  // Wire up tile click → open edit modal. The record is looked up rather than
  // read back off data-* attributes: the sheet needs the photo too, and a
  // download URL has no business round-tripping through the DOM.
  inventoryGrid.querySelectorAll('.inventory-tile').forEach(tile => {
    tile.addEventListener('click', () => {
      const item = currentInventory.find(i => i.item === tile.dataset.itemId);
      if (item) openInventoryEditModal(item);
    });
  });
}

// ── Item photo picker ──
// The captured/picked photo lives only in this Blob and the object URL used to
// preview it. Nothing is written to the device: no downloads, no localStorage,
// no IndexedDB, and the in-app camera avoids the OS camera app entirely.
//
// One factory drives both the add and the edit sheet — they have identical
// markup under different id prefixes — so a photo can be attached, replaced or
// removed in exactly the same way whether the item is new or already saved.
const MAX_PICK_BYTES = 12 * 1024 * 1024;   // reject absurd files before decoding

function createImagePicker(prefix) {
  const el = (suffix) => $(`${prefix}-${suffix}`);

  let blob = null;             // a freshly captured/picked photo, else null
  let objectUrl = null;        // revoked whenever that photo is dropped
  let startedWithPhoto = false;   // the item already had one when the sheet opened
  let showingExisting = false;    // ...and it is still the one on screen

  function setState(state) {
    // state: "empty" | "live" | "preview"
    el("placeholder").classList.toggle("hidden", state !== "empty");
    el("video").classList.toggle("hidden", state !== "live");
    el("preview").classList.toggle("hidden", state !== "preview");
    el("pick-actions").classList.toggle("hidden", state !== "empty");
    el("shoot-actions").classList.toggle("hidden", state !== "live");
    el("retake-actions").classList.toggle("hidden", state !== "preview");
  }

  /** Forget whatever photo is on screen, new or already saved. */
  function drop() {
    blob = null;
    showingExisting = false;
    if (objectUrl) {
      URL.revokeObjectURL(objectUrl);   // release the in-memory blob
      objectUrl = null;
    }
    el("preview").removeAttribute("src");
  }

  function show(newBlob) {
    drop();
    blob = newBlob;
    objectUrl = URL.createObjectURL(newBlob);
    el("preview").src = objectUrl;
    setState("preview");
  }

  el("camera-btn").addEventListener("click", async () => {
    if (!isCameraSupported()) {
      showToast("📷 Camera needs a secure (https) connection. Use Gallery instead.");
      return;
    }
    try {
      setState("live");
      await startCamera(el("video"));
    } catch (err) {
      setState("empty");
      showToast("❌ " + cameraErrorMessage(err));
    }
  });

  el("shutter").addEventListener("click", async () => {
    try {
      const shot = await captureFrame(el("video"));
      stopCamera(el("video"));
      show(shot);
    } catch {
      showToast("❌ Could not capture the photo. Try again.");
    }
  });

  el("cancel-shot").addEventListener("click", () => {
    stopCamera(el("video"));
    setState(blob ? "preview" : "empty");
  });

  el("retake").addEventListener("click", () => {
    drop();
    el("camera-btn").click();
  });

  el("remove-photo").addEventListener("click", () => {
    drop();
    setState("empty");
  });

  el("gallery-btn").addEventListener("click", () => {
    stopCamera(el("video"));
    el("file").click();
  });

  el("file").addEventListener("change", async e => {
    const file = e.target.files && e.target.files[0];
    e.target.value = "";                    // allow re-picking the same file
    if (!file) return;
    if (file.size > MAX_PICK_BYTES) {
      showToast("❌ That photo is too large.");
      return;
    }
    try {
      show(await compressImage(file));
    } catch {
      showToast("❌ Could not read that photo. Try a JPG, PNG or WebP.");
    }
  });

  return {
    /** Reset the picker. `existingUrl` is the saved photo, if the item has one. */
    open(existingUrl) {
      drop();
      startedWithPhoto = !!existingUrl;
      if (existingUrl) {
        showingExisting = true;
        el("preview").src = existingUrl;
        setState("preview");
      } else {
        setState("empty");
      }
    },

    close() {
      stopCamera(el("video"));
      drop();
    },

    /** Add this sheet's photo decision to the outgoing form: a new file, an
     *  explicit removal, or — the common case — nothing at all, which leaves a
     *  saved photo untouched. */
    attachTo(form) {
      // The filename is cosmetic — the server ignores it and writes to a uuid path.
      if (blob) form.append("image", blob, "item.webp");
      else if (startedWithPhoto && !showingExisting) form.append("remove_image", "true");
    },

    /** Called when the tab is backgrounded — see the listener below. */
    stopIfRunning() {
      if (!isCameraRunning()) return;
      stopCamera(el("video"));
      setState(blob || showingExisting ? "preview" : "empty");
    }
  };
}

const addPicker = createImagePicker("inventory-add");
const editPicker = createImagePicker("inventory-edit");

// Never leave the camera running when the tab is backgrounded — the hardware
// indicator staying lit looks like the app is still watching. camera.js holds a
// single stream, so at most one picker can actually have it.
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) return;
  addPicker.stopIfRunning();
  editPicker.stopIfRunning();
});

// ── Inventory Edit Modal ──
function openInventoryEditModal(item) {
  $("inventory-edit-original-id").value = item.item;
  $("inventory-edit-name").value = item.item;
  $("inventory-edit-qty").value = item.quantity ?? 0;
  $("inventory-edit-price").value = item.price || '';
  // The thumbnail, not the full-size image: the grid has already fetched and
  // cached it, so the saved photo appears the instant the sheet opens.
  editPicker.open(item.thumb_url || null);
  // Name the unit in the labels — editing "12" on a dozen item means 12 dozen,
  // and the shopkeeper should see that before typing.
  const u = packUnit(item.unit);
  $("inventory-edit-qty-label").textContent = u ? `Quantity (${UNIT_MANY[u]})` : 'Quantity';
  $("inventory-edit-price-label").textContent = u ? `Price (₹ per ${UNIT_ONE[u]})` : 'Price (₹)';
  $("inventory-edit-modal").classList.add("open");
  setTimeout(() => $("inventory-edit-name").focus(), 100);
}

function closeEditModal() {
  editPicker.close();
  $("inventory-edit-modal").classList.remove("open");
}

$("inventory-edit-cancel").addEventListener("click", closeEditModal);

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
  let cooldownMs = 0;

  try {
    const token = await auth.currentUser.getIdToken();
    const form = new FormData();
    form.append("item", newName);
    form.append("quantity", String(newQty));
    form.append("price", String(newPrice));
    editPicker.attachTo(form);

    // No Content-Type header: the browser must set the multipart boundary itself.
    const res = await fetch(`${API}/inventory/${encodeURIComponent(originalId)}`, {
      method: 'PUT',
      headers: { Authorization: `Bearer ${token}` },
      body: form
    });

    // Only a photo can be rate limited, and only the server knows the budget —
    // so this mirrors the add sheet rather than guessing client-side.
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
      closeEditModal();
      loadDashboardInventory();
    } else {
      showToast('❌ ' + (data.detail || data.message || 'Failed to update.'));
    }
  } catch {
    showToast('❌ Could not connect to server.');
  } finally {
    btn.textContent = 'Save';
    if (cooldownMs > 0) {
      setTimeout(() => { btn.disabled = false; }, cooldownMs);
    } else {
      btn.disabled = false;
    }
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
      closeEditModal();
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
function closeAddModal() {
  addPicker.close();
  $("inventory-add-modal").classList.remove("open");
}

// Retitle the price/stock fields for the chosen unit, so "Dozen" asks for the
// price of one dozen (₹100 for a dozen apples) and stock counts dozens.
function syncAddUnitLabels() {
  const u = packUnit($("inventory-add-unit").value);
  const per = u ? ` per ${UNIT_ONE[u]}` : "";
  $("inventory-add-price-label").textContent = `Sell Price${per} (₹) *`;
  $("inventory-add-cost-label").textContent = `Cost Price${per} (₹)`;
  $("inventory-add-qty-label").textContent =
    u ? `Opening Stock (${UNIT_MANY[u]})` : "Opening Stock";
}

$("inventory-add-unit").addEventListener("change", syncAddUnitLabels);

function openInventoryAddModal() {
  ["inventory-add-name", "inventory-add-price", "inventory-add-cost",
   "inventory-add-qty", "inventory-add-category"].forEach(id => { $(id).value = ""; });
  $("inventory-add-unit").value = "pcs";
  syncAddUnitLabels();
  addPicker.open(null);
  $("inventory-add-modal").classList.add("open");
  setTimeout(() => $("inventory-add-name").focus(), 100);
}

$("inventory-add-btn").addEventListener("click", openInventoryAddModal);
$("inventory-add-cancel").addEventListener("click", closeAddModal);

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
    addPicker.attachTo(form);

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
