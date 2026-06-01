/**
 * Voice recording — microphone handling, cooldown, spacebar shortcuts
 */

import { $, auth, API, getToken, setCurrentAuth, signOut } from "./config.js";
import { renderResults, showToast } from "./ui.js";

let mediaRecorder, audioChunks = [], activeStream;
const recordBtn = $("recordBtn");
const statusEl  = $("status");
const resultEl  = $("result");

const startRecording = async e => {
  if (e.cancelable) e.preventDefault();
  if (!getToken()) return;
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
      const token = await auth.currentUser.getIdToken();
      setCurrentAuth(token, auth.currentUser.uid);

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
const appView = $("app-view");
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
