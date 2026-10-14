const $ = (selector) => document.querySelector(selector);
const state = { selectedFile: null, events: [] };

const ui = {
  status: $("#systemStatus"),
  form: $("#uploadForm"),
  input: $("#videoInput"),
  dropzone: $("#dropzone"),
  selectedFile: $("#selectedFile"),
  urfdPreview: $("#urfdPreview"),
  button: $("#analyzeButton"),
  empty: $("#emptyResult"),
  result: $("#resultContent"),
  processing: $("#processing"),
  error: $("#errorBox"),
  timeline: $("#timeline"),
  eventRows: $("#eventRows"),
  dialog: $("#feedbackDialog"),
  feedbackForm: $("#feedbackForm"),
  toast: $("#toast"),
};

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;",
  })[character]);
}

function showToast(message) {
  ui.toast.textContent = message;
  ui.toast.classList.add("show");
  window.clearTimeout(showToast.timer);
  showToast.timer = window.setTimeout(() => ui.toast.classList.remove("show"), 2800);
}

async function api(path, options = {}) {
  const response = await fetch(path, options);
  let body;
  try { body = await response.json(); } catch { body = {}; }
  if (!response.ok) {
    const detail = Array.isArray(body.detail)
      ? body.detail.map((item) => item.msg).join(", ")
      : body.detail;
    throw new Error(detail || `HTTP ${response.status}`);
  }
  return body;
}

async function bootstrap() {
  try {
    const [health, model, metrics] = await Promise.all([
      api("/health"), api("/v1/model"), api("/v1/metrics/summary"),
    ]);
    ui.status.classList.add(health.status === "ok" ? "online" : "offline");
    ui.status.querySelector("span").textContent = health.status === "ok" ? "Hệ thống sẵn sàng" : "Hệ thống suy giảm";
    $("#heroModel").textContent = `${model.name} · ${model.version}`;
    if (model.fallback) $("#heroState").textContent = "SAFE MODE";
    renderMetrics(metrics);
  } catch (error) {
    ui.status.classList.add("offline");
    ui.status.querySelector("span").textContent = "Mất kết nối";
  }
  await loadEvents();
}

function renderMetrics(metrics) {
  $("#metricTotal").textContent = metrics.total_events ?? 0;
  $("#metricHigh").textContent = metrics.high_risk ?? 0;
  $("#metricRecovered").textContent = metrics.recovered ?? 0;
  $("#metricInactive").textContent = `${Number(metrics.average_inactive_seconds || 0).toFixed(1)}s`;
}

function pickFile(file) {
  if (!file) return;
  state.selectedFile = file;
  ui.selectedFile.textContent = `${file.name} · ${(file.size / 1024 / 1024).toFixed(1)} MB`;
  ui.urfdPreview.checked = /URFD|^(fall|adl)-\d+/i.test(file.name);
}

ui.input.addEventListener("change", () => pickFile(ui.input.files[0]));
["dragenter", "dragover"].forEach((name) => ui.dropzone.addEventListener(name, (event) => {
  event.preventDefault();
  ui.dropzone.classList.add("dragging");
}));
["dragleave", "drop"].forEach((name) => ui.dropzone.addEventListener(name, (event) => {
  event.preventDefault();
  ui.dropzone.classList.remove("dragging");
}));
ui.dropzone.addEventListener("drop", (event) => pickFile(event.dataTransfer.files[0]));

ui.form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const file = state.selectedFile || ui.input.files[0];
  if (!file) return showToast("Hãy chọn một video trước.");
  const form = new FormData();
  form.append("file", file);
  const camera = encodeURIComponent($("#cameraId").value.trim() || "UPLOAD");
  const person = $("#personId").value.trim();
  const query = `camera_id=${camera}`
    + `${person ? `&person_id=${encodeURIComponent(person)}` : ""}`
    + `&urfd_preview=${ui.urfdPreview.checked}`;
  setResultMode("processing");
  ui.button.disabled = true;
  try {
    const result = await api(`/v1/predict/video?${query}`, { method: "POST", body: form });
    renderResult(result);
    setResultMode("result");
    showToast("Phân tích hoàn tất.");
    await Promise.all([loadEvents(), refreshMetrics()]);
  } catch (error) {
    ui.error.textContent = `Không thể phân tích: ${error.message}`;
    setResultMode("error");
  } finally {
    ui.button.disabled = false;
  }
});

function setResultMode(mode) {
  ui.empty.classList.toggle("hidden", mode !== "empty");
  ui.result.classList.toggle("hidden", mode !== "result");
  ui.processing.classList.toggle("hidden", mode !== "processing");
  ui.error.classList.toggle("hidden", mode !== "error");
}

