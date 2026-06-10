/**
 * UI utilities — table renderer, toast, navigation, drawer, helpers
 */

import { $, auth } from "./config.js";
import { loadDashboardInventory } from "./dashboard.js";
import { loadHistory } from "./history.js";
import { loadSuppliers, loadSavedSuppliers } from "./suppliers.js";
import { loadLedgerCustomers } from "./ledger.js";

// ═══════ TABLE RENDERER ═══════
const numericColumns = new Set(["#", "Stock", "Qty", "Sold", "Added", "Previous", "Current", "Current Stock", "Qty Owed", "Amount Owed", "Amount", "Stock Now", "Rate", "Total Owed", "Total Ordered", "Entries", "Amount Cleared"]);

export function buildResultHTML(results, errors, { isHistory = false } = {}) {
  // Escape any value before inserting into innerHTML — customer/item names
  // come from voice transcripts and manual entry
  const esc = (v) => escapeHtml(String(v ?? '-'));
  let html = '';
  for (const group of results) {
    html += '<div class="result-card">';
    html += `<div class="result-card-header">
      <span class="result-card-icon">${esc(group.icon)}</span>
      <span class="result-card-title">${esc(group.title)}</span>
    </div>`;
    if (group.empty_message) {
      html += `<div class="result-card-empty">${esc(group.empty_message)}</div>`;
    } else if (group.rows && group.rows.length > 0) {
      html += '<div class="table-scroll"><table class="result-table"><thead><tr>';
      for (const col of group.columns) {
        const cls = numericColumns.has(col) ? ' class="cell-num"' : '';
        html += `<th${cls}>${esc(col)}</th>`;
      }
      html += '</tr></thead><tbody>';
      for (const row of group.rows) {
        html += '<tr>';
        for (const col of group.columns) {
          const cls = numericColumns.has(col) ? ' class="cell-num"' : '';
          html += `<td${cls}>${esc(row[col])}</td>`;
        }
        html += '</tr>';
      }
      html += '</tbody></table></div>';
    }
    // WhatsApp reminder action
    if (group.reminder_data) {
      const rd = group.reminder_data;
      const escaped = JSON.stringify(rd).replace(/"/g, '&quot;');
      html += `<div class="reminder-action" data-reminder="${escaped}">
        <button class="btn btn-whatsapp reminder-wa-btn"><svg width="18" height="18" viewBox="0 0 24 24" fill="#fff"><path d="M17.472 14.382c-.297-.149-1.758-.867-2.03-.967-.273-.099-.471-.148-.67.15-.197.297-.767.966-.94 1.164-.173.199-.347.223-.644.075-.297-.15-1.255-.463-2.39-1.475-.883-.788-1.48-1.761-1.653-2.059-.173-.297-.018-.458.13-.606.134-.133.298-.347.446-.52.149-.174.198-.298.298-.497.099-.198.05-.371-.025-.52-.075-.149-.669-1.612-.916-2.207-.242-.579-.487-.5-.669-.51-.173-.008-.371-.01-.57-.01-.198 0-.52.074-.792.372-.272.297-1.04 1.016-1.04 2.479 0 1.462 1.065 2.875 1.213 3.074.149.198 2.096 3.2 5.077 4.487.709.306 1.262.489 1.694.625.712.227 1.36.195 1.871.118.571-.085 1.758-.719 2.006-1.413.248-.694.248-1.289.173-1.413-.074-.124-.272-.198-.57-.347m-5.421 7.403h-.004a9.87 9.87 0 01-5.031-1.378l-.361-.214-3.741.982.998-3.648-.235-.374a9.86 9.86 0 01-1.51-5.26c.001-5.45 4.436-9.884 9.888-9.884 2.64 0 5.122 1.03 6.988 2.898a9.825 9.825 0 012.893 6.994c-.003 5.45-4.437 9.884-9.885 9.884m8.413-18.297A11.815 11.815 0 0012.05 0C5.495 0 .16 5.335.157 11.892c0 2.096.547 4.142 1.588 5.945L.057 24l6.305-1.654a11.882 11.882 0 005.683 1.448h.005c6.554 0 11.89-5.335 11.893-11.893a11.821 11.821 0 00-3.48-8.413z"/></svg>Send on WhatsApp</button>
      </div>`;
    }
    // Customer disambiguation prompt
    if (group.requires_disambiguation) {
      if (isHistory) {
        // No listeners are wired in history, and re-resolving an old
        // transaction would apply it twice — render a static note instead
        html += '<div class="confirm-result confirm-cancelled" style="padding:12px 16px;">Customer selection was requested.</div>';
      } else {
        const opts = JSON.stringify(group.disambiguation_options).replace(/"/g, '&quot;').replace(/</g, '&lt;');
        const pending = JSON.stringify(group.pending_transaction).replace(/"/g, '&quot;').replace(/</g, '&lt;');
        html += `<div class="disambig-prompt" data-options="${opts}" data-pending="${pending}">
          <p class="disambig-message">Multiple customers with this name found. Please select:</p>
          <div class="disambig-buttons">`;
        for (const opt of group.disambiguation_options) {
          const baseName = group.title.replace('Which ', '').replace('?', '');
          const label = opt.modifier ? `${baseName} (${opt.modifier})` : baseName;
          const phone = opt.phone || 'No number';
          const mod = (opt.modifier || '').replace(/"/g, '&quot;');
          html += `<button class="disambig-option-btn" data-modifier="${mod}">
            <span class="disambig-option-name">${escapeHtml(label)}</span>
            <span class="disambig-option-phone">${escapeHtml(phone)}</span>
          </button>`;
        }
        html += `</div></div>`;
      }
    }
    // Confirmation prompt for destructive actions
    if (group.requires_confirmation) {
      if (isHistory) {
        html += '<div class="confirm-result confirm-cancelled" style="padding:12px 16px;">Inventory deletion was attempted.</div>';
      } else {
        html += `<div class="confirm-prompt" data-action="${esc(group.action)}">
          <p class="confirm-message">${esc(group.confirmation_message)}</p>
          <div class="confirm-buttons">
            <button class="confirm-yes-btn" data-action="${group.action}">🗑️ Yes, Delete All</button>
            <button class="confirm-no-btn" data-action="${group.action}">Cancel</button>
          </div>
        </div>`;
      }
    }
    html += '</div>';
  }
  if (errors.length > 0) {
    html += '<ul class="error-list">';
    for (const err of errors) {
      html += `<li class="error-item">❌ ${esc(err)}</li>`;
    }
    html += '</ul>';
  }
  return html;
}

export function renderResults(results, errors) {
  const resultEl = $("result");
  const API = (location.hostname === "localhost" || location.hostname === "127.0.0.1") ? "http://localhost:8000" : "";
  const html = buildResultHTML(results, errors);
  resultEl.innerHTML = html || '<div class="result-placeholder">No results</div>';

  // Wire up disambiguation buttons
  resultEl.querySelectorAll('.disambig-option-btn').forEach(btn => {
    btn.addEventListener('click', async () => {
      const prompt = btn.closest('.disambig-prompt');
      const card = btn.closest('.result-card');
      const pending = JSON.parse(prompt.dataset.pending);
      const modifier = btn.dataset.modifier;

      prompt.querySelectorAll('.disambig-option-btn').forEach(b => { b.disabled = true; });
      btn.innerHTML = '<div class="spinner"></div>';

      try {
        const token = await auth.currentUser.getIdToken();
        const res = await fetch(`${API}/voice/resolve`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
          body: JSON.stringify({ transaction: pending, selected_modifier: modifier }),
        });
        const data = await res.json();
        if (data.status === 'success') {
          card.remove();
          const tmpDiv = document.createElement('div');
          tmpDiv.innerHTML = buildResultHTML(data.results || [], data.errors || []);
          while (tmpDiv.firstChild) resultEl.appendChild(tmpDiv.firstChild);
        } else {
          prompt.innerHTML = `<div class="confirm-result confirm-error">❌ ${data.message || 'Failed to process.'}</div>`;
        }
      } catch {
        prompt.innerHTML = '<div class="confirm-result confirm-error">❌ Could not connect to server.</div>';
      }
    });
  });

  // Wire up WhatsApp reminder buttons
  resultEl.querySelectorAll('.reminder-wa-btn').forEach(btn => {
    btn.addEventListener('click', async () => {
      const container = btn.closest('.reminder-action');
      const rd = JSON.parse(container.dataset.reminder);

      if (!rd.whatsapp_number) {
        showToast('WhatsApp number nahi mila. Ledger mein number add karein.');
        return;
      }
      if (!rd.upi_id) {
        showToast('Pehle Account Settings mein UPI ID set karein.');
        return;
      }

      let payToken;
      try {
        const token = await auth.currentUser.getIdToken();
        const res = await fetch(`${API}/pay/create`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
          body: JSON.stringify({ pa: rd.upi_id, pn: 'BolKhata', am: rd.total_due, tn: 'Payment for ' + rd.customer_name })
        });
        const data = await res.json();
        payToken = data.token;
      } catch {
        showToast('Could not generate payment link.');
        return;
      }

      const dueStr = Number(rd.total_due).toLocaleString('en-IN');
      const phone = rd.whatsapp_number.startsWith('+')
        ? rd.whatsapp_number.substring(1)
        : (rd.whatsapp_number.length === 10 ? '91' + rd.whatsapp_number : rd.whatsapp_number);
      const payLink = `${window.location.origin}/pay?token=${encodeURIComponent(payToken)}`;
      const message = `Namaste ${rd.customer_name} ji,\n\nAapka ₹${dueStr} ka hisaab baaki hai.\n\nPayment karne ke liye yahan click karein:\n${payLink}\n\nDhanyavaad,\nBolKhata`;
      const waUrl = `https://wa.me/${phone}?text=${encodeURIComponent(message)}`;
      window.open(waUrl, '_blank');
    });
  });

  // Wire up confirmation buttons
  resultEl.querySelectorAll('.confirm-yes-btn').forEach(btn => {
    btn.addEventListener('click', async () => {
      const prompt = btn.closest('.confirm-prompt');
      const card = btn.closest('.result-card');
      btn.disabled = true;
      btn.innerHTML = '<div class="spinner"></div>';
      prompt.querySelector('.confirm-no-btn').disabled = true;

      try {
        const token = await auth.currentUser.getIdToken();
        const res = await fetch(`${API}/confirm_clear_inventory`, {
          method: "POST",
          headers: { Authorization: `Bearer ${token}` }
        });
        const data = await res.json();

        // Replace the card content with success/failure message
        card.querySelector('.result-card-header .result-card-title').textContent = 'Inventory Cleared';
        card.querySelector('.result-card-header .result-card-icon').textContent = '🗑️';
        prompt.innerHTML = `<div class="confirm-result confirm-success">${data.message}</div>`;
      } catch {
        prompt.innerHTML = '<div class="confirm-result confirm-error">❌ Failed to clear inventory. Please try again.</div>';
      }
    });
  });

  resultEl.querySelectorAll('.confirm-no-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const card = btn.closest('.result-card');
      card.querySelector('.result-card-header .result-card-title').textContent = 'Deletion Cancelled';
      card.querySelector('.result-card-header .result-card-icon').textContent = '🚫';
      const prompt = btn.closest('.confirm-prompt');
      prompt.innerHTML = '<div class="confirm-result confirm-cancelled">Inventory deletion cancelled.</div>';

      // Auto-dismiss the card after 5 seconds
      setTimeout(() => {
        card.classList.add('fade-out');
        card.addEventListener('animationend', () => card.remove());
      }, 5000);
    });
  });
}

