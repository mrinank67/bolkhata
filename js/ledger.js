/**
 * Customer Ledger page — customers, search, sort, add entry, WhatsApp reminders
 */

import { $, auth, API } from "./config.js";
import { showToast, escapeHtml, capitalize, WA_SVG } from "./ui.js";
import { isWide, onLayoutChange } from "./layout.js";

async function saveWhatsAppNumber(customerName, customerModifier, waNumber) {
  try {
    const token = await auth.currentUser.getIdToken();
    await fetch(`${API}/ledger/whatsapp-reminder`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
      body: JSON.stringify({
        customer_name: customerName,
        customer_modifier: customerModifier,
        whatsapp_number: waNumber,
      })
    });
  } catch { /* silent — number save is best-effort */ }
}

function parseWaCode(wa) {
  if (!wa) return '+91';
  wa = wa.replace(/[\s\-()]/g, '');
  if (wa.startsWith('+') && wa.length > 10) return wa.slice(0, wa.length - 10);
  if (!wa.startsWith('+') && wa.length > 10) return '+' + wa.slice(0, wa.length - 10);
  return '+91';
}

function parseWaNumber(wa) {
  if (!wa) return '';
  wa = wa.replace(/[\s\-()]/g, '');
  if (wa.startsWith('+')) wa = wa.substring(1);
  return wa.length > 10 ? wa.slice(-10) : wa;
}

let currentLedgerData = { customers: [], total_due: 0, customer_count: 0 };
let currentLedgerSort = 'recent';
let ledgerSearchQuery = '';
// Which customer the desktop detail pane is showing. Unused below 1024px,
// where every card carries its own detail as an accordion instead.
let selectedCustomerKey = null;

const customerKey = (c) => `${c.customer_name}|${c.customer_modifier || ''}`;

function displayNameOf(c) {
  return c.customer_modifier
    ? `${capitalize(c.customer_name)} (${c.customer_modifier})`
    : capitalize(c.customer_name);
}

export async function loadLedgerCustomers() {
  const listEl = $('ledger-customer-list');
  listEl.innerHTML = '<div class="inventory-empty">Loading ledger…</div>';
  try {
    const token = await auth.currentUser.getIdToken();
    const res = await fetch(`${API}/ledger/customers`, {
      headers: { Authorization: `Bearer ${token}` }
    });
    const data = await res.json();
    currentLedgerData = data;
    $('ledger-total-due').textContent = `₹${(data.total_due || 0).toLocaleString('en-IN')}`;
    $('ledger-customer-count').textContent = data.customer_count || 0;
    renderLedgerCustomers();
  } catch {
    listEl.innerHTML = '<div class="inventory-empty">Could not load ledger.</div>';
  }
}

function renderLedgerCustomers() {
  const listEl = $('ledger-customer-list');
  let customers = [...(currentLedgerData.customers || [])];

  // Filter by search
  if (ledgerSearchQuery) {
    const q = ledgerSearchQuery.toLowerCase();
    customers = customers.filter(c =>
      (c.customer_name || '').toLowerCase().includes(q) ||
      (c.customer_modifier || '').toLowerCase().includes(q)
    );
  }

  if (customers.length === 0) {
    listEl.innerHTML = '<div class="inventory-empty">No customers found.<br>Use Voice or the + button to add entries.</div>';
    selectedCustomerKey = null;
    renderLedgerDetail(null);
    return;
  }

  // Sort
  if (currentLedgerSort === 'name-asc') {
    customers.sort((a, b) => (a.customer_name || '').localeCompare(b.customer_name || ''));
  } else if (currentLedgerSort === 'amount-desc') {
    customers.sort((a, b) => (b.total_due || 0) - (a.total_due || 0));
  }
  // 'recent' is already sorted from API

  // Desktop shows one customer's detail in the pane; keep the previous
  // selection if it survived the filter, otherwise fall back to the first row
  // so the pane is never empty.
  const wide = isWide();
  let selected = null;
  if (wide) {
    selected = customers.find(c => customerKey(c) === selectedCustomerKey) || customers[0];
    selectedCustomerKey = selected ? customerKey(selected) : null;
  }

  let html = '';
  for (const c of customers) {
    const key = customerKey(c);
    const lastEntry = c.last_entry ? `Last entry ${formatLedgerDate(c.last_entry)}` : '';
    const isSelected = wide && key === selectedCustomerKey;

    html += `<div class="ledger-customer-card${isSelected ? ' selected' : ''}" data-customer-key="${escapeHtml(key)}">
      ${buildLedgerHeaderHTML(c, lastEntry)}
      ${wide ? '' : `<div class="ledger-card-details">${buildLedgerDetailHTML(c)}</div>`}
    </div>`;
  }
  listEl.innerHTML = html;
  wireLedgerCards(listEl);
  renderLedgerDetail(selected);
}

