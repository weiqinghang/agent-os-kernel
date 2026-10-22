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
    if (p.type === "final") return "正在整理结论";
    return "思考中";
  }
  if (kind === "state") {
    if (p.state === "awaiting_hitl") return "等确认";
    if (p.state === "completed") return "正在整理结论";
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
export function presentAssistant(markdown, debug) {
  const raw = String(markdown || "");
  if (debug) return { text: raw, actions: [] };
  const actions = [];
  const seen = new Set();
  let finding = null;
  const kept = [];
  for (const line of raw.split(/\r?\n/)) {
    const num = /^\s*(\d+)\.\s/.exec(line);
    if (num) finding = Number(num[1]);
    if (/evidence\s*:/i.test(line)) {
      if (finding && !seen.has(finding)) {
        seen.add(finding);
        actions.push({ n: finding, label: "查看第" + finding + "条依据" });
      }
      continue;
    }
    if (/tool\s*gateway/i.test(line)) continue;
    kept.push(
      line
        .replace(/dataset\s*=\s*`[^`]*`\s*[；;]?\s*/gi, "")
        .replace(/dataset\s*=\s*\S+\s*[；;]?\s*/gi, "")
    );
  }
  return { text: kept.join("\n"), actions };
}
