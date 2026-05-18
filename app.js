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
const numericColumns = new Set(["#", "Stock", "Qty", "Sold", "Added", "Previous", "Current", "Current Stock", "Quantity Owed"]);

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
const pages = ["voice", "dashboard", "history"];
const pageTitles = { voice: "Voice", dashboard: "Dashboard", history: "History" };

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

// ═══════ 11. PWA SERVICE WORKER ═══════
if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("/sw.js").catch(() => {});
}
