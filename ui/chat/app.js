const msgs = document.getElementById("msgs");
const form = document.getElementById("f");
const inp = document.getElementById("inp");
const sendBtn = document.getElementById("send");
const meta = document.getElementById("meta");
const chipsEl = document.getElementById("chips");

let sessionId = null;
let pendingHitl = null;
let hasConclusion = false;
let currentTw = {
  label: "近90天",
  start: "2025-07-25",
  end: "2025-10-22",
};
let pendingRebind = null; // {tw, askEl}

const KIND_LABEL = {
  model_step: "模型步骤",
  tool_call: "工具调用",
  hitl: "人审",
  state: "状态",
  run_created: "Run 创建",
};

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;" }[c]));
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

function eventSummary(ev) {
  const p = ev.payload || {};
  const kind = ev.kind;
  const label = KIND_LABEL[kind] || kind;
  let detail = "";
  if (kind === "tool_call") {
    detail = p.tool || p.name || "";
    if (p.fingerprint) detail += (detail ? " · " : "") + "fp " + p.fingerprint;
  } else if (kind === "model_step") {
    const bits = [];
    if (p.type) bits.push(p.type);
    if (p.mode) bits.push(p.mode);
    if (typeof p.step === "number") bits.push("step " + p.step);
    if (typeof p.n_calls === "number" && p.n_calls > 0) bits.push(p.n_calls + " calls");
    detail = bits.join(" · ");
  } else if (kind === "hitl") {
    const bits = [];
    if (p.kind) bits.push(p.kind);
    if (p.auto) bits.push("auto");
    if (p.approved === true) bits.push("批准");
    if (p.approved === false) bits.push("拒绝");
    detail = bits.join(" · ");
  } else if (kind === "state") {
    detail = p.state || "";
    if (p.skill) detail += (detail ? " · " : "") + p.skill;
  } else if (kind === "run_created") {
    detail = p.session_id ? "session " + p.session_id.slice(0, 12) + "…" : "";
  } else {
    detail = Object.keys(p).length ? JSON.stringify(p).slice(0, 80) : "";
  }
  return { label, detail };
}

function buildChainPanel(runId, events) {
  const panel = document.createElement("details");
  panel.className = "chain";
  panel.open = true;
  const summary = document.createElement("summary");
  const n = (events || []).length;
  summary.innerHTML =
    '<span class="chain-title">Agent 链路</span>' +
    '<span class="chain-meta">run ' + escapeHtml(runId) + " · " + n + " 事件</span>";
  panel.appendChild(summary);
  const ol = document.createElement("ol");
  ol.className = "chain-list";
  (events || []).forEach((ev) => {
    const { label, detail } = eventSummary(ev);
    const li = document.createElement("li");
    li.className = "chain-item kind-" + escapeHtml(ev.kind || "other");
    const kindSpan = document.createElement("span");
    kindSpan.className = "chain-kind";
    kindSpan.textContent = label;
    li.appendChild(kindSpan);
    if (detail) {
      const det = document.createElement("span");
      det.className = "chain-detail";
      det.textContent = detail;
      li.appendChild(det);
    }
    ol.appendChild(li);
  });
  if (!events || !events.length) {
    const li = document.createElement("li");
    li.className = "chain-item";
    li.textContent = "暂无事件";
    ol.appendChild(li);
  }
  panel.appendChild(ol);
  return panel;
}

async function fetchRunEvents(runId) {
  if (!runId) return null;
  try {
    const r = await fetch("/runs/" + encodeURIComponent(runId));
    if (!r.ok) return null;
    return await r.json();
  } catch (_) {
    return null;
  }
}

async function appendChain(msgEl, runId) {
  if (!msgEl || !runId) return;
  const data = await fetchRunEvents(runId);
  const events = (data && data.events) || [];
  const old = msgEl.querySelector(".chain");
  if (old) old.remove();
  msgEl.appendChild(buildChainPanel(runId, events));
  msgs.scrollTop = msgs.scrollHeight;
}

function addMsg(role, text, extra) {
  const div = document.createElement("div");
  div.className = "msg " + role + (role === "assistant" ? " md" : "");
  const roleEl = document.createElement("div");
  roleEl.className = "role";
  roleEl.textContent = role === "user" ? "你" : "助理";
  div.appendChild(roleEl);
  const body = document.createElement("div");
  if (role === "assistant") body.innerHTML = simpleMd(text);
  else body.textContent = text;
  div.appendChild(body);
  if (extra) div.appendChild(extra);
  msgs.appendChild(div);
  msgs.scrollTop = msgs.scrollHeight;
  return div;
}

function refreshMeta(extra) {
  const tw = currentTw.label || "—";
  meta.textContent =
    (sessionId ? "session " + sessionId : "…") +
    " · 时间窗 " + tw +
    (extra ? " · " + extra : "");
}

function setChipActive(twKey) {
  chipsEl.querySelectorAll(".chip").forEach((btn) => {
    btn.classList.toggle("active", btn.getAttribute("data-tw") === String(twKey));
  });
}

