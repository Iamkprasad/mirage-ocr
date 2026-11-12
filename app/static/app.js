const drop = document.getElementById("drop");
const fileInput = document.getElementById("file");
const dpiSelect = document.getElementById("dpi");
const status = document.getElementById("status");
const viewer = document.getElementById("viewer");
const textOut = document.getElementById("text");
const logOut = document.getElementById("log");
const copyBtn = document.getElementById("copy");
const downloadBtn = document.getElementById("download");
const barFill = document.getElementById("bar-fill");
const stageOut = document.getElementById("stage");

let lastResult = null;
let lastObjectUrl = null;
let running = false;

drop.addEventListener("click", () => fileInput.click());
fileInput.addEventListener("change", () => fileInput.files[0] && run(fileInput.files[0]));

["dragenter", "dragover"].forEach((event) =>
  drop.addEventListener(event, (e) => {
    e.preventDefault();
    drop.classList.add("over");
  })
);
["dragleave", "drop"].forEach((event) =>
  drop.addEventListener(event, (e) => {
    e.preventDefault();
    drop.classList.remove("over");
  })
);
drop.addEventListener("drop", (e) => e.dataTransfer.files[0] && run(e.dataTransfer.files[0]));

copyBtn.addEventListener("click", async () => {
  await navigator.clipboard.writeText(textOut.textContent);
  status.textContent = "copied";
});

downloadBtn.addEventListener("click", () => {
  const blob = new Blob([textOut.textContent], { type: "text/plain" });
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = (lastResult?.filename || "ocr").replace(/\.[^.]+$/, "") + ".txt";
  link.click();
  URL.revokeObjectURL(link.href);
});

function setStatus(message, kind = "") {
  status.textContent = message;
  status.className = kind;
}

function setStage(message) {
  stageOut.textContent = message;
}

function setProgress(fraction) {
  barFill.style.width = Math.max(0, Math.min(1, fraction)) * 100 + "%";
}

function logLine(text, level = "INFO") {
  const row = document.createElement("div");
  row.className = level;
  const time = new Date().toLocaleTimeString("en-GB", { hour12: false });
  row.innerHTML = `<span class="t">${time}</span> ${text}`;
  logOut.appendChild(row);
  logOut.scrollTop = logOut.scrollHeight;
}

async function run(file) {
  if (running) return;
  running = true;
  copyBtn.disabled = downloadBtn.disabled = true;

  const form = new FormData();
  form.append("file", file);
  form.append("dpi", dpiSelect.value);

  if (lastObjectUrl) URL.revokeObjectURL(lastObjectUrl);
  lastObjectUrl = URL.createObjectURL(file);

  viewer.innerHTML = "";
  textOut.textContent = "";
  setStage("uploading…");
  setProgress(0);
  setStatus("reading…", "busy");
  logLine(`upload ${file.name} (${Math.round(file.size / 1024)} KB)`);

  try {
    const response = await fetch("/api/ocr/stream", { method: "POST", body: form });
    if (!response.ok) {
      const detail = await response.json().catch(() => ({}));
      throw new Error(detail.detail || response.statusText);
    }
    await readStream(response, file);
  } catch (error) {
    setStatus(error.message, "error");
    setStage("failed: " + error.message);
    logLine("error " + error.message, "ERROR");
  } finally {
    running = false;
    refreshMetrics();
  }
}

async function readStream(response, file) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  const state = { pages: new Map(), holders: new Map(), file: file };

  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let split;
    while ((split = buffer.indexOf("\n\n")) !== -1) {
      const frame = buffer.slice(0, split);
      buffer = buffer.slice(split + 2);
      handleFrame(frame, state);
    }
  }
}

function handleFrame(frame, state) {
  const event = /^event: (.+)$/m.exec(frame);
  const data = /^data: (.*)$/m.exec(frame);
  if (!event || !data) return;
  const payload = JSON.parse(data[1]);

  if (event[1] === "progress") return onProgress(payload, state);
  if (event[1] === "error") {
    setStatus(payload.detail, "error");
    setStage("failed: " + payload.detail);
    logLine("error " + payload.detail, "ERROR");
    return;
  }
  if (event[1] === "result") {
    lastResult = payload;
    render(payload);
    setProgress(1);
    setStage(`done in ${(payload.ms / 1000).toFixed(1)}s`);
    setStatus(`${payload.kind.toUpperCase()} · ${payload.pages.length} page(s) · ${(payload.ms / 1000).toFixed(1)}s`, "done");
    copyBtn.disabled = downloadBtn.disabled = false;
  }
}

