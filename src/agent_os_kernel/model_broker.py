"""Model broker: every decide() goes through here — live OpenAI-compat or StubBackend."""
from __future__ import annotations

import json
import os
from typing import Any


def has_llm() -> bool:
    return bool(
        os.getenv("MODEL_API_KEY")
        or os.getenv("XAI_API_KEY")
        or os.getenv("OPENAI_API_KEY")
    )


def model_mode() -> str:
    return "live" if has_llm() else "stub"


def decide(
    *,
    system: str,
    messages: list[dict[str, Any]],
    allowed_tools: list[str],
    tool_results: list[dict[str, Any]],
) -> dict[str, Any]:
    """Return structured decision: tool_calls | final.

    Always goes through broker. Stub still returns real tool plans / markdown
    built only from tool_results (never invents numbers).
    """
    if has_llm():
        return _LiveBackend.decide(
            system=system,
            messages=messages,
            allowed_tools=allowed_tools,
            tool_results=tool_results,
        )
    return _StubBackend.decide(
        system=system,
        messages=messages,
        allowed_tools=allowed_tools,
        tool_results=tool_results,
    )


# ---------------------------------------------------------------------------
# Live (OpenAI-compatible)
# ---------------------------------------------------------------------------


class _LiveBackend:
    @staticmethod
    def decide(
        *,
        system: str,
        messages: list[dict[str, Any]],
        allowed_tools: list[str],
        tool_results: list[dict[str, Any]],
    ) -> dict[str, Any]:
        import httpx

        key = os.getenv("MODEL_API_KEY") or os.getenv("XAI_API_KEY") or os.getenv("OPENAI_API_KEY")
        base = os.getenv("MODEL_BASE_URL") or (
            "https://api.x.ai/v1" if os.getenv("XAI_API_KEY") else "https://api.openai.com/v1"
        )
        model = os.getenv("MODEL_NAME") or "grok-2-latest"

        chat_msgs: list[dict[str, str]] = [{"role": "system", "content": system}]
        for m in messages:
            role = m.get("role") or "user"
            if role not in ("user", "assistant", "system"):
                role = "user"
            chat_msgs.append({"role": role, "content": str(m.get("content") or "")})
        if tool_results:
            chat_msgs.append(
                {
                    "role": "user",
                    "content": "工具结果（只用这些数字，勿臆造）:\n"
                    + json.dumps(tool_results, ensure_ascii=False)[:12000],
                }
            )
        chat_msgs.append(
            {
                "role": "user",
                "content": (
                    "请以 JSON 回复，二选一：\n"
                    '{"type":"tool_calls","calls":[{"name":"...","arguments":{...}}]}\n'
                    '或 {"type":"final","content":"markdown..."}\n'
                    f"允许工具: {allowed_tools}"
                ),
            }
        )

        try:
            with httpx.Client(timeout=60.0) as client:
                r = client.post(
                    f"{base.rstrip('/')}/chat/completions",
                    headers={"Authorization": f"Bearer {key}"},
                    json={
                        "model": model,
                        "messages": chat_msgs,
                        "temperature": 0.2,
                    },
                )
            if r.status_code == 200:
                text = r.json()["choices"][0]["message"]["content"]
                parsed = _parse_json_decision(text)
                if parsed:
                    return parsed
                # Model returned plain markdown — treat as final
                return {"type": "final", "content": text}
        except Exception:  # noqa: BLE001
            pass
        # Fall through to stub on live failure so chain still completes
        return _StubBackend.decide(
            system=system,
            messages=messages,
            allowed_tools=allowed_tools,
            tool_results=tool_results,
        )


def _parse_json_decision(text: str) -> dict[str, Any] | None:
    text = (text or "").strip()
    if not text:
        return None
    # strip markdown fence if present
    if "```" in text:
        parts = text.split("```")
        for p in parts:
            p = p.strip()
            if p.startswith("json"):
                p = p[4:].strip()
            if p.startswith("{"):
                text = p
                break
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            obj = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None
    if not isinstance(obj, dict):
        return None
    t = obj.get("type")
    if t == "tool_calls" and isinstance(obj.get("calls"), list):
        return {"type": "tool_calls", "calls": obj["calls"]}
    if t == "final" and isinstance(obj.get("content"), str):
        return {"type": "final", "content": obj["content"]}
    return None


# ---------------------------------------------------------------------------
# Stub — same decide contract; plans tools from intent; finals cite tool_results
# ---------------------------------------------------------------------------


def _last_user_text(messages: list[dict[str, Any]]) -> str:
    for m in reversed(messages):
        if m.get("role") == "user":
            return str(m.get("content") or "")
    return ""


