/**
 * History page — load, render, and clear transaction history
 */

import { $, auth, API } from "./config.js";
import { buildResultHTML, escapeHtml } from "./ui.js";
import { isWide, onLayoutChange } from "./layout.js";

function formatTime(isoStr) {
  if (!isoStr) return '';
  const d = new Date(isoStr);
  const now = new Date();
  const isToday = d.toDateString() === now.toDateString();
  const time = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  if (isToday) return `Today, ${time}`;
  const yesterday = new Date(now); yesterday.setDate(now.getDate() - 1);
  if (d.toDateString() === yesterday.toDateString()) return `Yesterday, ${time}`;
  return `${d.toLocaleDateString([], { day: 'numeric', month: 'short' })}, ${time}`;
}

const historyBody = $("history-body");

// The API caps history at 50 entries (routes/history.py), so the whole list is
// always in memory — re-rendering on selection or a breakpoint change is free.
let currentHistory = [];
let selectedHistoryIndex = 0;

export async function loadHistory() {
  historyBody.innerHTML = '<div class="history-empty">Loading...</div>';
  try {
    const token = await auth.currentUser.getIdToken();
    const res = await fetch(`${API}/history`, {
      headers: { Authorization: `Bearer ${token}` }
    });
    const data = await res.json();
    currentHistory = data.history || [];
    selectedHistoryIndex = 0;
    renderHistory();
  } catch {
    currentHistory = [];
    historyBody.innerHTML = '<div class="history-empty">Could not load history.</div>';
    renderHistoryDetail(null);
  }
}

/** One line describing what an entry did, for the desktop list column. */
function summarize(entry) {
  const titles = (entry.results || []).map(g => g.title).filter(Boolean);
  const errors = (entry.errors || []).length;
  const bits = [];
  if (titles.length) bits.push(titles.join(', '));
  if (errors) bits.push(`${errors} error${errors !== 1 ? 's' : ''}`);
  return bits.join(' · ') || 'No results';
}

function renderHistory() {
  if (currentHistory.length === 0) {
    historyBody.innerHTML = '<div class="history-empty">No transactions yet.<br>Your history will appear here.</div>';
    renderHistoryDetail(null);
    return;
  }

  const wide = isWide();
  if (selectedHistoryIndex >= currentHistory.length) selectedHistoryIndex = 0;

  let html = '';
  currentHistory.forEach((entry, i) => {
    // Wide: a compact clickable row, tables go to the pane. Narrow: the
    // original stacked layout with every entry's tables inline.
    if (wide) {
      html += `<button type="button" class="history-row${i === selectedHistoryIndex ? ' selected' : ''}" data-index="${i}">
        <span class="history-timestamp">${escapeHtml(formatTime(entry.timestamp))}</span>
        <span class="history-row-summary">${escapeHtml(summarize(entry))}</span>
      </button>`;
    } else {
      html += '<div class="history-entry">';
      html += `<div class="history-timestamp">${escapeHtml(formatTime(entry.timestamp))}</div>`;
      html += buildResultHTML(entry.results || [], entry.errors || [], { isHistory: true });
      html += '</div>';
    }
  });
  historyBody.innerHTML = html;

  historyBody.querySelectorAll('.history-row').forEach(row => {
    row.addEventListener('click', () => {
      selectedHistoryIndex = Number(row.dataset.index);
      historyBody.querySelectorAll('.history-row').forEach(el => {
        el.classList.toggle('selected', el === row);
      });
      renderHistoryDetail(currentHistory[selectedHistoryIndex]);
    });
  });

  renderHistoryDetail(wide ? currentHistory[selectedHistoryIndex] : null);
}

function renderHistoryDetail(entry) {
  const paneEl = $('history-detail');
  if (!paneEl) return;
  if (!entry) {
    paneEl.innerHTML = '<div class="detail-pane-empty">Select an entry to see what it recorded.</div>';
    return;
  }
  // isHistory:true keeps the disambiguation/confirmation prompts inert — a past
  // transaction must not be re-applied from the log.
  paneEl.innerHTML = `<div class="history-entry">
    <div class="history-timestamp">${escapeHtml(formatTime(entry.timestamp))}</div>
    ${buildResultHTML(entry.results || [], entry.errors || [], { isHistory: true })}
  </div>`;
}

// Crossing 1024px swaps between the stacked log and the list + pane.
onLayoutChange(() => {
  if (currentHistory.length) renderHistory();
});

$("clear-history-btn").addEventListener("click", async () => {
  try {
    const token = await auth.currentUser.getIdToken();
    await fetch(`${API}/history`, {
      method: "DELETE",
      headers: { Authorization: `Bearer ${token}` }
    });
    loadHistory();
  } catch {
    // silently fail
  }
});
