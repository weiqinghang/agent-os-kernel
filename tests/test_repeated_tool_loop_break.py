"""重复工具调用在耗尽步数前停住，并尽量用已有证据完成。"""
from __future__ import annotations

import copy
from pathlib import Path

import pytest
from pytest_bdd import given, scenarios, then, when

from agent_os_kernel import model_broker, obs, orchestrator, run, session
from agent_os_kernel.tool_gateway import ToolGateway

scenarios(str(Path(__file__).parent / "features" / "repeated_tool_loop_break.feature"))

REPORT = "请基于电商销量数据写一份简短洞察报告"
WINDOW = {"label": "近90天", "start": "2025-07-25", "end": "2025-10-22"}
SAMPLE = {
    "name": "sample_records",
    "arguments": {
        "limit": 5,
        "filters": [{"field": "category", "value": "电子产品"}],
    },
}


def _sample_call() -> dict:
    return copy.deepcopy(SAMPLE)


def _near_calls() -> list[dict]:
    sample = {
        "limit": 5,
        "q": "  a  ",
        "flag": True,
        "note": None,
        "ratio": 1.5,
        "filters": [{"field": "category", "value": " 电子 "}],
    }
    twin = {
        "q": "a",
        "limit": 5.0,
        "flag": True,
        "note": None,
        "ratio": 1.5,
        "filters": [{"value": "电子", "field": "category"}],
    }
    third = {
        "limit": 5,
        "q": "a",
        "flag": True,
        "note": None,
        "ratio": 1.5,
        "filters": [{"field": "category", "value": "电子"}],
    }
    return [
        {"name": "list_datasets", "arguments": {}},
        {"name": "sample_records", "arguments": sample},
        {"name": "sample_records", "arguments": twin},
        {"name": "sample_records", "arguments": third},
        {"name": "describe_dataset", "arguments": {"name": "ecommerce_sales_v2"}},
    ]


def _bindings() -> dict:
    return {"skill": "insight_report", "dataset": "ecommerce_sales_v2", "time_window": dict(WINDOW)}


def _open_run() -> tuple[str, str]:
    bindings = _bindings()
    sid = session.create_session({"bindings": bindings})["id"]
    session.add_message(sid, "user", REPORT)
    rid = run.create_run(sid, context_bindings=bindings)["id"]
    return sid, rid


def _events(run_id: str, kind: str) -> list[dict]:
    return [event for event in obs.list_events(run_id) if event["kind"] == kind]


def _after(box: dict):
    kind = box["after"]
    if kind == "final":
        return {"type": "final", "content": "模型收束结论"}
    if kind == "blank":
        return {"type": "final", "content": "  \n"}
    if kind == "boom":
        raise RuntimeError("decide exploded")
    if kind == "weird":
        return "not-a-decision"
    return {"type": "tool_calls", "calls": [_sample_call()]}


def _install(box: dict) -> None:
    state = {"n": 0}

    def decide(**kwargs):
        state["n"] += 1
        results = list(kwargs.get("tool_results") or [])
        box["seen"].append(results)
        box["systems"].append(str(kwargs.get("system") or ""))
        mode = box["mode"]
        if mode == "repeat":
            if any(item.get("loop_break") for item in results):
                return _after(box)
            return {"type": "tool_calls", "calls": [_sample_call()]}
        if mode == "fail":
            return {"type": "tool_calls", "calls": [{"name": "drop_table", "arguments": {"i": 1}}]}
        if mode == "switch":
            if state["n"] <= 2:
                return {"type": "tool_calls", "calls": [{"name": "drop_table", "arguments": {"i": 1}}]}
            if state["n"] == 3:
                return {"type": "tool_calls", "calls": [{"name": "list_datasets", "arguments": {}}]}
            return {"type": "final", "content": "换工具后的结论"}
        if mode == "near":
            if any(item.get("loop_break") for item in results):
                return {"type": "final", "content": "前缀结论"}
            return {"type": "tool_calls", "calls": _near_calls()}
        if mode == "budget":
            return {
                "type": "tool_calls",
                "calls": [{"name": "drop_table", "arguments": {"i": state["n"]}}],
            }
        if mode == "nondict":
            if any(item.get("loop_break") for item in results):
                return {"type": "tool_calls", "calls": ["nope"]}
            return {"type": "tool_calls", "calls": ["nope", "nope", "nope"]}
        raise AssertionError(mode)

    box["monkeypatch"].setattr(orchestrator.model_broker, "decide", decide)


