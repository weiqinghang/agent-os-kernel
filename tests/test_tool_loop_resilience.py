"""Batch A：工具失败回灌、DAS 一次重试、live 一次重试后降级。"""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from agent_os_kernel import model_broker, obs, orchestrator, run, session
from agent_os_kernel.model_broker import LIVE_RETRY_BACKOFF_S
from agent_os_kernel.tool_gateway import DAS_RETRY_BACKOFF_S, ToolGateway, ToolGatewayError

scenarios(str(Path(__file__).parent / "features" / "tool_loop_resilience.feature"))

REPORT = "请基于电商销量数据写一份简短洞察报告"
WINDOW = {"label": "近90天", "start": "2025-07-25", "end": "2025-10-22"}


class ScriptedDas:
    def __init__(self) -> None:
        self.plan: list = []
        self.requests = 0
        self._i = 0

    def reset(self, plan: list) -> None:
        self.plan = list(plan)
        self.requests = 0
        self._i = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests += 1
        kind = self.plan[self._i] if self._i < len(self.plan) else self.plan[-1]
        self._i += 1
        if kind == "timeout":
            raise httpx.ReadTimeout("simulated timeout", request=request)
        if kind == "connect":
            raise httpx.ConnectError("simulated connect", request=request)
        if kind == "ok":
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "result": {"structuredContent": {"datasets": [{"name": "ecommerce_sales_v2"}]}},
            }
            return httpx.Response(200, json=payload)
        if kind == "rpc":
            return httpx.Response(
                200,
                json={"jsonrpc": "2.0", "id": 1, "error": {"code": -32602, "message": "bad args"}},
            )
        return httpx.Response(int(kind), text="nope")


def _bindings(skill: str = "insight_report") -> dict:
    return {"skill": skill, "dataset": "ecommerce_sales_v2", "time_window": dict(WINDOW)}


def _open_run(text: str = REPORT, skill: str = "insight_report") -> tuple[str, str]:
    sid = session.create_session({"bindings": _bindings(skill)})["id"]
    session.add_message(sid, "user", text)
    rid = run.create_run(sid, context_bindings=_bindings(skill))["id"]
    return sid, rid


def _canned(name: str) -> dict:
    if name == "query_stats":
        return {"rows": [{"key": "电子产品", "value": 4242.5}]}
    if name == "list_datasets":
        return {"datasets": [{"name": "ecommerce_sales_v2"}]}
    if name == "describe_dataset":
        return {
            "name": "ecommerce_sales_v2",
            "row_count": 200,
            "summary": {"total_gmv": 1, "min_date": "a", "max_date": "b", "return_pct": 1},
        }
    if name in ("sample_records", "search_records"):
        return {
            "count": 1,
            "records": [
                {
                    "order_id": "ORD1",
                    "category": "电子产品",
                    "channel": "抖音电商",
                    "province": "江苏",
                    "order_date": "2025-08-01",
                    "gmv": 1,
                }
            ],
        }
    return {"ok": True}


def _install_canned_gateway(monkeypatch, fail_name: str | None = None, fail_message: str = "") -> None:
    def call(self, name, arguments=None, *, bindings=None):
        del self, arguments, bindings
        if fail_name and name == fail_name:
            raise ToolGatewayError(fail_message or f"injected {name} failure")
        return {"data": _canned(name), "evidence": {"fingerprint": "fp" + name[:4], "tool": name}}

    monkeypatch.setattr(ToolGateway, "call", call)


def _install_spy(box) -> None:
    real = model_broker.decide

    def spy(**kwargs):
        box["seen"].append(list(kwargs.get("tool_results") or []))
        return real(**kwargs)

    box["monkeypatch"].setattr(orchestrator.model_broker, "decide", spy)


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
    sleeps: list[float] = []
    # Both modules import the same time module; patch it once.
    monkeypatch.setattr("agent_os_kernel.tool_gateway.time.sleep", lambda seconds: sleeps.append(seconds))
    real_has = model_broker.has_llm
    monkeypatch.setattr(model_broker, "has_llm", lambda: False)
    obs.init_db()
    return {
        "monkeypatch": monkeypatch,
        "sleeps": sleeps,
        "seen": [],
        "real_has_llm": real_has,
        "result": None,
        "run_id": None,
    }


def _run(box, text: str = REPORT, skill: str = "insight_report") -> dict:
    sid, rid = _open_run(text, skill)
    box["run_id"] = rid
    box["result"] = orchestrator.handle_user_message(sid, rid, text)
    return box["result"]


def _install_live_transport(monkeypatch, handler) -> None:
    real = httpx.Client

    def factory(*args, **kwargs):
        kwargs = dict(kwargs)
        kwargs["transport"] = httpx.MockTransport(handler)
        return real(*args, **kwargs)

    monkeypatch.setattr(httpx, "Client", factory)