// ═══════ PAGE NAVIGATION ═══════
let currentPage = "voice";
const pages = ["voice", "dashboard", "history", "suppliers", "ledger"];
const pageTitles = { voice: "Voice", dashboard: "Dashboard", history: "History", suppliers: "Suppliers", ledger: "Ledger" };
const pageTitleEl = $("page-title");

export function navigateTo(page) {
  if (!pages.includes(page)) return;
  currentPage = page;

  // Toggle page visibility
  pages.forEach(p => {
    const el = $(`page-${p}`);
    if (el) el.classList.toggle("hidden", p !== page);
  });

  // Update nav active state
  document.querySelectorAll(".nav-item").forEach(btn => {
    btn.classList.toggle("active", btn.dataset.page === page);
  });

  // Update page title in topbar
  pageTitleEl.textContent = pageTitles[page] || page;

  // Close drawer
  closeDrawer();

  // Load data for the target page
  if (page === "dashboard") loadDashboardInventory();
  if (page === "history") loadHistory();
  if (page === "suppliers") { loadSuppliers(); loadSavedSuppliers(); }
  if (page === "ledger") loadLedgerCustomers();
}

// Wire nav items
document.querySelectorAll(".nav-item").forEach(btn => {
  btn.addEventListener("click", () => navigateTo(btn.dataset.page));
});