def _intent(user_text: str) -> str:
    t = user_text.lower()
    if ("报告" in user_text or "洞察" in user_text) and not any(
        k in user_text for k in ("哪个省", "哪省", "哪个渠道", "哪个类目")
    ):
        return "report"
    if "省" in user_text or "province" in t or "区域" in user_text:
        return "province"
    if "渠道" in user_text or "channel" in t:
        return "channel"
    if "类目" in user_text or "category" in t:
        return "category"
    if "退货" in user_text or "return" in t:
        return "return"
    # default: if no prior tools, treat as report; else province dump
    return "report"


def _call_key(name: str, arguments: dict[str, Any] | None) -> str:
    args = arguments or {}
    return json.dumps({"name": name, "args": args}, sort_keys=True, ensure_ascii=False)


def _have_keys(tool_results: list[dict[str, Any]]) -> set[str]:
    return {_call_key(t.get("name", ""), t.get("arguments") or {}) for t in tool_results}


def _fmt_rows(rows: list[dict[str, Any]], n: int = 5) -> str:
    parts = []
    for r in rows[:n]:
        parts.append(f"{r.get('key')}={r.get('value')}")
    return "；".join(parts)


def _pick(tool_results: list[dict[str, Any]], name: str, **arg_match: Any) -> dict[str, Any] | None:
    for t in tool_results:
        if t.get("name") != name:
            continue
        args = t.get("arguments") or {}
        if all(args.get(k) == v for k, v in arg_match.items()):
            return t
    # fallback: first matching name
    for t in tool_results:
        if t.get("name") == name:
            return t
    return None


def _build_report(tool_results: list[dict[str, Any]]) -> str:
    desc_t = _pick(tool_results, "describe_dataset") or {}
    desc = desc_t.get("data") or {}
    summary = desc.get("summary") or {}
    by_cat = (_pick(tool_results, "query_stats", group_by="category", metric="sum_gmv") or {}).get("data") or {}
    by_ch = (_pick(tool_results, "query_stats", group_by="channel", metric="sum_gmv") or {}).get("data") or {}
    by_prov = (_pick(tool_results, "query_stats", group_by="province", metric="sum_gmv") or {}).get("data") or {}
    ret_ch = (
        _pick(tool_results, "query_stats", group_by="channel", metric="return_rate") or {}
    ).get("data") or {}

    def fp(t: dict[str, Any] | None) -> str:
        if not t:
            return "n/a"
        return (t.get("evidence") or {}).get("fingerprint") or "n/a"

    top_cat = (by_cat.get("rows") or [{}])[0]
    top_ch = (by_ch.get("rows") or [{}])[0]
    top_prov = (by_prov.get("rows") or [{}])[0]
    worst_ret = (ret_ch.get("rows") or [{}])[0]

    lines = [
        "# 电商销量洞察报告（近 90 天）",
        "",
        f"**数据概览**：共 {desc.get('row_count', summary.get('total_qty', '—'))} 行订单；"
        f"日期 {summary.get('min_date')} ~ {summary.get('max_date')}；"
        f"总 GMV ≈ {summary.get('total_gmv')}；整体退货率 {summary.get('return_pct')}%。",
        f"_evidence: {fp(_pick(tool_results, 'describe_dataset'))}_",
        "",
        "## 发现",
        "",
        f"1. **类目集中**：GMV 最高类目为 **{top_cat.get('key')}**（{top_cat.get('value')}）。"
        f" Top：{_fmt_rows(by_cat.get('rows') or [])}。",
        f"   - evidence: `{fp(_pick(tool_results, 'query_stats', group_by='category', metric='sum_gmv'))}`",
        "",
        f"2. **渠道结构**：GMV 最高渠道为 **{top_ch.get('key')}**（{top_ch.get('value')}）。"
        f" Top：{_fmt_rows(by_ch.get('rows') or [])}。",
        f"   - evidence: `{fp(_pick(tool_results, 'query_stats', group_by='channel', metric='sum_gmv'))}`",
        "",
        f"3. **区域贡献**：GMV 最高省份为 **{top_prov.get('key')}**（{top_prov.get('value')}）。"
        f" Top：{_fmt_rows(by_prov.get('rows') or [])}。",
        f"   - evidence: `{fp(_pick(tool_results, 'query_stats', group_by='province', metric='sum_gmv'))}`",
        "",
        f"4. **退货风险**：按渠道退货率最高为 **{worst_ret.get('key')}**（{worst_ret.get('value')}%）。"
        f" 明细：{_fmt_rows(ret_ch.get('rows') or [])}。",
        f"   - evidence: `{fp(_pick(tool_results, 'query_stats', group_by='channel', metric='return_rate'))}`",
        "",
        f"5. **运营建议（POC）**：优先稳住 {top_cat.get('key')} × {top_ch.get('key')} 组合；"
        f"对 {worst_ret.get('key')} 做退货归因抽检；区域上可加大 {top_prov.get('key')} 供给。",
        "",
        "---",
        "_本报告经 Orchestrator → Model Broker(stub) → Tool Gateway → DAS；数字均来自工具结果，非臆造。_",
    ]
    return "\n".join(lines)


