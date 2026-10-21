"""insight_report skill: call DAS, emit Markdown with evidence anchors."""
from __future__ import annotations

from typing import Any

from .. import context, hitl, model_broker, obs, run
from ..tool_gateway import ToolGateway


def _call(gw: ToolGateway, run_id: str, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    out = gw.call(name, arguments or {})
    summary = context.summarize_tool(name, out.get("evidence"), out.get("data") or {})
    obs.emit(run_id, "tool_call", summary)
    return out


def _fmt_rows(rows: list[dict[str, Any]], n: int = 5) -> str:
    parts = []
    for r in rows[:n]:
        parts.append(f"{r.get('key')}={r.get('value')}")
    return "；".join(parts)


def build_stub_report(bundle: dict[str, Any]) -> str:
    """Deterministic Chinese report citing real aggregates."""
    desc = bundle.get("describe") or {}
    summary = desc.get("summary") or {}
    by_cat = bundle.get("by_category") or {}
    by_ch = bundle.get("by_channel") or {}
    by_prov = bundle.get("by_province") or {}
    ret_ch = bundle.get("return_by_channel") or {}
    fps = bundle.get("fingerprints") or {}

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
        f"_evidence: {fps.get('describe', 'n/a')}_",
        "",
        "## 发现",
        "",
        f"1. **类目集中**：GMV 最高类目为 **{top_cat.get('key')}**（{top_cat.get('value')}）。"
        f" Top：{_fmt_rows(by_cat.get('rows') or [])}。",
        f"   - evidence: `{fps.get('by_category', 'n/a')}`",
        "",
        f"2. **渠道结构**：GMV 最高渠道为 **{top_ch.get('key')}**（{top_ch.get('value')}）。"
        f" Top：{_fmt_rows(by_ch.get('rows') or [])}。",
        f"   - evidence: `{fps.get('by_channel', 'n/a')}`",
        "",
        f"3. **区域贡献**：GMV 最高省份为 **{top_prov.get('key')}**（{top_prov.get('value')}）。"
        f" Top：{_fmt_rows(by_prov.get('rows') or [])}。",
        f"   - evidence: `{fps.get('by_province', 'n/a')}`",
        "",
        f"4. **退货风险**：按渠道退货率最高为 **{worst_ret.get('key')}**（{worst_ret.get('value')}%）。"
        f" 明细：{_fmt_rows(ret_ch.get('rows') or [])}。",
        f"   - evidence: `{fps.get('return_by_channel', 'n/a')}`",
        "",
        f"5. **运营建议（POC）**：优先稳住 {top_cat.get('key')} × {top_ch.get('key')} 组合；"
        f"对 {worst_ret.get('key')} 做退货归因抽检；区域上可加大 {top_prov.get('key')} 供给。",
        "",
        "---",
        "_本报告由 insight_report skill 基于 DAS 白名单工具聚合生成；数字均来自查询，非臆造。_",
    ]
    return "\n".join(lines)


def answer_followup(gw: ToolGateway, run_id: str, text: str) -> str:
    """Handle simple follow-ups using DAS."""
    t = text.lower()
    fps: dict[str, str] = {}

    if "省" in text or "province" in t or "区域" in text or "gmv" in t:
        out = _call(gw, run_id, "query_stats", {"group_by": "province", "metric": "sum_gmv", "limit": 10})
        rows = (out["data"] or {}).get("rows") or []
        fp = (out.get("evidence") or {}).get("fingerprint", "")
        fps["province"] = fp
        if not rows:
            return "未能从 DAS 取到省份聚合。"
        top = rows[0]
        detail = _fmt_rows(rows, 8)
        return (
            f"**省份 GMV 排名**（最高：**{top['key']}**，GMV={top['value']}）\n\n"
            f"{detail}\n\n"
            f"_evidence: `{fp}`_"
        )

    if "渠道" in text or "channel" in t:
        out = _call(gw, run_id, "query_stats", {"group_by": "channel", "metric": "sum_gmv", "limit": 10})
        rows = (out["data"] or {}).get("rows") or []
        fp = (out.get("evidence") or {}).get("fingerprint", "")
        top = rows[0] if rows else {}
        return (
            f"**渠道 GMV**：最高 **{top.get('key')}**={top.get('value')}\n\n"
            f"{_fmt_rows(rows)}\n\n_evidence: `{fp}`_"
        )

    if "类目" in text or "category" in t:
        out = _call(gw, run_id, "query_stats", {"group_by": "category", "metric": "sum_gmv", "limit": 10})
        rows = (out["data"] or {}).get("rows") or []
        fp = (out.get("evidence") or {}).get("fingerprint", "")
        top = rows[0] if rows else {}
        return (
            f"**类目 GMV**：最高 **{top.get('key')}**={top.get('value')}\n\n"
            f"{_fmt_rows(rows)}\n\n_evidence: `{fp}`_"
        )

    if "退货" in text or "return" in t:
        out = _call(gw, run_id, "query_stats", {"group_by": "channel", "metric": "return_rate", "limit": 10})
        rows = (out["data"] or {}).get("rows") or []
        fp = (out.get("evidence") or {}).get("fingerprint", "")
        top = rows[0] if rows else {}
        return (
            f"**渠道退货率**：最高 **{top.get('key')}**={top.get('value')}%\n\n"
            f"{_fmt_rows(rows)}\n\n_evidence: `{fp}`_"
        )

    # default: small stats dump
    out = _call(gw, run_id, "query_stats", {"group_by": "province", "metric": "sum_gmv", "limit": 5})
    rows = (out["data"] or {}).get("rows") or []
    fp = (out.get("evidence") or {}).get("fingerprint", "")
    return (
        f"已按省份 GMV 汇总（可追问类目/渠道/退货）：\n\n{_fmt_rows(rows)}\n\n"
        f"_evidence: `{fp}`_"
    )


