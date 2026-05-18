const API_BASE = window.location.protocol === "file:" ? "http://127.0.0.1:5000" : "";

const elements = {
  fetchForm: document.querySelector("#fetchForm"),
  videoUrl: document.querySelector("#videoUrl"),
  fetchButton: document.querySelector("#fetchButton"),
  downloadButton: document.querySelector("#downloadButton"),
  statusMessage: document.querySelector("#statusMessage"),
  thumbnail: document.querySelector("#thumbnail"),
  thumbnailEmpty: document.querySelector("#thumbnailEmpty"),
  videoTitle: document.querySelector("#videoTitle"),
  durationText: document.querySelector("#durationText"),
  uploaderText: document.querySelector("#uploaderText"),
  platformBadge: document.querySelector("#platformBadge"),
  qualitySelect: document.querySelector("#qualitySelect"),
  formatSelect: document.querySelector("#formatSelect"),
  playlistToggle: document.querySelector("#playlistToggle"),
  subtitlesToggle: document.querySelector("#subtitlesToggle"),
  thumbnailToggle: document.querySelector("#thumbnailToggle"),
  progressLabel: document.querySelector("#progressLabel"),
  progressPercent: document.querySelector("#progressPercent"),
  progressFill: document.querySelector("#progressFill"),
  speedText: document.querySelector("#speedText"),
  etaText: document.querySelector("#etaText"),
  resultArea: document.querySelector("#resultArea"),
  historyList: document.querySelector("#historyList"),
  refreshHistory: document.querySelector("#refreshHistory"),
  qualityCount: document.querySelector("#qualityCount"),
  formatCount: document.querySelector("#formatCount"),
  ytStatus: document.querySelector("#ytStatus"),
  ffmpegStatus: document.querySelector("#ffmpegStatus"),
  dropZone: document.querySelector("#dropZone"),
};

const state = {
  metadata: null,
  progressSource: null,
  activeDownloadKey: null,
};

function setStatus(message, type = "info") {
  elements.statusMessage.textContent = message;
  elements.statusMessage.classList.toggle("success", type === "success");
  elements.statusMessage.classList.toggle("error", type === "error");
}

function setProgress(percent, label = "Idle", speed = null, eta = null) {
  const safePercent = Math.max(0, Math.min(100, Number(percent) || 0));
  elements.progressFill.style.width = `${safePercent}%`;
  elements.progressPercent.textContent = `${Math.round(safePercent)}%`;
  elements.progressLabel.textContent = label;
  elements.speedText.textContent = `Speed: ${speed || "--"}`;
  elements.etaText.textContent = `ETA: ${eta || "--"}`;
}

function setLoading(isLoading) {
  elements.fetchButton.disabled = isLoading;
  elements.fetchButton.querySelector("span").textContent = isLoading ? "Fetching" : "Fetch Video";
}

function clearSelect(select, placeholder) {
  select.innerHTML = "";
  const option = document.createElement("option");
  option.value = "";
  option.textContent = placeholder;
  select.append(option);
}

function addOption(select, value, label, selected = false) {
  const option = document.createElement("option");
  option.value = value;
  option.textContent = label;
  option.selected = selected;
  select.append(option);
}

function bestDefaultQuality(qualities) {
  const preferred = ["720p", "1080p", "480p", "360p", "1440p", "4K", "240p", "144p"];
  const available = new Set(qualities.map((quality) => quality.label));
  return preferred.find((label) => available.has(label)) || qualities.at(-1)?.label || "";
}

