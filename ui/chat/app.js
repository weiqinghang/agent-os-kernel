/**
 * 洞察报告助理前台。进度与脱敏都在这一层：
 * 发令后立刻放一条呼吸条带，再轮询 run_events，把白名单文案写回同一条。
 * 轮询期间可以停止。调试链路默认关；打开后也只显示白名单短句。
 */
import {
  IMMEDIATE,
  PLAN_STATUS_TEXT,
  debugLines,
  presentAssistant,
  project,
  projectPlan,
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
  const stopBtn = doc.getElementById("stop");
  const meta = doc.getElementById("meta");
  const chipsEl = doc.getElementById("chips");

  let sessionId = null;
  let activeRunId = null;
  let pendingCancel = null;
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

  function paintPlan(events) {
    const steps = projectPlan(events);
    if (!steps) return;
    let card = doc.querySelector(".plan-card.is-current");
    if (!card) {
      card = doc.createElement("details");
      card.className = "plan-card is-current";
      card.open = true;
      const summary = doc.createElement("summary");
      summary.textContent = "报告大纲";
      card.appendChild(summary);
      const list = doc.createElement("ol");
      list.className = "plan-steps";
      card.appendChild(list);
      const strip = doc.querySelector(".progress-strip");
      if (strip) msgs.insertBefore(card, strip);
      else msgs.appendChild(card);
    }
    const list = card.querySelector(".plan-steps");
    list.replaceChildren();
    steps.forEach((step) => {
      const li = doc.createElement("li");
      const word = PLAN_STATUS_TEXT[step.status] || "";
      if (word) li.setAttribute("data-status", step.status);
      const label = doc.createElement("span");
      label.className = "plan-label";
      label.textContent = step.label;
      li.appendChild(label);
      if (word) {
        const mark = doc.createElement("span");
        mark.className = "plan-status";
        mark.textContent = word;
        li.appendChild(mark);
      }
      list.appendChild(li);
    });
    msgs.scrollTop = msgs.scrollHeight;
  }

  function settlePlan() {
    const card = doc.querySelector(".plan-card.is-current");
    if (card) card.classList.remove("is-current");
  }

  async function playEvents(strip, events) {
    paintPlan(events);
    const projected = project(events);
    const textEl = strip.querySelector(".progress-text");
    const from = strip.__played || 0;
    for (let i = from; i < projected.steps.length; i++) {
      const text = projected.steps[i];
      const changed = textEl.textContent !== text;
      if (changed) {
        textEl.textContent = text;
        strip.__updates.push(text);
      }
      if (changed && settings.replayMs > 0 && i < projected.steps.length - 1) {
        await new Promise((r) => setTimeout(r, settings.replayMs));
      }
    }
    if (projected.steps.length > from) strip.__played = projected.steps.length;
    win.__lastProgressUpdates = strip.__updates.slice();
    noteStrips();
    return projected;
  }

  function debugOn() {
    const box = doc.getElementById("debug-chain");
    return !!(box && box.checked);
  }

  function debugEntryEnabled() {
    const search = (win.location && win.location.search) || "";
    return new URLSearchParams(search).get("debug") === "1";
  }

  const debugBox = doc.getElementById("debug-chain");
  const debugLabel = doc.querySelector("label.debug-toggle");
  if (debugLabel) debugLabel.hidden = !debugEntryEnabled();
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

  function isTerminalStatus(status) {
    return status === "completed" || status === "failed" || status === "awaiting_hitl" || status === "cancelled";
  }

  function showStop(runId) {
    activeRunId = runId || null;
    pendingCancel = null;
    if (!stopBtn) return;
    const on = !!activeRunId;
    stopBtn.hidden = !on;
    stopBtn.disabled = !on;
  }

  function hideStop() {
    activeRunId = null;
    pendingCancel = null;
    if (!stopBtn) return;
    stopBtn.hidden = true;
    stopBtn.disabled = true;
  }

  function applyStop(view) {
    if (!pendingCancel) return false;
    view.status = pendingCancel.status || "cancelled";
    view.markdown = pendingCancel.markdown || "已停止";
    return true;
  }

  async function requestStop() {
    const runId = activeRunId;
    if (!runId || !stopBtn || stopBtn.hidden) return;
    stopBtn.disabled = true;
    try {
      const resp = await win.fetch("/runs/" + encodeURIComponent(runId) + "/cancel", { method: "POST" });
      const body = resp.ok ? await resp.json().catch(() => ({})) : null;
      if (activeRunId !== runId) return;
      if (!resp.ok || !body) {
        stopBtn.disabled = false;
        return;
      }
      pendingCancel = {
        status: body.status || "cancelled",
        markdown: body.markdown || body.result || "已停止",
      };
    } catch (_) {
      if (activeRunId === runId && stopBtn && !stopBtn.hidden) stopBtn.disabled = false;
    }
  }

  if (stopBtn) {
    stopBtn.addEventListener("click", () => {
      requestStop();
    });
  }

  async function fetchEventPage(runId, afterId) {
    try {
      const r = await win.fetch(
        "/runs/" + encodeURIComponent(runId) + "/events?after_id=" + encodeURIComponent(afterId || 0)
      );
      if (!r.ok) return null;
      return await r.json();
    } catch (_) {
      return null;
    }
  }

  function renderOutcome(view, events) {
    const shown =
      view && view.status === "cancelled"
        ? (events || []).concat([{ kind: "state", payload: { state: "cancelled" } }])
        : events;
    paintPlan(shown);
    settlePlan();
    clearProgress();
    const status = view.status;
    const markdown = view.markdown || view.message || view.text || "";
    if (status === "failed") {
      const extra = view.run_id ? retryControl(view.run_id) : null;
      addMsg("assistant", visibleAssistant("failed", markdown), extra);
      return;
    }
    let msgEl;
    if (status === "awaiting_hitl" && view.hitl) {
      pendingHitl = { runId: view.run_id, token: view.hitl.resume_token };
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
      msgEl = addMsg("assistant", markdown || "请确认后继续", wrap);
    } else {
      msgEl = addMsg("assistant", visibleAssistant(status, markdown));
      if (status === "completed") hasConclusion = true;
    }
    if (view.context_bindings && view.context_bindings.time_window) {
      currentTw = Object.assign({}, currentTw, view.context_bindings.time_window);
    }
    refreshMeta();
    maybeAppendDebug(msgEl, events);
  }

  async function followTurn(strip, accepted) {
    const view = {
      run_id: accepted.run_id || "",
      status: accepted.status || "",
      markdown: accepted.markdown || accepted.message || accepted.text || "",
      hitl: accepted.hitl || null,
      context_bindings: accepted.context_bindings || null,
    };
    showStop(view.run_id);
    let after = 0;
    let events = [];
    let failedPoll = false;
    const started = Date.now();
    try {
      while (true) {
        if (applyStop(view)) break;
        if (view.run_id && !failedPoll) {
          const page = await fetchEventPage(view.run_id, after);
          if (!page) {
            failedPoll = true;
          } else {
            const fresh = Array.isArray(page.events) ? page.events : [];
            if (fresh.length) {
              events = events.concat(fresh);
              const last = fresh[fresh.length - 1];
              if (last && typeof last.id === "number") after = last.id;
              await playEvents(strip, events);
            }
            if (page.state) view.status = page.state;
            if (typeof page.result === "string") view.markdown = page.result;
            if (page.hitl) view.hitl = page.hitl;
            if (page.context_bindings) view.context_bindings = page.context_bindings;
          }
        }
        if (applyStop(view)) break;
        if (failedPoll && !isTerminalStatus(view.status)) view.status = "failed";
        if (isTerminalStatus(view.status) || failedPoll || !view.run_id) break;
        if (Date.now() - started >= settings.pollBudgetMs) {
          view.status = "failed";
          break;
        }
        await new Promise((r) => setTimeout(r, settings.pollMs));
      }
      renderOutcome(view, events);
    } finally {
      hideStop();
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
      const text = "用" + tw.label + "再出一版洞察报告";
      try {
        card.remove();
        pendingRebind = null;
        addMsg("user", text);
        await patchSessionTw(tw);
        setChipActive(chipKey(tw));
        await submitText(text, { echo: false });
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

  async function submitText(text, opts) {
    const value = String(text || "").trim();
    if (!value) return;
    inp.value = "";
    sendBtn.disabled = true;
    if (!opts || opts.echo !== false) addMsg("user", value);
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
      await followTurn(strip, j);
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

  function retryControl(runId) {
    const wrap = doc.createElement("div");
    wrap.className = "retry";
    const btn = doc.createElement("button");
    btn.type = "button";
    btn.textContent = "重试";
    btn.addEventListener("click", () => {
      retryRun(runId, btn);
    });
    wrap.appendChild(btn);
    return wrap;
  }

  async function retryRun(runId, btn) {
    if (btn) btn.disabled = true;
    sendBtn.disabled = true;
    addMsg("user", "请重试上一轮。");
    win.__stripsThisTurn = 0;
    const strip = showProgress();
    try {
      const resp = await win.fetch("/runs/" + encodeURIComponent(runId) + "/retry", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}",
      });
      const body = await resp.json();
      if (!resp.ok || !body || !body.run_id) throw new Error("retry");
      await followTurn(strip, body);
    } catch (_) {
      clearProgress();
      addMsg("assistant", visibleAssistant("failed", ""));
    } finally {
      sendBtn.disabled = false;
      if (inp.focus) inp.focus();
    }
  }

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
      await followTurn(strip, j);
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
