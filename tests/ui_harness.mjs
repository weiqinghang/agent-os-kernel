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
settings.pollMs = 20;
settings.pollBudgetMs = 120000;

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
  "cancelled",
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

const LEAKY = [
  "# 电商销量洞察报告（近90天）",
  "",
  "**范围**：dataset=`ecommerce_sales`；时间窗 2025-07-25 ~ 2025-10-22（近90天）。",
  "dataset=ecommerce_sales 裸串",
  "**数据概览**：销量稳定。",
  "_evidence: overviewhash_",
  "",
  "## 发现",
  "",
  "1. **类目集中**：GMV 最高类目为 **电子产品**。",
  "   - evidence: `aaaa1111`",
  "",
  "2. **渠道结构**：GMV 最高渠道为 **抖音电商**。",
  "   - evidence: `deadbeefcafe`",
  "   - evidence: `deadbeefcafe`",
  "",
  "3. **区域贡献**：GMV 最高省份为 **江苏**。",
  "   - evidence: `bbbb2222`",
  "",
  "4. **退货风险**：按渠道退货率最高为 **拼多多**。",
  "   - evidence: `cccc3333`",
  "",
  "---",
  "_数字均来自 Tool Gateway；可追问下钻。_",
].join("\n");

const DRILL = [
  "## 第2条依据",
  "",
  "这条发现讲的是渠道 **抖音电商**。下面只列出「抖音电商」的样本。",
  "",
  "共 5 条样本（展示前 5）：",
  "",
  "1. ORD1001 · 电子产品 · 抖音电商 · 江苏 · 2025-10-03 · GMV=110",
  "2. ORD1002 · 电子产品 · 抖音电商 · 江苏 · 2025-10-03 · GMV=120",
  "3. ORD1003 · 电子产品 · 抖音电商 · 江苏 · 2025-10-04 · GMV=130",
  "4. ORD1004 · 电子产品 · 抖音电商 · 江苏 · 2025-10-04 · GMV=140",
  "5. ORD1005 · 电子产品 · 抖音电商 · 江苏 · 2025-10-05 · GMV=150",
  "   - evidence: `deadbeefcafe`",
  "",
  "_evidence: `deadbeefcafe`_",
].join("\n");

let current = null;

if (process.stdout._handle && process.stdout._handle.setBlocking) {
  process.stdout._handle.setBlocking(true);
}

