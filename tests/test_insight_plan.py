"""Batch G：计划步骤只从 Skill 抄一次，大纲正文用同一步骤。"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from pytest_bdd import given, scenarios, then, when

from agent_os_kernel import model_broker, obs, orchestrator, run, session
from agent_os_kernel.skills.insight_report import SKILL
from agent_os_kernel.tool_gateway import ToolGateway

scenarios(str(Path(__file__).parent / "features" / "insight_plan.feature"))

REPORT = "请基于电商销量数据写一份简短洞察报告"
WINDOW = {"label": "近90天", "start": "2025-07-25", "end": "2025-10-22"}
_LABEL = re.compile(r"^[\u4e00-\u9fff，。、]+$")
_TOOL_NAMES = {
    "list_datasets",
    "describe_dataset",
    "describe_fields",
    "describe_tag_hierarchies",
    "query_stats",
    "search_records",
    "sample_records",
}
_DIRTY_LABELS = ["先看范围", "再看类目", "再看渠道", "再看省份", "再看退货"]


def _bindings(skill: str = "insight_report") -> dict:
    return {"skill": skill, "dataset": "ecommerce_sales_v2", "time_window": dict(WINDOW)}


def _plans(run_id: str) -> list[dict]:
    return [event for event in obs.list_events(run_id) if event["kind"] == "plan"]


def _install_gateway(monkeypatch) -> None:
    def call(self, name, arguments=None, *, bindings=None):
        del self, name, arguments, bindings
        return {
            "data": {
                "rows": [{"key": "电子产品", "value": 1}],
                "records": [],
                "datasets": [{"name": "ecommerce_sales_v2"}],
                "row_count": 1,
                "summary": {"total_gmv": 1, "min_date": "a", "max_date": "b", "return_pct": 0},
            },
            "evidence": {"fingerprint": "fpxxxx", "tool": "query_stats"},
        }

    monkeypatch.setattr(ToolGateway, "call", call)


@pytest.fixture
def box(monkeypatch, tmp_path):
    monkeypatch.setattr(obs, "DB_PATH", tmp_path / "kernel.db")
    monkeypatch.setattr(model_broker, "has_llm", lambda: False)
    for key in ("MODEL_API_KEY", "XAI_API_KEY", "OPENAI_API_KEY", "MODEL_BASE_URL", "MODEL_NAME"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("HITL_AUTO", "1")
    obs.init_db()
    return {"monkeypatch": monkeypatch, "result": None, "rid": None, "expect": None}


def _open(box, text: str = REPORT, skill: str = "insight_report") -> None:
    bindings = _bindings(skill)
    sid = session.create_session({"bindings": bindings})["id"]
    session.add_message(sid, "user", text)
    box["sid"] = sid
    box["rid"] = run.create_run(sid, context_bindings=bindings)["id"]


def _patch_skill(box, **overrides) -> None:
    real = orchestrator.registry.get

    def wrapped(name: str | None):
        skill = dict(real(name))
        skill.update(overrides)
        return skill

    box["monkeypatch"].setattr(orchestrator.registry, "get", wrapped)


@given("一份带计划步骤的洞察 Skill")
def given_insight_plan(box):
    steps = SKILL["plan_steps"]
    assert 1 <= len(steps) <= 5
    ids = []
    for step in steps:
        assert set(step) == {"id", "label"}
        assert step["id"] not in _TOOL_NAMES
        assert _LABEL.fullmatch(step["label"])
        assert not any(name in step["label"] for name in _TOOL_NAMES)
        ids.append(step["id"])
    assert len(ids) == len(set(ids))
    box["expect"] = [{"id": step["id"], "label": step["label"], "status": "pending"} for step in steps]


@given("一份没有计划步骤的 Skill")
def given_deferred(box):
    # The contract is "no plan_steps means no plan event". The return-risk skill now has steps,
    # so this fixture clears them on the insight manifest and finishes without a tool round.
    _patch_skill(box, plan_steps=[], hitl_kinds=[], hitl_prompts={})
    box["no_plan"] = True
    box["expect"] = []


@given("一份步骤声明不干净的 Skill")
def given_dirty(box):
    _patch_skill(
        box,
        hitl_prompts={},
        plan_steps=[
            "query_stats",
            {"id": 1, "label": "数字编号"},
            {"id": "blank", "label": "   "},
            {"id": "  ", "label": "空白编号"},
            {"id": "ok1", "label": "先看范围"},
            {"id": "ok2", "label": "再看类目", "tool": "query_stats"},
            {"id": "ok3", "label": "再看渠道"},
            {"id": "ok4", "label": "再看省份"},
            {"id": "ok5", "label": "再看退货"},
            {"id": "ok6", "label": "不该出现"},
        ],
    )
    box["expect"] = [
        {"id": f"ok{i}", "label": label, "status": "pending"} for i, label in enumerate(_DIRTY_LABELS, 1)
    ]
    box["intro"] = "请确认后继续。"


@given("自动确认大纲")
def given_auto(box):
    box["monkeypatch"].setenv("HITL_AUTO", "1")
    _install_gateway(box["monkeypatch"])


@given("需要人工确认大纲")
def given_manual(box):
    box["monkeypatch"].setenv("HITL_AUTO", "0")


@when("用户发出洞察请求并跑完")
def run_through(box):
    _open(box, skill=box.get("skill") or "insight_report")
    _install_gateway(box["monkeypatch"])
    box["result"] = orchestrator.handle_user_message(box["sid"], box["rid"], REPORT)


@when("用户发出该 Skill 的请求并跑完")
def run_deferred(box):
    box["monkeypatch"].setattr(
        model_broker,
        "decide",
        lambda **_kwargs: {"type": "final", "content": "没有计划步骤，直接结束。"},
    )
    _open(box, text="看一下范围", skill="insight_report")
    box["result"] = orchestrator.handle_user_message(box["sid"], box["rid"], "看一下范围")


@when("用户发出洞察请求并停在大纲")
def run_paused(box):
    _open(box)
    box["result"] = orchestrator.handle_user_message(box["sid"], box["rid"], REPORT)


@when("用户确认后跑完")
def resume_done(box):
    _install_gateway(box["monkeypatch"])
    box["result"] = orchestrator.resume_after_hitl(box["rid"], "请继续生成报告")


@then("事件里恰好有一条 plan 且步骤都是未开始")
@then("事件里仍然只有一条 plan")
def one_pending_plan(box):
    plans = _plans(box["rid"])
    assert len(plans) == 1
    payload = plans[0]["payload"]
    assert set(payload) == {"revision", "steps"}
    assert payload["revision"] == 1
    assert payload["steps"]
    for step in payload["steps"]:
        assert set(step) == {"id", "label", "status"}
        assert step["status"] == "pending"
    box["plan"] = payload


@then("事件里没有 plan")
def no_plan(box):
    assert _plans(box["rid"]) == []
    assert box["result"]["status"] == "completed"


@then("这些步骤与 Skill 声明一致")
def steps_match_skill(box):
    assert box["result"]["status"] == "completed"
    assert box["plan"]["steps"] == box["expect"]
    blob = json.dumps(box["plan"], ensure_ascii=False)
    assert "query_stats" not in blob
    assert "fingerprint" not in blob
    assert "dataset" not in blob


@then("大纲正文按顺序写出每一步")
def outline_lists_steps(box):
    assert box["result"]["status"] == "awaiting_hitl"
    md = box["result"]["markdown"]
    labels = [step["label"] for step in box["expect"]]
    cursor = -1
    for index, label in enumerate(labels, 1):
        found = md.find(f"{index}. {label}")
        assert found > cursor
        cursor = found
    assert "不该出现" not in md
    assert "query_stats" not in md
    plans = _plans(box["rid"])
    assert [step["label"] for step in plans[0]["payload"]["steps"]] == labels
    if box.get("intro"):
        assert md.startswith(box["intro"])


@then("计划里只留下五条干净步骤")
def five_clean(box):
    plans = _plans(box["rid"])
    assert len(plans) == 1
    assert plans[0]["payload"]["steps"] == box["expect"]
    blob = json.dumps(plans[0]["payload"], ensure_ascii=False)
    assert "query_stats" not in blob
    assert "不该出现" not in blob
    assert "数字编号" not in blob


@then("事件种类里没有 phase_ 前缀")
def no_phase_kinds(box):
    kinds = [event["kind"] for event in obs.list_events(box["rid"])]
    assert kinds
    assert not any(kind.startswith("phase_") for kind in kinds)
    assert "plan" in kinds or box.get("no_plan")


def test_plan_copy_ignores_empty_declarations():
    assert orchestrator._copied_plan({}) is None
    assert orchestrator._copied_plan({"plan_steps": []}) is None
    assert orchestrator._copied_plan({"plan_steps": "query_stats"}) is None
    assert orchestrator._copied_plan({"plan_steps": [{"id": "a", "label": " "}, "nope"]}) is None
    assert orchestrator._outline_markdown({"hitl_prompts": ["nope"]}, None) == "请确认后继续。"
    blank = orchestrator._outline_markdown({"hitl_prompts": {"outline_confirm": "  "}}, None)
    assert blank == "请确认后继续。"