function onProgress(event, state) {
  if (event.stage === "model") {
    logLine(`model ready in ${event.ms}ms`);
    setStage(`model ready in ${event.ms}ms`);
  } else if (event.stage === "rendered") {
    logLine(`pdf rendered ${event.pages} page(s) in ${event.ms}ms`);
    setStage(`rendered ${event.pages} page(s)`);
  } else if (event.stage === "page_start") {
    logLine(`page ${event.page}/${event.total_pages} started`);
    setStage(`page ${event.page} of ${event.total_pages}`);
  } else if (event.stage === "window") {
    if (event.skipped) {
      logLine(`  window ${event.index}/${event.total} empty, skipped`);
    } else {
      logLine(`  window ${event.index}/${event.total} read in ${event.ms}ms, ${event.readings} readings`);
    }
    drawPage(state, event);
    setProgress(progressFraction(event));
    setStage(
      `page ${event.page ?? 1} · window ${event.index}/${event.total} · ${event.ms}ms` +
        (event.skipped ? " (empty, skipped)" : "")
    );
  } else if (event.stage === "page_done") {
    logLine(`  page done in ${event.ms}ms, ${event.lines} lines`);
  } else if (event.stage === "page_text") {
    state.pages.set(event.page, { width: event.width, height: event.height, text: event.text });
    refreshText(state.pages);
  }
}

function progressFraction(event) {
  const windows = event.total || 1;
  return (event.index / windows) * 0.9;
}

function refreshText(pages) {
  const blocks = [...pages.entries()]
    .sort((a, b) => a[0] - b[0])
    .map(([number, page]) => `--- page ${number} ---\n${page.text}`);
  textOut.textContent = blocks.join("\n\n");
}

function drawPage(state, event) {
  if (!event.lines || !event.lines.length || !event.width) return;
  const number = event.page ?? 1;
  let holder = state.holders.get(number);
  if (!holder) {
    const frame = document.createElement("div");
    frame.className = "page";
    holder = document.createElement("div");
    holder.style.position = "relative";
    holder.style.width = "100%";
    holder.style.aspectRatio = `${event.width} / ${event.height}`;
    if (state.file.type !== "application/pdf") {
      const image = document.createElement("img");
      image.src = lastObjectUrl;
      image.style.display = "block";
      image.style.width = "100%";
      image.style.height = "auto";
      holder.appendChild(image);
    }
    frame.appendChild(holder);
    viewer.appendChild(frame);
    state.holders.set(number, holder);
  }
  holder.querySelectorAll(".box").forEach((box) => box.remove());
  for (const line of event.lines) {
    const box = document.createElement("div");
    box.className = "box" + (line.score < 0.4 ? " low" : "");
    box.title = `${line.text} (${Math.round(line.score * 100)}%)`;
    box.style.left = (line.bbox[0] / event.width) * 100 + "%";
    box.style.top = (line.bbox[1] / event.height) * 100 + "%";
    box.style.width = ((line.bbox[2] - line.bbox[0]) / event.width) * 100 + "%";
    box.style.height = ((line.bbox[3] - line.bbox[1]) / event.height) * 100 + "%";
    holder.appendChild(box);
  }
}

function render(data) {
  viewer.innerHTML = "";
  const pages = new Map();
  data.pages.forEach((page) => {
    const frame = document.createElement("div");
    frame.className = "page";
    const holder = document.createElement("div");
    holder.style.position = "relative";
    holder.style.width = "100%";
    holder.style.aspectRatio = `${page.width} / ${page.height}`;
    const image = document.createElement("img");
    image.src = page.preview || lastObjectUrl;
    image.style.display = "block";
    image.style.width = "100%";
    image.style.height = "auto";
    holder.appendChild(image);
    page.lines.forEach((line) => {
      const box = document.createElement("div");
      box.className = "box" + (line.score < 0.4 ? " low" : "");
      box.title = `${line.text} (${Math.round(line.score * 100)}%)`;
      box.style.left = (line.bbox[0] / page.width) * 100 + "%";
      box.style.top = (line.bbox[1] / page.height) * 100 + "%";
      box.style.width = ((line.bbox[2] - line.bbox[0]) / page.width) * 100 + "%";
      box.style.height = ((line.bbox[3] - line.bbox[1]) / page.height) * 100 + "%";
      holder.appendChild(box);
    });
    frame.appendChild(holder);
    viewer.appendChild(frame);
    pages.set(page.page, { width: page.width, height: page.height, text: page.text });
  });
  refreshText(pages);
}

async function refreshMetrics() {
  try {
    const response = await fetch("/api/metrics");
    const data = await response.json();
    const last = data.last || {};
    document.getElementById("m-last").textContent = last.ms ? `${(last.ms / 1000).toFixed(1)}s` : "–";
    document.getElementById("m-page").textContent = data.avg_ms_per_page || "–";
    document.getElementById("m-window").textContent = data.avg_ms_per_window || "–";
    document.getElementById("m-windows").textContent = `${data.windows} / ${data.windows + data.windows_skipped}`;
    document.getElementById("m-skipped").textContent = data.windows_skipped;
    document.getElementById("m-lines").textContent = data.lines;
  } catch (error) {
    /* the monitor is a nicety, never break the app over it */
  }
}

setInterval(refreshMetrics, 3000);
refreshMetrics();
