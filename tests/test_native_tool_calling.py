"""Batch F：live broker 走 OpenAI tools / tool_calls，stub 仍不发 HTTP。"""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from pytest_bdd import given, scenarios, then, when

from agent_os_kernel import model_broker, obs, orchestrator, run, session
from agent_os_kernel.skills.insight_report import SKILL
from agent_os_kernel.tool_gateway import ToolGateway, ToolGatewayError

scenarios(str(Path(__file__).parent / "features" / "native_tool_calling.feature"))

REPORT = "请基于电商销量数据写一份简短洞察报告"
WINDOW = {"label": "近90天", "start": "2025-07-25", "end": "2025-10-22"}
JSON_INSTRUCTION = "请以 JSON"


def _bindings() -> dict:
    return {"skill": "insight_report", "dataset": "ecommerce_sales_v2", "time_window": dict(WINDOW)}


def _open_run(text: str = REPORT) -> tuple[str, str]:
    sid = session.create_session({"bindings": _bindings()})["id"]
    session.add_message(sid, "user", text)
    rid = run.create_run(sid, context_bindings=_bindings())["id"]
    return sid, rid


def _canned(name: str) -> dict:
    if name == "list_datasets":
        return {"datasets": [{"name": "ecommerce_sales_v2"}]}
    if name == "describe_dataset":
        return {"name": "ecommerce_sales_v2", "row_count": 200}
    return {"ok": True}


def _install_transport(monkeypatch, handler) -> None:
    real = httpx.Client

    def factory(*args, **kwargs):
        kwargs = dict(kwargs)
        kwargs["transport"] = httpx.MockTransport(handler)
        return real(*args, **kwargs)

    monkeypatch.setattr(httpx, "Client", factory)


def _arm_live(monkeypatch) -> None:
    monkeypatch.setattr(model_broker, "has_llm", lambda: True)
    monkeypatch.setenv("MODEL_API_KEY", "test-key")
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("MODEL_BASE_URL", raising=False)
    monkeypatch.setenv("MODEL_NAME", "demo-model")


def _no_json_instruction(messages: list[dict]) -> None:
    assert not any(JSON_INSTRUCTION in str(message.get("content") or "") for message in messages)
    assert not any(str(message.get("content") or "").startswith("工具结果") for message in messages)


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
        "captured": [],
        "posts": 0,
        "result": None,
        "run_id": None,
        "decision": None,
    }


@given("可拦截的 live chat completions")
def given_live_capture(box):
    _arm_live(box["monkeypatch"])

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        box["captured"].append(body)
        box["posts"] += 1
        script = box.get("script") or []
        item = script[len(box["captured"]) - 1] if script else {"choices": [{"message": {"content": "ok"}}]}
        return httpx.Response(200, json=item)

    _install_transport(box["monkeypatch"], handler)


@given("live 网关对 describe_dataset 抛出可恢复错误")
def given_describe_fails(box):
    def call(self, name, arguments=None, *, bindings=None):
        del self, arguments, bindings
        if name == "describe_dataset":
            raise ToolGatewayError("injected describe failure")
        return {"data": _canned(name), "evidence": {"fingerprint": "fp" + name[:4], "tool": name}}

    box["monkeypatch"].setattr(ToolGateway, "call", call)


@given("stub 模式且 chat completions 被禁止")
def given_stub_blocks_http(box):
    assert model_broker.model_mode() == "stub"

    def call(self, name, arguments=None, *, bindings=None):
        del self, arguments, bindings
        return {"data": _canned(name), "evidence": {"fingerprint": "fp" + name[:4], "tool": name}}

    box["monkeypatch"].setattr(ToolGateway, "call", call)

    def handler(request: httpx.Request) -> httpx.Response:
        box["posts"] += 1
        return httpx.Response(500, text=str(request.url))

    _install_transport(box["monkeypatch"], handler)


@when("decide 收到两个原生 tool_calls")
def when_two_native_calls(box):
    box["script"] = [
        {
            "choices": [
                {
                    "message": {
                        "content": '{"type":"final","content":"不要用这段"}',
                        "tool_calls": [
                            {
                                "id": "a",
                                "type": "function",
                                "function": {
                                    "name": "query_stats",
                                    "arguments": json.dumps(
                                        {"dataset_id": "ecommerce_sales_v2", "limit": 3},
                                        ensure_ascii=False,
                                    ),
                                },
                            },
                            {
                                "id": "b",
                                "type": "function",
                                "function": {
                                    "name": "sample_records",
                                    "arguments": {"limit": 5, "dataset_id": "ecommerce_sales_v2"},
                                },
                            },
                        ],
                    }
                }
            ]
        }
    ]
    box["decision"] = model_broker.decide(
        system="sys",
        messages=[{"role": "user", "content": "看渠道"}],
        allowed_tools=["query_stats", "query_stats", "", "extra_tool"],
        tool_results=[],
    )


