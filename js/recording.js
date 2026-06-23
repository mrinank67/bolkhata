/**
 * Voice recording — microphone handling, cooldown, spacebar shortcuts
 */

import { $, auth, API, getToken, setCurrentAuth, signOut } from "./config.js";
import { renderResults, showToast, getCurrentPage } from "./ui.js";

let mediaRecorder, audioChunks = [], activeStream, isPressed = false;
const recordBtn = $("recordBtn");
const statusEl  = $("status");
const resultEl  = $("result");

const stopStream = stream => stream?.getTracks().forEach(t => t.stop());

const startRecording = async e => {
  if (e.cancelable) e.preventDefault();
  if (!getToken()) return;
  if (recordBtn.disabled) return; // Cooldown active
  if (mediaRecorder && mediaRecorder.state !== "inactive") return; // Already recording

  isPressed = true;
  try {
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        noiseSuppression: true,
        echoCancellation: true,
        autoGainControl: true
      }
    });

    // iOS Safari: getUserMedia can resolve after the press was already
    // released (slow grant / permission dialog) — don't start an
    // unstoppable recording, release the mic immediately.
    if (!isPressed) {
      stopStream(stream);
      return;
    }

    // Never leak a previous stream if one is somehow still live
    stopStream(activeStream);
    activeStream = stream;
    mediaRecorder = new MediaRecorder(stream);
    audioChunks = [];

    mediaRecorder.ondataavailable = ev => audioChunks.push(ev.data);

    mediaRecorder.onstop = async () => {
      stopStream(stream);
      if (activeStream === stream) activeStream = null;
      // Block new recordings while the request is in flight; the timed
      // cooldown in `finally` re-enables the button.
      recordBtn.disabled = true;
      recordBtn.classList.add("cooldown");
      let token;
      try {
        token = await auth.currentUser.getIdToken();
        setCurrentAuth(token, auth.currentUser.uid);
      } catch {
        statusEl.innerText = "Offline";
        applyRecordCooldown(2);
        return;
      }

      statusEl.innerText = "Processing";
      statusEl.classList.add("processing-dots");

      const blob = new Blob(audioChunks, { type: "audio/webm" });
      const form = new FormData();
      form.append("audio", blob, "recording.webm");

      try {
        const res = await fetch(`${API}/process_voice`, {
          method: "POST",
          headers: { Authorization: `Bearer ${token}` },
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
let recordCooldownUntil = 0;
function applyRecordCooldown(seconds) {
  const until = Date.now() + seconds * 1000;
  if (until <= recordCooldownUntil) return; // longer cooldown already running
  recordCooldownUntil = until;
  recordBtn.disabled = true;
  recordBtn.classList.add("cooldown");
  clearTimeout(recordCooldownTimer);
  recordCooldownTimer = setTimeout(() => {
    recordCooldownUntil = 0;
    recordBtn.disabled = false;
    recordBtn.classList.remove("cooldown");
  }, seconds * 1000);
}


const stopRecording = e => {
  if (e.cancelable) e.preventDefault();
  isPressed = false;
  if (mediaRecorder && mediaRecorder.state !== "inactive") {
    mediaRecorder.stop();
    recordBtn.classList.remove("recording");
  }
};

// Release the mic if the page is backgrounded mid-recording (iOS Safari
// suspends the recorder and onstop may never fire otherwise)
const releaseOnHide = () => {
  isPressed = false;
  if (mediaRecorder && mediaRecorder.state !== "inactive") {
    try { mediaRecorder.stop(); } catch { /* already stopped */ }
  } else {
    stopStream(activeStream);
    activeStream = null;
  }
  recordBtn.classList.remove("recording");
};
window.addEventListener("pagehide", releaseOnHide);
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "hidden") releaseOnHide();
});

recordBtn.addEventListener("mousedown", startRecording);
recordBtn.addEventListener("mouseup", stopRecording);
recordBtn.addEventListener("mouseleave", stopRecording);
recordBtn.addEventListener("touchstart", startRecording, { passive: false });
recordBtn.addEventListener("touchend", stopRecording);
recordBtn.addEventListener("touchcancel", stopRecording);

// Spacebar to Record (Desktop) — only on the voice page. The mic button is
// hidden on other pages, but the spacebar listener is global, so without this
// guard pressing space on the ledger/suppliers page would silently record
// (no visible feedback, no page refresh) and still hit the server.
const appView = $("app-view");
const onVoicePage = () => !appView.classList.contains("hidden") && getCurrentPage() === "voice";
window.addEventListener("keydown", e => {
  if (e.code === "Space" && onVoicePage()) {
    if (document.activeElement.tagName === "INPUT" || document.activeElement.tagName === "TEXTAREA") return;
    e.preventDefault();
    if (!recordBtn.classList.contains("recording")) {
      startRecording(e);
    }
  }
});
window.addEventListener("keyup", e => {
  if (e.code === "Space" && onVoicePage()) {
    if (document.activeElement.tagName === "INPUT" || document.activeElement.tagName === "TEXTAREA") return;
    e.preventDefault();
    stopRecording(e);
  }
});
