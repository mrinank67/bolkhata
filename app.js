import { initializeApp } from "https://www.gstatic.com/firebasejs/10.9.0/firebase-app.js";
import {
  getAuth,
  signInWithEmailAndPassword,
  createUserWithEmailAndPassword,
  onAuthStateChanged,
  signOut,
  RecaptchaVerifier,
  signInWithPhoneNumber,
  GoogleAuthProvider,
  signInWithPopup
} from "https://www.gstatic.com/firebasejs/10.9.0/firebase-auth.js";

// ── Config ──
const isLocal = location.hostname === "localhost" || location.hostname === "127.0.0.1";
const API = isLocal ? "http://localhost:8000" : "";

const configRes = await fetch(`${API}/config`);
const firebaseConfig = await configRes.json();

const app = initializeApp(firebaseConfig);
const auth = getAuth(app);

// ── DOM References ──
const $ = id => document.getElementById(id);
const loginView  = $("login-view");
const appView    = $("app-view");
const loginError = $("login-error");
const authMain   = $("auth-main");
const authOtp    = $("auth-otp");
const authEmail  = $("auth-email");
const drawerOverlay = $("drawer-overlay");
const historyBody = $("history-body");
const inventoryGrid = $("inventory-grid");
const pageTitleEl = $("page-title");

let currentToken = null;
let currentUid = null;

// ═══════ 1. AUTH STATE ═══════
onAuthStateChanged(auth, async user => {
  if (user) {
    const token = await user.getIdToken();
    try {
      const res = await fetch(`${API}/verify_access`, {
        headers: { Authorization: `Bearer ${token}` }
      });
      if (!res.ok) {
        throw new Error("Access Denied");
      }
    } catch (e) {
      await signOut(auth);
      loginError.innerText = "Access Denied: You do not have permission to view this preview version.";
      return;
    }

    currentToken = token;
    currentUid = user.uid;
    authMain.classList.remove("hidden");
    authOtp.classList.add("hidden");
    authEmail.classList.add("hidden");
    loginError.innerText = "";
    loginView.classList.add("hidden");
    appView.classList.remove("hidden");

    // Update drawer user info
    const displayName = user.displayName || user.email || user.phoneNumber || "User";
    $("drawer-user-name").textContent = displayName;
    $("drawer-user-email").textContent = user.email || user.phoneNumber || "";
  } else {
    currentToken = null;
    currentUid = null;
    appView.classList.add("hidden");
    loginView.classList.remove("hidden");
  }
});

const logoutModal = $("logout-modal");

$("logout-btn").addEventListener("click", () => {
  closeDrawer();
  logoutModal.classList.add("open");
});

$("modal-cancel-btn").addEventListener("click", () => {
  logoutModal.classList.remove("open");
});

$("modal-logout-btn").addEventListener("click", () => {
  logoutModal.classList.remove("open");
  signOut(auth);
});

// ═══════ 2. PHONE AUTH ═══════
window.recaptchaVerifier = new RecaptchaVerifier(auth, "recaptcha-container", {
  size: "invisible"
});

let confirmationResult = null;

$("send-sms-btn").addEventListener("click", async () => {
  loginError.innerText = "";
  const phone = $("phone-number").value.trim();
  if (phone.length < 10) { loginError.innerText = "Enter a valid phone number."; return; }

  const fullPhone = $("phone-country").value + phone;
  const btn = $("send-sms-btn");
  btn.innerHTML = '<div class="spinner"></div>';

  try {
    confirmationResult = await signInWithPhoneNumber(auth, fullPhone, window.recaptchaVerifier);
    authMain.classList.add("hidden");
    authOtp.classList.remove("hidden");
  } catch (err) {
    loginError.innerText = err.message || "Failed to send SMS.";
    if (window.recaptchaVerifier) window.recaptchaVerifier.render().then(id => grecaptcha.reset(id));
  } finally {
    btn.innerText = "Send OTP";
  }
});

$("phone-number").addEventListener("keydown", e => {
  if (e.key === "Enter") $("send-sms-btn").click();
});