/**
 * The always-visible summary line for a customer — name, last activity, amount.
 * Shared by the list row and the detail pane's title block.
 */
function buildLedgerHeaderHTML(c, subtitle) {
  const due = c.total_due || 0;
  const amountClass = due > 3000 ? 'high' : due > 0 ? 'due' : 'low';
  return `<div class="ledger-card-header">
    <div class="ledger-card-info">
      <div class="ledger-card-name">${escapeHtml(displayNameOf(c))}</div>
      <div class="ledger-card-subtitle">${escapeHtml(subtitle || '')}</div>
    </div>
    <div class="ledger-card-right">
      <div class="ledger-card-amount ${amountClass}">₹${due.toLocaleString('en-IN')}</div>
    </div>
  </div>`;
}

/**
 * A customer's full record, as an HTML string.
 *
 * Pure — it reads nothing from the DOM, so the same markup can be dropped
 * inside the customer's card (mobile accordion) or into the detail pane
 * (desktop). The buttons inside are found later by wireLedgerCards() walking up
 * to `.ledger-customer-card`, which is why the pane wrapper has to carry that
 * class and the same data-customer-key. See js/layout.js.
 */
function buildLedgerDetailHTML(c) {
  const wide = isWide();
  const wa = c.whatsapp_number || '';

  // Entry dates are the FIFO order that a payment settles in (see
  // apply_payment in db_operations.py), so the oldest debt reads top-down.
  // Only shown wide — a fourth column doesn't fit a 320px phone card.
  let itemsHtml = '';
  if (c.items && c.items.length > 0) {
    itemsHtml = `<table class="ledger-items-table"><thead><tr>
      <th>Item</th><th>Qty</th>${wide ? '<th>Date</th>' : ''}<th class="cell-num">₹</th>
    </tr></thead><tbody>`;
    for (const item of c.items) {
      const unitStr = item.unit ? ` ${item.unit}` : '';
      const qtyStr = item.quantity ? `${item.quantity}${unitStr}` : '';
      const dateCell = wide
        ? `<td class="ledger-item-date">${escapeHtml(formatLedgerDate(item.timestamp))}</td>`
        : '';
      itemsHtml += `<tr>
        <td>${escapeHtml(capitalize(item.item))}</td>
        <td>${escapeHtml(qtyStr)}</td>
        ${dateCell}
        <td class="cell-num">₹${(item.amount || 0).toLocaleString('en-IN')}</td>
      </tr>`;
    }
    itemsHtml += '</tbody></table>';
  }

  // due_note has been written by the API since the ledger shipped but was never
  // rendered anywhere — it's the shopkeeper's own note about the debt.
  const noteHtml = c.due_note
    ? `<div class="ledger-due-note"><span class="ledger-due-note-label">Note</span>${escapeHtml(c.due_note)}</div>`
    : '';

  const reminderBits = [];
  if (c.reminder_schedule) reminderBits.push(`Reminder: ${capitalize(c.reminder_schedule)}`);
  if (c.reminder_sent) reminderBits.push('Last reminder sent');
  const reminderHtml = reminderBits.length
    ? `<div class="ledger-reminder-status">${escapeHtml(reminderBits.join(' · '))}</div>`
    : '';

  return `${itemsHtml}
    ${noteHtml}
    ${reminderHtml}
    <div class="ledger-clear-section">
      <button class="btn btn-outline ledger-clear-btn" data-customer="${escapeHtml(c.customer_name)}" data-modifier="${escapeHtml(c.customer_modifier || '')}" data-due="${c.total_due || 0}">💰 Clear / Settle Dues</button>
    </div>
    <div class="whatsapp-section">
      <div class="whatsapp-section-label">WHATSAPP NUMBER</div>
      <div class="whatsapp-input wa-split-input">
        <input type="tel" class="wa-code-input" value="${escapeHtml(parseWaCode(wa))}" maxlength="4" />
        <input type="tel" class="wa-number-input" placeholder="98765 43210" value="${escapeHtml(parseWaNumber(wa))}" maxlength="10" inputmode="numeric" />
      </div>
      <button class="btn btn-whatsapp wa-remind-btn" data-customer="${escapeHtml(c.customer_name)}" data-modifier="${escapeHtml(c.customer_modifier || '')}" data-due="${c.total_due || 0}">${WA_SVG}Send Reminder</button>
    </div>`;
}

