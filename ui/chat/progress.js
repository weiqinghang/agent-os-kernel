/**
 * App 侧进度文案。只读现有 run_events kind，不发明 phase_*，不把业务阶段机放进内核。
 * 呼吸条只允许白名单短句；计划卡状态另用 Skill 的计数规则在本地算，呼吸条不读它。
 * tool 名、mode、id、原始 JSON、CoT 一律不进入文案。
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

/**
 * 与 insight_report.PLAN_PROGRESS 同一张表。只含计数和文案通配，没有工具名。
 * v0 由页面计算；编排仍只发 revision 1、全部 pending。
 */
export const PLAN_PROGRESS = [
  { when: "outline_confirm", status: "pending" },
  { when: "tool_calls", count: 1, label: "*范围*" },
  { when: "tool_calls", count: 2, label: "*类目*" },
  { when: "tool_calls", count: 3, label: "*渠道*" },
  { when: "tool_calls", count: 4, label: "*省*" },
  { when: "tool_calls", count: 5, label: "*退货*" },
  { when: "finalizing", label: "*结论*", status: "active" },
];

export const PLAN_STATUS_TEXT = {
  pending: "未开始",
  active: "进行中",
  done: "完成",
  skipped: "跳过",
};

/** 计划卡用的中文短句。不进呼吸白名单。 */
const PLAN_LIMIT = 5;

function isBusinessLabel(label) {
  if (label.length > 24) return false;
  return /^[\u4e00-\u9fff，。、]+$/.test(label);
}

function labelMatches(label, pattern) {
  if (typeof pattern !== "string" || pattern.length === 0) return false;
  const parts = pattern.split("*").filter((part) => part.length > 0);
  if (!parts.length) return false;
  let pos = 0;
  for (const part of parts) {
    const found = label.indexOf(part, pos);
    if (found < 0) return false;
    pos = found + part.length;
  }
  return true;
}

function readSignals(events) {
  let outline = false;
  let seenOutline = false;
  let successes = 0;
  let finalizing = false;
  let cancelled = false;
  for (const ev of events || []) {
    if (!ev || typeof ev !== "object") continue;
    const kind = ev.kind;
    const p = payloadOf(ev);
    if (kind === "tool_call") {
      if (p.ok === true) successes += 1;
      continue;
    }
    if (kind === "hitl" && p.kind === "outline_confirm") {
      if (p.auto === true || p.approved === true) {
        outline = false;
        seenOutline = false;
      } else {
        outline = true;
        seenOutline = true;
      }
      continue;
    }
    if (kind === "state") {
      const state = typeof p.state === "string" ? p.state : "";
      const held = p.hitl_kind === "outline_confirm" || seenOutline;
      if (state === "awaiting_hitl" && held) {
        outline = true;
        seenOutline = true;
      } else if (seenOutline && state && state !== "awaiting_hitl") {
        outline = false;
      }
      if (state === "failed") finalizing = false;
      else if (state === "cancelled") cancelled = true;
      else if (state === "completed" || state === "finalizing") finalizing = true;
      continue;
    }
    if (kind === "model_step") {
      if (writingModelStep(p)) finalizing = true;
      continue;
    }
    if (kind === "assistant") finalizing = true;
  }
  return { outline, successes, finalizing, cancelled };
}

function hasWhen(rules, when) {
  return (rules || []).some((rule) => rule && rule.when === when);
}

function statusesFromCounts(steps, rules, successes) {
  const work = [];
  for (const rule of rules || []) {
    if (!rule || rule.when !== "tool_calls") continue;
    const count = rule.count;
    const pattern = rule.label;
    if (typeof count !== "number" || count < 1 || typeof pattern !== "string" || !pattern) continue;
    work.push({ count, pattern });
  }
  work.sort((a, b) => a.count - b.count);
  const assigned = new Map();
  for (const rule of work) {
    for (let i = 0; i < steps.length; i++) {
      if (assigned.has(i)) continue;
      if (labelMatches(steps[i].label, rule.pattern)) {
        assigned.set(i, rule.count);
        break;
      }
    }
  }
  const statuses = steps.map(() => "pending");
  let activeAt = -1;
  let activeCount = Infinity;
  for (const [index, count] of assigned) {
    if (successes >= count) statuses[index] = "done";
    else if (count < activeCount) {
      activeAt = index;
      activeCount = count;
    }
  }
  if (activeAt >= 0) statuses[activeAt] = "active";
  return statuses;
}