$("verify-otp-btn").addEventListener("click", async () => {
  const digits = document.querySelectorAll(".otp-digit");
  const code = Array.from(digits).map(d => d.value).join("");
  loginError.innerText = "";

  if (code.length < 6) {
    loginError.innerText = "Please enter all 6 digits.";
    digits.forEach(d => { if (!d.value) d.classList.add("error"); });
    setTimeout(() => digits.forEach(d => d.classList.remove("error")), 600);
    return;
  }

  const btn = $("verify-otp-btn");
  btn.innerHTML = '<div class="spinner"></div>';
  try {
    await confirmationResult.confirm(code);
    digits.forEach(d => d.classList.add("success"));
  } catch {
    loginError.innerText = "Invalid OTP code.";
    digits.forEach(d => {
      d.value = "";
      d.classList.remove("filled");
      d.classList.add("error");
    });
    setTimeout(() => {
      digits.forEach(d => d.classList.remove("error"));
      digits[0].focus();
    }, 600);
  } finally {
    btn.innerText = "Verify";
  }
});

// ── OTP Input Behavior ──
const otpDigits = document.querySelectorAll(".otp-digit");

otpDigits.forEach((input, idx) => {
  // Only allow single digit
  input.addEventListener("input", e => {
    const val = input.value.replace(/[^0-9]/g, "");
    input.value = val.slice(0, 1);

    if (val) {
      input.classList.add("filled");
      // Auto-advance to next input
      if (idx < 5) {
        otpDigits[idx + 1].focus();
      } else {
        // Last digit entered — auto-submit
        input.blur();
        $("verify-otp-btn").click();
      }
    } else {
      input.classList.remove("filled");
    }
  });

  // Handle backspace navigation
  input.addEventListener("keydown", e => {
    if (e.key === "Backspace") {
      if (!input.value && idx > 0) {
        otpDigits[idx - 1].focus();
        otpDigits[idx - 1].value = "";
        otpDigits[idx - 1].classList.remove("filled");
      } else {
        input.value = "";
        input.classList.remove("filled");
      }
    }
    // Arrow key navigation
    if (e.key === "ArrowLeft" && idx > 0) {
      e.preventDefault();
      otpDigits[idx - 1].focus();
    }
    if (e.key === "ArrowRight" && idx < 5) {
      e.preventDefault();
      otpDigits[idx + 1].focus();
    }
    // Enter to submit
    if (e.key === "Enter") {
      $("verify-otp-btn").click();
    }
  });

  // Select text on focus for easy overwrite
  input.addEventListener("focus", () => {
    input.select();
    input.classList.remove("error");
  });

  // Handle paste (spread digits across all inputs)
  input.addEventListener("paste", e => {
    e.preventDefault();
    const pasted = (e.clipboardData.getData("text") || "").replace(/[^0-9]/g, "").slice(0, 6);
    if (!pasted) return;
    pasted.split("").forEach((char, i) => {
      if (otpDigits[i]) {
        otpDigits[i].value = char;
        otpDigits[i].classList.add("filled");
      }
    });
    // Focus last filled or submit
    if (pasted.length >= 6) {
      otpDigits[5].blur();
      $("verify-otp-btn").click();
    } else {
      otpDigits[Math.min(pasted.length, 5)].focus();
    }
  });
});

$("cancel-otp-btn").addEventListener("click", () => {
  authOtp.classList.add("hidden");
  authMain.classList.remove("hidden");
  loginError.innerText = "";
  otpDigits.forEach(d => {
    d.value = "";
    d.classList.remove("filled", "error", "success");
  });
});

// ═══════ 3. GOOGLE AUTH ═══════
$("google-login-btn").addEventListener("click", async () => {
  loginError.innerText = "";
  try { await signInWithPopup(auth, new GoogleAuthProvider()); }
  catch (err) { loginError.innerText = err.message || "Google sign-in failed."; }
});

// ═══════ 4. EMAIL AUTH ═══════
let isSignup = false;

$("show-email-btn").addEventListener("click", () => {
  authMain.classList.add("hidden");
  authEmail.classList.remove("hidden");
  loginError.innerText = "";
});

$("back-to-main-btn").addEventListener("click", () => {
  authEmail.classList.add("hidden");
  authMain.classList.remove("hidden");
  loginError.innerText = "";
  $("email").value = "";
  $("password").value = "";
});

$("toggle-email-mode-btn").addEventListener("click", e => {
  e.preventDefault();
  isSignup = !isSignup;
  $("login-btn").innerText = isSignup ? "Create Account" : "Sign In";
  $("toggle-email-mode-btn").innerText = isSignup ? "Already have an account?" : "Create account";
});

