/**
 * main.js — entry point for the support console.
 *
 * Access is decided by the server, not here: the console renders nothing until
 * GET /admin/me returns 200. The client-side claim check that precedes it is a
 * convenience — it avoids a pointless request for an ordinary shopkeeper who
 * lands on this URL — and is never the thing granting access.
 */

import "../theme.js";
// Same 24-hour idle auto-signout the shopkeeper's app uses. Worth more here:
// an unattended console has read and write access to every shop.
import "../idle-timer.js";
import {
  auth,
  GoogleAuthProvider,
  onAuthStateChanged,
  signInWithEmailAndPassword,
  signInWithPopup,
  signOut
} from "../config.js";
import { $, apiFetch, esc, setActing, show, toast, when } from "./core.js";
import {
  loadBills,
  loadInventory,
  loadLedger,
  loadOrders,
  loadSettings,
  loadSuppliers
} from "./panels.js";
import { loadVoiceLogs, wireVoicePanel } from "./voice.js";

const PANELS = {
  voice: loadVoiceLogs,
  orders: loadOrders,
  ledger: loadLedger,
  suppliers: loadSuppliers,
  inventory: loadInventory,
  bills: loadBills,
  settings: loadSettings
};

let activeTab = "voice";

// ── Views ──

function showLogin(message) {
  show($("admin-login"), true);
  show($("admin-denied"), false);
  show($("admin-app"), false);
  $("admin-login-error").textContent = message || "";
}

function showDenied() {
  show($("admin-login"), false);
  show($("admin-denied"), true);
  show($("admin-app"), false);
}

function showConsole(identity) {
  show($("admin-login"), false);
  show($("admin-denied"), false);
  show($("admin-app"), true);
  $("admin-whoami").textContent = identity.email || identity.uid;
}

// ── Shop selection ──

function buildShopRow(user) {
  const label = user.display_name || user.email || user.phone_number || user.uid;
  return `
    <button class="admin-shop-row" data-uid="${esc(user.uid)}" data-label="${esc(label)}" type="button">
      <span class="admin-shop-name">${esc(label)}</span>
      <span class="admin-meta">${esc(user.phone_number || "")} ${esc(user.email || "")}</span>
      <code class="admin-uid">${esc(user.uid)}</code>
      ${user.disabled ? '<span class="admin-badge bad">Disabled</span>' : ""}
      ${user.is_admin ? '<span class="admin-badge info">Support</span>' : ""}
    </button>`;
}

async function searchShops() {
  const results = $("admin-results");
  const q = $("admin-search-input").value.trim();
  results.innerHTML = `<div class="admin-empty">Searching…</div>`;

  try {
    const data = await apiFetch(`/admin/users?q=${encodeURIComponent(q)}`);
    const users = data.users || [];
    if (!users.length) {
      results.innerHTML = `<div class="admin-empty">No account matches “${esc(q)}”.</div>`;
      return;
    }
    results.innerHTML = `
      <div class="admin-results-head">${q ? "Matches" : "Recent signups"}</div>
      <div class="admin-shop-rows">${users.map(buildShopRow).join("")}</div>`;
  } catch (err) {
    results.innerHTML = `<div class="admin-empty error">${esc(err.message)}</div>`;
  }
}

async function selectShop(uid, label) {
  setActing(uid, label);
  show($("admin-shop"), true);

  const header = $("admin-shop-header");
  header.innerHTML = `<div class="admin-empty">Loading ${esc(label)}…</div>`;

  try {
    const data = await apiFetch(`/admin/users/${encodeURIComponent(uid)}/overview`);
    const { account, settings, counts, voice_usage: usage } = data;

    const count = key => (counts[key] === null ? "?" : counts[key]);
    header.innerHTML = `
      <div class="admin-shop-title">
        <div>
          <h2>${esc(settings.shop_name || account.display_name || "Unnamed shop")}</h2>
          <div class="admin-meta">
            ${esc(account.phone_number || "")} ${esc(account.email || "")}
            · <code>${esc(uid)}</code>
          </div>
        </div>
        <div class="admin-meta">
          last sign-in ${esc(when(account.last_sign_in))}
          ${usage && usage.daily_count ? ` · ${esc(usage.daily_count)} voice request(s) today` : ""}
        </div>
      </div>
      <div class="admin-counts">
        ${[
          ["Voice logs", "voice_logs"],
          ["Orders", "orders"],
          ["Ledger", "udhaar"],
          ["Stock", "stock"],
          ["Purchases", "suppliers_purchases"],
          ["Bills", "bills"]
        ]
          .map(([label2, key]) => `<span><b>${esc(count(key))}</b> ${esc(label2)}</span>`)
          .join("")}
      </div>`;
  } catch (err) {
    header.innerHTML = `<div class="admin-empty error">${esc(err.message)}</div>`;
    return;
  }

  await openTab(activeTab);
}