function populateMetadata(metadata) {
  state.metadata = metadata;
  elements.videoTitle.textContent = metadata.title || "Untitled video";
  elements.durationText.textContent = `Duration: ${metadata.durationText || "--"}`;
  elements.uploaderText.textContent = metadata.uploader ? `By ${metadata.uploader}` : "Uploader unavailable";
  elements.platformBadge.textContent = metadata.playlist?.isPlaylist
    ? `${metadata.platform || "Playlist"} playlist`
    : metadata.platform || "Video";

  if (metadata.thumbnail) {
    elements.thumbnail.src = metadata.thumbnail;
    elements.thumbnail.hidden = false;
    elements.thumbnailEmpty.hidden = true;
  } else {
    elements.thumbnail.removeAttribute("src");
    elements.thumbnail.hidden = true;
    elements.thumbnailEmpty.hidden = false;
  }

  clearSelect(elements.qualitySelect, "Choose quality");
  const defaultQuality = bestDefaultQuality(metadata.qualities || []);
  for (const quality of metadata.qualities || []) {
    const size = quality.sizeDisplay ? ` · ${quality.sizeDisplay}` : "";
    addOption(elements.qualitySelect, quality.label, `${quality.label}${size}`, quality.label === defaultQuality);
  }
  elements.qualitySelect.disabled = !(metadata.qualities || []).length;

  clearSelect(elements.formatSelect, "Choose format");
  const formats = metadata.formats || [];
  for (const format of formats) {
    addOption(elements.formatSelect, format.value, `${format.label} · ${format.kind}`, format.value === "mp4");
  }
  elements.formatSelect.disabled = !formats.length;

  elements.downloadButton.disabled = !defaultQuality || !formats.length;
  elements.qualityCount.textContent = String((metadata.qualities || []).length);
  elements.formatCount.textContent = String(formats.length);
}

async function postJson(path, body) {
  const response = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.error || "Request failed.");
  }
  return payload;
}

async function fetchHealth() {
  try {
    const response = await fetch(`${API_BASE}/health`);
    const payload = await response.json();
    updatePill(elements.ytStatus, "yt-dlp", payload.ytDlpAvailable);
    updatePill(elements.ffmpegStatus, "FFmpeg", payload.ffmpegAvailable);
  } catch {
    updatePill(elements.ytStatus, "yt-dlp", false);
    updatePill(elements.ffmpegStatus, "FFmpeg", false);
  }
}

function updatePill(element, label, online) {
  element.textContent = `${label} ${online ? "ready" : "missing"}`;
  element.classList.toggle("online", Boolean(online));
  element.classList.toggle("offline", !online);
}

async function handleFetch(event) {
  event.preventDefault();
  const url = elements.videoUrl.value.trim();
  if (!url) {
    setStatus("Enter a video URL first.", "error");
    return;
  }

  setLoading(true);
  elements.downloadButton.disabled = true;
  elements.resultArea.hidden = true;
  setProgress(0, "Fetching metadata");
  setStatus("Fetching metadata with yt-dlp...");

  try {
    const metadata = await postJson("/api/metadata", {
      url,
      playlist: elements.playlistToggle.checked,
    });
    populateMetadata(metadata);
    const playlistText = metadata.playlist?.isPlaylist && metadata.playlist?.count
      ? ` Playlist items detected: ${metadata.playlist.count}.`
      : "";
    setStatus(`Metadata ready.${playlistText}`, "success");
    setProgress(0, "Ready");
  } catch (error) {
    setStatus(error.message, "error");
    setProgress(0, "Failed");
  } finally {
    setLoading(false);
  }
}

function currentDownloadKey() {
  return JSON.stringify({
    url: elements.videoUrl.value.trim(),
    quality: elements.qualitySelect.value,
    format: elements.formatSelect.value,
    playlist: elements.playlistToggle.checked,
    subtitles: elements.subtitlesToggle.checked,
    thumbnail: elements.thumbnailToggle.checked,
  });
}

async function handleDownload() {
  const url = elements.videoUrl.value.trim();
  const quality = elements.qualitySelect.value;
  const format = elements.formatSelect.value;
  if (!url || !quality || !format) {
    setStatus("Fetch metadata and choose a quality and format first.", "error");
    return;
  }

  const key = currentDownloadKey();
  if (state.activeDownloadKey === key) {
    setStatus("This download is already running.", "error");
    return;
  }

  elements.downloadButton.disabled = true;
  elements.resultArea.hidden = true;
  state.activeDownloadKey = key;
  setProgress(0, "Queued");
  setStatus("Starting download...");

  try {
    const payload = await postJson("/api/download", {
      url,
      quality,
      format,
      playlist: elements.playlistToggle.checked,
      subtitles: elements.subtitlesToggle.checked,
      thumbnail: elements.thumbnailToggle.checked,
    });
    if (payload.duplicate) {
      setStatus("Matching download found. Showing existing task.", "success");
    }
    watchProgress(payload.taskId);
  } catch (error) {
    state.activeDownloadKey = null;
    elements.downloadButton.disabled = false;
    setStatus(error.message, "error");
    setProgress(0, "Failed");
  }
}