/** Paint the desktop detail pane. No-op in effect below 1024px (pane is display:none). */
function renderLedgerDetail(c) {
  const paneEl = $('ledger-detail');
  if (!paneEl) return;

  if (!c) {
    paneEl.innerHTML = '<div class="detail-pane-empty">Select a customer to see their entries.</div>';
    return;
  }

  const lastEntry = c.last_entry ? `Last entry ${formatLedgerDate(c.last_entry)}` : '';
  // Same class + data-customer-key as the list card: wireLedgerCards() and the
  // reminder handler both reach their record via closest('.ledger-customer-card').
  paneEl.innerHTML = `<div class="ledger-customer-card expanded" data-customer-key="${escapeHtml(customerKey(c))}">
    ${buildLedgerHeaderHTML(c, lastEntry)}
    <div class="ledger-card-details">${buildLedgerDetailHTML(c)}</div>
  </div>`;
  wireLedgerCards(paneEl);
}

/**
 * Wire the interactive bits inside `container`.
 *
 * Called on the list (mobile accordions) and on the detail pane (desktop) —
 * the markup is identical in both, so this doesn't care which it got.
 */
function wireLedgerCards(container) {
  // Card header: select on desktop, expand/collapse on mobile. This replaces an
  // inline onclick that could only ever do the accordion.
  container.querySelectorAll('.ledger-card-header').forEach(header => {
    const card = header.closest('.ledger-customer-card');
    if (!card || container.id === 'ledger-detail') return;   // pane title isn't clickable
    header.addEventListener('click', () => {
      if (isWide()) {
        selectedCustomerKey = card.dataset.customerKey;
        container.querySelectorAll('.ledger-customer-card').forEach(el => {
          el.classList.toggle('selected', el === card);
        });
        const c = (currentLedgerData.customers || []).find(x => customerKey(x) === selectedCustomerKey);
        renderLedgerDetail(c);
      } else {
        card.classList.toggle('expanded');
      }
    });
  });

  // Restrict number input to digits only, max 10
  container.querySelectorAll('.wa-number-input').forEach(input => {
    input.addEventListener('input', () => {
      input.value = input.value.replace(/\D/g, '').slice(0, 10);
    });
  });

  // Wire up Clear Dues buttons
  container.querySelectorAll('.ledger-clear-btn').forEach(btn => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      openClearDuesModal(btn.dataset.customer, btn.dataset.modifier, Number(btn.dataset.due));
    });
  });

  // Wire up WhatsApp reminder buttons
  container.querySelectorAll('.wa-remind-btn').forEach(btn => {
    btn.addEventListener('click', async () => {
      const card = btn.closest('.ledger-customer-card');
      const waCodeInput = card.querySelector('.wa-code-input');
      const waNumInput = card.querySelector('.wa-number-input');
      const code = waCodeInput.value.trim().replace(/[^+\d]/g, '');
      const num = waNumInput.value.trim().replace(/\D/g, '');

      if (!num || num.length !== 10) { showToast('❌ Please enter a valid 10-digit WhatsApp number.'); return; }
      const waNumber = code + num;

      // Persist the number first so it stays on file even if we can't send the
      // reminder right now (nothing due, or UPI ID not configured yet).
      saveWhatsAppNumber(btn.dataset.customer, btn.dataset.modifier, waNumber);

      const due = Number(btn.dataset.due);
      if (!due || due <= 0) { showToast('✅ Is customer ka koi baaki hisaab nahi hai.'); return; }

      let upiId = '';
      try {
        const token = await auth.currentUser.getIdToken();
        const res = await fetch(`${API}/settings`, { headers: { Authorization: `Bearer ${token}` } });
        const data = await res.json();
        upiId = data.upi_id || '';
      } catch { /* silent */ }

      if (!upiId) { showToast('❌ Pehle Account Settings mein apna UPI ID set karein.'); return; }

      const customerName = capitalize(btn.dataset.customer);
      const dueStr = due.toLocaleString('en-IN');

      let payToken;
      try {
        const token = await auth.currentUser.getIdToken();
        const res = await fetch(`${API}/pay/create`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
          body: JSON.stringify({ pa: upiId, pn: 'BolKhata', am: due, tn: `Payment for ${customerName}` })
        });
        const data = await res.json();
        payToken = data.token;
      } catch {
        showToast('Could not generate payment link.');
        return;
      }

      const phone = waNumber.startsWith('+') ? waNumber.substring(1) : (waNumber.length === 10 ? '91' + waNumber : waNumber);
      const payLink = `${window.location.origin}/pay?token=${encodeURIComponent(payToken)}`;

      const message = `Namaste ${customerName} ji,\n\nAapka ₹${dueStr} ka hisaab baaki hai.\n\nPayment karne ke liye yahan click karein:\n${payLink}\n\nDhanyavaad,\nBolKhata`;

      const waUrl = `https://wa.me/${phone}?text=${encodeURIComponent(message)}`;
      window.open(waUrl, '_blank');
    });
  });
}

