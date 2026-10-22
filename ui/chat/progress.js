/**
 * App 侧进度文案。只读现有 run_events kind，不发明 phase_*，不把业务阶段机放进内核。
 * 映射结果只允许白名单短句；tool 名、mode、id、原始 JSON、CoT 一律不进入文案。
 */

export const IMMEDIATE = "处理中";
export const FAILURE_COPY = "这次没能完成，换个问法试试。";
export const EMPTY_COPY = "（没有可展示的结论）";
export const WHITELIST = [IMMEDIATE, "思考中", "正在查询数据", "正在整理结论", "等确认"];

/** 测试可把 replayMs / pollMs 调小。页面轮询 events，不在内核里写进度文案。 */
export const settings = { replayMs: 40, pollMs: 200, pollBudgetMs: 120000 };

const RANK = {
  处理中: 0,
  思考中: 1,
  正在查询数据: 2,
  正在整理结论: 3,
};

function payloadOf(ev) {
  const p = ev && ev.payload;
  return p && typeof p === "object" ? p : {};
}

const SEEKING_TYPES = new Set(["tool_calls", "live_retry", "live_fallback", "live_fallback_stub"]);
const WRITING_TYPES = new Set(["final", "finalizing", "loop_break"]);

/**
 * 收尾才进「正在整理结论」。认 late model_step（final / finalizing / loop_break，
 * 或这两个标记，或已不再要工具且 n_calls 为 0）。不认 phase_*，也不读工具名。
 */
function writingModelStep(p) {
  const type = typeof p.type === "string" ? p.type : "";
  if (SEEKING_TYPES.has(type)) return false;
  if (WRITING_TYPES.has(type)) return true;
  if (p.finalizing === true) return true;
  if (p.loop_break === true) return true;
  if (p.n_calls === 0 && type) return true;
  return false;
}

/** 只看 kind 与少量中性字段。不读取 name/tool/mode/id/thinking。 */
function suggest(ev) {
  const kind = ev && ev.kind;
  const p = payloadOf(ev);
  if (kind === "tool_call") return "正在查询数据";
  if (kind === "assistant") return "正在整理结论";
  if (kind === "hitl") {
    if (p.auto === true || p.approved === true) return null;
    return "等确认";
  }
  if (kind === "model_step") {
    if (writingModelStep(p)) return "正在整理结论";
    return "思考中";
  }
  if (kind === "state") {
    if (p.state === "awaiting_hitl") return "等确认";
    if (p.state === "completed" || p.state === "finalizing") return "正在整理结论";
    if (p.state === "failed") return "failed";
    if (p.state === "acting" || p.state === "created") return "思考中";
    return null;
  }
  return null;
}

export function project(events) {
  const state = {
    text: IMMEDIATE,
    failed: false,
    waiting: false,
    steps: [IMMEDIATE],
  };
  for (const ev of events || []) {
    const next = suggest(ev || {});
    if (next === "failed") {
      state.failed = true;
      continue;
    }
    if (!next || WHITELIST.indexOf(next) === -1) continue;
    if (next === "等确认") {
      if (state.text !== next) state.steps.push(next);
      state.text = next;
      state.waiting = true;
      continue;
    }
    if (state.waiting) continue;
    if ((RANK[next] ?? 0) < (RANK[state.text] ?? 0)) continue;
    if (state.text !== next) {
      state.text = next;
      state.steps.push(next);
    }
  }
  return { text: state.text, failed: state.failed, steps: state.steps.slice() };
}

export function debugLines(events) {
  const steps = project(events).steps.filter((s) => s !== IMMEDIATE);
  return steps.length ? steps : ["暂无更多进度"];
}

export function rebindLine(tw) {
  const label = (tw && tw.label) || "";
  const start = (tw && tw.start) || "";
  const end = (tw && tw.end) || "";
  return `时间窗改为${label}（${start} ~ ${end}），用这个范围再出一版？`;
}

export function isHumanFailure(markdown) {
  const s = String(markdown || "").trim();
  if (!s || s.length > 80) return false;
  if (
    /[{}\[\]]|traceback|exception|\berror\b|query_stats|sample_records|search_records|describe_dataset|list_datasets|\bstub\b|\blive\b|run_|ses_|agent_id|resume_token|fingerprint|phase_|tool_call|model_step/i.test(
      s
    )
  ) {
    return false;
  }
  return true;
}

export function visibleAssistant(status, markdown) {
  const raw = String(markdown || "");
  if (status === "failed") return isHumanFailure(raw) ? raw.trim() : FAILURE_COPY;
  const trimmed = raw.trim();
  return trimmed || EMPTY_COPY;
}

/**
 * 市场经理看见的结论。指纹、dataset、Tool Gateway 留在原文里，默认不上屏。
 * 调试开关打开时原文照示，供对指纹。不另建脱敏服务。
 */
function stripSurface(line) {
  return line
    .replace(/dataset\s*=\s*`[^`]*`\s*[；;]?\s*/gi, "")
    .replace(/dataset\s*=\s*\S+\s*[；;]?\s*/gi, "");
}

/** 依据按钮只挂在「这一条自己的依据条目」上。连续编号（样本 1.–5.）不算。 */
function ownsEvidence(line, pending, streak) {
  if (!/^\s*[-*+]\s*evidence\s*:/i.test(line)) return false;
  return !!(pending && streak === 1);
}

export function presentAssistant(markdown, debug) {
  const raw = String(markdown || "");
  if (debug) return { text: raw, actions: [] };
  const actions = [];
  const seen = new Set();
  let pending = null;
  let streak = 0;
  const kept = [];
  for (const line of raw.split(/\r?\n/)) {
    const num = /^\s*(\d+)\.\s/.exec(line);
    if (num) {
      pending = Number(num[1]);
      streak += 1;
    }
    if (/evidence\s*:/i.test(line)) {
      if (ownsEvidence(line, pending, streak) && !seen.has(pending)) {
        seen.add(pending);
        actions.push({ n: pending, label: "查看第" + pending + "条依据" });
      }
      pending = null;
      streak = 0;
      continue;
    }
    if (/tool\s*gateway/i.test(line)) continue;
    kept.push(stripSurface(line));
  }
  return { text: kept.join("\n"), actions };
}
