/**
 * 洞察报告助理前台。进度与脱敏都在这一层：
 * 发令后立刻放一条呼吸条带，再用已发生的 run_events 原地改白名单文案。
 * 调试链路默认关；打开后也只显示白名单短句。
 */
import {
  IMMEDIATE,
  debugLines,
  presentAssistant,
  project,
  rebindLine,
  settings,
  visibleAssistant,
} from "./progress.js";

const EXAMPLES = ["近90天销量洞察", "哪个渠道卖得最好", "哪些省贡献最大"];

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[c]));
}

function simpleMd(text) {
  let t = escapeHtml(text);
  t = t.replace(/^### (.+)$/gm, "<h3>$1</h3>");
  t = t.replace(/^## (.+)$/gm, "<h2>$1</h2>");
  t = t.replace(/^# (.+)$/gm, "<h1>$1</h1>");
  t = t.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
  t = t.replace(/`([^`]+)`/g, "<code>$1</code>");
  t = t.replace(/_evidence: ([^\n_]+)_/g, "<em>evidence: $1</em>");
  t = t.replace(/\n\n/g, "</p><p>");
  return "<p>" + t + "</p>";
}

export function boot(doc, win) {
  const msgs = doc.getElementById("msgs");
  const form = doc.getElementById("f");
  const inp = doc.getElementById("inp");
  const sendBtn = doc.getElementById("send");
  const meta = doc.getElementById("meta");
  const chipsEl = doc.getElementById("chips");

  let sessionId = null;
  let pendingHitl = null;
  let hasConclusion = false;
  let currentTw = { label: "近90天", start: "2025-07-25", end: "2025-10-22" };
  let pendingRebind = null;

  function noteStrips() {
    const n = doc.querySelectorAll(".progress-strip").length;
    win.__maxConcurrentStrips = Math.max(win.__maxConcurrentStrips || 0, n);
  }

  function addMsg(role, text, extra) {
    const div = doc.createElement("div");
    div.className = "msg " + role + (role === "assistant" ? " md" : "");
    const roleEl = doc.createElement("div");
    roleEl.className = "role";
    roleEl.textContent = role === "user" ? "你" : "助理";
    div.appendChild(roleEl);
    if (role === "assistant") paintAssistant(div, text);
    else {
      const body = doc.createElement("div");
      body.className = "body";
      body.textContent = text;
      div.appendChild(body);
    }
    if (extra) div.appendChild(extra);
    msgs.appendChild(div);
    msgs.scrollTop = msgs.scrollHeight;
    return div;
  }

  function paintAssistant(div, raw) {
    div.__raw = raw;
    const presented = presentAssistant(raw, debugOn());
    let body = div.querySelector(".body");
    if (!body) {
      body = doc.createElement("div");
      body.className = "body";
      div.appendChild(body);
    }
    body.innerHTML = simpleMd(presented.text);
    div.querySelectorAll(".evidence-link").forEach((el) => el.remove());
    if (!debugOn()) {
      presented.actions.forEach((action) => {
        const btn = doc.createElement("button");
        btn.type = "button";
        btn.className = "evidence-link";
        btn.textContent = action.label;
        btn.addEventListener("click", () => submitText("第" + action.n + "条依据是什么"));
        div.appendChild(btn);
      });
    }
  }

  function renderEmpty() {
    const div = doc.createElement("div");
    div.className = "msg assistant empty-state";
    const roleEl = doc.createElement("div");
    roleEl.className = "role";
    roleEl.textContent = "助理";
    const title = doc.createElement("p");
    title.className = "empty-title";
    title.textContent = "问我要一份洞察结论";
    const hint = doc.createElement("p");
    hint.className = "empty-hint";
    hint.textContent = "会前直接点一个例子，或写下你想看的范围。";
    div.appendChild(roleEl);
    div.appendChild(title);
    div.appendChild(hint);
    EXAMPLES.forEach((question) => {
      const btn = doc.createElement("button");
      btn.type = "button";
      btn.className = "example";
      btn.textContent = question;
      btn.addEventListener("click", () => submitText(question));
      div.appendChild(btn);
    });
    msgs.appendChild(div);
    msgs.scrollTop = msgs.scrollHeight;
  }

  function refreshMeta() {
    meta.textContent = "时间窗 " + (currentTw.label || "—");
  }

  function setChipActive(twKey) {
    chipsEl.querySelectorAll(".chip").forEach((btn) => {
      btn.classList.toggle("active", btn.getAttribute("data-tw") === String(twKey));
    });
  }

  function chipKey(tw) {
    if (tw.label === "近30天") return "30";
    if (tw.label === "近7天") return "7";
    return "90";
  }

  function showProgress() {
    clearProgress();
    const strip = doc.createElement("div");
    strip.className = "progress-strip";
    strip.setAttribute("role", "status");
    strip.setAttribute("aria-live", "polite");
    const dot = doc.createElement("span");
    dot.className = "progress-breathe";
    dot.setAttribute("aria-hidden", "true");
    const text = doc.createElement("span");
    text.className = "progress-text";
    text.textContent = IMMEDIATE;
    strip.appendChild(dot);
    strip.appendChild(text);
    strip.__updates = [IMMEDIATE];
    msgs.appendChild(strip);
    msgs.scrollTop = msgs.scrollHeight;
    win.__stripsThisTurn = (win.__stripsThisTurn || 0) + 1;
    win.__lastProgressUpdates = strip.__updates.slice();
    noteStrips();
    return strip;
  }

  function clearProgress() {
    const old = doc.querySelector(".progress-strip");
    if (old && old.__updates) win.__lastProgressUpdates = old.__updates.slice();
    if (old) old.remove();
  }

  async function playEvents(strip, events) {
    const projected = project(events);
    const textEl = strip.querySelector(".progress-text");
    for (let i = 0; i < projected.steps.length; i++) {
      const text = projected.steps[i];
      if (textEl.textContent !== text) {
        textEl.textContent = text;
        strip.__updates.push(text);
      }
      if (settings.replayMs > 0 && i < projected.steps.length - 1) {
        await new Promise((r) => setTimeout(r, settings.replayMs));
      }
    }
    win.__lastProgressUpdates = strip.__updates.slice();
    noteStrips();
    return projected;
  }

  function debugOn() {
    const box = doc.getElementById("debug-chain");
    return !!(box && box.checked);
  }

  const debugBox = doc.getElementById("debug-chain");
  if (debugBox) {
    debugBox.addEventListener("change", () => {
      msgs.querySelectorAll(".msg.assistant").forEach((div) => {
        if (div.__raw != null) paintAssistant(div, div.__raw);
      });
    });
  }

  function maybeAppendDebug(parent, events) {
    if (!debugOn()) return;
    const details = doc.createElement("details");
    details.className = "chain";
    details.open = true;
    const summary = doc.createElement("summary");
    summary.textContent = "调试链路";
    details.appendChild(summary);
    const ol = doc.createElement("ol");
    ol.className = "chain-list";
    debugLines(events).forEach((line) => {
      const li = doc.createElement("li");
      li.textContent = line;
      ol.appendChild(li);
    });
    details.appendChild(ol);
    parent.appendChild(details);
  }

  async function fetchRunEvents(runId) {
    if (!runId) return null;
    try {
      const r = await win.fetch("/runs/" + encodeURIComponent(runId));
      if (!r.ok) return null;
      return await r.json();
    } catch (_) {
      return null;
    }
  }

  async function ensureSession() {
    if (sessionId) return;
    const r = await win.fetch("/sessions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        bindings: {
          skill: "insight_report",
          dataset: "ecommerce_sales_v2",
          time_window: currentTw,
        },
      }),
    });
    if (!r.ok) throw new Error("session");
    const j = await r.json();
    sessionId = j.id;
    if (j.bindings && j.bindings.time_window) {
      currentTw = Object.assign({}, currentTw, j.bindings.time_window);
    }
    refreshMeta();
  }

  async function patchSessionTw(tw) {
    await ensureSession();
    const r = await win.fetch("/sessions/" + sessionId + "/context", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        bindings: { time_window: { label: tw.label, start: tw.start, end: tw.end } },
      }),
    });
    if (!r.ok) throw new Error("patch");
    currentTw = tw;
    refreshMeta();
  }

  function askRebindConfirm(tw) {
    if (pendingRebind && pendingRebind.askEl) pendingRebind.askEl.remove();
    const card = doc.createElement("div");
    card.className = "msg assistant rebind-card";
    const wrap = doc.createElement("div");
    wrap.className = "rebind";
    const line = doc.createElement("p");
    line.className = "rebind-line";
    line.textContent = rebindLine(tw);
    wrap.appendChild(line);
    const ok = doc.createElement("button");
    ok.type = "button";
    ok.textContent = "确认";
    ok.onclick = async () => {
      try {
        card.remove();
        pendingRebind = null;
        await patchSessionTw(tw);
        setChipActive(chipKey(tw));
        submitText("用" + tw.label + "再出一版洞察报告");
      } catch (_) {
        addMsg("assistant", visibleAssistant("failed", ""));
      }
    };
    const no = doc.createElement("button");
    no.type = "button";
    no.textContent = "先不跑";
    no.style.background = "#475569";
    no.onclick = async () => {
      try {
        card.remove();
        pendingRebind = null;
        await patchSessionTw(tw);
        setChipActive(chipKey(tw));
        addMsg("assistant", "已改：时间窗→" + tw.label + "。需要时再说「出一份洞察报告」。");
      } catch (_) {
        addMsg("assistant", visibleAssistant("failed", ""));
      }
    };
    wrap.appendChild(ok);
    wrap.appendChild(no);
    card.appendChild(wrap);
    msgs.appendChild(card);
    msgs.scrollTop = msgs.scrollHeight;
    pendingRebind = { tw, askEl: card };
  }

  chipsEl.addEventListener("click", async (e) => {
    const btn = e.target.closest(".chip");
    if (!btn) return;
    const tw = {
      label: btn.getAttribute("data-label"),
      start: btn.getAttribute("data-start"),
      end: btn.getAttribute("data-end"),
    };
    if (tw.label === currentTw.label && tw.start === currentTw.start) return;
    try {
      if (hasConclusion) askRebindConfirm(tw);
      else {
        await patchSessionTw(tw);
        setChipActive(chipKey(tw));
        addMsg("assistant", "已改：时间窗→" + tw.label + "。可以说「写一份洞察报告」。");
      }
    } catch (_) {
      addMsg("assistant", visibleAssistant("failed", ""));
    }
  });

  async function finishTurn(strip, j) {
    const data = await fetchRunEvents(j.run_id || "");
    const events = (data && data.events) || [];
    await playEvents(strip, events);
    clearProgress();
    if (j.status === "failed") {
      addMsg("assistant", visibleAssistant("failed", j.markdown || j.message || j.text || ""));
      return;
    }
    let msgEl;
    if (j.status === "awaiting_hitl" && j.hitl) {
      pendingHitl = { runId: j.run_id, token: j.hitl.resume_token };
      const wrap = doc.createElement("div");
      wrap.className = "hitl";
      const ok = doc.createElement("button");
      ok.type = "button";
      ok.textContent = "确认大纲并继续";
      ok.onclick = () => resumeHitl(true);
      const no = doc.createElement("button");
      no.type = "button";
      no.textContent = "拒绝";
      no.style.background = "#ef4444";
      no.onclick = () => resumeHitl(false);
      wrap.appendChild(ok);
      wrap.appendChild(no);
      msgEl = addMsg("assistant", j.markdown || j.message || "请确认后继续", wrap);
    } else {
      msgEl = addMsg("assistant", visibleAssistant(j.status, j.markdown || j.text || ""));
      if (j.status === "completed") hasConclusion = true;
    }
    if (j.context_bindings && j.context_bindings.time_window) {
      currentTw = Object.assign({}, currentTw, j.context_bindings.time_window);
    }
    refreshMeta();
    maybeAppendDebug(msgEl, events);
  }

  async function submitText(text) {
    const value = String(text || "").trim();
    if (!value) return;
    inp.value = "";
    sendBtn.disabled = true;
    addMsg("user", value);
    win.__stripsThisTurn = 0;
    const strip = showProgress();
    try {
      await ensureSession();
      const r = await win.fetch("/sessions/" + sessionId + "/messages", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: value, bindings: { time_window: currentTw } }),
      });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error("http");
      await finishTurn(strip, j);
    } catch (_) {
      clearProgress();
      addMsg("assistant", visibleAssistant("failed", ""));
    } finally {
      sendBtn.disabled = false;
      if (inp.focus) inp.focus();
    }
  }

  form.addEventListener("submit", (e) => {
    e.preventDefault();
    submitText(inp.value);
  });

  async function resumeHitl(approve) {
    if (!pendingHitl) return;
    const runId = pendingHitl.runId;
    const token = pendingHitl.token;
    pendingHitl = null;
    sendBtn.disabled = true;
    win.__stripsThisTurn = 0;
    const strip = showProgress();
    try {
      const r = await win.fetch("/runs/" + encodeURIComponent(runId) + "/resume", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ resume_token: token, approve }),
      });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error("resume");
      if (!j.run_id) j.run_id = runId;
      await finishTurn(strip, j);
    } catch (_) {
      clearProgress();
      addMsg("assistant", visibleAssistant("failed", ""));
    } finally {
      sendBtn.disabled = false;
      if (inp.focus) inp.focus();
    }
  }

  ensureSession()
    .then(() => {
      renderEmpty();
    })
    .catch(() => {
      addMsg("assistant", visibleAssistant("failed", ""));
    });
}

export function bootIfPresent(doc, win) {
  const documentRef = doc || globalThis.document;
  const windowRef = win || globalThis;
  if (documentRef && typeof documentRef.getElementById === "function" && documentRef.getElementById("msgs")) {
    boot(documentRef, windowRef);
  }
}

bootIfPresent();