$("login-form").addEventListener("submit", async e => {
  e.preventDefault();
  loginError.innerText = "";
  const email = $("email").value, password = $("password").value;
  const btn = $("login-btn");
  btn.innerHTML = '<div class="spinner"></div>';
  try {
    isSignup
      ? await createUserWithEmailAndPassword(auth, email, password)
      : await signInWithEmailAndPassword(auth, email, password);
  } catch (err) {
    if (err.code === "auth/email-already-in-use") loginError.innerText = "Account exists — sign in instead.";
    else if (err.code === "auth/weak-password") loginError.innerText = "Password too weak (min 6 characters).";
    else loginError.innerText = err.message;
  } finally {
    btn.innerText = isSignup ? "Create Account" : "Sign In";
  }
});

// ═══════ 5. RECORDING ═══════
let mediaRecorder, audioChunks = [], activeStream;
const recordBtn = $("recordBtn");
const statusEl  = $("status");
const resultEl  = $("result");

const startRecording = async e => {
  if (e.cancelable) e.preventDefault();
  if (!currentToken) return;
  if (recordBtn.disabled) return; // Cooldown active

  try {
    activeStream = await navigator.mediaDevices.getUserMedia({ 
      audio: { 
        noiseSuppression: true, 
        echoCancellation: true, 
        autoGainControl: true 
      } 
    });
    mediaRecorder = new MediaRecorder(activeStream);
    audioChunks = [];

    mediaRecorder.ondataavailable = ev => audioChunks.push(ev.data);

    mediaRecorder.onstop = async () => {
      activeStream.getTracks().forEach(t => t.stop());
      currentToken = await auth.currentUser.getIdToken();

      statusEl.innerText = "Processing";
      statusEl.classList.add("processing-dots");

      const blob = new Blob(audioChunks, { type: "audio/webm" });
      const form = new FormData();
      form.append("audio", blob, "recording.webm");

      try {
        const res = await fetch(`${API}/process_voice`, {
          method: "POST",
          headers: { Authorization: `Bearer ${currentToken}` },
          body: form
        });

        if (res.status === 401) { signOut(auth); return; }

        // Handle rate limiting (429)
        if (res.status === 429) {
          const errData = await res.json();
          const retryAfter = errData.retry_after || 3;
          const message = errData.message || `Server busy. Retry in ${Math.ceil(retryAfter)}s.`;
          showToast(`⏳ ${message}`, Math.max(retryAfter * 1000, 3000));
          statusEl.innerText = "Rate Limited";
          resultEl.innerHTML = `<div class="error-item">⏳ ${message}</div>`;
          // Disable record button for the retry duration
          applyRecordCooldown(Math.ceil(retryAfter));
          return;
        }

        const data = await res.json();

        if (data.status === "success") {
          const results = data.results || [];
          const errors = data.errors || [];
          renderResults(results, errors);
          statusEl.innerText = "Done ✓";
        } else {
          resultEl.innerHTML = `<div class="error-item">❌ ${data.message || "Something went wrong."}</div>`;
          statusEl.innerText = "Error";
        }
      } catch {
        resultEl.innerHTML = '<div class="error-item">❌ Could not connect to the server.</div>';
        statusEl.innerText = "Offline";
      } finally {
        statusEl.classList.remove("processing-dots");
        // Brief client-side cooldown after every request (matches server cooldown)
        applyRecordCooldown(2);
      }
    };

    mediaRecorder.start();
    statusEl.innerText = "Listening…";
    statusEl.classList.remove("processing-dots");
    resultEl.innerHTML = '';
    recordBtn.classList.add("recording");
  } catch {
    statusEl.innerText = "Microphone access denied.";
  }
};

// ── Record Button Cooldown ──
let recordCooldownTimer = null;
function applyRecordCooldown(seconds) {
  recordBtn.disabled = true;
  recordBtn.classList.add("cooldown");
  clearTimeout(recordCooldownTimer);
  recordCooldownTimer = setTimeout(() => {
    recordBtn.disabled = false;
    recordBtn.classList.remove("cooldown");
  }, seconds * 1000);
}


const stopRecording = e => {
  if (e.cancelable) e.preventDefault();
  if (mediaRecorder?.state === "recording") {
    mediaRecorder.stop();
    recordBtn.classList.remove("recording");
  }
};

