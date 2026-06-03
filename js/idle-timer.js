import { auth, signOut, onAuthStateChanged } from "./config.js";

const IDLE_TIMEOUT_MS = 24 * 60 * 60 * 1000;
const STORAGE_KEY = "bolkhata_lastActivity";
const THROTTLE_MS = 30_000;

let lastWriteTime = 0;
let idleCheckInterval = null;

function updateActivity() {
  if (!auth.currentUser) return;
  const now = Date.now();
  const stored = localStorage.getItem(STORAGE_KEY);
  if (stored && now - Number(stored) > IDLE_TIMEOUT_MS) {
    localStorage.removeItem(STORAGE_KEY);
    signOut(auth);
    return;
  }
  if (now - lastWriteTime < THROTTLE_MS) return;
  lastWriteTime = now;
  localStorage.setItem(STORAGE_KEY, String(now));
}

function checkIdleTimeout() {
  if (!auth.currentUser) return;
  const stored = localStorage.getItem(STORAGE_KEY);
  if (!stored) return;
  if (Date.now() - Number(stored) > IDLE_TIMEOUT_MS) {
    localStorage.removeItem(STORAGE_KEY);
    signOut(auth);
  }
}

onAuthStateChanged(auth, user => {
  if (user) {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored && Date.now() - Number(stored) > IDLE_TIMEOUT_MS) {
      localStorage.removeItem(STORAGE_KEY);
      signOut(auth);
      return;
    }
    if (!stored) {
      localStorage.setItem(STORAGE_KEY, String(Date.now()));
    }
    if (!idleCheckInterval) {
      idleCheckInterval = setInterval(checkIdleTimeout, 60_000);
    }
  } else {
    localStorage.removeItem(STORAGE_KEY);
    if (idleCheckInterval) {
      clearInterval(idleCheckInterval);
      idleCheckInterval = null;
    }
  }
});

for (const evt of ["click", "touchstart", "keydown", "scroll"]) {
  document.addEventListener(evt, updateActivity, { passive: true });
}

document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible") checkIdleTimeout();
});
