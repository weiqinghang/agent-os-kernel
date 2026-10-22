"""G.1 规则表：只数成功的工具调用，并用步骤文案通配，不看工具名。"""
from __future__ import annotations

import json
from pathlib import Path

from agent_os_kernel.skills.insight_report import PLAN_PROGRESS, SKILL, apply_plan_progress

_TOOLS = (
    "list_datasets",
    "describe_dataset",
    "describe_fields",
    "describe_tag_hierarchies",
    "query_stats",
    "search_records",
    "sample_records",
)
_STEPS = [
    {"id": "scope", "label": "确认这次要看的范围"},
    {"id": "category", "label": "看清类目卖得怎么样"},
    {"id": "channel", "label": "看清渠道结构"},
    {"id": "province", "label": "看清各省贡献"},
    {"id": "returns", "label": "看清退货风险"},
    {"id": "write", "label": "整理结论"},
]


def _ok(tool: str) -> dict:
    return {"kind": "tool_call", "payload": {"ok": True, "tool": tool, "name": tool}}


def test_progress_rules_ignore_tool_names_and_gates():
    one = apply_plan_progress(_STEPS, [_ok("query_stats")])
    other = apply_plan_progress(_STEPS, [_ok("sample_records")])
    assert [step["status"] for step in one] == [step["status"] for step in other]
    assert [step["status"] for step in one] == ["done", "active", "pending", "pending", "pending", "pending"]

    failed = apply_plan_progress(
        _STEPS,
        [
            {"kind": "tool_call", "payload": {"ok": False, "tool": "list_datasets"}},
            {"kind": "tool_call", "payload": "query_stats"},
            None,
        ],
    )
    assert failed[0]["status"] == "active"
    assert failed[1]["status"] == "pending"

    held = [
        {"kind": "state", "payload": {"state": "acting"}},
        {"kind": "hitl", "payload": {"kind": "outline_confirm", "auto": False}},
        {"kind": "state", "payload": {"state": "awaiting_hitl", "hitl_kind": "outline_confirm"}},
        _ok("search_records"),
        _ok("describe_dataset"),
    ]
    assert {step["status"] for step in apply_plan_progress(_STEPS, held)} == {"pending"}

    resumed = held + [{"kind": "state", "payload": {"state": "acting", "resume": True}}, _ok("query_stats")]
    assert apply_plan_progress(_STEPS, resumed)[0]["status"] == "done"

    auto = [{"kind": "hitl", "payload": {"kind": "outline_confirm", "auto": True}}, _ok("query_stats")]
    assert apply_plan_progress(_STEPS, auto)[0]["status"] == "done"
    approved = [{"kind": "hitl", "payload": {"kind": "outline_confirm", "approved": True}}, _ok("query_stats")]
    assert apply_plan_progress(_STEPS, approved)[0]["status"] == "done"

    kind_only = [
        {"kind": "state", "payload": {"state": "awaiting_hitl", "hitl_kind": "outline_confirm"}},
        _ok("query_stats"),
    ]
    assert {step["status"] for step in apply_plan_progress(_STEPS, kind_only)} == {"pending"}
    assert apply_plan_progress(_STEPS, [{"kind": "state", "payload": {"state": "awaiting_hitl"}}])[0]["status"] == "active"