recordBtn.addEventListener("mousedown", startRecording);
recordBtn.addEventListener("mouseup", stopRecording);
recordBtn.addEventListener("mouseleave", stopRecording);
recordBtn.addEventListener("touchstart", startRecording, { passive: false });
recordBtn.addEventListener("touchend", stopRecording);
recordBtn.addEventListener("touchcancel", stopRecording);

// Spacebar to Record (Desktop)
window.addEventListener("keydown", e => {
  if (e.code === "Space" && !appView.classList.contains("hidden")) {
    if (document.activeElement.tagName === "INPUT" || document.activeElement.tagName === "TEXTAREA") return;
    e.preventDefault();
    if (!recordBtn.classList.contains("recording")) {
      startRecording(e);
    }
  }
});
window.addEventListener("keyup", e => {
  if (e.code === "Space" && !appView.classList.contains("hidden")) {
    if (document.activeElement.tagName === "INPUT" || document.activeElement.tagName === "TEXTAREA") return;
    e.preventDefault();
    stopRecording(e);
  }
});

// ═══════ 6. TABLE RENDERER ═══════
const numericColumns = new Set(["#", "Stock", "Qty", "Sold", "Added", "Previous", "Current", "Current Stock", "Quantity Owed", "Amount", "Stock Now"]);

function buildResultHTML(results, errors, { isHistory = false } = {}) {
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

function renderResults(results, errors) {
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

// ═══════ 7. PAGE NAVIGATION ═══════
let currentPage = "voice";
const pages = ["voice", "dashboard", "history", "suppliers", "ledger"];
const pageTitles = { voice: "Voice", dashboard: "Dashboard", history: "History", suppliers: "Suppliers", ledger: "Ledger" };

function navigateTo(page) {
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

// ═══════ 8. DRAWER CONTROLS ═══════
function openDrawer() {
  drawerOverlay.classList.add("open");
}

function closeDrawer() {
  drawerOverlay.classList.remove("open");
}

$("menu-btn").addEventListener("click", openDrawer);
$("drawer-close").addEventListener("click", closeDrawer);
drawerOverlay.addEventListener("click", e => {
  if (e.target === drawerOverlay) closeDrawer();
});

// ═══════ 9. HISTORY PAGE ═══════
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

async function loadHistory() {
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

// ═══════ 10. DASHBOARD — LIVE INVENTORY ═══════
let currentInventory = [];
let currentSort = 'name-asc';

async function loadDashboardInventory() {
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
  if (currentInventory.length === 0) {
    inventoryGrid.innerHTML = '<div class="inventory-empty">No items in inventory yet.<br>Use Voice to add stock.</div>';
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
    let qtyClass = '';
    if (qty === 0) qtyClass = 'out-of-stock';
    else if (qty <= 5) qtyClass = 'low-stock';

    html += `<div class="inventory-tile">
      <div class="inventory-tile-name">${item.item}</div>
      <div class="inventory-tile-qty ${qtyClass}">${qty}</div>
    </div>`;
  }
  html += `<div class="inventory-total">${items.length} item${items.length !== 1 ? 's' : ''} in stock</div>`;
  inventoryGrid.innerHTML = html;
}

$("inventory-sort").addEventListener("change", (e) => {
  currentSort = e.target.value;
  renderDashboardInventory();
});

$("dashboard-refresh-btn").addEventListener("click", () => {
  const btn = $("dashboard-refresh-btn");
  btn.classList.add("spinning");
  loadDashboardInventory().finally(() => {
    setTimeout(() => btn.classList.remove("spinning"), 800);
  });
});

// ═══════ 11. TOAST NOTIFICATION ═══════
function showToast(message, duration = 3000) {
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

// ═══════ 12. SUPPLIERS PAGE ═══════
let currentSupplierData = { purchases: [], month_total: 0, month_items: 0 };
let currentSupplierSort = 'recent';
let savedSuppliers = [];

// ── Tab Switching ──
document.querySelectorAll('.supplier-tab').forEach(tab => {
  tab.addEventListener('click', () => {
    document.querySelectorAll('.supplier-tab').forEach(t => t.classList.remove('active'));
    tab.classList.add('active');
    const target = tab.dataset.tab;
    document.querySelectorAll('.supplier-tab-content').forEach(c => c.classList.add('hidden'));
    $(`supplier-tab-${target}`).classList.remove('hidden');
  });
});

// ── Load Saved Suppliers (Directory) ──
async function loadSavedSuppliers() {
  const listEl = $('supplier-directory-list');
  listEl.innerHTML = '<div class="inventory-empty">Loading suppliers…</div>';
  try {
    const token = await auth.currentUser.getIdToken();
    const res = await fetch(`${API}/suppliers/list`, {
      headers: { Authorization: `Bearer ${token}` }
    });
    const data = await res.json();
    savedSuppliers = data.suppliers || [];
    renderSavedSuppliers();
  } catch {
    listEl.innerHTML = '<div class="inventory-empty">Could not load suppliers.</div>';
  }
}

function renderSavedSuppliers() {
  const listEl = $('supplier-directory-list');
  if (savedSuppliers.length === 0) {
    listEl.innerHTML = '<div class="inventory-empty">No suppliers added yet.<br>Tap + to add your first supplier.</div>';
    return;
  }

  let html = '';
  for (const s of savedSuppliers) {
    const mobileLine = s.mobile ? `<div class="supplier-dir-mobile">📱 ${escapeHtml(s.mobile)}</div>` : '';
    const gstLine = s.gst_number ? `<div class="supplier-dir-gst">GST: ${escapeHtml(s.gst_number)}</div>` : '';

    html += `<div class="supplier-dir-card" data-id="${s.id}" data-name="${escapeHtml(s.name)}">
      <div class="supplier-dir-header">
        <div class="supplier-dir-info">
          <div class="supplier-dir-name">${escapeHtml(s.name)}</div>
          ${mobileLine}
          ${gstLine}
        </div>
        <div class="supplier-dir-actions">
          <button class="supplier-add-purchase-btn" data-name="${escapeHtml(s.name)}" aria-label="Add purchase" title="Add purchase">＋</button>
          <button class="supplier-expand-btn" data-id="${s.id}" aria-label="Show purchases">▾</button>
          <button class="supplier-delete-btn" data-id="${s.id}" data-name="${escapeHtml(s.name)}" aria-label="Delete supplier">🗑️</button>
        </div>
      </div>
      <div class="supplier-dir-purchases hidden" id="supplier-purchases-${s.id}">
        <div class="inventory-empty">Loading…</div>
      </div>
    </div>`;
  }
  listEl.innerHTML = html;

  // Wire expand buttons
  listEl.querySelectorAll('.supplier-expand-btn').forEach(btn => {
    btn.addEventListener('click', () => toggleSupplierPurchases(btn.dataset.id));
  });

  // Wire delete buttons (opens custom modal)
  listEl.querySelectorAll('.supplier-delete-btn').forEach(btn => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      openDeleteSupplierModal(btn.dataset.id, btn.dataset.name);
    });
  });

  // Wire add-purchase buttons
  listEl.querySelectorAll('.supplier-add-purchase-btn').forEach(btn => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      openPurchaseModal(btn.dataset.name);
    });
  });
}

