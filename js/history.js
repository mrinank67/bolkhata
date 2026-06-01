/**
 * History page — load, render, and clear transaction history
 */

import { $, auth, API } from "./config.js";
import { buildResultHTML } from "./ui.js";

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

export async function loadHistory() {
  historyBody.innerHTML = '<div class="history-empty">Loading...</div>';
  try {
    const token = await auth.currentUser.getIdToken();
    const res = await fetch(`${API}/history`, {
      headers: { Authorization: `Bearer ${token}` }
    });
    const data = await res.json();
    const history = data.history || [];

    if (history.length === 0) {
      historyBody.innerHTML = '<div class="history-empty">No transactions yet.<br>Your history will appear here.</div>';
      return;
    }
    let html = '';
    for (const entry of history) {
      html += '<div class="history-entry">';
      html += `<div class="history-timestamp">${formatTime(entry.timestamp)}</div>`;
      html += buildResultHTML(entry.results || [], entry.errors || [], { isHistory: true });
      html += '</div>';
    }
    historyBody.innerHTML = html;
  } catch {
    historyBody.innerHTML = '<div class="history-empty">Could not load history.</div>';
  }
}

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