def _arm_live(box) -> None:
    box["monkeypatch"].setattr(model_broker, "has_llm", lambda: True)
    box["monkeypatch"].setenv("MODEL_API_KEY", "test-key")
    box["monkeypatch"].delenv("XAI_API_KEY", raising=False)
    box["monkeypatch"].delenv("OPENAI_API_KEY", raising=False)


@given("一条 stub 洞察 Run")
def given_stub_run(box):
    assert model_broker.model_mode() == "stub"
    box["seen"] = []


@given("网关对 describe_dataset 抛出可恢复错误")
def given_describe_fails(box):
    _install_spy(box)
    _install_canned_gateway(box["monkeypatch"], "describe_dataset", "injected describe failure")


@given("模型先返回坏调用再给出结论")
def given_bad_calls(box):
    box["seen"] = []
    steps = {"n": 0}

    def decide(**kwargs):
        steps["n"] += 1
        box["seen"].append(list(kwargs.get("tool_results") or []))
        if steps["n"] == 1:
            return {
                "type": "tool_calls",
                "calls": [
                    "nope",
                    {"name": "list_datasets", "arguments": ["bad"]},
                    {"name": "drop_table", "arguments": {}},
                    {"name": "list_datasets", "arguments": {}},
                ],
            }
        return {"type": "final", "content": "继续完成", "_broker_events": ["skip-me"]}

    box["monkeypatch"].setattr(orchestrator.model_broker, "decide", decide)

    def call(self, name, arguments=None, *, bindings=None):
        del self, name, arguments, bindings
        raise ToolGatewayError("y" * 400)

    box["monkeypatch"].setattr(ToolGateway, "call", call)


@given("可脚本化的 DAS HTTP 传输")
def given_scripted_das(box):
    script = ScriptedDas()
    client = httpx.Client(transport=httpx.MockTransport(script.handler))
    box["script"] = script
    box["gw"] = ToolGateway("http://das.test/api/mcp", token="sekret", client=client)


@given("一条 live 模型的洞察 Run")
def given_live_run(box):
    _arm_live(box)
    assert model_broker.model_mode() == "live"


@given("live 请求每次都失败")
def given_live_always_fails(box):
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(502, text="bad gateway")

    _install_live_transport(box["monkeypatch"], handler)
    _install_canned_gateway(box["monkeypatch"])


@given("live 第一次失败第二次返回 final")
def given_live_recovers(box):
    state = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        state["n"] += 1
        if state["n"] == 1:
            return httpx.Response(500, text="once")
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps({"type": "final", "content": "live ok"})}}]},
        )

    _install_live_transport(box["monkeypatch"], handler)


@when("用户要求生成洞察报告")
@when("编排器执行这一轮")
def when_report(box):
    _run(box)


@when("编排器处理未知 Skill")
def when_unknown_skill(box):
    _run(box, text="hi", skill="no-such-skill")


@when("模型返回空的 tool_calls")
def when_empty_calls(box):
    box["monkeypatch"].setattr(
        orchestrator.model_broker,
        "decide",
        lambda **kwargs: {"type": "tool_calls", "calls": []},
    )
    _run(box)


@when("模型返回未知决策")
def when_unknown_decision(box):
    box["monkeypatch"].setattr(
        orchestrator.model_broker,
        "decide",
        lambda **kwargs: {"type": "weird"},
    )
    _run(box)


@when("模型连续要求调用工具直到超过 MAX_STEPS")
def when_max_steps(box):
    box["monkeypatch"].setattr(
        orchestrator.model_broker,
        "decide",
        lambda **kwargs: {"type": "tool_calls", "calls": [{"name": "drop_table", "arguments": {"i": 1}}]},
    )
    _run(box)


def _invoke_gateway(box, plan: list) -> None:
    box["sleeps"].clear()
    box["script"].reset(plan)
    try:
        box["out"] = box["gw"].call("list_datasets", {})
        box["raised"] = None
    except ToolGatewayError as exc:
        box["out"] = None
        box["raised"] = exc


@when("DAS 第一次超时且第二次成功")
def when_timeout_then_ok(box):
    _invoke_gateway(box, ["timeout", "ok"])


@when("DAS 第一次返回 503 且第二次成功")
def when_503_then_ok(box):
    _invoke_gateway(box, [503, "ok"])


@when("DAS 连续两次返回 503")
def when_503_twice(box):
    _invoke_gateway(box, [503, 503])


@when("DAS 连续两次超时")
def when_timeout_twice(box):
    _invoke_gateway(box, ["timeout", "timeout"])


@when("DAS 返回 401")
def when_401(box):
    _invoke_gateway(box, [401])


@when("DAS 返回 400")
def when_400(box):
    _invoke_gateway(box, [400])


