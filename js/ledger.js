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
        <div class="whatsapp-section">
          <div class="whatsapp-section-label">WHATSAPP NUMBER</div>
          <div class="whatsapp-input">
            <input type="tel" class="wa-number-input" placeholder="+91 98765 43210" value="${escapeHtml(wa)}" />
          </div>
          <button class="btn btn-whatsapp wa-remind-btn" data-customer="${escapeHtml(c.customer_name)}" data-modifier="${escapeHtml(c.customer_modifier || '')}" data-due="${c.total_due || 0}">Send Reminder</button>
        </div>
      </div>
    </div>`;
  }
  listEl.innerHTML = html;

  // Wire up WhatsApp reminder buttons
  listEl.querySelectorAll('.wa-remind-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const card = btn.closest('.ledger-customer-card');
      const waInput = card.querySelector('.wa-number-input');
      const waNumber = waInput.value.trim().replace(/[\s\-()]/g, '');
      const upiId = localStorage.getItem('bolkhata_upi') || '';

      if (!waNumber) { showToast('❌ Please enter a WhatsApp number.'); return; }
      if (!upiId) { showToast('❌ Pehle Account Settings mein apna UPI ID set karein.'); return; }

      saveWhatsAppNumber(btn.dataset.customer, btn.dataset.modifier, waNumber);

      const customerName = capitalize(btn.dataset.customer);
      const due = Number(btn.dataset.due);
      const dueStr = due.toLocaleString('en-IN');

      const phone = waNumber.startsWith('+') ? waNumber.substring(1) : (waNumber.length === 10 ? '91' + waNumber : waNumber);
      const txnNote = `Payment for ${customerName}`;
      const payLink = `${window.location.origin}/pay?pa=${encodeURIComponent(upiId)}&pn=BolKhata&am=${due}&tn=${encodeURIComponent(txnNote)}`;

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