async function toggleSupplierPurchases(supplierId) {
  const panel = $(`supplier-purchases-${supplierId}`);
  const card = panel.closest('.supplier-dir-card');
  const btn = card.querySelector('.supplier-expand-btn');

  if (!panel.classList.contains('hidden')) {
    panel.classList.add('hidden');
    card.classList.remove('expanded');
    btn.textContent = '▾';
    return;
  }

  panel.classList.remove('hidden');
  card.classList.add('expanded');
  btn.textContent = '▴';

  // Find supplier name
  const supplierName = card.dataset.name;

  // Filter purchases by this supplier
  const purchases = (currentSupplierData.purchases || []).filter(
    p => (p.supplier_name || '').toLowerCase() === supplierName.toLowerCase()
  );

  if (purchases.length === 0) {
    panel.innerHTML = '<div class="inventory-empty" style="padding:10px 0;">No purchases recorded for this supplier.</div>';
    return;
  }

  let html = '<div class="supplier-purchase-list">';
  for (const p of purchases) {
    const dateStr = p.timestamp ? formatPurchaseDate(p.timestamp) : '';
    const itemDesc = `${p.item_name ? p.item_name.charAt(0).toUpperCase() + p.item_name.slice(1) : ''} × ${p.quantity}`;
    html += `<div class="supplier-purchase-row">
      <span class="supplier-purchase-item">${escapeHtml(itemDesc)}</span>
      <span class="supplier-purchase-amount">₹${(p.amount || 0).toLocaleString('en-IN')}</span>
      <span class="supplier-purchase-date">${dateStr}</span>
    </div>`;
  }
  html += '</div>';
  panel.innerHTML = html;
}