// ── Tabs ──

async function openTab(name) {
  activeTab = name;
  document.querySelectorAll(".admin-tab").forEach(btn => {
    btn.classList.toggle("active", btn.dataset.tab === name);
  });
  Object.keys(PANELS).forEach(key => {
    show($(`admin-panel-${key}`), key === name);
  });
  await PANELS[name]();
}

// ── Audit drawer ──

async function loadAudit() {
  const list = $("admin-audit-list");
  list.innerHTML = `<div class="admin-empty">Loading…</div>`;
  try {
    const data = await apiFetch("/admin/audit");
    const rows = data.audit || [];
    if (!rows.length) {
      list.innerHTML = `<div class="admin-empty">No support actions recorded yet.</div>`;
      return;
    }
    list.innerHTML = rows
      .map(
        r => `<div class="admin-audit-row">
          <code>${esc(r.method)}</code> ${esc(r.path)}
          <span class="admin-meta">as ${esc(r.acting_uid)} · ${esc(r.status_code)} · ${esc(when(r.at))}</span>
        </div>`
      )
      .join("");
  } catch (err) {
    list.innerHTML = `<div class="admin-empty error">${esc(err.message)}</div>`;
  }
}

// ── Wiring ──

$("admin-signin-btn").addEventListener("click", async () => {
  const email = $("admin-email").value.trim();
  const password = $("admin-password").value;
  if (!email || !password) {
    $("admin-login-error").textContent = "Enter an email and password.";
    return;
  }
  try {
    await signInWithEmailAndPassword(auth, email, password);
  } catch {
    // Deliberately vague: this page must not confirm which emails exist.
    $("admin-login-error").textContent = "Sign-in failed.";
  }
});

$("admin-password").addEventListener("keydown", e => {
  if (e.key === "Enter") $("admin-signin-btn").click();
});

$("admin-google-btn").addEventListener("click", async () => {
  try {
    await signInWithPopup(auth, new GoogleAuthProvider());
  } catch (err) {
    // A closed popup is the user changing their mind, not a failure to report.
    if (err.code === "auth/popup-closed-by-user" || err.code === "auth/cancelled-popup-request") {
      return;
    }
    $("admin-login-error").textContent = "Google sign-in failed.";
  }
});

$("admin-signout").addEventListener("click", () => signOut(auth));
$("admin-denied-signout").addEventListener("click", () => signOut(auth));

$("admin-search-btn").addEventListener("click", searchShops);
$("admin-search-input").addEventListener("keydown", e => {
  if (e.key === "Enter") searchShops();
});

$("admin-results").addEventListener("click", e => {
  const row = e.target.closest(".admin-shop-row");
  if (row) selectShop(row.dataset.uid, row.dataset.label);
});

$("admin-tabs").addEventListener("click", e => {
  const btn = e.target.closest(".admin-tab");
  if (btn) openTab(btn.dataset.tab);
});

$("admin-audit-open").addEventListener("click", () => {
  show($("admin-audit-drawer"), true);
  loadAudit();
});
$("admin-audit-close").addEventListener("click", () => {
  show($("admin-audit-drawer"), false);
});

wireVoicePanel();

// ── Session ──

onAuthStateChanged(auth, async user => {
  if (!user) {
    setActing("", "");
    show($("admin-shop"), false);
    $("admin-results").innerHTML = "";
    showLogin();
    return;
  }

  // Cheap pre-check so an ordinary shopkeeper who wanders here is turned away
  // without a request. The server decides for real, immediately below.
  try {
    const token = await user.getIdTokenResult();
    if (token.claims.admin !== true) {
      showDenied();
      return;
    }
  } catch {
    showDenied();
    return;
  }

  try {
    const identity = await apiFetch("/admin/me");
    showConsole(identity);
    searchShops();
  } catch (err) {
    if (err.status === 403) {
      showDenied();
    } else {
      showLogin(err.message);
      toast(err.message, "error");
    }
  }
});