// ═══════ DRAWER CONTROLS ═══════
const drawerOverlay = $("drawer-overlay");

function openDrawer() {
  drawerOverlay.classList.add("open");
}

export function closeDrawer() {
  drawerOverlay.classList.remove("open");
}

$("menu-btn").addEventListener("click", openDrawer);
$("drawer-close").addEventListener("click", closeDrawer);
drawerOverlay.addEventListener("click", e => {
  if (e.target === drawerOverlay) closeDrawer();
});

// ═══════ TOAST NOTIFICATION ═══════
export function showToast(message, duration = 3000) {
  let toast = document.querySelector('.toast');
  if (!toast) {
    toast = document.createElement('div');
    toast.className = 'toast';
    document.body.appendChild(toast);
  }
  toast.textContent = message;
  toast.classList.add('show');
  clearTimeout(toast._timeout);
  toast._timeout = setTimeout(() => {
    toast.classList.remove('show');
  }, duration);
}

// ═══════ HELPER FUNCTIONS ═══════
export function escapeHtml(str) {
  const div = document.createElement('div');
  div.textContent = str || '';
  return div.innerHTML;
}

export function capitalize(str) {
  if (!str) return '';
  return str.charAt(0).toUpperCase() + str.slice(1);
}

export function getCurrentPage() {
  return currentPage;
}