function wait(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function waitUntil(pred, ms) {
  const started = Date.now();
  while (Date.now() - started < ms) {
    if (pred()) return;
    await wait(15);
  }
  throw new Error("timed out waiting\n" + JSON.stringify(snapshot()));
}

function stamp(win, events) {
  const out = [];
  for (const ev of events || []) {
    if (!ev || typeof ev !== "object") continue;
    const id = win.__nextId || 1;
    win.__nextId = id + 1;
    out.push(Object.assign({ id }, ev));
  }
  return out;
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
  if (kind === "drill") {
    return {
      body: { run_id: RUN_ID, status: "completed", markdown: DRILL, text: DRILL },
      events: [],
    };
  }
  if (kind === "leaky") {
    return {
      body: {
        run_id: RUN_ID,
        status: "completed",
        markdown: LEAKY,
        text: LEAKY,
        context_bindings: {
          time_window: { label: "近90天", start: "2025-07-25", end: "2025-10-22" },
        },
      },
      events: realisticEvents(),
    };
  }
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
  window.__polls = 0;
  window.__nextId = 1;
  window.__runState = null;
  window.__result = null;
  window.__hitl = null;
  window.__bindings = null;
  window.__messagePosts = 0;
  window.__resumePosts = 0;
  window.__cancelPosts = 0;
  window.__cancelUrl = "";
  window.__cancelPending = null;
  window.__pollsStable = false;
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
  if (method === "POST" && url.includes("/cancel")) {
    window.__cancelPosts += 1;
    window.__cancelUrl = url;
    return new Promise((resolve) => {
      window.__cancelPending = resolve;
    });
  }
  if (method === "GET" && url.includes("/events")) {
    window.__polls = (window.__polls || 0) + 1;
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
    const parsed = new URL(url, "http://127.0.0.1:8787");
    const after = Number(parsed.searchParams.get("after_id") || 0);
    const events = (window.__events || []).filter((ev) => ev && typeof ev.id === "number" && ev.id > after);
    const body = {
      run_id: RUN_ID,
      state: window.__runState || null,
      events,
    };
    if (typeof window.__result === "string") body.result = window.__result;
    if (window.__hitl) body.hitl = window.__hitl;
    if (window.__bindings) body.context_bindings = window.__bindings;
    return Promise.resolve(jsonResponse(body));
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

function isShown(el) {
  if (!el) return false;
  return !el.hidden && !el.hasAttribute("hidden");
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
    buttons: Array.from(doc.querySelectorAll("button"))
      .filter((b) => !b.hidden)
      .map((b) => b.textContent),
    stopVisible: !!(doc.getElementById("stop") && !doc.getElementById("stop").hidden),
    cancelPosts: win.__cancelPosts || 0,
    cancelUrl: win.__cancelUrl || "",
    pollsStable: !!win.__pollsStable,
    chips: Array.from(doc.querySelectorAll(".chip")).map((b) => b.getAttribute("data-label")),
    datasetChips: doc.querySelectorAll("[data-dataset], .chip-focus, .focus-chip").length,
    diff: !!doc.querySelector(".diff, .compare, .side-by-side"),
    debugChecked: !!doc.getElementById("debug-chain").checked,
    debugVisible: isShown(doc.querySelector("label.debug-toggle")),
    activeChip: active ? active.getAttribute("data-label") : "",
    messagePosts: win.__messagePosts || 0,
    pending: (win.__pending || []).length,
    lastRequestText: win.__lastRequestText || "",
    stripsThisTurn: win.__stripsThisTurn || 0,
    maxConcurrentStrips: win.__maxConcurrentStrips || 0,
    imgCount: doc.querySelectorAll("img").length,
    leaks: findLeaks(visibleBlob(doc)),
    placeholder: (doc.getElementById("inp") && doc.getElementById("inp").placeholder) || "",
    userTexts: Array.from(doc.querySelectorAll(".msg.user .body")).map((el) => el.textContent || ""),
  };
}

async function doReset(msg) {
  settings.replayMs = 1;
  settings.pollMs = 20;
  settings.pollBudgetMs = 120000;
  if (current && current.win && typeof current.win.close === "function") current.win.close();
  const html = fs.readFileSync(HTML_PATH, "utf8").replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, "");
  const virtualConsole = new VirtualConsole();
  virtualConsole.on("jsdomError", (err) => process.stderr.write(String(err) + "\n"));
  const dom = new JSDOM(html, {
    url: (msg && msg.url) || "http://127.0.0.1:8787/",
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
  win.__events = stamp(win, spec.events || []);
  win.__runsFail = !!spec.runsFail;
  win.__runsBadJson = !!spec.runsBadJson;
  const body = spec.body || {};
  win.__runState = body.status || null;
  const result = body.markdown || body.message || body.text;
  win.__result = typeof result === "string" ? result : null;
  win.__hitl = body.hitl || null;
  win.__bindings = body.context_bindings || null;
  const pending = (win.__pending || []).shift();
  if (!pending) throw new Error("no pending request");
  if (spec.httpFail) pending.resolve(jsonResponse(spec.body || {}, 500));
  else pending.resolve(jsonResponse(spec.body || {}));
  await waitUntil(() => {
    const btn = current.doc.getElementById("send");
    return !!(btn && !btn.disabled);
  }, 800);
  return snapshot();
}

async function doAccept(msg) {
  const win = current.win;
  if (msg.pollBudgetMs != null) settings.pollBudgetMs = msg.pollBudgetMs;
  win.__runsFail = !!msg.runsFail;
  win.__runsBadJson = !!msg.runsBadJson;
  win.__runState = "acting";
  win.__result = null;
  win.__hitl = null;
  win.__bindings = {
    time_window: { label: "近90天", start: "2025-07-25", end: "2025-10-22" },
  };
  const pending = (win.__pending || []).shift();
  if (!pending) throw new Error("no pending request");
  pending.resolve(
    jsonResponse({
      run_id: RUN_ID,
      status: "acting",
      accepted: true,
      context_bindings: win.__bindings,
    })
  );
  if (msg.runsFail || msg.pollBudgetMs === 0) {
    await waitUntil(() => textOf(current.doc.body).includes("这次没能完成"), 800);
  } else {
    await waitUntil(() => (win.__polls || 0) >= 1, 800);
  }
  return snapshot();
}

function pushEvents(kind) {
  if (kind === "tool") return [{ kind: "tool_call", payload: { ok: true } }];
  if (kind === "writing") {
    return [
      {
        kind: "model_step",
        payload: {
          step: 2,
          type: "loop_break",
          loop_break: true,
          repeated_tool: "query_stats",
          fingerprint: "deadbeef",
          mode: "stub",
          thinking: "hidden-cot chain-of-thought",
        },
      },
      { kind: "model_step", payload: { step: 3, type: "finalizing", n_calls: 0, mode: "live" } },
      { kind: "model_step", payload: { step: 3, finalizing: true, type: "note" } },
      { kind: "model_step", payload: { step: 4, loop_break: true } },
      { kind: "model_step", payload: { step: 5, type: "wrap", n_calls: 0 } },
      { kind: "model_step", payload: { step: 6, type: "final", n_calls: 0, loop_break: true, mode: "stub" } },
      { kind: "state", payload: { state: "finalizing" } },
      { kind: "model_step", payload: { mode: "stub" } },
      { kind: "model_step", payload: { step: 7, type: "tool_calls", n_calls: 0, loop_break: true, mode: "stub" } },
    ];
  }
  if (kind === "phase") {
    return [
      { kind: "phase_writing", payload: { phase: "phase_writing", thinking: "hidden-cot" } },
      { kind: "phase_thinking", payload: { phase: "phase_thinking", thinking: "chain-of-thought secret" } },
      { kind: "phase_tool", payload: { name: "query_stats" } },
    ];
  }
  return [{ kind: "model_step", payload: { step: 0, type: "tool_calls" } }];
}

function progressExpect(kind) {
  if (kind === "tool") return "正在查询数据";
  if (kind === "writing") return "正在整理结论";
  return "思考中";
}

async function doPush(msg) {
  const win = current.win;
  const before = win.__polls || 0;
  const expect = progressExpect(msg.kind);
  win.__events = (win.__events || []).concat(stamp(win, pushEvents(msg.kind)));
  await waitUntil(() => (win.__polls || 0) >= before + 3, 800);
  await waitUntil(() => {
    const strip = current.doc.querySelector(".progress-strip");
    const text = strip && strip.querySelector(".progress-text");
    return !!(text && text.textContent === expect);
  }, 800);
  return snapshot();
}

async function doFinish(msg) {
  const win = current.win;
  if ((msg.kind || "success") === "success") {
    win.__events = (win.__events || []).concat(
      stamp(win, [
        { kind: "model_step", payload: { step: 2, type: "final" } },
        { kind: "state", payload: { state: "completed" } },
      ])
    );
  }
  win.__runState = "completed";
  win.__result = CONCLUSION;
  await waitUntil(() => current.doc.querySelectorAll(".progress-strip").length === 0, 800);
  await waitUntil(() => textOf(current.doc.body).includes("发现"), 800);
  return snapshot();
}

async function doAckCancel() {
  const win = current.win;
  win.__events = (win.__events || []).concat(
    stamp(win, [
      {
        kind: "tool_call",
        payload: { ok: true, tool: "query_stats", thinking: "hidden-cot chain-of-thought" },
      },
      { kind: "phase_writing", payload: { thinking: "hidden-cot" } },
    ])
  );
  const resolve = win.__cancelPending;
  if (!resolve) throw new Error("no cancel pending");
  resolve(
    jsonResponse({
      run_id: RUN_ID,
      status: "cancelled",
      accepted: true,
      markdown: "已停止",
      result: "已停止",
    })
  );
  await waitUntil(() => current.doc.querySelectorAll(".progress-strip").length === 0, 800);
  await waitUntil(() => textOf(current.doc.body).includes("已停止"), 800);
  const stop = current.doc.getElementById("stop");
  await waitUntil(() => !!(stop && stop.hidden), 800);
  const polls = win.__polls || 0;
  await wait(120);
  win.__pollsStable = (win.__polls || 0) <= polls + 1;
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
  if (msg.op === "accept") return doAccept(msg);
  if (msg.op === "push") return doPush(msg);
  if (msg.op === "finish") return doFinish(msg);
  if (msg.op === "ack-cancel") return doAckCancel();
  if (msg.op === "chip") return doChip(msg.label);
  if (msg.op === "click") return doClick(msg.text);
  if (msg.op === "label") return doLabel();
  if (msg.op === "debug") {
    const box = current.doc.getElementById("debug-chain");
    box.checked = !!msg.on;
    box.dispatchEvent(new current.win.Event("change", { bubbles: true }));
    await wait(20);
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