def _run(box: dict) -> dict:
    box["seen"] = []
    box["systems"] = []
    box["gateway"] = []
    sid, rid = _open_run()
    box["run_id"] = rid
    box["result"] = orchestrator.handle_user_message(sid, rid, REPORT)
    return box["result"]


@pytest.fixture
def box(monkeypatch, tmp_path):
    monkeypatch.setattr(obs, "DB_PATH", tmp_path / "kernel.db")
    monkeypatch.setenv("HITL_AUTO", "1")
    monkeypatch.delenv("AGENT_MAX_STEPS", raising=False)
    for key in ("MODEL_API_KEY", "XAI_API_KEY", "OPENAI_API_KEY", "MODEL_BASE_URL", "MODEL_NAME", "USE_FAKE_DAS"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(model_broker, "has_llm", lambda: False)
    obs.init_db()
    holder: dict = {
        "monkeypatch": monkeypatch,
        "mode": None,
        "after": "tools",
        "seen": [],
        "systems": [],
        "gateway": [],
        "result": None,
        "run_id": None,
    }

    def call(self, name, arguments=None, *, bindings=None):
        del self, bindings
        holder["gateway"].append({"name": name, "arguments": arguments})
        return {
            "data": {
                "count": 1,
                "rows": [{"key": "电子产品", "value": 1}],
                "summary": {"total_gmv": 1, "min_date": "a", "max_date": "b", "return_pct": 1},
                "row_count": 4,
            },
            "evidence": {"fingerprint": "fp-sample", "tool": name},
        }

    monkeypatch.setattr(ToolGateway, "call", call)
    return holder


@given("一条会跨步重复 sample_records 的洞察 Run")
def given_repeat(box):
    box["mode"] = "repeat"
    box["after"] = "tools"


@given("熔断后模型仍继续要工具")
def given_after_tools(box):
    box["after"] = "tools"


@given("熔断后模型改回 final")
def given_after_final(box):
    box["after"] = "final"


@given("熔断后模型给出空白 final")
def given_after_blank(box):
    box["after"] = "blank"


@given("熔断后的 decide 抛出异常")
def given_after_boom(box):
    box["after"] = "boom"


@given("熔断后 decide 返回非对象")
def given_after_weird(box):
    box["after"] = "weird"


@given("一条会跨步重复失败调用的洞察 Run")
def given_fail(box):
    box["mode"] = "fail"


@given("一条先重复失败再换工具的洞察 Run")
def given_switch(box):
    box["mode"] = "switch"


@given("一条把近义调用放在同一批的洞察 Run")
def given_near(box):
    box["mode"] = "near"


@given("一条走 stub 计划的洞察 Run")
def given_stub(box):
    box["mode"] = "stub"


@given("步数预算被设为 3")
def given_budget_env(box):
    box["monkeypatch"].setenv("AGENT_MAX_STEPS", "3")


@given("一条每步参数都不同的未授权调用 Run")
def given_budget_mode(box):
    box["mode"] = "budget"


@given("一条连续给出非对象调用的洞察 Run")
def given_nondict(box):
    box["mode"] = "nondict"


@when("编排器跑完重复调用这一轮")
def when_run(box):
    if box["mode"] != "stub":
        _install(box)
    _run(box)


@then("已执行的工具次数停在重复上限之前")
def then_stopped_early(box):
    events = _events(box["run_id"], "tool_call")
    assert len(events) == orchestrator.REPEAT_LIMIT - 1
    assert len(events) < orchestrator.resolve_max_steps()
    assert len(events) < orchestrator.MAX_STEPS


@then("这一轮没有耗尽步数预算")
def then_not_max_steps(box):
    errors = [
        event["payload"].get("error") or ""
        for event in _events(box["run_id"], "state")
        if event["payload"].get("state") == "failed"
    ]
    assert all(not error.startswith("exceeded MAX_STEPS") for error in errors)
    assert "exceeded MAX_STEPS" not in box["result"]["markdown"]


@then("降级结论写明已停止重复调用")
def then_degraded(box):
    markdown = box["result"]["markdown"]
    assert box["result"]["status"] == "completed"
    assert box["result"]["loop_break"] is True
    assert box["result"]["repeated_tool"] == "sample_records"
    assert "已停止重复调用" in markdown
    assert "`sample_records`" in markdown
    assert "fp-sample" in markdown
    assert markdown.count("fp-sample") >= orchestrator.REPEAT_LIMIT - 1
    assert "MAX_STEPS" not in markdown
    assert all("_loop_break" not in key for key in box["result"]["evidence"])
    completed = [
        event
        for event in _events(box["run_id"], "state")
        if event["payload"].get("state") == "completed"
    ]
    assert completed[-1]["payload"]["n_tools"] == orchestrator.REPEAT_LIMIT - 1
    assert completed[-1]["payload"]["loop_break"] is True
    assert completed[-1]["payload"]["repeated_tool"] == "sample_records"


@then("观测事件带有 loop_break 和 repeated_tool")
def then_loop_event(box):
    breaks = [
        event["payload"]
        for event in _events(box["run_id"], "model_step")
        if event["payload"].get("type") == "loop_break"
    ]
    assert breaks
    assert breaks[0]["loop_break"] is True
    assert breaks[0]["repeated_tool"] == "sample_records"
    assert breaks[0]["repeat_limit"] == orchestrator.REPEAT_LIMIT
    assert breaks[0]["mode"] == "stub"
    assert breaks[0]["fingerprint"]
    followups = [
        event["payload"]
        for event in _events(box["run_id"], "model_step")
        if event["payload"].get("loop_break") is True and event["payload"].get("type") != "loop_break"
    ]
    assert followups
    assert followups[0]["repeated_tool"] == "sample_records"


@then("熔断那次 decide 看见注入说明且没有再执行工具")
def then_note_injected(box):
    assert any(item.get("loop_break") for item in box["seen"][-1])
    assert "# Loop break" in box["systems"][-1]
    assert all("# Loop break" not in system for system in box["systems"][:-1])
    note = next(item for item in box["seen"][-1] if item.get("loop_break"))
    assert note["name"] == "_loop_break"
    assert note["repeated_tool"] == "sample_records"
    assert "final" in note["error"]
    assert len(box["gateway"]) == orchestrator.REPEAT_LIMIT - 1
    assert all(call["name"] == "sample_records" for call in box["gateway"])


@then("结论文本是模型收束结论")
def then_model_final(box):
    assert box["result"]["status"] == "completed"
    assert box["result"]["markdown"] == "模型收束结论"
    assert box["result"]["loop_break"] is True
    assert box["result"]["evidence"]


@then("结论文本是前缀结论")
def then_prefix_final(box):
    assert box["result"]["status"] == "completed"
    assert box["result"]["markdown"] == "前缀结论"
    assert box["result"]["loop_break"] is True


@then("失败原因不是编排失败")
def then_not_orchestrator_error(box):
    assert box["result"]["status"] == "completed"
    assert "编排失败" not in box["result"]["markdown"]
    assert "decide exploded" not in box["result"]["markdown"]


@then("Run 以 loop_break 失败且不提 MAX_STEPS")
def then_loop_failed(box):
    assert box["result"]["status"] == "failed"
    assert box["result"]["markdown"].startswith("loop_break:")
    assert "MAX_STEPS" not in box["result"]["markdown"]
    assert box["result"]["loop_break"] is True
    failed = [
        event["payload"]
        for event in _events(box["run_id"], "state")
        if event["payload"].get("state") == "failed"
    ]
    assert failed[-1]["loop_break"] is True
    assert failed[-1]["repeated_tool"]
    assert "loop_break" in failed[-1]["error"]


@then("熔断前的失败结果已回灌给 decide")
def then_failures_refeed(box):
    assert box["seen"][0] == []
    assert box["seen"][1]
    assert all(item.get("ok") is False and item.get("error") for item in box["seen"][1])
    assert not any(item.get("loop_break") for item in box["seen"][1])
    assert len(box["seen"][2]) == orchestrator.REPEAT_LIMIT - 1
    assert all(item.get("ok") is False for item in box["seen"][2])
    assert any(item.get("loop_break") for item in box["seen"][-1])
    assert "# Loop break" in box["systems"][-1]


@then("重复失败被回灌且随后的不同工具已执行")
def then_retry_then_switch(box):
    assert orchestrator.REPEAT_LIMIT > 2
    events = _events(box["run_id"], "tool_call")
    assert [event["payload"].get("ok") for event in events] == [False, False, True]
    assert events[0]["payload"]["tool"] == "drop_table"
    assert events[2]["payload"]["tool"] == "list_datasets"
    assert "ms" in events[2]["payload"]
    assert box["seen"][1][0]["ok"] is False
    assert len(box["seen"][2]) == 2
    assert all(item["ok"] is False for item in box["seen"][2])
    assert [call["name"] for call in box["gateway"]] == ["list_datasets"]


@then("这条 Run 完成且没有 loop_break")
def then_completed_without_break(box):
    assert box["result"]["status"] == "completed"
    assert "loop_break" not in box["result"]
    assert all(not event["payload"].get("loop_break") for event in _events(box["run_id"], "model_step"))
    assert all(not event["payload"].get("loop_break") for event in _events(box["run_id"], "state"))


@then("近义前缀按模型顺序写回且熔断后的调用没有执行")
def then_prefix_order(box):
    # Gateway entry order follows completion; writeback order is the contract.
    pairs = {
        (call["arguments"].get("limit"), call["arguments"].get("q"))
        for call in box["gateway"]
        if call["name"] == "sample_records"
    }
    assert pairs == {(5, "  a  "), (5.0, "a")}
    assert any(call["name"] == "list_datasets" for call in box["gateway"])
    assert all(call["name"] != "describe_dataset" for call in box["gateway"])
    assert len(box["gateway"]) == 3
    records = [item for item in box["seen"][-1] if not item.get("loop_break")]
    assert [item["name"] for item in records] == [
        "list_datasets",
        "sample_records",
        "sample_records",
    ]
    assert records[1]["arguments"]["limit"] == 5
    assert records[1]["arguments"]["q"] == "  a  "
    assert records[2]["arguments"]["limit"] == 5.0
    assert records[2]["arguments"]["q"] == "a"
    events = _events(box["run_id"], "tool_call")
    assert [event["payload"]["tool"] for event in events] == [item["name"] for item in records]
    assert all(event["payload"].get("ok") is True and "ms" in event["payload"] for event in events)


@then("正文里有发现")
def then_findings(box):
    assert "发现" in box["result"]["markdown"]
    assert len(_events(box["run_id"], "tool_call")) >= 4


@then("Run 因 MAX_STEPS 失败且工具次数等于预算")
def then_budget(box):
    assert box["result"]["status"] == "failed"
    assert box["result"]["markdown"] == "exceeded MAX_STEPS=3"
    assert len(_events(box["run_id"], "tool_call")) == 3
    assert orchestrator.resolve_max_steps() == 3
    assert orchestrator.MAX_STEPS != 3


@then("这条 Run 没有 loop_break")
def then_no_loop_event(box):
    assert "loop_break" not in box["result"]
    assert all(not event["payload"].get("loop_break") for event in _events(box["run_id"], "model_step"))


def test_step_budget_and_call_fingerprint(monkeypatch):
    assert orchestrator.MAX_STEPS == 12
    assert orchestrator.REPEAT_LIMIT == 3
    assert orchestrator.MAX_STEPS_CAP == 32
    monkeypatch.delenv("AGENT_MAX_STEPS", raising=False)
    assert orchestrator.resolve_max_steps() == 12
    for raw in ("", "   ", "nope", "0", "-3", "33", "100", "4.5"):
        monkeypatch.setenv("AGENT_MAX_STEPS", raw)
        assert orchestrator.resolve_max_steps() == 12
    monkeypatch.setenv("AGENT_MAX_STEPS", "32")
    assert orchestrator.resolve_max_steps() == 32
    monkeypatch.setenv("AGENT_MAX_STEPS", " 4 ")
    assert orchestrator.resolve_max_steps() == 4

    rich = {
        "limit": 5,
        "q": "  a  ",
        "flag": True,
        "note": None,
        "ratio": 1.5,
        "filters": [{"field": "category", "value": " 电子 "}],
    }
    twin = {
        "filters": [{"value": "电子", "field": "category"}],
        "note": None,
        "flag": True,
        "limit": 5.0,
        "q": "a",
        "ratio": 1.5,
    }
    assert orchestrator._call_fingerprint({"name": "sample_records", "arguments": rich}) == (
        orchestrator._call_fingerprint({"name": "sample_records", "arguments": twin})
    )
    assert orchestrator._call_fingerprint(
        {"name": "sample_records", "arguments": {"limit": "5"}}
    ) != orchestrator._call_fingerprint({"name": "sample_records", "arguments": {"limit": 5}})
    assert orchestrator._call_fingerprint(
        {"name": "list_datasets", "arguments": [" x "]}
    ) == orchestrator._call_fingerprint({"name": "list_datasets", "arguments": ["x"]})
    assert orchestrator._call_fingerprint(("sample_records",)) == orchestrator._call_fingerprint(
        ("sample_records",)
    )
    assert orchestrator._call_fingerprint(None) == orchestrator._call_fingerprint(None)
    assert orchestrator._call_fingerprint(5) != orchestrator._call_fingerprint("5")
    assert orchestrator._call_fingerprint({"name": "", "arguments": None}) == orchestrator._call_fingerprint(
        {"name": "", "arguments": None}
    )
