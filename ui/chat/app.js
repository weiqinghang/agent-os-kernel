const msgs = document.getElementById("msgs");
const form = document.getElementById("f");
const inp = document.getElementById("inp");
const sendBtn = document.getElementById("send");
const meta = document.getElementById("meta");

let sessionId = null;
let pendingHitl = null;

function escapeHtml(s) {
  return s.replace(/[&<>"']/g, (c) => ({ "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;" }[c]));
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
}

async function ensureSession() {
  if (sessionId) return;
  const r = await fetch("/sessions", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
  const j = await r.json();
  sessionId = j.id;
  meta.textContent = "session " + sessionId;
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
      body: JSON.stringify({ text }),
    });
    const j = await r.json();
    meta.textContent = `session ${sessionId} · run ${j.run_id} · ${j.status}`;
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
      addMsg("assistant", j.markdown || j.message || "等待确认", wrap);
    } else {
      addMsg("assistant", j.markdown || j.text || JSON.stringify(j));
    }
  } catch (err) {
    addMsg("assistant", "错误：" + err);
  } finally {
    sendBtn.disabled = false;
    inp.focus();
  }
});

async function resumeHitl(approve) {
  if (!pendingHitl) return;
  const r = await fetch("/runs/" + pendingHitl.runId + "/resume", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ resume_token: pendingHitl.token, approve }),
  });
  const j = await r.json();
  pendingHitl = null;
  addMsg("assistant", j.markdown || JSON.stringify(j));
  meta.textContent = `session ${sessionId} · run ${j.run_id} · ${j.status}`;
}

ensureSession().then(() => {
  addMsg("assistant", "你好。可以说「请基于电商销量数据写一份简短洞察报告」，随后可追问「哪个省份 GMV 最高？」。");
});