async function patchSessionTw(tw) {
  await ensureSession();
  const r = await fetch("/sessions/" + sessionId + "/context", {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      bindings: { time_window: { label: tw.label, start: tw.start, end: tw.end } },
    }),
  });
  if (!r.ok) throw new Error("patch bindings failed " + r.status);
  currentTw = tw;
  refreshMeta();
}

function askRebindConfirm(tw) {
  if (pendingRebind && pendingRebind.askEl) pendingRebind.askEl.remove();
  const wrap = document.createElement("div");
  wrap.className = "rebind";
  wrap.innerHTML =
    "已改时间窗 → <strong>" +
    escapeHtml(tw.label) +
    "</strong>（" +
    escapeHtml(tw.start) +
    " ~ " +
    escapeHtml(tw.end) +
    "）。用新上下文再出一版？";
  const ok = document.createElement("button");
  ok.textContent = "是，再跑一版";
  ok.onclick = async () => {
    wrap.remove();
    pendingRebind = null;
    await patchSessionTw(tw);
    setChipActive(
      tw.label === "近30天" ? "30" : tw.label === "近7天" ? "7" : "90"
    );
    inp.value = "请基于电商销量数据写一份简短洞察报告";
    form.requestSubmit();
  };
  const no = document.createElement("button");
  no.textContent = "先不跑";
  no.style.background = "#475569";
  no.onclick = async () => {
    wrap.remove();
    pendingRebind = null;
    await patchSessionTw(tw);
    setChipActive(
      tw.label === "近30天" ? "30" : tw.label === "近7天" ? "7" : "90"
    );
    addMsg("assistant", "已改：时间窗→" + tw.label + "。需要时再说「出一份洞察报告」。");
  };
  wrap.appendChild(ok);
  wrap.appendChild(no);
  const el = addMsg("assistant", "上下文变更", wrap);
  pendingRebind = { tw, askEl: el };
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
  if (hasConclusion) {
    askRebindConfirm(tw);
  } else {
    await patchSessionTw(tw);
    setChipActive(btn.getAttribute("data-tw"));
    addMsg("assistant", "已改：时间窗→" + tw.label + "。可以说「写一份洞察报告」。");
  }
});

async function ensureSession() {
  if (sessionId) return;
  const r = await fetch("/sessions", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      bindings: {
        skill: "insight_report",
        dataset: "ecommerce_sales",
        time_window: currentTw,
      },
    }),
  });
  const j = await r.json();
  sessionId = j.id;
  if (j.bindings && j.bindings.time_window) {
    currentTw = Object.assign({}, currentTw, j.bindings.time_window);
  }
  refreshMeta();
}

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const text = inp.value.trim();
  if (!text) return;
  inp.value = "";
  sendBtn.disabled = true;
  addMsg("user", text);
  try {
    await ensureSession();
    const r = await fetch("/sessions/" + sessionId + "/messages", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        text,
        bindings: { time_window: currentTw },
      }),
    });
    const j = await r.json();
    if (j.context_bindings && j.context_bindings.time_window) {
      currentTw = Object.assign({}, currentTw, j.context_bindings.time_window);
    }
    refreshMeta("run " + j.run_id + " · " + j.status);
    let msgEl;
    if (j.status === "awaiting_hitl" && j.hitl) {
      pendingHitl = { runId: j.run_id, token: j.hitl.resume_token };
      const wrap = document.createElement("div");
      wrap.className = "hitl";
      const ok = document.createElement("button");
      ok.textContent = "确认大纲并继续";
      ok.onclick = () => resumeHitl(true);
      const no = document.createElement("button");
      no.textContent = "拒绝";
      no.style.background = "#ef4444";
      no.onclick = () => resumeHitl(false);
      wrap.appendChild(ok);
      wrap.appendChild(no);
      msgEl = addMsg("assistant", j.markdown || j.message || "等待确认", wrap);
    } else {
      msgEl = addMsg("assistant", j.markdown || j.text || JSON.stringify(j));
      if (j.status === "completed") hasConclusion = true;
    }
    if (j.run_id) await appendChain(msgEl, j.run_id);
  } catch (err) {
    addMsg("assistant", "错误：" + err);
  } finally {
    sendBtn.disabled = false;
    inp.focus();
  }
});

async function resumeHitl(approve) {
  if (!pendingHitl) return;
  const runId = pendingHitl.runId;
  const r = await fetch("/runs/" + runId + "/resume", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ resume_token: pendingHitl.token, approve }),
  });
  const j = await r.json();
  pendingHitl = null;
  const msgEl = addMsg("assistant", j.markdown || JSON.stringify(j));
  if (j.status === "completed") hasConclusion = true;
  refreshMeta("run " + (j.run_id || runId) + " · " + j.status);
  await appendChain(msgEl, j.run_id || runId);
}

ensureSession().then(() => {
  addMsg(
    "assistant",
    "你好。三拍演示：①「写一份洞察报告」→ ②「第2条依据/样本」→ ③点上方时间窗 chip（如近30天）确认后再出一版。本切片仅开放时间窗绑定；退货风险 Skill 已推迟。"
  );
});
