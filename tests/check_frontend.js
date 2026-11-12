// Runs app.js against a small DOM stub and a recorded stream, to catch typos
// and missing elements that a browser would show as a blank page.
// Usage: node tests/check_frontend.js tests/fixtures/stream.txt
const fs = require("fs");

function element(tag) {
  const node = {
    tagName: tag,
    children: [],
    style: {},
    dataset: {},
    className: "",
    textContent: "",
    innerHTML: "",
    value: "",
    files: [],
    title: "",
    scrollTop: 0,
    scrollHeight: 0,
    classList: { add() {}, remove() {}, contains: () => false },
    appendChild(child) { this.children.push(child); return child; },
    addEventListener() {},
    remove() {},
    querySelectorAll: () => [],
    setAttribute() {},
  };
  return node;
}

const nodes = new Map();
global.document = {
  getElementById(id) {
    if (!nodes.has(id)) nodes.set(id, element("div"));
    return nodes.get(id);
  },
  createElement: (tag) => element(tag),
};

const frames = fs.readFileSync(process.argv[2], "utf8");
const encoder = new TextEncoder();

global.fetch = async (url, options) => {
  if (String(url).includes("/api/metrics")) {
    return {
      ok: true,
      json: async () => ({
        requests: 1, pages: 2, windows: 24, windows_skipped: 16, lines: 14,
        avg_ms_per_page: 2500, avg_ms_per_window: 190, last: { ms: 7000 },
      }),
    };
  }
  const chunks = encoder.encode(frames);
  let sent = false;
  return {
    ok: true,
    statusText: "OK",
    json: async () => ({}),
    body: {
      getReader() {
        return {
          async read() {
            if (sent) return { done: true };
            sent = true;
            return { value: chunks, done: false };
          },
        };
      },
    },
  };
};

global.navigator = { clipboard: { writeText: async () => {} } };
global.URL = { createObjectURL: () => "blob:fake", revokeObjectURL: () => {} };
global.Blob = class { constructor(parts) { this.parts = parts; } };

const path = require("path");
const source = fs.readFileSync(path.join(__dirname, "../app/static/app.js"), "utf8");
eval(source);

(async () => {
  const file = { name: "document.pdf", size: 3188, type: "application/pdf" };
  await run(file);
  const text = nodes.get("text").textContent;
  const stage = nodes.get("stage").textContent;
  const log = nodes.get("log").children.length;
  const status = nodes.get("status").textContent;
  const problems = [];
  if (!/Invoice #A-2291/.test(text)) problems.push("text panel empty: " + JSON.stringify(text.slice(0, 80)));
  if (!/done in/.test(stage)) problems.push("stage not finished: " + stage);
  if (log < 5) problems.push("log panel has " + log + " rows");
  if (!/PDF|IMAGE/.test(status)) problems.push("status not updated: " + status);
  if (problems.length) {
    console.error("FAIL\n  " + problems.join("\n  "));
    process.exit(1);
  }
  console.log(`ok  ${log} log rows, stage "${stage}", status "${status}"`);
  process.exit(0);
})();