function renderResult(result) {
  const timeline = result.timeline || [];
  const last = timeline.at(-1);
  const event = last?.event;
  const finalState = event?.state || (result.events?.at(-1)?.state) || "NORMAL";
  const risk = event?.risk_level || (result.events?.at(-1)?.risk_level) || "LOW";
  $("#resultState").textContent = finalState;
  $("#resultState").style.color = risk === "HIGH" ? "var(--coral)" : risk === "MEDIUM" ? "var(--amber)" : "var(--mint)";
  $("#resultRisk").textContent = risk;
  $("#riskRing").style.borderTopColor = risk === "HIGH" ? "var(--coral)" : risk === "MEDIUM" ? "var(--amber)" : "var(--mint)";
  $("#resultFrames").textContent = Number(result.frame_count || 0).toLocaleString("vi-VN");
  $("#resultPoses").textContent = Number(result.pose_frame_count || 0).toLocaleString("vi-VN");
  $("#resultFps").textContent = `${Number(result.processing_fps || 0).toFixed(1)} FPS`;
  $("#timelineCount").textContent = `${timeline.length} mốc`;
  ui.timeline.innerHTML = timeline.slice(-100).map((point) => {
    const level = point.event?.risk_level?.toLowerCase() || "low";
    const title = `${(point.timestamp_ms / 1000).toFixed(1)}s · ${point.event?.state || "NORMAL"}`;
    return `<span class="timeline-dot ${escapeHtml(level)}" title="${escapeHtml(title)}"></span>`;
  }).join("") || '<span class="timeline-dot" title="Không phát hiện pose"></span>';
  setLink("#videoLink", result.output_video_url);
  setLink("#jsonLink", result.events_json_url);
  $("#heroState").textContent = finalState;
}

function setLink(selector, url) {
  const link = $(selector);
  link.classList.toggle("hidden", !url);
  if (url) link.href = url;
}

async function refreshMetrics() {
  try { renderMetrics(await api("/v1/metrics/summary")); } catch {}
}

async function loadEvents() {
  try {
    const response = await api("/v1/events?limit=25");
    state.events = response.items || [];
    renderEvents(state.events);
  } catch {
    ui.eventRows.innerHTML = '<tr><td class="loading-row" colspan="6">Không thể tải lịch sử sự kiện.</td></tr>';
  }
}

function renderEvents(events) {
  if (!events.length) {
    ui.eventRows.innerHTML = '<tr><td class="loading-row" colspan="6">Chưa có sự kiện được xác nhận.</td></tr>';
    return;
  }
  ui.eventRows.innerHTML = events.map((event) => {
    const timestamp = event.created_at ? new Date(event.created_at).toLocaleString("vi-VN") : `${event.timestamp_ms} ms`;
    const riskClass = event.risk_level === "HIGH" ? "high" : event.risk_level === "MEDIUM" ? "medium" : "";
    const feedback = event.feedback
      ? escapeHtml(event.feedback.label.replace("_", " "))
      : `<button class="feedback-button" data-event="${escapeHtml(event.event_id)}">Đánh giá</button>`;
    return `<tr>
      <td>${escapeHtml(timestamp)}</td>
      <td><strong>${escapeHtml(event.event_id)}</strong><small>${escapeHtml(event.camera_id || "—")}</small></td>
      <td><span class="badge">${escapeHtml(event.state)}</span></td>
      <td><span class="badge ${riskClass}">${escapeHtml(event.risk_level)}</span></td>
      <td>${Number(event.inactive_seconds || 0).toFixed(1)}s</td>
      <td>${feedback}</td>
    </tr>`;
  }).join("");
}

ui.eventRows.addEventListener("click", (event) => {
  const button = event.target.closest("[data-event]");
  if (!button) return;
  $("#feedbackEventId").value = button.dataset.event;
  ui.dialog.showModal();
});

ui.feedbackForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const eventId = $("#feedbackEventId").value;
  try {
    await api(`/v1/events/${encodeURIComponent(eventId)}/feedback`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        label: $("#feedbackLabel").value,
        notes: $("#feedbackNotes").value.trim() || null,
      }),
    });
    ui.dialog.close();
    showToast("Đã lưu phản hồi.");
    await Promise.all([loadEvents(), refreshMetrics()]);
  } catch (error) {
    showToast(`Không lưu được: ${error.message}`);
  }
});

$("#refreshEvents").addEventListener("click", () => Promise.all([loadEvents(), refreshMetrics()]));
bootstrap();