function formatLedgerDate(isoStr) {
  if (!isoStr) return '';
  const d = new Date(isoStr);
  const now = new Date();
  if (d.toDateString() === now.toDateString()) return 'today';
  const yesterday = new Date(now); yesterday.setDate(now.getDate() - 1);
  if (d.toDateString() === yesterday.toDateString()) return 'yesterday';
  return d.toLocaleDateString('en-IN', { day: 'numeric', month: 'short' });
}

// Crossing 1024px moves the detail between the card and the pane, so the
// markup has to be rebuilt. Cheap: the full customer list is already in memory.
onLayoutChange(() => {
  if (currentLedgerData.customers && currentLedgerData.customers.length) {
    renderLedgerCustomers();
  }
});

// Search
$('ledger-search').addEventListener('input', (e) => {
  ledgerSearchQuery = e.target.value;
  renderLedgerCustomers();
});

// Sort
$('ledger-sort').addEventListener('change', (e) => {
  currentLedgerSort = e.target.value;
  renderLedgerCustomers();
});

// Add Entry Modal
$('ledger-add-btn').addEventListener('click', () => {
  $('ledger-add-modal').classList.add('open');
});

$('ledger-modal-cancel').addEventListener('click', () => {
  $('ledger-add-modal').classList.remove('open');
});