def _build_followup(intent: str, tool_results: list[dict[str, Any]]) -> str:
    t = tool_results[-1] if tool_results else {}
    data = t.get("data") or {}
    rows = data.get("rows") or []
    fp = (t.get("evidence") or {}).get("fingerprint", "n/a")
    if not rows:
        return f"未能从 DAS 取到聚合（intent={intent}）。\n\n_evidence: `{fp}`_"
    top = rows[0]
    labels = {
        "province": ("省份 GMV 排名", "GMV"),
        "channel": ("渠道 GMV", "GMV"),
        "category": ("类目 GMV", "GMV"),
        "return": ("渠道退货率", "%"),
    }
    title, unit = labels.get(intent, ("汇总", ""))
    suffix = "%" if intent == "return" else ""
    return (
        f"**{title}**（最高：**{top.get('key')}**，{unit}={top.get('value')}{suffix}）\n\n"
        f"{_fmt_rows(rows, 8)}\n\n"
        f"_evidence: `{fp}`_"
    )


REPORT_PLAN: list[dict[str, Any]] = [
    {"name": "list_datasets", "arguments": {}},
    {"name": "describe_dataset", "arguments": {"name": "ecommerce_sales"}},
    {"name": "query_stats", "arguments": {"group_by": "category", "metric": "sum_gmv", "limit": 10}},
    {"name": "query_stats", "arguments": {"group_by": "channel", "metric": "sum_gmv", "limit": 10}},
    {"name": "query_stats", "arguments": {"group_by": "province", "metric": "sum_gmv", "limit": 10}},
    {"name": "query_stats", "arguments": {"group_by": "channel", "metric": "return_rate", "limit": 10}},
]

FOLLOWUP_PLAN: dict[str, list[dict[str, Any]]] = {
    "province": [
        {"name": "query_stats", "arguments": {"group_by": "province", "metric": "sum_gmv", "limit": 10}}
    ],
    "channel": [
        {"name": "query_stats", "arguments": {"group_by": "channel", "metric": "sum_gmv", "limit": 10}}
    ],
    "category": [
        {"name": "query_stats", "arguments": {"group_by": "category", "metric": "sum_gmv", "limit": 10}}
    ],
    "return": [
        {"name": "query_stats", "arguments": {"group_by": "channel", "metric": "return_rate", "limit": 10}}
    ],
}


class _StubBackend:
    @staticmethod
    def decide(
        *,
        system: str,
        messages: list[dict[str, Any]],
        allowed_tools: list[str],
        tool_results: list[dict[str, Any]],
    ) -> dict[str, Any]:
        del system  # stub uses fixed plans; system prompt is for live / docs
        user_text = _last_user_text(messages)
        intent = _intent(user_text)

        # If session already had an assistant reply and user asks a short follow-up,
        # prefer follow-up intents even when "报告" appears.
        assistant_count = sum(1 for m in messages if m.get("role") == "assistant")
        if assistant_count > 0 and intent == "report":
            # re-check follow-up keywords without requiring report exclusion
            t = user_text.lower()
            if "省" in user_text or "province" in t:
                intent = "province"
            elif "渠道" in user_text or "channel" in t:
                intent = "channel"
            elif "类目" in user_text or "category" in t:
                intent = "category"
            elif "退货" in user_text or "return" in t:
                intent = "return"

        if intent == "report":
            plan = [c for c in REPORT_PLAN if c["name"] in allowed_tools]
        else:
            plan = [c for c in FOLLOWUP_PLAN.get(intent, FOLLOWUP_PLAN["province"]) if c["name"] in allowed_tools]

        have = _have_keys(tool_results)
        missing = [c for c in plan if _call_key(c["name"], c.get("arguments")) not in have]
        if missing:
            return {"type": "tool_calls", "calls": missing}

        if intent == "report":
            return {"type": "final", "content": _build_report(tool_results)}
        return {"type": "final", "content": _build_followup(intent, tool_results)}