// ── Delete Supplier (custom modal) ──
let pendingDeleteSupplierId = null;

function openDeleteSupplierModal(id, name) {
  pendingDeleteSupplierId = id;
  $('supplier-delete-message').textContent = `Are you sure you want to delete "${name}"? This cannot be undone.`;
  $('supplier-delete-modal').classList.add('open');
}

$('supplier-delete-cancel').addEventListener('click', () => {
  pendingDeleteSupplierId = null;
  $('supplier-delete-modal').classList.remove('open');
});

$('supplier-delete-confirm').addEventListener('click', async () => {
  if (!pendingDeleteSupplierId) return;
  const id = pendingDeleteSupplierId;
  const btn = $('supplier-delete-confirm');
  btn.innerHTML = '<div class="spinner"></div>';
  btn.disabled = true;

  try {
    const token = await auth.currentUser.getIdToken();
    const res = await fetch(`${API}/suppliers/${id}`, {
      method: 'DELETE',
      headers: { Authorization: `Bearer ${token}` }
    });
    const data = await res.json();
    if (data.status === 'success') {
      showToast('✅ ' + data.message);
      loadSavedSuppliers();
      loadSuppliers();  // refresh summary cards + purchase history
    } else {
      showToast('❌ ' + (data.detail || 'Failed to delete.'));
    }
  } catch {
    showToast('❌ Could not connect to server.');
  } finally {
    btn.textContent = 'Delete';
    btn.disabled = false;
    pendingDeleteSupplierId = null;
    $('supplier-delete-modal').classList.remove('open');
  }
});

// ── Add Supplier Modal ──
$('supplier-add-btn').addEventListener('click', () => {
  $('supplier-add-modal').classList.add('open');
  $('supplier-modal-name').value = '';
  $('supplier-modal-mobile').value = '';
  $('supplier-modal-gst').value = '';
  setTimeout(() => $('supplier-modal-name').focus(), 100);
});

$('supplier-modal-cancel').addEventListener('click', () => {
  $('supplier-add-modal').classList.remove('open');
});

$('supplier-modal-save').addEventListener('click', async () => {
  const name = $('supplier-modal-name').value.trim();
  const mobile = $('supplier-modal-mobile').value.trim();
  const gst = $('supplier-modal-gst').value.trim();

  if (!name) {
    showToast('❌ Supplier name is required.');
    return;
  }

  const btn = $('supplier-modal-save');
  btn.innerHTML = '<div class="spinner"></div>';
  btn.disabled = true;

  try {
    const token = await auth.currentUser.getIdToken();
    const res = await fetch(`${API}/suppliers/add`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${token}`
      },
      body: JSON.stringify({ name, mobile: mobile ? `+91${mobile}` : '', gst_number: gst })
    });
    const data = await res.json();
    if (data.status === 'success') {
      showToast('✅ ' + data.message);
      $('supplier-add-modal').classList.remove('open');
      loadSavedSuppliers();
    } else {
      showToast('❌ ' + (data.detail || 'Failed to add supplier.'));
    }
  } catch {
    showToast('❌ Could not connect to server.');
  } finally {
    btn.textContent = 'Add Supplier';
    btn.disabled = false;
  }
});

// ── Manual Purchase Modal ──
function openPurchaseModal(supplierName) {
  $('purchase-modal-supplier').value = supplierName || '';
  $('purchase-modal-item').value = '';
  $('purchase-modal-qty').value = '';
  $('purchase-modal-amount').value = '';
  $('supplier-purchase-modal').classList.add('open');
  setTimeout(() => $('purchase-modal-item').focus(), 100);
}

$('purchase-modal-cancel').addEventListener('click', () => {
  $('supplier-purchase-modal').classList.remove('open');
});

$('purchase-modal-save').addEventListener('click', async () => {
  const supplier = $('purchase-modal-supplier').value.trim();
  const item = $('purchase-modal-item').value.trim();
  const qty = parseInt($('purchase-modal-qty').value) || 0;
  const amount = parseFloat($('purchase-modal-amount').value) || 0;

  if (!supplier || !item || qty <= 0) {
    showToast('❌ Please fill in supplier, item, and quantity.');
    return;
  }

  const btn = $('purchase-modal-save');
  btn.innerHTML = '<div class="spinner"></div>';
  btn.disabled = true;

  try {
    const token = await auth.currentUser.getIdToken();
    const res = await fetch(`${API}/suppliers/purchase`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${token}`
      },
      body: JSON.stringify({
        supplier_name: supplier,
        item_name: item,
        quantity: qty,
        amount: amount,
        proof_image_url: ''
      })
    });
    const data = await res.json();
    if (data.status === 'success') {
      showToast('✅ ' + data.message);
      $('supplier-purchase-modal').classList.remove('open');
      loadSuppliers();
    } else {
      showToast('❌ ' + (data.detail || 'Failed to add purchase.'));
    }
  } catch {
    showToast('❌ Could not connect to server.');
  } finally {
    btn.textContent = 'Add Purchase';
    btn.disabled = false;
  }
});

