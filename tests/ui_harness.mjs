/**
 * 长驻 jsdom，给 pytest-bdd 按行调用。
 * 断言只看用户看得见的文案；事件夹具里的工具名 / mode / id 不应当出现在页面上。
 */
import fs from "node:fs";
import path from "node:path";
import readline from "node:readline";
import { fileURLToPath } from "node:url";

import { JSDOM, VirtualConsole } from "jsdom";

import { bootIfPresent } from "../ui/chat/app.js";
import { settings } from "../ui/chat/progress.js";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const HTML_PATH = path.join(ROOT, "ui/chat/index.html");

const SESSION_ID = "ses_abc123def456";
const RUN_ID = "run_3cbdbe50347f";
const TOKEN = "hitl_0123456789abcdef";

settings.replayMs = 1;

const SECRETS = [
  "query_stats",
  "sample_records",
  "search_records",
  "describe_dataset",
  "list_datasets",
  "insight_report",
  "ecommerce_sales",
  SESSION_ID,
  RUN_ID,
  "agent_id",
  "resume_token",
  TOKEN,
  "deadbeef",
  "fingerprint",
  "chain-of-thought",
  "hidden-cot",
  "phase_thinking",
  "phase_tool",
  "phase_writing",
  "tool_call",
  "model_step",
  "run_created",
  "awaiting_hitl",
  "live_fallback_stub",
  "select 1",
  "Traceback",
  "n_calls",
  "Agent 链路",
];

const CONCLUSION = [
  "# 标题",
  "",
  "## 发现",
  "",
  "### 细项",
  "",
  "**销量**稳定，a<b>&\"c'。",
  "",
  "`样本`",
  "",
  "_evidence: abc_",
  "",
  "第一段",
  "",
  "第二段",
  "",
  '<img class="leak-img" src="x">',
].join("\n");

let current = null;

if (process.stdout._handle && process.stdout._handle.setBlocking) {
  process.stdout._handle.setBlocking(true);
}

function wait(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function jsonResponse(body, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  };
}

function realisticEvents() {
  return [
    {
      kind: "run_created",
      payload: {
        session_id: SESSION_ID,
        agent_id: "agent_id",
        bindings: { skill: "insight_report", dataset: "ecommerce_sales" },
      },
    },
    { kind: "state", payload: { state: "created" } },
    {
      kind: "state",
      payload: { state: "acting", skill: "insight_report", bindings: { dataset: "ecommerce_sales" } },
    },
    { kind: "state", payload: { state: "running" } },
    { kind: "hitl", payload: { kind: "outline_confirm", auto: true, resume_token: TOKEN } },
    {
      kind: "phase_thinking",
      payload: { phase: "phase_thinking", thinking: "chain-of-thought secret", mode: "stub" },
    },
    { kind: "model_step", payload: { step: 0, type: "tool_calls", mode: "stub", n_calls: 1 } },
    { kind: "model_step", payload: { step: 0, type: "live_fallback_stub", mode: "live" } },
    {
      kind: "tool_call",
      payload: {
        name: "query_stats",
        tool: "query_stats",
        ok: true,
        fingerprint: "deadbeefcafe",
        mode: "stub",
        preview: { sql: "select 1" },
        arguments: { dataset: "ecommerce_sales" },
      },
    },
    { kind: "tool_call", payload: "query_stats" },
    { kind: "tool_call", payload: { name: "sample_records", tool: "sample_records" } },
    { kind: "tool_call", payload: { name: "search_records", tool: "search_records" } },
    { kind: "tool_call", payload: { name: "describe_dataset", tool: "describe_dataset" } },
    { kind: "tool_call", payload: { name: "list_datasets", tool: "list_datasets" } },
    null,
    { kind: "model_step", payload: { step: 1, type: "tool_calls", mode: "stub", n_calls: 1 } },
    { kind: "phase_tool", payload: { name: "query_stats" } },
    { kind: "phase_writing", payload: { thinking: "hidden-cot" } },
    { kind: "assistant", payload: { content: "hidden-cot chain-of-thought", thinking: "hidden-cot" } },
    { kind: "model_step", payload: { step: 2, type: "final", mode: "live", n_calls: 0 } },
    { kind: "state", payload: { state: "completed", n_tools: 5, error: "query_stats stack" } },
    { kind: "hitl", payload: { approved: true, kind: "outline_confirm", resume_token: TOKEN } },
    { kind: "model_step", payload: { mode: "stub" } },
  ];
}