@when("DAS 连接失败")
def when_connect(box):
    _invoke_gateway(box, ["connect"])


@when("DAS 返回 JSON-RPC 错误")
def when_rpc(box):
    _invoke_gateway(box, ["rpc"])


@then("Run 状态是 completed")
def then_completed(box):
    assert box["result"]["status"] == "completed"
    states = [event["payload"].get("state") for event in obs.list_events(box["run_id"]) if event["kind"] == "state"]
    assert "completed" in states
    assert "failed" not in states


@then("事件里有 describe_dataset 的失败 tool_call")
def then_describe_failed(box):
    failed = [
        event
        for event in _tool_events(box["run_id"])
        if event["payload"].get("tool") == "describe_dataset" and event["payload"].get("ok") is False
    ]
    assert failed
    assert failed[0]["payload"]["error"] == "injected describe failure"


@then("同一步里后续工具仍然成功")
def then_later_tools_ok(box):
    events = _tool_events(box["run_id"])
    failed_at = next(i for i, event in enumerate(events) if event["payload"].get("ok") is False)
    assert any(event["payload"].get("ok") is True for event in events[failed_at + 1 :])


@then("下一次 decide 看见 ok 为 false 的短错误")
def then_model_sees_failure(box):
    assert len(box["seen"]) >= 2
    failed = [item for item in box["seen"][1] if item.get("name") == "describe_dataset"]
    assert failed
    assert failed[0]["ok"] is False
    assert failed[0]["error"] == "injected describe failure"
    assert box["seen"][0] == []


@then("事件里有 ok 为 false 的 tool_call")
def then_some_tool_failed(box):
    assert any(event["payload"].get("ok") is False for event in _tool_events(box["run_id"]))


@then("过长的工具错误被截短")
def then_truncated(box):
    errors = [event["payload"].get("error") or "" for event in _tool_events(box["run_id"])]
    long = [error for error in errors if error.endswith("…")]
    assert long
    assert all(len(error) <= 180 for error in long)
    assert "tool call must be an object" in errors
    assert "tool arguments must be an object" in errors
    assert any(error.startswith("tool not allowed") for error in errors)


@then("下一次 decide 看见这些失败结果")
def then_model_sees_bad_calls(box):
    assert len(box["seen"]) >= 2
    assert box["seen"][1]
    assert all(item.get("ok") is False and item.get("error") for item in box["seen"][1])
    assert box["result"]["markdown"] == "继续完成"


@then(parsers.parse("Run 失败且原因包含「{snippet}」"))
def then_failed_reason(box, snippet):
    assert box["result"]["status"] == "failed"
    assert snippet in box["result"]["markdown"]
    failed = [event for event in obs.list_events(box["run_id"]) if event["kind"] == "state" and event["payload"].get("state") == "failed"]
    assert failed


@then("失败前每一步工具错误都已回灌")
def then_max_steps_recorded(box):
    failed_tools = [event for event in _tool_events(box["run_id"]) if event["payload"].get("ok") is False]
    assert len(failed_tools) == orchestrator.MAX_STEPS
    assert all("drop_table" in (event["payload"].get("error") or "") for event in failed_tools)


@then("调用成功并且只退避了一次")
def then_retry_succeeded(box):
    assert box["raised"] is None
    assert box["script"].requests == 2
    assert box["sleeps"] == [DAS_RETRY_BACKOFF_S]
    assert box["out"]["data"]["datasets"][0]["name"] == "ecommerce_sales_v2"


@then(parsers.parse("调用失败且状态为 {status:d} 并且只请求了 {count:d} 次"))
def then_gateway_failed(box, status, count):
    assert box["raised"] is not None
    assert box["raised"].status_code == status
    assert box["script"].requests == count
    if count == 1:
        assert box["sleeps"] == []
    else:
        assert box["sleeps"] == [DAS_RETRY_BACKOFF_S]


@then("调用失败且没有重试")
def then_rpc_not_retried(box):
    assert box["raised"] is not None
    assert "JSON-RPC" in str(box["raised"])
    assert box["script"].requests == 1
    assert box["sleeps"] == []


@then("事件包含 live_retry 和 live_fallback")
def then_both_broker_events(box):
    types = [event["payload"].get("type") for event in _model_events(box["run_id"])]
    assert "live_retry" in types
    assert "live_fallback" in types
    retry = next(event["payload"] for event in _model_events(box["run_id"]) if event["payload"].get("type") == "live_retry")
    fallback = next(event["payload"] for event in _model_events(box["run_id"]) if event["payload"].get("type") == "live_fallback")
    assert retry["ok"] is False and retry["attempt"] == 2 and retry["mode"] == "live"
    assert "502" in retry["error"]
    assert fallback["ok"] is False and fallback["mode"] == "stub"
    assert LIVE_RETRY_BACKOFF_S in box["sleeps"]