// ── Purchase History (existing, now in Purchases tab) ──
async function loadSuppliers() {
  const supplierList = $('supplier-list');
  supplierList.innerHTML = '<div class="inventory-empty">Loading purchases…</div>';
  try {
    const token = await auth.currentUser.getIdToken();
    const res = await fetch(`${API}/suppliers`, {
      headers: { Authorization: `Bearer ${token}` }
    });
    const data = await res.json();
    currentSupplierData = data;
    $('supplier-month-total').textContent = `₹${(data.month_total || 0).toLocaleString('en-IN')}`;
    $('supplier-month-items').textContent = data.month_items || 0;
    renderSupplierList();
  } catch {
    supplierList.innerHTML = '<div class="inventory-empty">Could not load purchases.</div>';
  }
}

function renderSupplierList() {
  const supplierList = $('supplier-list');
  let purchases = [...(currentSupplierData.purchases || [])];

  if (purchases.length === 0) {
    supplierList.innerHTML = '<div class="inventory-empty">No purchases recorded yet.<br>Use voice to add purchases.</div>';
    return;
  }

  // Sort
  if (currentSupplierSort === 'name-asc') {
    purchases.sort((a, b) => (a.supplier_name || '').localeCompare(b.supplier_name || ''));
  } else if (currentSupplierSort === 'name-desc') {
    purchases.sort((a, b) => (b.supplier_name || '').localeCompare(a.supplier_name || ''));
  } else if (currentSupplierSort === 'amount-desc') {
    purchases.sort((a, b) => (b.amount || 0) - (a.amount || 0));
  } else {
    purchases.sort((a, b) => (b.timestamp || 0) - (a.timestamp || 0));
  }

  let html = '';
  for (const p of purchases) {
    const dateStr = p.timestamp ? formatPurchaseDate(p.timestamp) : '';
    const itemDesc = `${p.item_name ? p.item_name.charAt(0).toUpperCase() + p.item_name.slice(1) : ''} x ${p.quantity}`;
    html += `<div class="supplier-card">
      <div class="supplier-card-info">
        <div class="supplier-card-name">${escapeHtml(p.supplier_name)}</div>
        <div class="supplier-card-items">${escapeHtml(itemDesc)}</div>
      </div>
      <div class="supplier-card-right">
        <div class="supplier-card-amount">₹${(p.amount || 0).toLocaleString('en-IN')}</div>
        <div class="supplier-card-date">${dateStr}</div>
      </div>
    </div>`;
  }
  supplierList.innerHTML = html;
}

function formatPurchaseDate(tsMs) {
  const d = new Date(tsMs);
  const now = new Date();
  if (d.toDateString() === now.toDateString()) return 'Today';
  const yesterday = new Date(now); yesterday.setDate(now.getDate() - 1);
  if (d.toDateString() === yesterday.toDateString()) return 'Yesterday';
  return d.toLocaleDateString('en-IN', { day: 'numeric', month: 'short' });
}

function escapeHtml(str) {
  const div = document.createElement('div');
  div.textContent = str || '';
  return div.innerHTML;
}

$('supplier-sort').addEventListener('change', (e) => {
  currentSupplierSort = e.target.value;
  renderSupplierList();
});

$('supplier-refresh-btn').addEventListener('click', () => {
  const btn = $('supplier-refresh-btn');
  btn.classList.add('spinning');
  Promise.all([loadSuppliers(), loadSavedSuppliers()]).finally(() => {
    setTimeout(() => btn.classList.remove('spinning'), 800);
  });
});





// ═══════ 13. CUSTOMER LEDGER PAGE ═══════
let currentLedgerData = { customers: [], total_due: 0, customer_count: 0 };
let currentLedgerSort = 'recent';
let ledgerSearchQuery = '';