def run_insight(
    run_id: str,
    user_text: str,
    *,
    is_followup: bool = False,
    gw: ToolGateway | None = None,
) -> dict[str, Any]:
    gw = gw or ToolGateway()
    run.set_state(run_id, "acting")
    obs.emit(run_id, "skill_start", {"skill": "insight_report", "followup": is_followup})

    # Optional HITL outline pause
    if hitl.should_pause("outline_confirm"):
        token = hitl.make_token("outline_confirm")
        run.set_state(
            run_id,
            "awaiting_hitl",
            resume_token=token["resume_token"],
            hitl_kind="outline_confirm",
        )
        return {
            "status": "awaiting_hitl",
            "hitl": token,
            "message": "请确认报告大纲（近90天电商销量洞察：类目/渠道/省份/退货）后继续。",
        }

    try:
        if is_followup or _looks_like_followup(user_text):
            md = answer_followup(gw, run_id, user_text)
            run.set_state(run_id, "completed", result=md)
            obs.emit(run_id, "skill_done", {"mode": "followup"})
            return {"status": "completed", "markdown": md}

        # Full report path — always call DAS
        list_out = _call(gw, run_id, "list_datasets", {})
        desc_out = _call(gw, run_id, "describe_dataset", {"name": "ecommerce_sales"})
        by_cat = _call(gw, run_id, "query_stats", {"group_by": "category", "metric": "sum_gmv", "limit": 10})
        by_ch = _call(gw, run_id, "query_stats", {"group_by": "channel", "metric": "sum_gmv", "limit": 10})
        by_prov = _call(gw, run_id, "query_stats", {"group_by": "province", "metric": "sum_gmv", "limit": 10})
        ret_ch = _call(gw, run_id, "query_stats", {"group_by": "channel", "metric": "return_rate", "limit": 10})

        bundle = {
            "list": list_out["data"],
            "describe": desc_out["data"],
            "by_category": by_cat["data"],
            "by_channel": by_ch["data"],
            "by_province": by_prov["data"],
            "return_by_channel": ret_ch["data"],
            "fingerprints": {
                "list": (list_out.get("evidence") or {}).get("fingerprint"),
                "describe": (desc_out.get("evidence") or {}).get("fingerprint"),
                "by_category": (by_cat.get("evidence") or {}).get("fingerprint"),
                "by_channel": (by_ch.get("evidence") or {}).get("fingerprint"),
                "by_province": (by_prov.get("evidence") or {}).get("fingerprint"),
                "return_by_channel": (ret_ch.get("evidence") or {}).get("fingerprint"),
            },
        }

        evidence_list = [
            {"tool": k, "fp": v, "preview": context.summarize_tool(k, {"fingerprint": v}, bundle.get(k.replace("by_", "by_") , {}) if False else {})}
            for k, v in bundle["fingerprints"].items()
        ]
        # Prefer LLM if available; else stub with real numbers
        llm_md = model_broker.complete(
            "你是洞察报告助理。只用给定证据写中文结论页，每条发现带 evidence 指纹。",
            user_text or "请基于电商销量数据写一份简短洞察报告",
            [
                {"describe": bundle["describe"]},
                {"by_category": bundle["by_category"]},
                {"by_channel": bundle["by_channel"]},
                {"by_province": bundle["by_province"]},
                {"return_by_channel": bundle["return_by_channel"]},
                {"fingerprints": bundle["fingerprints"]},
            ],
        )
        md = llm_md.strip() if llm_md and ("发现" in llm_md or "evidence" in llm_md.lower()) else build_stub_report(bundle)

        run.set_state(run_id, "completed", result=md)
        obs.emit(run_id, "skill_done", {"mode": "report", "fps": bundle["fingerprints"]})
        return {"status": "completed", "markdown": md, "evidence": bundle["fingerprints"]}
    except Exception as e:  # noqa: BLE001
        msg = f"报告生成失败：{e}"
        run.set_state(run_id, "failed", result=msg)
        obs.emit(run_id, "skill_failed", {"error": str(e)})
        return {"status": "failed", "markdown": msg}


def _looks_like_followup(text: str) -> bool:
    keys = ["哪个省", "哪省", "省份", "渠道", "类目", "退货", "最高", "GMV最高", "再问"]
    if any(k in text for k in keys) and "报告" not in text:
        return True
    if "写一份" in text or "洞察报告" in text or "简短洞察" in text:
        return False
    return False


def resume_after_hitl(run_id: str, user_text: str) -> dict[str, Any]:
    """Continue after HITL approval — treat as full report."""
    return run_insight(run_id, user_text or "请继续生成报告", is_followup=False)