@then("结论仍来自 stub 路径")
def then_stub_conclusion(box):
    assert "发现" in box["result"]["markdown"]
    assert "编排失败" not in box["result"]["markdown"]


@then("事件包含 live_retry 且没有 live_fallback")
def then_retry_without_fallback(box):
    types = [event["payload"].get("type") for event in _model_events(box["run_id"])]
    assert "live_retry" in types
    assert "live_fallback" not in types
    assert box["sleeps"] == [LIVE_RETRY_BACKOFF_S]


@then("结论文本是「live ok」")
def then_live_text(box):
    assert box["result"]["markdown"] == "live ok"


def test_resume_unknown_run_fails(box):
    with pytest.raises(ValueError, match="run not found"):
        orchestrator.resume_after_hitl("run_missing")


def test_live_parse_targets_and_unreadable_body(box):
    """Cover live URL selection and decision parsing without a product scenario."""
    monkeypatch = box["monkeypatch"]
    monkeypatch.setattr(model_broker, "has_llm", box["real_has_llm"])
    captured: list[dict] = []
    queue: list[object] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        captured.append({"url": str(request.url), "model": body["model"], "messages": body["messages"]})
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        if isinstance(item, str):
            return httpx.Response(200, text=item)
        return item

    _install_live_transport(monkeypatch, handler)

    def decide_once(content, *, attempts: int = 1, **env):
        for key in ("MODEL_API_KEY", "XAI_API_KEY", "OPENAI_API_KEY", "MODEL_BASE_URL", "MODEL_NAME"):
            monkeypatch.delenv(key, raising=False)
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        for _ in range(attempts):
            queue.append(content)
        box["sleeps"].clear()
        return model_broker.decide(
            system="sys",
            messages=[{"role": "tool", "content": "hidden"}, {"role": "user", "content": "hi"}],
            allowed_tools=["list_datasets"],
            tool_results=[{"name": "list_datasets", "ok": False, "error": "short"}],
        )

    fenced = decide_once(
        httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": '```json\n{"type":"tool_calls","calls":[{"name":"list_datasets","arguments":{}}]}\n```'
                        }
                    }
                ]
            },
        ),
        MODEL_BASE_URL="http://models.test/v1",
        MODEL_API_KEY="k",
        MODEL_NAME="demo-model",
    )
    assert fenced["type"] == "tool_calls"
    assert captured[-1]["url"] == "http://models.test/v1/chat/completions"
    assert captured[-1]["model"] == "demo-model"
    assert box["sleeps"] == []
    assert any(message["role"] == "user" and "short" in message["content"] for message in captured[-1]["messages"])
    assert any(message["content"] == "hidden" and message["role"] == "user" for message in captured[-1]["messages"])

    loose = decide_once(
        httpx.Response(
            200,
            json={"choices": [{"message": {"content": 'note {"type":"final","content":"loose ok"} tail'}}]},
        ),
        XAI_API_KEY="k",
    )
    assert loose == {"type": "final", "content": "loose ok"}
    assert captured[-1]["url"] == "https://api.x.ai/v1/chat/completions"

    prose = decide_once(
        httpx.Response(200, json={"choices": [{"message": {"content": "not json at all"}}]}),
        OPENAI_API_KEY="k",
    )
    assert prose == {"type": "final", "content": "not json at all"}
    assert captured[-1]["url"] == "https://api.openai.com/v1/chat/completions"

    empty = decide_once(
        httpx.Response(200, json={"choices": [{"message": {"content": None}}]}),
        MODEL_API_KEY="k",
    )
    assert empty == {"type": "final", "content": ""}

    unreadable = decide_once("not-json", attempts=2, MODEL_API_KEY="k")
    assert unreadable["_broker_events"][0]["type"] == "live_retry"
    assert unreadable["_broker_events"][1]["type"] == "live_fallback"
    assert "unreadable" in unreadable["_broker_events"][1]["error"]

    long_error = "e" * 400
    queue.append(RuntimeError(long_error))
    queue.append(
        httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"type":"nope"}'}}]},
        )
    )
    monkeypatch.setenv("MODEL_API_KEY", "k")
    box["sleeps"].clear()
    recovered = model_broker.decide(
        system="sys",
        messages=[],
        allowed_tools=[],
        tool_results=[],
    )
    assert recovered["type"] == "final"
    assert recovered["content"] == '{"type":"nope"}'
    assert recovered["_broker_events"][0]["type"] == "live_retry"
    assert recovered["_broker_events"][0]["error"].endswith("…")
    assert len(recovered["_broker_events"][0]["error"]) <= 160
    assert "live_fallback" not in [event["type"] for event in recovered["_broker_events"]]

    assert box["real_has_llm"]() is True
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    assert box["real_has_llm"]() is False
    assert model_broker.model_mode() == "stub"