async function loadLedgerCustomers() {
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
    const reminderSched = c.reminder_schedule || '';
    const reminderSent = c.reminder_sent ? 'Reminder sent ✓' : 'Reminder not scheduled';
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
            <input type="tel" class="wa-number-input" placeholder="+91 98765 43210" value="${escapeHtml(wa)}" data-customer="${escapeHtml(c.customer_name)}" data-modifier="${escapeHtml(c.customer_modifier || '')}" />
          </div>
          <div class="whatsapp-section-label">REMINDER SCHEDULE</div>
          <select class="reminder-select" data-customer="${escapeHtml(c.customer_name)}" data-modifier="${escapeHtml(c.customer_modifier || '')}">
            <option value="" ${!reminderSched ? 'selected' : ''}>Select time</option>
            <option value="Today evening" ${reminderSched === 'Today evening' ? 'selected' : ''}>Today evening</option>
            <option value="Tomorrow morning" ${reminderSched === 'Tomorrow morning' ? 'selected' : ''}>Tomorrow morning</option>
            <option value="This weekend" ${reminderSched === 'This weekend' ? 'selected' : ''}>This weekend</option>
            <option value="Next week" ${reminderSched === 'Next week' ? 'selected' : ''}>Next week</option>
          </select>
          <button class="btn btn-whatsapp wa-remind-btn" data-customer="${escapeHtml(c.customer_name)}" data-modifier="${escapeHtml(c.customer_modifier || '')}">Schedule WhatsApp reminder</button>
          <div class="reminder-status">${reminderSent}</div>
        </div>
      </div>
    </div>`;
  }
  listEl.innerHTML = html;

  // Wire up WhatsApp reminder buttons
  listEl.querySelectorAll('.wa-remind-btn').forEach(btn => {
    btn.addEventListener('click', async () => {
      const card = btn.closest('.ledger-customer-card');
      const waInput = card.querySelector('.wa-number-input');
      const schedSelect = card.querySelector('.reminder-select');
      const waNumber = waInput.value.trim();
      const schedule = schedSelect.value;

      if (!waNumber) { showToast('❌ Please enter a WhatsApp number.'); return; }
      if (!schedule) { showToast('❌ Please select a reminder schedule.'); return; }

      btn.innerHTML = '<div class="spinner"></div>';
      btn.disabled = true;

      try {
        const token = await auth.currentUser.getIdToken();
        const res = await fetch(`${API}/ledger/whatsapp-reminder`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
          body: JSON.stringify({
            customer_name: btn.dataset.customer,
            customer_modifier: btn.dataset.modifier,
            whatsapp_number: waNumber,
            reminder_schedule: schedule
          })
        });
        const data = await res.json();
        showToast('📱 ' + (data.message || 'Reminder scheduled!'));
        const statusEl = card.querySelector('.reminder-status');
        if (statusEl) statusEl.textContent = `Scheduled: ${schedule}`;
      } catch {
        showToast('❌ Could not schedule reminder.');
      } finally {
        btn.textContent = 'Schedule WhatsApp reminder';
        btn.disabled = false;
      }
    });
  });
}

function capitalize(str) {
  if (!str) return '';
  return str.charAt(0).toUpperCase() + str.slice(1);
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

// ═══════ 14. PWA SERVICE WORKER — AUTO-UPDATE ═══════
if ("serviceWorker" in navigator) {
  let refreshing = false;

  // When a new SW takes control, reload to get fresh assets
  navigator.serviceWorker.addEventListener("controllerchange", () => {
    if (!refreshing) {
      refreshing = true;
      window.location.reload();
    }
  });

  navigator.serviceWorker.register("/sw.js").then(reg => {
    // Check for updates every 60 seconds while the app is open
    setInterval(() => reg.update(), 60 * 1000);

    // If a new SW is waiting (e.g. user had the tab open during deploy),
    // tell it to skip waiting so controllerchange fires
    if (reg.waiting) {
      reg.waiting.postMessage({ type: "SKIP_WAITING" });
    }

    reg.addEventListener("updatefound", () => {
      const newWorker = reg.installing;
      if (newWorker) {
        newWorker.addEventListener("statechange", () => {
          if (newWorker.state === "installed" && navigator.serviceWorker.controller) {
            // New version available — activate it immediately
            newWorker.postMessage({ type: "SKIP_WAITING" });
          }
        });
      }
    });
  }).catch(() => {});
}
