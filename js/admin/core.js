/**
 * core.js — shared state and the one fetch wrapper every admin call goes through.
 *
 * The whole console is built on a single idea: an admin request is an ordinary
 * BolKhata request with an X-Acting-Uid header on it. So there is no parallel
 * API client here — adminFetch() adds that one header, and everything the
 * shopkeeper's app can do to their own shop, this can do to theirs.
 *
 * Reuses js/config.js rather than initialising Firebase again: same project,
 * same /config fetch, same token handling. Nothing from the main app's UI
 * modules is imported, so none of it ships to this page.
 */

import { API, auth } from "../config.js";

let actingUid = "";
let actingLabel = "";

export function getActingUid() {
  return actingUid;
}

export function getActingLabel() {
  return actingLabel;
}

export function setActing(uid, label) {
  actingUid = uid || "";
  actingLabel = label || uid || "";
}

/**
 * A request against the shop currently being supported.
 *
 * The token is minted per call rather than cached: a support session outlives
 * the one-hour ID token lifetime, and a stale token would 401 halfway through
 * an edit.
 */
export async function adminFetch(path, options = {}) {
  const user = auth.currentUser;
  if (!user) throw new Error("Signed out.");

  const headers = { ...(options.headers || {}) };
  headers.Authorization = `Bearer ${await user.getIdToken()}`;

  // Sent only when a shop is selected. /admin/* routes take the uid in the path
  // and do not want it; ordinary routes need it to retarget.
  if (actingUid && options.acting !== false) {
    headers["X-Acting-Uid"] = actingUid;
  }
  if (options.body !== undefined && !(options.body instanceof FormData)) {
    headers["Content-Type"] = "application/json";
  }

  const res = await fetch(`${API}${path}`, { ...options, headers });

  if (res.status === 204) return null;

  let data = null;
  try {
    data = await res.json();
  } catch {
    data = null;
  }

  if (!res.ok) {
    const detail = (data && (data.detail || data.message)) || `Request failed (${res.status}).`;
    const err = new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
    err.status = res.status;
    throw err;
  }
  return data;
}

/** A request about a shop rather than as one — the /admin/* routes. */
export function apiFetch(path, options = {}) {
  return adminFetch(path, { ...options, acting: false });
}

// ── DOM helpers ──

export const $ = id => document.getElementById(id);

/** Escape text before it goes anywhere near innerHTML.
 *
 * Load-bearing here in a way it is not in the main app: every value on this
 * page is another user's data — item names, customer names, and above all raw
 * LLM output and transcripts, none of which is sanitised on the way in.
 */
export function esc(value) {
  return String(value ?? "").replace(
    /[&<>"']/g,
    c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]
  );
}

export function money(n) {
  const value = Number(n) || 0;
  return `₹${value.toLocaleString("en-IN", { maximumFractionDigits: 2 })}`;
}

export function when(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleString("en-IN", { dateStyle: "medium", timeStyle: "short" });
}

export function ms(value) {
  if (value === null || value === undefined) return "—";
  return `${value} ms`;
}

let toastTimer = null;

export function toast(message, kind = "info") {
  const el = $("admin-toast");
  if (!el) return;
  el.textContent = message;
  el.className = `admin-toast show ${kind}`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    el.className = "admin-toast";
  }, kind === "error" ? 6000 : 3000);
}

export function show(el, visible) {
  if (el) el.classList.toggle("hidden", !visible);
}

/** Render a load failure in place instead of leaving a blank panel. */
export function renderError(container, err) {
  container.innerHTML = `<div class="admin-empty error">${esc(err.message || "Failed to load.")}</div>`;
}

export function empty(container, message) {
  container.innerHTML = `<div class="admin-empty">${esc(message)}</div>`;
}
