"""Batch H：洞察 stub 金额两位小数，尾注和范围行改人话。"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from agent_os_kernel import agents
from agent_os_kernel.run import STATES
from agent_os_kernel.skills.insight_report import SKILL, _build_drill, _build_followup, _build_report

scenarios(str(Path(__file__).parent / "features" / "insight_dogfood_polish.feature"))

ROOT = Path(__file__).resolve().parents[1]
ALLOWED_SRC = {
    "src/agent_os_kernel/skills/insight_report.py",
    "src/agent_os_kernel/skills/return_risk_brief.py",
    "src/agent_os_kernel/skills/money_fmt.py",
    "src/agent_os_kernel/skills/stat_rows.py",
}
ALLOWED_UI = {"ui/chat/app.js"}
_LONG = "934.0200000000"
_CAT = "1200.5000000001"
_ROW = "15.1200000000"
_RATE = "12.3400000001"


@pytest.fixture
def box():
    return {}


def _tool(name: str, arguments: dict, data: dict, fingerprint: str = "fp") -> dict:
    return {
        "name": name,
        "arguments": arguments,
        "data": data,
        "evidence": {"fingerprint": fingerprint},
    }


def _record(**extra) -> dict:
    row = {
        "order_id": "ORD1001",
        "category": "电子产品",
        "channel": "抖音电商",
        "province": "江苏",
        "order_date": "2025-10-01",
        "gmv": _LONG,
    }
    row.update(extra)
    return row


def _bindings() -> dict:
    return {
        "dataset": "ecommerce_sales_v2",
        "time_window": {"label": "近90天", "start": "2025-07-25", "end": "2025-10-22"},
    }


def _report_results() -> list[dict]:
    summary = {
        "min_date": "2025-07-25",
        "max_date": "2025-10-22",
        "total_gmv": _LONG,
        "filtered_rows": 200,
        "return_pct": 6.5,
    }
    return [
        _tool("describe_dataset", {"name": "ecommerce_sales_v2"}, {"row_count": 200, "summary": summary}, "fp-desc"),
        _tool(
            "query_stats",
            {"group_by": "category", "metric": "sum_gmv"},
            {"rows": [{"key": "电子产品", "value": _CAT}, {"key": "服装", "value": _ROW}]},
            "fp-cat",
        ),
        _tool(
            "query_stats",
            {"group_by": "channel", "metric": "sum_gmv"},
            {"rows": [{"key": "抖音电商", "value": 110}]},
            "fp-ch",
        ),
        _tool(
            "query_stats",
            {"group_by": "province", "metric": "sum_gmv"},
            {"rows": [{"key": "江苏", "value": 50.5}]},
            "fp-prov",
        ),
        _tool(
            "query_stats",
            {"group_by": "channel", "metric": "return_rate"},
            {"rows": [{"key": "拼多多", "value": _RATE}]},
            "fp-ret",
        ),
    ]


def _git(*args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return proc.stdout


def _changed_paths() -> list[str]:
    names = [line.strip() for line in _git("diff", "--name-only", "origin/main").splitlines() if line.strip()]
    untracked = [
        line.strip()
        for line in _git("ls-files", "--others", "--exclude-standard").splitlines()
        if line.strip()
    ]
    for path in untracked:
        if path not in names:
            names.append(path)
    return names


def _src_addition_lines() -> list[str]:
    lines: list[str] = []
    for line in _git("diff", "-U0", "origin/main", "--", "src").splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            lines.append(line[1:])
    for rel in _git("ls-files", "--others", "--exclude-standard", "--", "src").splitlines():
        rel = rel.strip()
        if rel:
            lines.extend((ROOT / rel).read_text(encoding="utf-8").splitlines())
    return lines


@given("insight_report 下钻或 stub 依据含 gmv=934.0200000000 源值")
def given_long_gmv(box):
    sample = _tool(
        "sample_records",
        {"limit": 5},
        {"count": 2, "records": [_record(), _record(order_id="ORD1002", gmv=110)]},
        "fp-sample",
    )
    floated = _tool(
        "sample_records",
        {"limit": 5, "filters": {"channel": "抖音电商"}},
        {"count": 1, "records": [_record(order_id="ORD1003", gmv=934.0200000000)]},
        "fp-float",
    )
    box["drills"] = [
        _build_drill([sample], "第2条依据"),
        _build_drill([floated], "看一下依据"),
    ]


@when("组装默认可见样本行")
def when_sample_lines(box):
    box["text"] = "\n".join(box["drills"])


@then("行内 GMV 展示为至多两位小数（如 934.02）")
def then_sample_gmv(box):
    text = box["text"]
    assert "GMV=934.02" in text
    assert _LONG not in text
    assert "934.0200000000" not in text
    assert "GMV=110" in text
    assert "GMV=110.00" not in text
    assert "GMV=110.0" not in text


@given("stub 洞察报告含总 GMV 与类目/渠道 Top 金额")
def given_report_amounts(box):
    box["results"] = _report_results()
    box["bindings"] = _bindings()


@when("组装报告正文")
def when_report(box):
    box["text"] = _build_report(box["results"], box["bindings"])


@then("金额类数字无长尾浮点串")
def then_amounts(box):
    text = box["text"]
    assert "总 GMV ≈ 934.02" in text
    assert "电子产品**（1200.5）" in text
    assert "服装=15.12" in text
    assert "抖音电商**（110）" in text
    assert "江苏**（50.5）" in text
    assert _LONG not in text
    assert _CAT not in text
    assert _ROW not in text
    assert "110.00" not in text
    assert f"（{_RATE}%）" in text
    assert "整体退货率 6.5%" in text


@when("组装 stub 洞察结论")
def when_stub_conclusion(box):
    box["text"] = _build_report(_report_results(), _bindings())


@then(parsers.parse('正文不含 "{needle}"'))
def then_no_literal(box, needle):
    text = box["text"]
    assert needle not in text
    assert "本批销量数据 ecommerce_sales_v2" in text
    assert "可追问第 2 条依据下钻，或改时间窗后再出一版。" in text
    assert "_evidence:" in text


@when("检视本批 diff")
def when_diff(box):
    box["paths"] = _changed_paths()
    box["added"] = _src_addition_lines()


@then("无 phase_*、无 run_events usage/ms 产品字段、无新 MCP")
def then_kernel_unchanged(box):
    src = [path for path in box["paths"] if path.startswith("src/")]
    ui = [path for path in box["paths"] if path.startswith("ui/")]
    assert set(src) <= ALLOWED_SRC
    assert set(ui) <= ALLOWED_UI
    assert not any("mcp" in Path(path).name.lower() for path in box["paths"])
    blob = "\n".join(box["added"])
    assert "phase_" not in blob
    assert "run_events" not in blob
    assert "elapsed_ms" not in blob
    assert "usage" not in blob
    assert STATES == {"created", "acting", "awaiting_hitl", "completed", "failed", "cancelled"}
    assert [row["id"] for row in agents.list_agents()] == ["insight.report", "return_risk.brief"]
    assert all(row["mcps"] == ["das"] for row in agents.list_agents())


def test_followup_gmv_trims_and_return_rate_stays():
    gmv = _build_followup(
        "province",
        [_tool("query_stats", {"group_by": "province", "metric": "sum_gmv"}, {"rows": [{"key": "江苏", "value": _LONG}]})],
    )
    assert "GMV=934.02" in gmv
    assert _LONG not in gmv
    rate = _build_followup(
        "return",
        [
            _tool(
                "query_stats",
                {"group_by": "channel", "metric": "return_rate"},
                {"rows": [{"key": "拼多多", "value": _RATE}]},
            )
        ],
    )
    assert f"{_RATE}%" in rate
    assert "12.34%" not in rate


def test_system_prompt_has_no_gateway_or_dataset_prefix():
    prompt = SKILL["system_prompt"]
    assert "Tool Gateway" not in prompt
    assert "dataset=" not in prompt