function hitlEvents() {
  return [
    { kind: "state", payload: { state: "acting" } },
    { kind: "state", payload: { state: "awaiting_hitl", hitl_kind: "outline_confirm" } },
    { kind: "hitl", payload: { kind: "outline_confirm", auto: false, resume_token: TOKEN } },
    { kind: "tool_call", payload: { tool: "query_stats", name: "query_stats" } },
  ];
}

function failedEvents() {
  return [
    { kind: "state", payload: { state: "acting" } },
    { kind: "tool_call", payload: { tool: "describe_dataset", name: "describe_dataset" } },
    { kind: "state", payload: { state: "failed", error: "Traceback query_stats" } },
  ];
}

function successBody() {
  return {
    run_id: RUN_ID,
    status: "completed",
    markdown: CONCLUSION,
    text: CONCLUSION,
    context_bindings: {
      time_window: { label: "近90天", start: "2025-07-25", end: "2025-10-22" },
    },
  };
}

function releaseSpec(kind) {
  if (kind === "success") return { body: successBody(), events: realisticEvents() };
  if (kind === "noevents") return { body: successBody(), events: [] };
  if (kind === "empty") return { body: { status: "completed", markdown: "  ", text: "" }, events: [] };
  if (kind === "runsFail") {
    return { body: successBody(), events: [], runsFail: true };
  }
  if (kind === "runsBadJson") {
    return { body: successBody(), events: [], runsBadJson: true };
  }
  if (kind === "httpFail") return { httpFail: true, body: { markdown: "query_stats Traceback" } };
  if (kind === "failed") {
    return {
      body: {
        run_id: RUN_ID,
        status: "failed",
        markdown: "编排失败：query_stats Traceback",
      },
      events: failedEvents(),
    };
  }
  if (kind === "longfail") {
    return {
      body: { run_id: RUN_ID, status: "failed", markdown: "没能完成。".repeat(30) },
      events: [],
    };
  }
  if (kind === "hitl") {
    return {
      body: {
        run_id: RUN_ID,
        status: "awaiting_hitl",
        message: "请确认大纲",
        hitl: { resume_token: TOKEN, kind: "outline_confirm" },
      },
      events: hitlEvents(),
    };
  }
  if (kind === "rejected") {
    return {
      body: { status: "failed", markdown: "已拒绝" },
      events: [{ kind: "hitl", payload: { approved: false, resume_token: TOKEN } }],
    };
  }
  throw new Error("unknown release " + kind);
}

function installFetch(window, opts) {
  window.__events = [];
  window.__pending = [];
  window.__messagePosts = 0;
  window.__resumePosts = 0;
  window.__lastRequestText = "";
  window.__runsFail = false;
  window.__runsBadJson = false;
  window.__patchFail = !!opts.patchFail;
  window.__sessionFail = !!opts.sessionFail;
  window.__maxConcurrentStrips = 0;
  window.__stripsThisTurn = 0;
  window.__lastProgressUpdates = [];
  window.fetch = (url, options = {}) => route(window, String(url), options || {});
}

