"""Batch B：同一次 decide 的工具调用有界并行，并按模型顺序写回。"""
from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest
from pytest_bdd import given, scenarios, then, when

from agent_os_kernel import model_broker, obs, orchestrator, run, session
from agent_os_kernel.tool_gateway import ToolGateway, ToolGatewayError

scenarios(str(Path(__file__).parent / "features" / "bounded_parallel_tools.feature"))

REPORT = "请基于电商销量数据写一份简短洞察报告"
WINDOW = {"label": "近90天", "start": "2025-07-25", "end": "2025-10-22"}
SLEEP_S = 0.4
WAVE = 3

READS = [
    {"name": "list_datasets", "arguments": {}},
    {"name": "describe_dataset", "arguments": {"name": "ecommerce_sales_v2"}},
    {"name": "query_stats", "arguments": {"group_by": "category", "metric": "sum_gmv"}},
    {"name": "query_stats", "arguments": {"group_by": "channel", "metric": "sum_gmv"}},
]
MIXED = [
    {"name": "list_datasets", "arguments": {}},
    {"name": "describe_dataset", "arguments": {"name": "ecommerce_sales_v2"}},
    {"name": "query_stats", "arguments": {"group_by": "category", "metric": "sum_gmv"}},
]


def _bindings() -> dict:
    return {"skill": "insight_report", "dataset": "ecommerce_sales_v2", "time_window": dict(WINDOW)}


def _open_run() -> tuple[str, str]:
    sid = session.create_session({"bindings": _bindings()})["id"]
    session.add_message(sid, "user", REPORT)
    rid = run.create_run(sid, context_bindings=_bindings())["id"]
    return sid, rid


def _run(box) -> dict:
    sid, rid = _open_run()
    box["run_id"] = rid
    started = time.perf_counter()
    box["result"] = orchestrator.handle_user_message(sid, rid, REPORT)
    box["elapsed"] = time.perf_counter() - started
    return box["result"]


def _install_decide(box, calls: list[dict], final: str) -> None:
    state = {"n": 0}

    def decide(**kwargs):
        state["n"] += 1
        box["seen"].append(list(kwargs.get("tool_results") or []))
        if state["n"] == 1:
            return {"type": "tool_calls", "calls": calls}
        return {"type": "final", "content": final}

    box["steps"] = state
    box["monkeypatch"].setattr(orchestrator.model_broker, "decide", decide)


def _canned(name: str) -> dict:
    return {"data": {"rows": [{"key": name, "value": 1}]}, "evidence": {"fingerprint": "fp" + name[:4], "tool": name}}


def _tool_events(run_id: str) -> list[dict]:
    return [event for event in obs.list_events(run_id) if event["kind"] == "tool_call"]


def _model_events(run_id: str) -> list[dict]:
    return [event for event in obs.list_events(run_id) if event["kind"] == "model_step"]