function watchProgress(taskId) {
  if (state.progressSource) {
    state.progressSource.close();
  }

  state.progressSource = new EventSource(`${API_BASE}/api/progress/${taskId}`);
  state.progressSource.onmessage = (event) => {
    const task = JSON.parse(event.data);
    const label = task.message || task.status || "Working";
    setProgress(task.progress || 0, label, task.speed, task.eta);

    if (task.status === "completed") {
      state.progressSource.close();
      state.activeDownloadKey = null;
      elements.downloadButton.disabled = false;
      setStatus("Download completed.", "success");
      renderResult(task);
      loadHistory();
    }

    if (task.status === "failed" || task.status === "missing") {
      state.progressSource.close();
      state.activeDownloadKey = null;
      elements.downloadButton.disabled = false;
      setStatus(task.error || "Download failed.", "error");
    }
  };

  state.progressSource.onerror = () => {
    state.progressSource.close();
    state.activeDownloadKey = null;
    elements.downloadButton.disabled = false;
    setStatus("Progress connection closed.", "error");
  };
}

function renderResult(task) {
  elements.resultArea.innerHTML = "";
  const title = document.createElement("strong");
  title.textContent = task.playlist ? "Playlist artifacts are ready." : "File is ready.";
  elements.resultArea.append(title);

  if (task.fileUrl) {
    elements.resultArea.append(" ");
    const link = document.createElement("a");
    link.href = `${API_BASE}${task.fileUrl}`;
    link.textContent = "Download file";
    elements.resultArea.append(link);
  } else if (task.artifacts?.length) {
    const list = document.createElement("div");
    list.className = "history-list";
    for (const artifact of task.artifacts.slice(0, 6)) {
      const link = document.createElement("a");
      link.href = `${API_BASE}${artifact.url}`;
      link.textContent = `${artifact.name} (${artifact.sizeDisplay || "size unknown"})`;
      list.append(link);
    }
    elements.resultArea.append(list);
  }

  elements.resultArea.hidden = false;
}

async function loadHistory() {
  try {
    const response = await fetch(`${API_BASE}/api/history`);
    const payload = await response.json();
    renderHistory(payload.tasks || []);
  } catch {
    renderHistory([]);
  }
}

function renderHistory(tasks) {
  elements.historyList.innerHTML = "";
  if (!tasks.length) {
    const empty = document.createElement("p");
    empty.className = "muted";
    empty.textContent = "No downloads yet.";
    elements.historyList.append(empty);
    return;
  }

  for (const task of tasks) {
    const item = document.createElement("article");
    item.className = "history-item";

    const title = document.createElement("div");
    title.className = "history-title";
    title.textContent = `${task.format?.toUpperCase() || "FILE"} · ${task.quality || "quality"}`;
    item.append(title);

    const meta = document.createElement("div");
    meta.className = "history-meta";
    const status = document.createElement("span");
    status.textContent = task.status || "unknown";
    const date = document.createElement("span");
    date.textContent = task.createdAt ? new Date(task.createdAt).toLocaleTimeString() : "";
    meta.append(status, date);
    item.append(meta);

    if (task.fileUrl) {
      const link = document.createElement("a");
      link.href = `${API_BASE}${task.fileUrl}`;
      link.textContent = "Download";
      item.append(link);
    }

    elements.historyList.append(item);
  }
}

function setupDragAndDrop() {
  const stop = (event) => {
    event.preventDefault();
    event.stopPropagation();
  };

  ["dragenter", "dragover"].forEach((name) => {
    elements.dropZone.addEventListener(name, (event) => {
      stop(event);
      elements.dropZone.classList.add("drag-over");
    });
  });

  ["dragleave", "drop"].forEach((name) => {
    elements.dropZone.addEventListener(name, (event) => {
      stop(event);
      elements.dropZone.classList.remove("drag-over");
    });
  });

  elements.dropZone.addEventListener("drop", (event) => {
    const text = event.dataTransfer.getData("text").trim();
    if (text) {
      elements.videoUrl.value = text;
      setStatus("URL added.");
    }
  });
}

elements.fetchForm.addEventListener("submit", handleFetch);
elements.downloadButton.addEventListener("click", handleDownload);
elements.refreshHistory.addEventListener("click", loadHistory);
elements.playlistToggle.addEventListener("change", () => {
  if (elements.videoUrl.value.trim()) {
    setStatus("Playlist mode changed. Fetch metadata again.");
  }
});

setupDragAndDrop();
fetchHealth();
loadHistory();