$('ledger-modal-save').addEventListener('click', async () => {
  const customer = $('ledger-entry-customer').value.trim();
  const modifier = $('ledger-entry-modifier').value.trim();
  const item = $('ledger-entry-item').value.trim();
  const qty = parseInt($('ledger-entry-qty').value) || 0;
  const amount = parseFloat($('ledger-entry-amount').value) || 0;
  const unit = $('ledger-entry-unit').value.trim();

  if (!customer || !item || qty <= 0) {
    showToast('❌ Please fill customer name, item, and quantity.');
    return;
  }

  const btn = $('ledger-modal-save');
  btn.innerHTML = '<div class="spinner"></div>';
  btn.disabled = true;

  try {
    const token = await auth.currentUser.getIdToken();
    const res = await fetch(`${API}/ledger/entry`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
      body: JSON.stringify({
        customer_name: customer,
        customer_modifier: modifier,
        item: item,
        quantity: qty,
        amount: amount,
        unit: unit
      })
    });
    const data = await res.json();
    if (data.status === 'success') {
      showToast('✅ ' + data.message);
      $('ledger-add-modal').classList.remove('open');
      // Clear form
      $('ledger-entry-customer').value = '';
      $('ledger-entry-modifier').value = '';
      $('ledger-entry-item').value = '';
      $('ledger-entry-qty').value = '';
      $('ledger-entry-amount').value = '';
      $('ledger-entry-unit').value = '';
      loadLedgerCustomers();
    } else {
      showToast('❌ ' + (data.detail || 'Failed to add entry.'));
    }
  } catch {
    showToast('❌ Could not connect to server.');
  } finally {
    btn.textContent = 'Add Entry';
    btn.disabled = false;
  }
});

// ── Clear Dues Modal (full settle or partial clear) ──
let pendingClear = null;

function openClearDuesModal(customer, modifier, due) {
  if (!due || due <= 0) {
    showToast('✅ Is customer ka koi baaki hisaab nahi hai.');
    return;
  }
  pendingClear = { customer, modifier, due };
  const displayName = modifier ? `${capitalize(customer)} (${modifier})` : capitalize(customer);
  $('ledger-clear-name').textContent = displayName;
  $('ledger-clear-due').textContent = `₹${due.toLocaleString('en-IN')}`;
  const amountInput = $('ledger-clear-amount');
  amountInput.value = due;
  $('ledger-clear-modal').classList.add('open');
  setTimeout(() => amountInput.select(), 100);
}

// Don't let the entered amount exceed what's owed — the rest is auto-settled anyway
$('ledger-clear-amount').addEventListener('input', () => {
  const input = $('ledger-clear-amount');
  if (pendingClear && Number(input.value) > pendingClear.due) input.value = pendingClear.due;
});

$('ledger-clear-cancel').addEventListener('click', () => {
  pendingClear = null;
  $('ledger-clear-modal').classList.remove('open');
});

$('ledger-clear-confirm').addEventListener('click', async () => {
  if (!pendingClear) return;
  const amount = parseFloat($('ledger-clear-amount').value);
  if (!amount || amount <= 0) { showToast('❌ Sahi amount daalein.'); return; }

  const btn = $('ledger-clear-confirm');
  btn.innerHTML = '<div class="spinner"></div>';
  btn.disabled = true;

  try {
    const token = await auth.currentUser.getIdToken();
    const res = await fetch(`${API}/ledger/clear`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
      body: JSON.stringify({
        customer_name: pendingClear.customer,
        customer_modifier: pendingClear.modifier,
        amount: amount,
      })
    });
    const data = await res.json();
    if (data.status === 'success') {
      showToast('✅ ' + data.message);
      $('ledger-clear-modal').classList.remove('open');
      loadLedgerCustomers();
    } else {
      showToast('❌ ' + (data.detail || 'Could not clear dues.'));
    }
  } catch {
    showToast('❌ Could not connect to server.');
  } finally {
    btn.textContent = 'Clear Dues';
    btn.disabled = false;
    pendingClear = null;
  }
});