def test_progress_finalizing_cancel_and_dirty_rules():
    quiet = apply_plan_progress(
        _STEPS,
        [{"kind": "model_step", "payload": {"type": "tool_calls", "n_calls": 0, "loop_break": True}}],
    )
    assert quiet[-1]["status"] == "pending"

    writing = {"kind": "model_step", "payload": {"type": "final", "n_calls": 0}}
    active = apply_plan_progress(_STEPS, [writing])
    assert active[-1]["status"] == "active"
    assert active[-1]["label"] == "整理结论"

    for payload in (
        {"type": "finalizing"},
        {"type": "loop_break"},
        {"type": "note", "finalizing": True},
        {"type": "note", "loop_break": True},
        {"type": "wrap", "n_calls": 0},
        {"finalizing": True},
    ):
        got = apply_plan_progress(_STEPS, [{"kind": "model_step", "payload": payload}])
        assert got[-1]["status"] == "active"
    assert apply_plan_progress(_STEPS, [{"kind": "assistant", "payload": {}}])[-1]["status"] == "active"
    assert apply_plan_progress(_STEPS, [{"kind": "state", "payload": {"state": "completed"}}])[-1]["status"] == "active"
    assert apply_plan_progress(_STEPS, [{"kind": "state", "payload": {"state": "finalizing"}}])[-1]["status"] == "active"
    failed_after = [
        {"kind": "model_step", "payload": {"type": "final"}},
        {"kind": "state", "payload": {"state": "failed"}},
    ]
    assert apply_plan_progress(_STEPS, failed_after)[-1]["status"] == "pending"
    assert apply_plan_progress(_STEPS, [{"kind": "phase_writing", "payload": {"name": "query_stats"}}])[-1]["status"] == "pending"
    assert apply_plan_progress(_STEPS, [{"kind": "state", "payload": {"state": 1}}])[0]["status"] == "active"

    done_then_write = apply_plan_progress(
        [{"id": "write", "label": "整理结论"}],
        [_ok("query_stats"), writing],
        [
            {"when": "tool_calls", "count": 1, "label": "*结论*", "tool": "query_stats"},
            {"when": "finalizing", "label": "*结论*"},
        ],
    )
    assert done_then_write[0]["status"] == "done"

    cancelled = apply_plan_progress(
        _STEPS,
        [_ok("describe_fields"), {"kind": "state", "payload": {"state": "cancelled"}}],
    )
    assert cancelled[0]["status"] == "done"
    assert {step["status"] for step in cancelled[1:]} == {"skipped"}

    held_cancel = apply_plan_progress(
        _STEPS,
        [
            {"kind": "hitl", "payload": {"kind": "outline_confirm"}},
            {"kind": "state", "payload": {"state": "cancelled", "hitl_kind": 3}},
        ],
    )
    assert {step["status"] for step in held_cancel} == {"skipped"}

    assert apply_plan_progress(None, None) == []
    assert apply_plan_progress(["nope", {"id": 1, "label": "  "}, {"label": "有字"}], "nope", "nope") == [
        {"id": "", "label": "有字", "status": "pending"}
    ]
    starred = apply_plan_progress(
        [{"label": "确认这次要看的范围"}],
        [],
        [
            {"when": "tool_calls", "count": True, "label": "*范围*"},
            {"when": "tool_calls", "count": 0, "label": "*范围*"},
            {"when": "tool_calls", "count": 1, "label": "*"},
            {"when": "tool_calls", "count": 1, "label": ""},
            {"when": "nope"},
            "nope",
            {"when": "finalizing", "label": ""},
            {"when": "finalizing", "label": 3},
        ],
    )
    assert starred[0]["status"] == "pending"
    assert apply_plan_progress(_STEPS[:1], [_ok("query_stats")], None)[0]["status"] == "done"
    no_gate = apply_plan_progress(
        _STEPS[:1],
        [{"kind": "hitl", "payload": {"kind": "outline_confirm"}}, _ok("query_stats")],
        [{"when": "tool_calls", "count": 1, "label": "*范围*"}],
    )
    assert no_gate[0]["status"] == "done"


def test_js_rule_table_matches_skill():
    assert SKILL["plan_progress"] is PLAN_PROGRESS
    js = Path("ui/chat/progress.js").read_text(encoding="utf-8")
    start = js.index("export const PLAN_PROGRESS")
    block = js[start : js.index("];", start)]
    for rule in PLAN_PROGRESS:
        label = rule.get("label")
        if isinstance(label, str):
            assert label in block
        if "count" in rule:
            assert f"count: {rule['count']}" in block
        assert rule["when"] in block
    for name in _TOOLS:
        assert name not in block
    assert "query_stats" not in json.dumps(PLAN_PROGRESS, ensure_ascii=False)
