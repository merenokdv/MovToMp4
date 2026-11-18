const dropzone = document.getElementById("dropzone");
const fileInput = document.getElementById("file");
const modeSelect = document.getElementById("mode");
const statusEl = document.getElementById("status");
const queueEl = document.getElementById("queue");

const jobs = new Map();

function setStatus(text) {
  statusEl.textContent = text;
}

function ensureQueueVisible() {
  queueEl.hidden = jobs.size === 0;
}

function cardHtml(job) {
  const pct = Math.round(job.progress || 0);
  const meta =
    job.status === "done"
      ? "Готово"
      : job.status === "error"
        ? job.message || "Ошибка"
        : `${pct}% · ${job.message || "Конвертация…"}`;

  const actions =
    job.status === "done"
      ? `<div class="card-actions">
          <a class="btn" href="/api/download/${job.id}">Скачать MP4</a>
          <button class="btn secondary" type="button" data-open="${job.id}">Открыть папку</button>
        </div>`
      : "";

  return `
    <article class="card ${job.status}" data-id="${job.id}">
      <div class="card-top">
        <p class="card-name">${escapeHtml(job.source || "video.mov")}</p>
        <p class="card-meta">${escapeHtml(meta)}</p>
      </div>
      <div class="bar" aria-hidden="true"><i style="width:${pct}%"></i></div>
      ${actions}
    </article>
  `;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function render() {
  ensureQueueVisible();
  queueEl.innerHTML = [...jobs.values()]
    .slice()
    .reverse()
    .map(cardHtml)
    .join("");
}

async function poll(jobId) {
  try {
    const res = await fetch(`/api/jobs/${jobId}`);
    if (!res.ok) throw new Error("Задача не найдена");
    const data = await res.json();
    jobs.set(jobId, { ...jobs.get(jobId), ...data });
    render();

    if (data.status === "done") {
      setStatus("Конвертация завершена");
      return;
    }
    if (data.status === "error") {
      setStatus(data.message || "Ошибка конвертации");
      return;
    }
    window.setTimeout(() => poll(jobId), 400);
  } catch (err) {
    const current = jobs.get(jobId) || { id: jobId };
    jobs.set(jobId, {
      ...current,
      status: "error",
      message: err.message || "Сбой опроса",
    });
    render();
    setStatus("Не удалось получить статус");
  }
}

async function convertFile(file) {
  if (!file) return;
  if (!/\.mov$/i.test(file.name)) {
    setStatus("Нужен файл с расширением .mov");
    return;
  }

  const tempId = `local-${Date.now()}`;
  jobs.set(tempId, {
    id: tempId,
    source: file.name,
    status: "queued",
    progress: 0,
    message: "Загрузка…",
  });
  render();
  setStatus(`Загружаю ${file.name}…`);

  const form = new FormData();
  form.append("file", file, file.name);
  form.append("mode", modeSelect.value);

  try {
    const res = await fetch("/api/convert", { method: "POST", body: form });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Ошибка сервера");

    jobs.delete(tempId);
    jobs.set(data.id, {
      id: data.id,
      source: file.name,
      status: "queued",
      progress: 0,
      message: "В очереди",
    });
    render();
    setStatus("Конвертация…");
    poll(data.id);
  } catch (err) {
    jobs.set(tempId, {
      id: tempId,
      source: file.name,
      status: "error",
      progress: 0,
      message: err.message || "Не удалось начать",
    });
    render();
    setStatus(err.message || "Ошибка");
  }
}

function onFiles(fileList) {
  [...fileList].forEach(convertFile);
}

dropzone.addEventListener("click", () => fileInput.click());
dropzone.addEventListener("keydown", (e) => {
  if (e.key === "Enter" || e.key === " ") {
    e.preventDefault();
    fileInput.click();
  }
});

fileInput.addEventListener("change", () => {
  onFiles(fileInput.files);
  fileInput.value = "";
});

["dragenter", "dragover"].forEach((type) => {
  dropzone.addEventListener(type, (e) => {
    e.preventDefault();
    dropzone.classList.add("is-drag");
  });
});

["dragleave", "drop"].forEach((type) => {
  dropzone.addEventListener(type, (e) => {
    e.preventDefault();
    dropzone.classList.remove("is-drag");
  });
});

dropzone.addEventListener("drop", (e) => {
  onFiles(e.dataTransfer.files);
});

queueEl.addEventListener("click", async (e) => {
  const btn = e.target.closest("[data-open]");
  if (!btn) return;
  try {
    await fetch("/api/open-output");
    setStatus("Открыл папку output");
  } catch {
    setStatus("MP4 лежит в папке output рядом с приложением");
  }
});

fetch("/api/health")
  .then((r) => r.json())
  .then((data) => {
    if (!data.ffmpeg) {
      setStatus("ffmpeg не найден — установите: brew install ffmpeg");
    } else {
      setStatus("Готов к работе");
    }
  })
  .catch(() => setStatus("Сервер недоступен"));