@pytest.fixture
def box(monkeypatch, tmp_path):
    monkeypatch.setattr(obs, "DB_PATH", tmp_path / "kernel.db")
    monkeypatch.setenv("HITL_AUTO", "1")
    for key in ("MODEL_API_KEY", "XAI_API_KEY", "OPENAI_API_KEY", "MODEL_BASE_URL", "MODEL_NAME", "USE_FAKE_DAS"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(model_broker, "has_llm", lambda: False)
    obs.init_db()
    return {
        "monkeypatch": monkeypatch,
        "seen": [],
        "result": None,
        "run_id": None,
        "elapsed": None,
        "gate": None,
        "finished": [],
        "steps": {"n": 0},
    }


@given("一条会睡眠的四读网关")
def given_sleeping_reads(box):
    gate = {"inflight": 0, "peak": 0, "started": 0, "lock": threading.Lock()}
    release = threading.Event()

    def call(self, name, arguments=None, *, bindings=None):
        del self, arguments, bindings
        with gate["lock"]:
            gate["inflight"] += 1
            gate["peak"] = max(gate["peak"], gate["inflight"])
            gate["started"] += 1
            if gate["started"] >= WAVE:
                release.set()
        try:
            if not release.wait(3):
                raise RuntimeError("parallel wave did not reach 3")
            time.sleep(SLEEP_S)
            return _canned(name)
        finally:
            with gate["lock"]:
                gate["inflight"] -= 1

    box["gate"] = gate
    box["monkeypatch"].setattr(ToolGateway, "call", call)
    _install_decide(box, READS, "四路并行完成")


@given("一条会故意乱序完成的读网关")
def given_reordered_reads(box):
    # Model order is list → describe → query. Completion is forced to the reverse
    # shape describe, then query, then list, so a completion-order writeback fails.
    failed = threading.Event()
    third_done = threading.Event()
    finished: list[str] = []
    lock = threading.Lock()

    def call(self, name, arguments=None, *, bindings=None):
        del self, arguments, bindings
        if name == "describe_dataset":
            with lock:
                finished.append(name)
            failed.set()
            raise ToolGatewayError("middle failed")
        if name == "query_stats":
            assert failed.wait(2)
            with lock:
                finished.append(name)
            third_done.set()
            return _canned(name)
        if name == "list_datasets":
            assert third_done.wait(2)
            with lock:
                finished.append(name)
            return _canned(name)
        raise ToolGatewayError(f"unexpected tool {name}")

    box["finished"] = finished
    box["monkeypatch"].setattr(ToolGateway, "call", call)
    _install_decide(box, MIXED, "混排写回完成")


@given("第二个读调用会抛出非网关异常")
def given_fatal_second_call(box):
    def call(self, name, arguments=None, *, bindings=None):
        del self, arguments, bindings
        if name == "describe_dataset":
            raise RuntimeError("boom-not-gateway")
        time.sleep(0.05)
        return _canned(name)

    box["monkeypatch"].setattr(ToolGateway, "call", call)
    _install_decide(
        box,
        [
            {"name": "list_datasets", "arguments": {}},
            {"name": "describe_dataset", "arguments": {"name": "ecommerce_sales_v2"}},
        ],
        "不应走到结论",
    )


@when("编排器执行这四个读调用")
@when("编排器执行这组混排调用")
@when("编排器执行这对调用")
def when_run_batch(box):
    _run(box)


@then("墙钟低于四次串行睡眠且峰值并发为 3")
def then_faster_than_serial(box):
    assert box["result"]["status"] == "completed", box["result"]
    serial = SLEEP_S * len(READS)
    assert box["elapsed"] < serial * 0.75, box["elapsed"]
    assert box["elapsed"] > SLEEP_S
    assert orchestrator.MAX_PARALLEL_TOOLS == WAVE
    assert box["gate"]["peak"] == WAVE
    assert box["result"]["markdown"] == "四路并行完成"


@then("四条 tool_call 都带 ms 且 model_step 的 n_calls 为 4")
def then_obs_ms_and_n_calls(box):
    tools = _tool_events(box["run_id"])
    assert [event["payload"].get("tool") for event in tools] == [call["name"] for call in READS]
    assert all(event["payload"].get("ok") is True for event in tools)
    floor = int(SLEEP_S * 1000 * 0.5)
    assert all(isinstance(event["payload"].get("ms"), int) and event["payload"]["ms"] >= floor for event in tools)
    steps = [event for event in _model_events(box["run_id"]) if event["payload"].get("type") == "tool_calls"]
    assert steps and steps[0]["payload"].get("n_calls") == len(READS)
    fed = box["seen"][1]
    assert [(item["name"], item.get("arguments"), item["ok"]) for item in fed] == [
        (call["name"], call["arguments"], True) for call in READS
    ]


@then("混排这一轮 Run 完成")
def then_mixed_completed(box):
    assert box["result"]["status"] == "completed", box["result"]
    assert box["result"]["markdown"] == "混排写回完成"
    assert "failed" not in [
        event["payload"].get("state") for event in obs.list_events(box["run_id"]) if event["kind"] == "state"
    ]


@then("tool_results 与 tool_call 事件都保持模型顺序且中间失败")
def then_order_preserved(box):
    assert box["finished"] == ["describe_dataset", "query_stats", "list_datasets"]
    fed = box["seen"][1]
    assert [item["name"] for item in fed] == [call["name"] for call in MIXED]
    assert [item["ok"] for item in fed] == [True, False, True]
    assert fed[1]["error"] == "middle failed"
    assert fed[1]["arguments"] == MIXED[1]["arguments"]
    assert fed[1]["data"] is None
    tools = _tool_events(box["run_id"])
    assert [event["payload"].get("tool") for event in tools] == [call["name"] for call in MIXED]
    assert [event["payload"].get("ok") for event in tools] == [True, False, True]
    assert tools[1]["payload"].get("error") == "middle failed"
    assert all(isinstance(event["payload"].get("ms"), int) and event["payload"]["ms"] >= 0 for event in tools)
    assert tools[1]["payload"].get("bindings_fingerprint")
    steps = [event for event in _model_events(box["run_id"]) if event["payload"].get("type") == "tool_calls"]
    assert steps[0]["payload"].get("n_calls") == len(MIXED)


@then("这一轮 Run 失败且原因包含 boom-not-gateway")
def then_fatal_run(box):
    assert box["result"]["status"] == "failed"
    assert "boom-not-gateway" in box["result"]["markdown"]
    assert box["steps"]["n"] == 1


@then("抛出的异常没有变成 ok 为 false 的工具结果")
def then_fatal_not_refeed(box):
    tools = _tool_events(box["run_id"])
    assert [event["payload"].get("tool") for event in tools] == ["list_datasets"]
    assert tools[0]["payload"].get("ok") is True
    assert isinstance(tools[0]["payload"].get("ms"), int)
    assert not any(event["payload"].get("ok") is False for event in tools)
    assert box["seen"] == [[]]