function route(window, url, options) {
  const method = String(options.method || "GET").toUpperCase();
  if (method === "POST" && url === "/sessions") {
    if (window.__sessionFail) return Promise.resolve(jsonResponse({}, 500));
    return Promise.resolve(
      jsonResponse({
        id: SESSION_ID,
        bindings: { time_window: { label: "近90天", start: "2025-07-25", end: "2025-10-22" } },
      })
    );
  }
  if (method === "PATCH" && url.includes("/context")) {
    if (window.__patchFail) return Promise.resolve(jsonResponse({}, 500));
    return Promise.resolve(jsonResponse({ ok: true }));
  }
  if (method === "POST" && url.includes("/messages")) {
    const body = JSON.parse(options.body || "{}");
    window.__lastRequestText = body.text || "";
    window.__messagePosts += 1;
    return new Promise((resolve) => window.__pending.push({ resolve }));
  }
  if (method === "POST" && url.includes("/resume")) {
    window.__resumePosts += 1;
    return new Promise((resolve) => window.__pending.push({ resolve }));
  }
  if (method === "GET" && url.includes("/runs/")) {
    if (window.__runsBadJson) {
      return Promise.resolve({
        ok: true,
        status: 200,
        json: async () => {
          throw new Error("bad json");
        },
      });
    }
    if (window.__runsFail) return Promise.resolve(jsonResponse({}, 500));
    return Promise.resolve(jsonResponse({ events: window.__events || [] }));
  }
  process.stderr.write("unexpected fetch " + method + " " + url + "\n");
  return Promise.resolve(jsonResponse({}, 404));
}

function textOf(el) {
  if (!el) return "";
  if (typeof el.innerText === "string") return el.innerText;
  return el.textContent || "";
}

function findLeaks(text) {
  const found = [];
  for (const secret of SECRETS) {
    if (text.includes(secret)) found.push(secret);
  }
  if (/\bstub\b/i.test(text)) found.push("stub");
  if (/\blive\b/i.test(text)) found.push("live");
  return found;
}

function visibleBlob(doc) {
  const parts = [textOf(doc.body)];
  doc.querySelectorAll(".progress-strip, .chain, .rebind, .hitl, .msg, #meta").forEach((el) => {
    parts.push(el.innerHTML, textOf(el));
  });
  return parts.join("\n");
}

function snapshot() {
  const { doc, win } = current;
  const strip = doc.querySelector(".progress-strip");
  const chain = doc.querySelector(".chain");
  const rebind = doc.querySelector(".rebind-line");
  const active = doc.querySelector(".chip.active");
  const style = doc.querySelector("style");
  const updates =
    strip && strip.__updates ? strip.__updates.slice() : (win.__lastProgressUpdates || []).slice();
  return {
    stripCount: doc.querySelectorAll(".progress-strip").length,
    progress: strip ? strip.querySelector(".progress-text").textContent : null,
    updates,
    breathe: !!(strip && strip.querySelector(".progress-breathe")),
    styleHasBreathe: !!(style && /@keyframes\s+breathe/.test(style.textContent)),
    chain: !!chain,
    chainText: chain ? textOf(chain) : "",
    chainLines: chain ? Array.from(chain.querySelectorAll("li")).map((li) => textOf(li)) : [],
    body: textOf(doc.body),
    rebind: rebind ? rebind.textContent : "",
    buttons: Array.from(doc.querySelectorAll("button")).map((b) => b.textContent),
    chips: Array.from(doc.querySelectorAll(".chip")).map((b) => b.getAttribute("data-label")),
    datasetChips: doc.querySelectorAll("[data-dataset], .chip-focus, .focus-chip").length,
    diff: !!doc.querySelector(".diff, .compare, .side-by-side"),
    debugChecked: !!doc.getElementById("debug-chain").checked,
    activeChip: active ? active.getAttribute("data-label") : "",
    messagePosts: win.__messagePosts || 0,
    pending: (win.__pending || []).length,
    lastRequestText: win.__lastRequestText || "",
    stripsThisTurn: win.__stripsThisTurn || 0,
    maxConcurrentStrips: win.__maxConcurrentStrips || 0,
    imgCount: doc.querySelectorAll("img").length,
    leaks: findLeaks(visibleBlob(doc)),
  };
}