function markConclusion(steps, statuses, rules) {
  for (const rule of rules || []) {
    if (!rule || rule.when !== "finalizing") continue;
    if (typeof rule.label !== "string" || !rule.label) continue;
    for (let i = 0; i < steps.length; i++) {
      if (labelMatches(steps[i].label, rule.label) && statuses[i] !== "done") {
        statuses[i] = "active";
        break;
      }
    }
  }
}

/** 纯函数：步骤文案 + 已有事件 + 规则表 → 每步状态。不读工具名。 */
export function applyPlanProgress(steps, events, rules) {
  const table = rules || PLAN_PROGRESS;
  const clean = [];
  for (const step of steps || []) {
    if (!step || typeof step !== "object" || Array.isArray(step)) continue;
    const label = typeof step.label === "string" ? step.label.trim() : "";
    if (!label) continue;
    const id = typeof step.id === "string" ? step.id.trim() : "";
    clean.push({ id, label });
  }
  const signals = readSignals(events);
  let statuses;
  if (signals.outline && !signals.cancelled && hasWhen(table, "outline_confirm")) {
    statuses = clean.map(() => "pending");
  } else {
    statuses = statusesFromCounts(clean, table, signals.successes);
    if (signals.finalizing) markConclusion(clean, statuses, table);
  }
  if (signals.cancelled) statuses = statuses.map((status) => (status === "done" ? "done" : "skipped"));
  return clean.map((step, index) => ({ id: step.id, label: step.label, status: statuses[index] }));
}

function collectPlanSteps(events) {
  let steps = null;
  for (const ev of events || []) {
    if (!ev || ev.kind !== "plan") continue;
    const raw = Array.isArray(payloadOf(ev).steps) ? payloadOf(ev).steps : [];
    const clean = [];
    for (const step of raw) {
      if (!step || typeof step !== "object" || Array.isArray(step)) continue;
      const label = typeof step.label === "string" ? step.label.trim() : "";
      if (!label || !isBusinessLabel(label)) continue;
      const id = typeof step.id === "string" ? step.id.trim() : "";
      clean.push({ id, label });
      if (clean.length >= PLAN_LIMIT) break;
    }
    if (clean.length) steps = clean;
  }
  return steps;
}

/**
 * 取最后一条能上屏的 plan，再用规则表重算 status。
 * 事件里自带的 status 不采用。返回 null 表示还没有可显示的步骤。
 */
/** 退货风险简报的计数表。形状与洞察相同，通配对的是风险步骤文案。 */
export const RETURN_RISK_PLAN_PROGRESS = [
  { when: "outline_confirm", status: "pending" },
  { when: "tool_calls", count: 1, label: "*窗口*" },
  { when: "tool_calls", count: 2, label: "*渠道*" },
  { when: "tool_calls", count: 3, label: "*类目*" },
  { when: "tool_calls", count: 4, label: "*省*" },
  { when: "tool_calls", count: 5, label: "*排序*" },
  { when: "finalizing", label: "*结论*", status: "active" },
];

export function projectPlan(events, rules) {
  const steps = collectPlanSteps(events);
  if (!steps) return null;
  return applyPlanProgress(steps, events, rules || PLAN_PROGRESS);
}

/** 只看 kind 与少量中性字段。不读取 name/tool/mode/id/thinking。 */
function suggest(ev) {
  const kind = ev && ev.kind;
  const p = payloadOf(ev);
  // plan 交给计划卡；retry 只记新回合。呼吸条文案仍走白名单，不读载荷。
  if (kind === "plan" || kind === "retry") return null;
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
