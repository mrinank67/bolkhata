/**
 * UI utilities — table renderer, toast, navigation, drawer, helpers
 */

import { $, auth } from "./config.js";
import { loadDashboardInventory } from "./dashboard.js";
import { loadHistory } from "./history.js";
import { loadSuppliers, loadSavedSuppliers } from "./suppliers.js";
import { loadLedgerCustomers } from "./ledger.js";

// ═══════ TABLE RENDERER ═══════
const numericColumns = new Set(["#", "Stock", "Qty", "Sold", "Added", "Previous", "Current", "Current Stock", "Quantity Owed", "Amount", "Stock Now"]);

export function buildResultHTML(results, errors, { isHistory = false } = {}) {
  let html = '';
  for (const group of results) {
    html += '<div class="result-card">';
    html += `<div class="result-card-header">
      <span class="result-card-icon">${group.icon}</span>
      <span class="result-card-title">${group.title}</span>
    </div>`;
    if (group.empty_message) {
      html += `<div class="result-card-empty">${group.empty_message}</div>`;
    } else if (group.rows && group.rows.length > 0) {
      html += '<div class="table-scroll"><table class="result-table"><thead><tr>';
      for (const col of group.columns) {
        const cls = numericColumns.has(col) ? ' class="cell-num"' : '';
        html += `<th${cls}>${col}</th>`;
      }
      html += '</tr></thead><tbody>';
      for (const row of group.rows) {
        html += '<tr>';
        for (const col of group.columns) {
          const cls = numericColumns.has(col) ? ' class="cell-num"' : '';
          html += `<td${cls}>${row[col] ?? '-'}</td>`;
        }
        html += '</tr>';
      }
      html += '</tbody></table></div>';
    }
    // Confirmation prompt for destructive actions
    if (group.requires_confirmation) {
      if (isHistory) {
        html += '<div class="confirm-result confirm-cancelled" style="padding:12px 16px;">Inventory deletion was attempted.</div>';
      } else {
        html += `<div class="confirm-prompt" data-action="${group.action}">
          <p class="confirm-message">${group.confirmation_message}</p>
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
      html += `<li class="error-item">❌ ${err}</li>`;
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
