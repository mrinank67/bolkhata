/**
 * Authentication — Phone, Google, Email auth + logout
 */

import {
  $, auth, API, setCurrentAuth,
  onAuthStateChanged, signOut,
  RecaptchaVerifier, signInWithPhoneNumber,
  GoogleAuthProvider, signInWithPopup,
  signInWithEmailAndPassword, createUserWithEmailAndPassword
} from "./config.js";

// ── DOM References ──
const loginView  = $("login-view");
const appView    = $("app-view");
const loginError = $("login-error");
const authMain   = $("auth-main");
const authOtp    = $("auth-otp");
const authEmail  = $("auth-email");

// ═══════ 1. AUTH STATE ═══════
onAuthStateChanged(auth, async user => {
  if (user) {
    const token = await user.getIdToken();

    setCurrentAuth(token, user.uid);
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

    // Account settings modal
    const settingsModal = $("account-settings-modal");
    const settingsUpiInput = $("settings-upi-input");
    const settingsShopNameInput = $("settings-shop-name-input");
    const settingsShopMobileInput = $("settings-shop-mobile-input");
    const settingsShopAddressInput = $("settings-shop-address-input");

    $("drawer-user-info").addEventListener("click", async () => {
      document.getElementById("drawer-overlay").classList.remove("open");
      settingsUpiInput.value = "";
      settingsShopNameInput.value = "";
      settingsShopMobileInput.value = "";
      settingsShopAddressInput.value = "";
      settingsModal.classList.add("open");
      try {
        const t = await auth.currentUser.getIdToken();
        const res = await fetch(`${API}/settings`, { headers: { Authorization: `Bearer ${t}` } });
        const data = await res.json();
        settingsUpiInput.value = data.upi_id || "";
        settingsShopNameInput.value = data.shop_name || "";
        settingsShopMobileInput.value = data.shop_mobile || "";
        settingsShopAddressInput.value = data.shop_address || "";
      } catch { /* silent */ }
    });

    $("settings-save-btn").addEventListener("click", async () => {
      const val = settingsUpiInput.value.trim();
      settingsModal.classList.remove("open");

      try {
        const t = await auth.currentUser.getIdToken();
        await fetch(`${API}/settings`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${t}` },
          body: JSON.stringify({
            upi_id: val,
            shop_name: settingsShopNameInput.value.trim(),
            shop_mobile: settingsShopMobileInput.value.trim(),
            shop_address: settingsShopAddressInput.value.trim(),
          })
        });
      } catch { /* silent */ }
    });

    $("settings-cancel-btn").addEventListener("click", () => {
      settingsModal.classList.remove("open");
    });
  } else {
    setCurrentAuth(null, null);
    appView.classList.add("hidden");
    loginView.classList.remove("hidden");
  }
});

// ── Logout Modal ──
const logoutModal = $("logout-modal");

$("logout-btn").addEventListener("click", () => {
  // Close drawer (imported function will be called via event)
  document.getElementById("drawer-overlay").classList.remove("open");
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