@when("模型先原生调用两个工具再给出正文结论")
def when_live_turn(box):
    box["script"] = [
        {
            "choices": [
                {
                    "message": {
                        "content": None,
                        "tool_calls": [
                            {"type": "function", "function": {"name": "list_datasets", "arguments": "{}"}},
                            {
                                "type": "function",
                                "function": {"name": "describe_dataset", "arguments": '{"dataset_id":"ecommerce_sales_v2"}'},
                            },
                        ],
                    }
                }
            ]
        },
        {"choices": [{"message": {"content": "live 洞察"}}]},
    ]
    sid, rid = _open_run()
    box["run_id"] = rid
    box["result"] = orchestrator.handle_user_message(sid, rid, REPORT)


@when("响应没有 tool_calls 只有围栏 JSON")
def when_fenced_json(box):
    box["script"] = [
        {
            "choices": [
                {
                    "message": {
                        "tool_calls": [],
                        "content": '```json\n{"type":"tool_calls","calls":[{"name":"list_datasets","arguments":{}}]}\n```',
                    }
                }
            ]
        }
    ]
    box["decision"] = model_broker.decide(
        system="sys",
        messages=[{"role": "user", "content": "hi"}],
        allowed_tools=["list_datasets"],
        tool_results=[],
    )


@when("用户在 stub 下要求生成洞察报告")
def when_report(box):
    sid, rid = _open_run()
    box["run_id"] = rid
    box["result"] = orchestrator.handle_user_message(sid, rid, REPORT)


@then("请求 tools 含 query_stats 且没有 JSON 指令")
def then_tools_shape(box):
    body = box["captured"][0]
    names = [item["function"]["name"] for item in body["tools"]]
    assert names == ["query_stats", "extra_tool"]
    assert body["tool_choice"] == "auto"
    stats = body["tools"][0]["function"]["parameters"]["properties"]
    assert "filters" in stats and "metrics" in stats and "group_by" in stats
    assert body["tools"][1]["function"]["parameters"]["additionalProperties"] is True
    _no_json_instruction(body["messages"])
    assert body["model"] == "demo-model"
    assert [message["role"] for message in body["messages"]] == ["system", "user"]


@then("decide 返回的 calls 顺序与参数保持不变")
def then_call_order(box):
    assert box["decision"]["type"] == "tool_calls"
    assert box["decision"]["calls"] == [
        {"name": "query_stats", "arguments": {"dataset_id": "ecommerce_sales_v2", "limit": 3}},
        {"name": "sample_records", "arguments": {"limit": 5, "dataset_id": "ecommerce_sales_v2"}},
    ]
    assert "不要用这段" not in json.dumps(box["decision"], ensure_ascii=False)


@then("第一次请求包含洞察工具且没有 JSON 指令")
def then_first_request(box):
    body = box["captured"][0]
    names = [item["function"]["name"] for item in body["tools"]]
    assert names == list(SKILL["allowed_tools"])
    assert body["tool_choice"] == "auto"
    _no_json_instruction(body["messages"])
    assert all(item["type"] == "function" for item in body["tools"])


@then("第二次请求把失败写在 tool 角色消息里")
def then_tool_role_refeed(box):
    body = box["captured"][1]
    roles = [message["role"] for message in body["messages"]]
    assert roles[-4:] == ["assistant", "tool", "assistant", "tool"]
    tool_messages = [message for message in body["messages"] if message["role"] == "tool"]
    assert len(tool_messages) == 2
    assert tool_messages[0]["tool_call_id"] == "call_0"
    assert '"ok": true' in tool_messages[0]["content"]
    assert "ecommerce_sales_v2" in tool_messages[0]["content"]
    assert tool_messages[1]["tool_call_id"] == "call_1"
    assert '"ok": false' in tool_messages[1]["content"]
    assert "injected describe failure" in tool_messages[1]["content"]
    assistant = [message for message in body["messages"] if message["role"] == "assistant"]
    assert assistant[1]["tool_calls"][0]["function"]["name"] == "describe_dataset"
    _no_json_instruction(body["messages"])


@then("原生工具这一轮 Run 完成")
def then_completed(box):
    assert box["result"]["status"] == "completed"


@then("结论文本是「live 洞察」")
def then_live_text(box):
    assert box["result"]["markdown"] == "live 洞察"


@then("两次工具调用按模型顺序留下事件")
def then_tool_order(box):
    tools = [event for event in obs.list_events(box["run_id"]) if event["kind"] == "tool_call"]
    assert [event["payload"].get("tool") for event in tools] == ["list_datasets", "describe_dataset"]
    assert [event["payload"].get("ok") for event in tools] == [True, False]
    steps = [event["payload"] for event in obs.list_events(box["run_id"]) if event["kind"] == "model_step"]
    assert steps[0]["type"] == "tool_calls" and steps[0]["n_calls"] == 2
    assert steps[1]["type"] == "final"


