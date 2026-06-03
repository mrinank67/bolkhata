/**
 * BolKhata — Main entry point
 * 
 * All logic is split into modules under js/:
 *   config.js    — Firebase init, API config, shared state
 *   auth.js      — Phone, Google, Email auth + logout
 *   recording.js — Mic recording, voice processing, cooldown
 *   ui.js        — Table renderer, toast, navigation, drawer
 *   dashboard.js — Inventory grid, edit/delete modals
 *   history.js   — Transaction history
 *   suppliers.js — Supplier directory & purchases
 *   ledger.js    — Customer ledger & WhatsApp reminders
 */

// Import all modules — each self-registers its event listeners on import
import "./js/config.js";
import "./js/auth.js";
import "./js/idle-timer.js";
import "./js/ui.js";
import "./js/recording.js";
import "./js/dashboard.js";
import "./js/history.js";
import "./js/suppliers.js";
import "./js/ledger.js";

// ═══════ PWA SERVICE WORKER — AUTO-UPDATE ═══════
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
