/**
 * voice.js — the screen the panel exists for.
 *
 * Lays each voice request out as the pipeline actually ran: what Sarvam heard,
 * what the LLM made of it, and what the database did about it. Reading the three
 * side by side is what separates "STT misheard the item" from "the LLM parsed it
 * wrong" from "the item isn't in their inventory" — three complaints that sound
 * identical over the phone.
 */

import { $, adminFetch, empty, esc, getActingUid, ms, renderError, when } from "./core.js";

const STATUS_LABEL = {
  ok: "Succeeded",
  stt_empty: "Heard nothing",
  stt_error: "STT failed",
  llm_error: "Intent failed",
  rate_limited: "Rate limited",
  audio_too_short: "Audio too short",
  audio_too_long: "Audio too long",
  resolve: "Disambiguation"
};

// Anything not listed is a failure, and failures share one loud colour: a new
// status added server-side should stand out here rather than render as neutral.
const STATUS_KIND = { ok: "good", resolve: "info", rate_limited: "warn" };

function statusBadge(status) {
  const kind = STATUS_KIND[status] || "bad";
  const label = STATUS_LABEL[status] || status || "unknown";
  return `<span class="admin-badge ${kind}">${esc(label)}</span>`;
}

/** Pretty-print the stored intent, falling back to the raw text.
 *
 * The value is whatever the LLM emitted. When that is not valid JSON — which is
 * exactly the case worth looking at — showing the raw string beats showing
 * nothing.
 */
function formatIntent(raw) {
  if (!raw) return "";
  try {
    return JSON.stringify(JSON.parse(raw), null, 2);
  } catch {
    return raw;
  }
}

function timingBar(entry) {
  const parts = [
    ["STT", entry.stt_ms],
    ["Intent", entry.llm_ms],
    ["Database", entry.db_ms],
    ["Total", entry.total_ms]
  ].filter(([, v]) => v !== null && v !== undefined);

  if (!parts.length) return "";
  return `<div class="admin-timings">${parts
    .map(([label, v]) => `<span><b>${label}</b> ${esc(ms(v))}</span>`)
    .join("")}</div>`;
}

function resultRows(results) {
  if (!Array.isArray(results) || !results.length) return "";
  return results
    .map(group => {
      const rows = (group.rows || [])
        .map(row => `<li>${esc(Array.isArray(row) ? row.join(" · ") : row)}</li>`)
        .join("");
      return `
        <div class="admin-result-group">
          <div class="admin-result-title">${esc(group.icon || "")} ${esc(group.title || group.action || "")}</div>
          <ul>${rows}</ul>
        </div>`;
    })
    .join("");
}

function contextNote(entry) {
  if (!entry.recent_customer && !entry.recent_order_id) return "";
  const who = entry.recent_customer
    ? `${esc(entry.recent_customer)}${entry.recent_modifier ? ` (${esc(entry.recent_modifier)})` : ""}`
    : "—";
  return `
    <div class="admin-context-note">
      <b>Context given to the model:</b> recent customer ${who}
      ${entry.recent_order_id ? `· appends to order <code>${esc(entry.recent_order_id)}</code>` : ""}
    </div>`;
}

function buildEntry(entry) {
  const audio = entry.audio_size
    ? `${(entry.audio_size / 1024).toFixed(1)} KB ${esc(entry.audio_mime || "")}`
    : "no audio read";

  const transcript = entry.transcript
    ? `<blockquote class="admin-transcript">${esc(entry.transcript)}</blockquote>`
    : `<div class="admin-none">Nothing was transcribed.</div>`;

  const intent = entry.intent
    ? `<pre class="admin-json">${esc(formatIntent(entry.intent))}</pre>`
    : `<div class="admin-none">The model was never reached.</div>`;

  const errors = (entry.errors || []).length
    ? `<div class="admin-errors"><b>Reported to the shopkeeper</b><ul>${entry.errors
        .map(e => `<li>${esc(e)}</li>`)
        .join("")}</ul></div>`
    : "";

  const detail = entry.error_detail
    ? `<div class="admin-errors internal"><b>Server-side reason</b> (never shown to the shopkeeper)<pre>${esc(
        entry.error_detail
      )}</pre></div>`
    : "";

  const outcome = resultRows(entry.results);

  return `
    <article class="admin-card voice-entry">
      <header class="admin-card-head">
        <div>${statusBadge(entry.status)} <span class="admin-when">${esc(when(entry.timestamp))}</span></div>
        <div class="admin-meta">${esc(audio)} · ${esc(entry.stt_model || "?")} → ${esc(entry.llm_model || "?")}</div>
      </header>

      ${contextNote(entry)}

      <div class="admin-pipeline">
        <section>
          <h4>1 · Heard <span class="admin-hint">Sarvam</span></h4>
          ${transcript}
        </section>
        <section>
          <h4>2 · Understood <span class="admin-hint">Groq</span></h4>
          ${intent}
        </section>
        <section>
          <h4>3 · Applied <span class="admin-hint">${
            entry.transaction_count === null || entry.transaction_count === undefined
              ? "—"
              : `${entry.transaction_count} transaction(s)`
          }</span></h4>
          ${outcome || `<div class="admin-none">Nothing was written.</div>`}
          ${errors}
        </section>
      </div>

      ${detail}
      ${timingBar(entry)}
    </article>`;
}

export async function loadVoiceLogs() {
  const list = $("admin-voice-list");
  const uid = getActingUid();
  if (!uid) return;

  list.innerHTML = `<div class="admin-empty">Loading…</div>`;

  const status = $("admin-voice-status").value;
  const query = status ? `?status=${encodeURIComponent(status)}` : "";

  try {
    // An /admin/* route: the uid is in the path, so no acting header.
    const data = await adminFetch(
      `/admin/users/${encodeURIComponent(uid)}/voice-logs${query}`,
      { acting: false }
    );
    const entries = data.voice_logs || [];
    if (!entries.length) {
      empty(
        list,
        status
          ? "No voice requests with that outcome in the last 30 days."
          : "No voice requests recorded. Logs are kept for 30 days."
      );
      return;
    }
    list.innerHTML = entries.map(buildEntry).join("");
  } catch (err) {
    renderError(list, err);
  }
}

export function wireVoicePanel() {
  $("admin-voice-refresh").addEventListener("click", loadVoiceLogs);
  $("admin-voice-status").addEventListener("change", loadVoiceLogs);
}