@then("decide 类型是 tool_calls")
def then_decision_calls(box):
    assert box["decision"]["type"] == "tool_calls"
    assert box["decision"]["calls"][0]["name"] == "list_datasets"


@then("这次请求仍然带 tools")
def then_still_tools(box):
    body = box["captured"][0]
    assert body["tools"][0]["function"]["name"] == "list_datasets"
    assert body["tool_choice"] == "auto"
    _no_json_instruction(body["messages"])


@then("没有发出 chat completions")
def then_no_http(box):
    assert box["posts"] == 0


@then("洞察结论文本来自 stub")
def then_stub_copy(box):
    assert "发现" in box["result"]["markdown"]
    assert "编排失败" not in box["result"]["markdown"]


def test_native_argument_and_history_edges(box):
    """Odd provider payloads and tool history stay on the public decide() path."""
    _arm_live(box["monkeypatch"])
    queue: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        box["captured"].append(body)
        return httpx.Response(200, json=queue.pop(0))

    _install_transport(box["monkeypatch"], handler)

    def decide_once(payload: dict, **kwargs) -> dict:
        queue.append(payload)
        box["captured"].clear()
        return model_broker.decide(
            system="sys",
            messages=kwargs.get("messages") or [{"role": "user", "content": "hi"}],
            allowed_tools=kwargs.get("allowed_tools", ["list_datasets"]),
            tool_results=kwargs.get("tool_results") or [],
        )

    mixed = decide_once(
        {
            "choices": [
                {
                    "message": {
                        "content": 3,
                        "tool_calls": [
                            "bad",
                            {"function": "nope"},
                            {"function": {"name": "", "arguments": {}}},
                            {"function": {"name": 7, "arguments": 1}},
                            {"function": {"name": "query_stats", "arguments": "not-json"}},
                            {"function": {"name": "sample_records", "arguments": "[1]"}},
                            {"function": {"name": "search_records", "arguments": ""}},
                            {"function": {"name": "list_datasets", "arguments": None}},
                        ],
                    }
                }
            ]
        }
    )
    assert mixed["calls"] == [
        {"name": "", "arguments": {}},
        {"name": "", "arguments": 1},
        {"name": "query_stats", "arguments": "not-json"},
        {"name": "sample_records", "arguments": [1]},
        {"name": "search_records", "arguments": {}},
        {"name": "list_datasets", "arguments": {}},
    ]

    plain = decide_once(
        {"choices": [{"message": {"tool_calls": ["bad", {"function": "nope"}], "content": "plain final"}}]},
        allowed_tools=["list_datasets", 3, "list_datasets"],
    )
    assert plain == {"type": "final", "content": "plain final"}
    assert [item["function"]["name"] for item in box["captured"][0]["tools"]] == ["list_datasets"]

    number = decide_once({"choices": [{"message": {"content": 3}}]})
    assert number == {"type": "final", "content": "3"}

    empty = decide_once({"choices": [{"message": {"content": None}}]})
    assert empty == {"type": "final", "content": ""}

    missing = decide_once({"choices": [{"message": {"tool_calls": "nope", "content": "only text"}}]})
    assert missing == {"type": "final", "content": "only text"}

    huge = "测" * 13000
    note = decide_once(
        {"choices": [{"message": {"content": "seen"}}]},
        tool_results=[
            None,
            {"name": "_loop_break", "loop_break": True, "ok": False, "error": "loop_break: 停"},
            {"ok": False, "error": "nameless"},
            {"name": "_secret"},
            {"name": "", "error": ""},
            {
                "name": "list_datasets",
                "arguments": ["bad"],
                "ok": True,
                "data": {"blob": huge},
                "evidence": {"fingerprint": "fp"},
            },
        ],
    )
    assert note["content"] == "seen"
    messages = box["captured"][0]["messages"]
    notes = [message["content"] for message in messages if message["role"] == "user" and message["content"] != "hi"]
    assert notes[:4] == ["loop_break: 停", "nameless", "_secret", "tool result"]
    tool_messages = [message for message in messages if message["role"] == "tool"]
    assert len(tool_messages) == 1
    assert len(tool_messages[0]["content"]) <= 12000
    assert tool_messages[0]["content"].endswith("…")
    assert '"ok": true' in tool_messages[0]["content"]
    assistant = next(message for message in messages if message["role"] == "assistant")
    assert assistant["tool_calls"][0]["function"]["arguments"] == "{}"

    queue.append({"choices": [{"message": "nope"}]})
    queue.append({"choices": [{"message": {"content": "after retry"}}]})
    retried = model_broker.decide(
        system="sys",
        messages=[],
        allowed_tools=["list_datasets"],
        tool_results=[],
    )
    assert retried["content"] == "after retry"
    assert retried["_broker_events"][0]["type"] == "live_retry"
    assert "unreadable" in retried["_broker_events"][0]["error"]
