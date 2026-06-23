/**
 * Customer Ledger page — customers, search, sort, add entry, WhatsApp reminders
 */

import { $, auth, API } from "./config.js";
import { showToast, escapeHtml, capitalize } from "./ui.js";

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
    return;
  }

  // Sort
  if (currentLedgerSort === 'name-asc') {
    customers.sort((a, b) => (a.customer_name || '').localeCompare(b.customer_name || ''));
  } else if (currentLedgerSort === 'amount-desc') {
    customers.sort((a, b) => (b.total_due || 0) - (a.total_due || 0));
  }
  // 'recent' is already sorted from API

  let html = '';
  for (const c of customers) {
    const displayName = c.customer_modifier
      ? `${capitalize(c.customer_name)} (${c.customer_modifier})`
      : capitalize(c.customer_name);
    const lastEntry = c.last_entry ? `Last entry ${formatLedgerDate(c.last_entry)}` : '';
    const amountClass = (c.total_due || 0) > 3000 ? 'high' : (c.total_due || 0) > 0 ? 'due' : 'low';
    const wa = c.whatsapp_number || '';
    const customerKey = `${c.customer_name}|${c.customer_modifier || ''}`;

    // Items table
    let itemsHtml = '';
    if (c.items && c.items.length > 0) {
      itemsHtml += '<table class="ledger-items-table"><thead><tr><th></th><th></th><th>₹</th></tr></thead><tbody>';
      for (const item of c.items) {
        const unitStr = item.unit ? ` ${item.unit}` : '';
        const qtyStr = item.quantity ? `${item.quantity}${unitStr}` : '';
        itemsHtml += `<tr>
          <td>${escapeHtml(capitalize(item.item))}</td>
          <td>${escapeHtml(qtyStr)}</td>
          <td>₹${(item.amount || 0).toLocaleString('en-IN')}</td>
        </tr>`;
      }
      itemsHtml += '</tbody></table>';
    }

    html += `<div class="ledger-customer-card" data-customer-key="${escapeHtml(customerKey)}">
      <div class="ledger-card-header" onclick="this.parentElement.classList.toggle('expanded')">
        <div class="ledger-card-info">
          <div class="ledger-card-name">${escapeHtml(displayName)}</div>
          <div class="ledger-card-subtitle">${lastEntry}</div>
        </div>
        <div class="ledger-card-right">
          <div class="ledger-card-amount ${amountClass}">₹${(c.total_due || 0).toLocaleString('en-IN')}</div>
        </div>
      </div>
      <div class="ledger-card-details">
        ${itemsHtml}
        <div class="ledger-clear-section">
          <button class="btn btn-outline ledger-clear-btn" data-customer="${escapeHtml(c.customer_name)}" data-modifier="${escapeHtml(c.customer_modifier || '')}" data-due="${c.total_due || 0}">💰 Clear / Settle Dues</button>
        </div>
        <div class="whatsapp-section">
          <div class="whatsapp-section-label">WHATSAPP NUMBER</div>
          <div class="whatsapp-input wa-split-input">
            <input type="tel" class="wa-code-input" value="${escapeHtml(parseWaCode(wa))}" maxlength="4" />
            <input type="tel" class="wa-number-input" placeholder="98765 43210" value="${escapeHtml(parseWaNumber(wa))}" maxlength="10" inputmode="numeric" />
          </div>
          <button class="btn btn-whatsapp wa-remind-btn" data-customer="${escapeHtml(c.customer_name)}" data-modifier="${escapeHtml(c.customer_modifier || '')}" data-due="${c.total_due || 0}"><svg width="18" height="18" viewBox="0 0 24 24" fill="#fff"><path d="M17.472 14.382c-.297-.149-1.758-.867-2.03-.967-.273-.099-.471-.148-.67.15-.197.297-.767.966-.94 1.164-.173.199-.347.223-.644.075-.297-.15-1.255-.463-2.39-1.475-.883-.788-1.48-1.761-1.653-2.059-.173-.297-.018-.458.13-.606.134-.133.298-.347.446-.52.149-.174.198-.298.298-.497.099-.198.05-.371-.025-.52-.075-.149-.669-1.612-.916-2.207-.242-.579-.487-.5-.669-.51-.173-.008-.371-.01-.57-.01-.198 0-.52.074-.792.372-.272.297-1.04 1.016-1.04 2.479 0 1.462 1.065 2.875 1.213 3.074.149.198 2.096 3.2 5.077 4.487.709.306 1.262.489 1.694.625.712.227 1.36.195 1.871.118.571-.085 1.758-.719 2.006-1.413.248-.694.248-1.289.173-1.413-.074-.124-.272-.198-.57-.347m-5.421 7.403h-.004a9.87 9.87 0 01-5.031-1.378l-.361-.214-3.741.982.998-3.648-.235-.374a9.86 9.86 0 01-1.51-5.26c.001-5.45 4.436-9.884 9.888-9.884 2.64 0 5.122 1.03 6.988 2.898a9.825 9.825 0 012.893 6.994c-.003 5.45-4.437 9.884-9.885 9.884m8.413-18.297A11.815 11.815 0 0012.05 0C5.495 0 .16 5.335.157 11.892c0 2.096.547 4.142 1.588 5.945L.057 24l6.305-1.654a11.882 11.882 0 005.683 1.448h.005c6.554 0 11.89-5.335 11.893-11.893a11.821 11.821 0 00-3.48-8.413z"/></svg>Send Reminder</button>
        </div>
      </div>
    </div>`;
  }
  listEl.innerHTML = html;

  // Restrict number input to digits only, max 10
  listEl.querySelectorAll('.wa-number-input').forEach(input => {
    input.addEventListener('input', () => {
      input.value = input.value.replace(/\D/g, '').slice(0, 10);
    });
  });

  // Wire up Clear Dues buttons
  listEl.querySelectorAll('.ledger-clear-btn').forEach(btn => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      openClearDuesModal(btn.dataset.customer, btn.dataset.modifier, Number(btn.dataset.due));
    });
  });

  // Wire up WhatsApp reminder buttons
  listEl.querySelectorAll('.wa-remind-btn').forEach(btn => {
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
