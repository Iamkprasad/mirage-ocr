// Checks that the server sent events carry everything app.js reads.
const fs = require("fs");

const raw = fs.readFileSync(process.argv[2], "utf8");
const frames = raw.split("\n\n").filter(Boolean);
const events = [];
for (const frame of frames) {
  const event = /^event: (.+)$/m.exec(frame);
  const data = /^data: (.*)$/m.exec(frame);
  if (!event || !data) continue;
  events.push({ name: event[1], data: JSON.parse(data[1]) });
}

const problems = [];
const need = (cond, message) => { if (!cond) problems.push(message); };

const progress = events.filter((e) => e.name === "progress").map((e) => e.data);
const result = events.find((e) => e.name === "result");
const error = events.find((e) => e.name === "error");

need(!error, "stream carried an error: " + JSON.stringify(error));
need(!!result, "no result event");

const stages = progress.map((p) => p.stage);
need(stages.includes("model"), "no model event");
need(stages.includes("window"), "no window event");

const windows = progress.filter((p) => p.stage === "window");
need(windows.length > 0, "no window events");
for (const w of windows) {
  need(typeof w.index === "number", "window without index");
  need(typeof w.total === "number", "window without total");
  need(typeof w.ms === "number", "window without ms");
  need(typeof w.width === "number", "window without width");
  need(typeof w.height === "number", "window without height");
  need(Array.isArray(w.lines), "window without lines array");
  for (const line of w.lines) {
    need(typeof line.text === "string", "line without text");
    need(Array.isArray(line.bbox) && line.bbox.length === 4, "line without bbox");
    need(typeof line.score === "number", "line without score");
  }
}

if (result) {
  const payload = result.data;
  need(typeof payload.kind === "string", "result without kind");
  need(typeof payload.ms === "number", "result without ms");
  need(Array.isArray(payload.pages), "result without pages");
  need(typeof payload.text === "string", "result without text");
  for (const page of payload.pages) {
    need(typeof page.width === "number", "page without width");
    need(typeof page.height === "number", "page without height");
    need(Array.isArray(page.lines), "page without lines");
    need(typeof page.text === "string", "page without text");
  }
}

if (problems.length) {
  console.error("FAIL\n  " + problems.join("\n  "));
  process.exit(1);
}
console.log(`ok  ${events.length} events, ${windows.length} windows`);
