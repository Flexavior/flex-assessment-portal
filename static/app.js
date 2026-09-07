const form = document.getElementById("upload-form");
const fileInput = document.getElementById("file-input");
const fileName = document.getElementById("file-name");
const fileMap = document.getElementById("file-map");
const dropzone = document.getElementById("dropzone");
const submitBtn = document.getElementById("submit-btn");
const statusEl = document.getElementById("status");
const statusText = document.getElementById("status-text");
const queuePanel = document.getElementById("queue-panel");
const queueSummary = document.getElementById("queue-summary");
const jobList = document.getElementById("job-list");
const providerSelect = document.getElementById("provider-select");
const modelSelect = document.getElementById("model-select");

let providerCatalog = [];
let studentCatalog = [];
let pollTimer = null;
let trackedJobIds = new Set();

function esc(value) {
  return String(value || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/"/g, "&quot;");
}

async function loadProviders() {
  const data = await api("/api/providers");
  providerCatalog = data.providers || [];
  providerSelect.innerHTML = "";
  if (!providerCatalog.length) {
    providerSelect.disabled = true;
    modelSelect.disabled = true;
    submitBtn.disabled = true;
    return;
  }
  providerCatalog.forEach((provider) => {
    const option = document.createElement("option");
    option.value = provider.id;
    option.textContent = provider.label;
    if (provider.is_default || provider.id === data.default_provider) option.selected = true;
    providerSelect.appendChild(option);
  });
  populateModels();
}

async function loadStudents() {
  const data = await api("/api/students");
  studentCatalog = data.students || [];
}

function populateModels() {
  const selected = providerCatalog.find((p) => p.id === providerSelect.value);
  modelSelect.innerHTML = "";
  if (!selected) return;
  selected.models.forEach((modelName, index) => {
    const option = document.createElement("option");
    option.value = modelName;
    option.textContent = modelName;
    if (index === 0 || modelName === selected.model) option.selected = true;
    modelSelect.appendChild(option);
  });
}

providerSelect.addEventListener("change", populateModels);

function guessStudentId(filename) {
  const base = filename.replace(/\.[^.]+$/, "").toLowerCase().replace(/[^a-z0-9]+/g, "");
  for (const student of studentCatalog) {
    const name = String(student.full_name || "").toLowerCase().replace(/[^a-z0-9]+/g, "");
    const email = String(student.email || "").split("@")[0].toLowerCase().replace(/[^a-z0-9]+/g, "");
    if (name.length > 3 && base.includes(name)) return String(student.id);
    if (email.length > 2 && base.includes(email)) return String(student.id);
  }
  return "";
}

function studentOptions(selectedId) {
  const rows = [`<option value="">— Unmapped —</option>`];
  studentCatalog.forEach((student) => {
    const label = [student.full_name || student.email, student.batch_name].filter(Boolean).join(" · ");
    rows.push(`<option value="${student.id}"${String(student.id) === selectedId ? " selected" : ""}>${esc(label)}</option>`);
  });
  return rows.join("");
}

function renderFileMap() {
  const files = fileInput.files;
  if (!files || !files.length) {
    fileMap.innerHTML = "";
    fileMap.classList.add("hidden");
    return;
  }
  fileMap.classList.remove("hidden");
  fileMap.innerHTML = `
    <p class="file-map-title">Map files to student accounts</p>
    <ul class="file-map-list">
      ${[...files].map((file, index) => {
        const guess = guessStudentId(file.name);
        return `<li class="file-map-row">
          <span class="file-map-name" title="${esc(file.name)}">${esc(file.name)}</span>
          <select id="student-map-${index}" class="file-map-select">${studentOptions(guess)}</select>
        </li>`;
      }).join("")}
    </ul>`;
}

function updateFileLabel() {
  const files = fileInput.files;
  if (!files || !files.length) {
    fileName.textContent = "";
    renderFileMap();
    return;
  }
  fileName.textContent = files.length === 1 ? files[0].name : `${files.length} files selected`;
  renderFileMap();
}

fileInput.addEventListener("change", updateFileLabel);

["dragenter", "dragover"].forEach((eventName) => {
  dropzone.addEventListener(eventName, (e) => {
    e.preventDefault();
    dropzone.classList.add("drag");
  });
});

["dragleave", "drop"].forEach((eventName) => {
  dropzone.addEventListener(eventName, (e) => {
    e.preventDefault();
    dropzone.classList.remove("drag");
  });
});

dropzone.addEventListener("drop", (e) => {
  fileInput.files = e.dataTransfer.files;
  updateFileLabel();
});

function statusLabel(status) {
  return ({ queued: "Queued", running: "Grading…", done: "Done", failed: "Failed" }[status] || status);
}

function renderJobs(jobs) {
  jobList.innerHTML = "";
  jobs
    .filter((j) => trackedJobIds.has(j.id))
    .sort((a, b) => a.created_at.localeCompare(b.created_at))
    .forEach((job) => {
      const li = document.createElement("li");
      li.className = `job-item job-${job.status}`;
      let html = `<strong>${esc(job.filename)}</strong> — ${statusLabel(job.status)}`;
      if (job.status === "done") {
        html += job.total_score_percent != null ? ` — ${job.total_score_percent}%` : "";
        if (job.report) {
          html += ` — <a href="/reports/${encodeURIComponent(job.report)}" target="_blank" rel="noopener">PDF</a>`;
        }
      }
      if (job.status === "failed" && job.error) {
        html += `<br><span class="error">${esc(job.error)}</span>`;
      }
      if (job.status === "running") {
        html += `<br><span class="hint">Writing report to data/reports/ when complete…</span>`;
      }
      li.innerHTML = html;
      jobList.appendChild(li);
    });
}

async function pollJobs() {
  try {
    const jobs = (
      await Promise.all(
        [...trackedJobIds].map((id) => api("/api/jobs/" + id).catch(() => null))
      )
    ).filter(Boolean);
    renderJobs(jobs);
    const active = jobs.filter((j) => j.status === "queued" || j.status === "running").length;
    const done = jobs.filter((j) => j.status === "done").length;
    const failed = jobs.filter((j) => j.status === "failed").length;
    queueSummary.textContent = `${done} done, ${failed} failed, ${active} in this batch`;
    if (active === 0 && jobs.length > 0) {
      statusEl.classList.add("hidden");
      submitBtn.disabled = false;
      if (pollTimer) {
        clearInterval(pollTimer);
        pollTimer = null;
      }
    }
  } catch (err) {
    queueSummary.textContent = `Could not refresh queue: ${err.message}`;
  }
}

function startPolling() {
  queuePanel.classList.remove("hidden");
  if (pollTimer) clearInterval(pollTimer);
  pollJobs();
  pollTimer = setInterval(pollJobs, 2500);
}

function collectStudentIds(fileCount) {
  const ids = [];
  for (let index = 0; index < fileCount; index += 1) {
    const select = document.getElementById(`student-map-${index}`);
    ids.push(select && select.value ? Number(select.value) : null);
  }
  return ids;
}

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const files = fileInput.files;
  if (!files || !files.length) return;

  submitBtn.disabled = true;
  statusEl.classList.remove("hidden");
  statusText.textContent = `Uploading ${files.length} file(s)…`;

  const provider = providerSelect.value;
  const model = modelSelect.value;

  try {
    const body = new FormData();
    for (const file of files) body.append("files", file);
    body.append("student_ids", JSON.stringify(collectStudentIds(files.length)));
    const uploaded = await api("/upload-batch/", { method: "POST", body, headers: {} });

    statusText.textContent = `Queued ${uploaded.count} file(s) for grading…`;

    const graded = await api("/grade/batch", {
      method: "POST",
      body: JSON.stringify({
        filenames: uploaded.uploaded.map((u) => u.filename),
        provider,
        model,
        compare: true,
      }),
    });

    graded.jobs.forEach((j) => trackedJobIds.add(j.id));
    statusText.textContent = `${graded.queued} assessment(s) queued. Reports appear in data/reports/.`;
    startPolling();
  } catch (err) {
    statusText.textContent = err.message || "Request failed";
    submitBtn.disabled = false;
  }
});

requireAuth(["supervisor", "educator"]).then(async () => {
  try {
    await Promise.all([loadProviders(), loadStudents()]);
  } catch (err) {
    submitBtn.disabled = true;
  }
  onNavSearch(() => renderFileMap());
});