async function doReset(msg) {
  if (current && current.win && typeof current.win.close === "function") current.win.close();
  const html = fs.readFileSync(HTML_PATH, "utf8").replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, "");
  const virtualConsole = new VirtualConsole();
  virtualConsole.on("jsdomError", (err) => process.stderr.write(String(err) + "\n"));
  const dom = new JSDOM(html, {
    url: "http://127.0.0.1:8787/",
    pretendToBeVisual: true,
    virtualConsole,
    beforeParse(window) {
      installFetch(window, msg || {});
    },
  });
  current = { dom, win: dom.window, doc: dom.window.document };
  bootIfPresent(current.doc, current.win);
  await wait(40);
  return snapshot();
}

async function doSend(text) {
  const { doc, win } = current;
  doc.getElementById("inp").value = text;
  doc.getElementById("f").dispatchEvent(new win.Event("submit", { bubbles: true, cancelable: true }));
  await wait(40);
  return snapshot();
}

async function doRelease(msg) {
  const spec = releaseSpec(msg.kind);
  const win = current.win;
  win.__events = spec.events || [];
  win.__runsFail = !!spec.runsFail;
  win.__runsBadJson = !!spec.runsBadJson;
  const pending = (win.__pending || []).shift();
  if (!pending) throw new Error("no pending request");
  if (spec.httpFail) pending.resolve(jsonResponse(spec.body || {}, 500));
  else pending.resolve(jsonResponse(spec.body || {}));
  await wait(60);
  return snapshot();
}

async function doChip(label) {
  const { doc, win } = current;
  const btn = Array.from(doc.querySelectorAll(".chip")).find((b) => b.getAttribute("data-label") === label);
  if (!btn) throw new Error("missing chip " + label);
  btn.dispatchEvent(new win.MouseEvent("click", { bubbles: true }));
  await wait(50);
  return snapshot();
}

async function doClick(text) {
  const { doc, win } = current;
  const btn = Array.from(doc.querySelectorAll("button")).find((b) => b.textContent === text);
  if (!btn) throw new Error("missing button " + text);
  btn.dispatchEvent(new win.MouseEvent("click", { bubbles: true }));
  await wait(50);
  return snapshot();
}

async function doLabel() {
  const { doc, win } = current;
  doc.querySelector(".chip-label").dispatchEvent(new win.MouseEvent("click", { bubbles: true }));
  await wait(30);
  return snapshot();
}

async function handle(msg) {
  if (msg.op === "reset") return doReset(msg);
  if (msg.op === "send") return doSend(msg.text || "");
  if (msg.op === "blank") return doSend("   ");
  if (msg.op === "release") return doRelease(msg);
  if (msg.op === "chip") return doChip(msg.label);
  if (msg.op === "click") return doClick(msg.text);
  if (msg.op === "label") return doLabel();
  if (msg.op === "debug") {
    current.doc.getElementById("debug-chain").checked = !!msg.on;
    return snapshot();
  }
  if (msg.op === "flags") {
    if (msg.patchFail) current.win.__patchFail = true;
    return snapshot();
  }
  if (msg.op === "conclude") {
    await doSend("请基于电商销量数据写一份简短洞察报告");
    return doRelease({ kind: "success" });
  }
  if (msg.op === "snapshot") return snapshot();
  throw new Error("unknown op " + msg.op);
}

const rl = readline.createInterface({ input: process.stdin });
let queue = Promise.resolve();

function reply(payload) {
  process.stdout.write(JSON.stringify(payload) + "\n");
}

rl.on("line", (line) => {
  queue = queue.then(async () => {
    let msg;
    try {
      msg = JSON.parse(line);
    } catch (err) {
      reply({ ok: false, error: "bad json " + err });
      return;
    }
    try {
      const result = await handle(msg);
      reply({ ok: true, ...result });
    } catch (err) {
      reply({ ok: false, error: String(err && err.stack ? err.stack : err) });
    }
  });
});

rl.on("close", () => {
  queue.then(() => process.exit(0));
});
