"""Batch E：超长 messages / tool_results 在 decide 前按规则截断。"""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from pytest_bdd import given, scenarios, then, when

from agent_os_kernel import context, model_broker, obs, orchestrator, run, session
from agent_os_kernel.skills import registry
from agent_os_kernel.skills.insight_report import SKILL
from agent_os_kernel.tool_gateway import ToolGateway

scenarios(str(Path(__file__).parent / "features" / "context_length.feature"))

REPORT = "请基于电商销量数据写一份简短洞察报告"
LATEST = "最新问题：请保留这句话"
OLD_TURN = "OLD_TURN_SHOULD_DROP"
DROP_BLOB = "DROP_BLOB_SHOULD_DROP"
KEEP_FP = "keep-fp-9"
WINDOW = {"label": "近90天", "start": "2025-07-25", "end": "2025-10-22"}


def _bindings() -> dict:
    return {"skill": "insight_report", "dataset": "ecommerce_sales_v2", "time_window": dict(WINDOW)}


def _history() -> dict:
    system = context.compose_system("技能提示保持不变", _bindings())
    messages = []
    for index in range(6):
        messages.append({"role": "user", "content": f"{OLD_TURN}-{index}-" + ("旧" * 4000)})
        messages.append({"role": "assistant", "content": f"中间答复{index}" + ("答" * 2000)})
    messages.append({"role": "user", "content": LATEST})
    messages.append({"role": "assistant", "content": "最近一句助手"})
    tools = [
        {
            "name": "query_stats",
            "ok": False,
            "error": DROP_BLOB + ("错" * 3000),
            "data": {"blob": DROP_BLOB},
            "evidence": None,
        },
        {
            "name": "query_stats",
            "ok": True,
            "arguments": {"group_by": "channel", "metric": "sum_gmv"},
            "data": {"rows": [{"key": "直播", "value": 10}]},
            "evidence": {"fingerprint": KEEP_FP},
        },
    ]
    return {"system": system, "messages": messages, "tools": tools}


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


@pytest.fixture
def box(monkeypatch, tmp_path):
    monkeypatch.setattr(obs, "DB_PATH", tmp_path / "kernel.db")
    monkeypatch.setenv("HITL_AUTO", "1")
    monkeypatch.setenv("AGENT_CONTEXT_MAX_CHARS", "2500")
    monkeypatch.setenv("AGENT_CONTEXT_MAX_MESSAGES", "8")
    for key in ("MODEL_API_KEY", "XAI_API_KEY", "OPENAI_API_KEY", "MODEL_BASE_URL", "MODEL_NAME", "USE_FAKE_DAS"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(model_broker, "has_llm", lambda: False)
    obs.init_db()
    return {
        "monkeypatch": monkeypatch,
        "captured": [],
        "seen": None,
        "decision": None,
        "result": None,
        "run_id": None,
        "history": None,
    }


@given("一条远超预算的会话历史和工具结果")
def given_oversized(box):
    box["history"] = _history()

    def stub_decide(skill_name, **kwargs):
        del skill_name
        box["seen"] = kwargs
        return {"type": "final", "content": "截断后仍可结论", "_broker_events": [{"type": "from-stub"}]}

    box["monkeypatch"].setattr(registry, "stub_decide", stub_decide)


@given("可拦截的长度策略 live 补全")
def given_live(box):
    _arm_live(box["monkeypatch"])

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        box["captured"].append(body)
        return httpx.Response(200, json={"choices": [{"message": {"content": "live 截断后仍可结论"}}]})

    _install_transport(box["monkeypatch"], handler)


@given("会话里堆了远超预算的旧消息")
def given_long_session(box):
    system = context.compose_system(str(SKILL["system_prompt"]), _bindings())
    box["monkeypatch"].setenv("AGENT_CONTEXT_MAX_CHARS", str(len(system) + 12000))
    box["monkeypatch"].setenv("AGENT_CONTEXT_MAX_MESSAGES", "40")

    def call(self, name, arguments=None, *, bindings=None):
        del self, arguments, bindings
        return {
            "data": {"rows": [{"key": "甲", "value": 1}], "row_count": 3},
            "evidence": {"fingerprint": "fp" + name[:4], "tool": name},
        }

    box["monkeypatch"].setattr(ToolGateway, "call", call)
    sid = session.create_session({"bindings": _bindings()})["id"]
    for index in range(12):
        session.add_message(sid, "user", f"{OLD_TURN}-{index}-" + ("旧" * 9000))
    session.add_message(sid, "user", REPORT)
    box["session_id"] = sid


@when("用这条历史上限调用 decide")
def when_decide(box):
    history = box["history"]
    box["decision"] = model_broker.decide(
        system=history["system"],
        messages=history["messages"],
        allowed_tools=["query_stats", "sample_records"],
        tool_results=history["tools"],
        bindings=_bindings(),
        skill_name="insight_report",
    )


@when("用户在长度预算下要求生成洞察报告")
def when_report(box):
    sid = box["session_id"]
    rid = run.create_run(sid, context_bindings=_bindings())["id"]
    box["run_id"] = rid
    box["result"] = orchestrator.handle_user_message(sid, rid, REPORT)


@then("截断事件带有丢掉的消息数和工具结果数")
def then_event_counts(box):
    events = box["decision"]["_broker_events"]
    truncated = next(event for event in events if event["type"] == "context_truncated")
    assert truncated["ok"] is True
    assert truncated["dropped_messages"] > 0
    assert truncated["dropped_tool_results"] > 0
    assert truncated["kept_chars"] <= truncated["max_chars"]
    assert truncated["bindings_fingerprint"] == context.bindings_fingerprint(_bindings())
    assert events[-1]["type"] == "from-stub" or box["captured"]


@then("送入模型的上下文留下系统提示、最近用户话和成功证据")
def then_stub_saw_kept(box):
    history = box["history"]
    fitted = context.fit_context(
        system=history["system"],
        messages=history["messages"],
        tool_results=history["tools"],
        bindings=_bindings(),
    )
    seen = box["seen"]
    assert fitted["system"] == history["system"]
    assert "Context bindings" in fitted["system"]
    assert "ecommerce_sales_v2" in fitted["system"]
    assert seen["messages"] == fitted["messages"]
    assert seen["tool_results"] == fitted["tool_results"]
    joined = "\n".join(str(message.get("content") or "") for message in seen["messages"])
    assert LATEST in joined
    assert OLD_TURN not in joined
    assert any(
        isinstance(tool, dict) and (tool.get("evidence") or {}).get("fingerprint") == KEEP_FP
        for tool in seen["tool_results"]
    )
    assert DROP_BLOB not in json.dumps(seen["tool_results"], ensure_ascii=False)
    assert history["tools"][0]["data"]["blob"] == DROP_BLOB
    assert any(str(message.get("content") or "").startswith("[context] dropped_messages=") for message in seen["messages"])


@then("decide 返回截断后的 final")
def then_final(box):
    assert box["decision"]["type"] == "final"
    assert box["decision"]["content"] == "截断后仍可结论"


@then("发出的 messages 不含被丢掉的旧块")
def then_http_dropped(box):
    body = box["captured"][0]
    text = json.dumps(body["messages"], ensure_ascii=False)
    assert LATEST in text
    assert OLD_TURN not in text
    assert DROP_BLOB not in text
    assert body["messages"][0]["content"] == box["history"]["system"]
    assert "Context bindings" in body["messages"][0]["content"]


@then("请求仍带 tools 且保留最近的成功 tool 消息")
def then_http_tools(box):
    body = box["captured"][0]
    names = [item["function"]["name"] for item in body["tools"]]
    assert names == ["query_stats", "sample_records"]
    assert body["tool_choice"] == "auto"
    tool_messages = [message for message in body["messages"] if message["role"] == "tool"]
    assert len(tool_messages) == 1
    assert KEEP_FP in tool_messages[0]["content"]
    assert '"ok": true' in tool_messages[0]["content"]
    assistant = next(message for message in body["messages"] if message.get("tool_calls"))
    assert assistant["tool_calls"][0]["function"]["name"] == "query_stats"


@then("这一轮截断后的 Run 完成")
def then_run_completed(box):
    assert box["result"]["status"] == "completed"
    assert "发现" in box["result"]["markdown"]
    assert box["result"]["context_bindings"]["dataset"] == "ecommerce_sales_v2"


@then("观测里有上下文截断事件")
def then_obs(box):
    steps = [event["payload"] for event in obs.list_events(box["run_id"]) if event["kind"] == "model_step"]
    truncated = [step for step in steps if step.get("type") == "context_truncated"]
    assert truncated
    assert truncated[0]["dropped_messages"] > 0
    assert truncated[0]["dropped_tool_results"] == 0
    assert any(step.get("type") == "final" for step in steps)
    assert any(step.get("type") == "tool_calls" for step in steps)


def test_limits_and_short_history_stay_put(monkeypatch):
    monkeypatch.delenv("AGENT_CONTEXT_MAX_CHARS", raising=False)
    monkeypatch.delenv("AGENT_CONTEXT_MAX_MESSAGES", raising=False)
    assert context.resolve_context_limits() == (context.CONTEXT_MAX_CHARS, context.CONTEXT_MAX_MESSAGES)

    monkeypatch.setenv("AGENT_CONTEXT_MAX_CHARS", "4000")
    monkeypatch.setenv("AGENT_CONTEXT_MAX_MESSAGES", "6")
    assert context.resolve_context_limits() == (4000, 6)

    monkeypatch.setenv("AGENT_CONTEXT_MAX_CHARS", "nope")
    monkeypatch.setenv("AGENT_CONTEXT_MAX_MESSAGES", "0")
    assert context.resolve_context_limits() == (context.CONTEXT_MAX_CHARS, context.CONTEXT_MAX_MESSAGES)
    monkeypatch.setenv("AGENT_CONTEXT_MAX_CHARS", "10")
    monkeypatch.setenv("AGENT_CONTEXT_MAX_MESSAGES", "9999")
    assert context.resolve_context_limits() == (context.CONTEXT_MAX_CHARS, context.CONTEXT_MAX_MESSAGES)

    messages = [{"role": "user", "content": "hi", "created_at": 1}]
    tools = [{"name": "list_datasets", "ok": True, "data": {"n": 1}}]
    fitted = context.fit_context(system="sys", messages=messages, tool_results=tools, bindings={})
    assert fitted["truncated"] is False
    assert fitted["messages"][0] is messages[0]
    assert fitted["tool_results"][0] is tools[0]
    assert fitted["bindings_fingerprint"] is None
    assert fitted["dropped_messages"] == 0


def test_fit_drops_oldest_and_shrinks_kept_blobs():
    rows = [{"key": "甲" * 80, "value": index, "note": "长" * 50} for index in range(6)]
    older_ok = {
        "name": "query_stats",
        "ok": True,
        "arguments": {"group_by": "category"},
        "data": {"rows": rows},
        "evidence": {"fingerprint": "old-ok"},
    }
    newer_ok = {
        "name": "sample_records",
        "ok": True,
        "arguments": {"limit": 5},
        "data": {"records": ["样本" * 100 for _ in range(4)]},
        "evidence": {"fingerprint": "new-ok"},
    }
    plain = {
        "name": "describe_dataset",
        "ok": True,
        "data": {"summary": "概览", "row_count": 3, "extra": {"skip": True}},
        "evidence": {"fingerprint": "plain"},
    }
    text_blob = {
        "name": "describe_fields",
        "ok": True,
        "data": "字段" * 300,
        "arguments": {"blob": "参" * 300},
        "error": "错" * 200,
        "evidence": {"fingerprint": "text"},
    }
    number_blob = {"name": "list_datasets", "ok": True, "data": 5, "evidence": {"fingerprint": "num"}}
    messages = [
        {"role": "user", "content": "更早的问题" + ("问" * 800)},
        {"role": "assistant", "content": "更早的答复"},
        {"role": "tool", "content": "坏角色"},
        {"content": None},
        "不是对象",
        {"role": "user", "content": LATEST},
        {"role": "assistant", "content": "尾部"},
    ]
    original_rows = list(older_ok["data"]["rows"])
    fitted = context.fit_context(
        system="系统提示",
        messages=messages,
        tool_results=[older_ok, newer_ok, plain, text_blob, number_blob, "坏工具"],
        bindings=_bindings(),
        max_chars=900,
        max_messages=3,
    )
    assert fitted["truncated"] is True
    assert fitted["dropped_messages"] > 0
    assert fitted["system"] == "系统提示"
    assert fitted["bindings_fingerprint"] == context.bindings_fingerprint(_bindings())
    contents = [message["content"] for message in fitted["messages"]]
    assert LATEST in contents
    assert "更早的问题" not in "\n".join(contents)
    assert any(text.startswith("[context] ") for text in contents)
    assert older_ok["data"]["rows"] == original_rows
    assert fitted["kept_chars"] <= fitted["max_chars"]


def test_fit_keeps_recent_success_and_loop_break_ahead_of_old_failures():
    failure = {"name": "query_stats", "ok": False, "error": DROP_BLOB + ("错" * 2000), "data": None}
    success = {
        "name": "query_stats",
        "ok": True,
        "arguments": {"group_by": "province"},
        "data": {"rows": [{"key": "江苏", "value": 9}]},
        "evidence": {"fingerprint": KEEP_FP},
    }
    older_break = {"name": "_loop_break", "ok": False, "loop_break": True, "error": "old break " + ("x" * 1500)}
    latest_break = {"name": "_loop_break", "ok": False, "loop_break": True, "error": "请直接 final"}
    fitted = context.fit_context(
        system="sys",
        messages=[{"role": "assistant", "content": "先"}, {"role": "user", "content": LATEST}],
        tool_results=[failure, older_break, success, latest_break],
        max_chars=700,
        max_messages=10,
    )
    assert fitted["dropped_tool_results"] >= 1
    names = [tool.get("name") for tool in fitted["tool_results"]]
    assert names.count("_loop_break") == 1
    assert fitted["tool_results"][-1]["error"] == "请直接 final"
    kept = next(tool for tool in fitted["tool_results"] if tool.get("ok") is True)
    assert kept["evidence"]["fingerprint"] == KEEP_FP
    assert DROP_BLOB not in json.dumps(fitted["tool_results"], ensure_ascii=False)
    assert LATEST in fitted["messages"][-1]["content"]


def test_fit_shrinks_one_huge_success_and_clips_a_huge_user_turn():
    huge = {
        "name": "query_stats",
        "ok": True,
        "data": {"rows": [{"key": "甲" * 100, "value": 1, "pad": "垫" * 400}]},
        "evidence": {"fingerprint": KEEP_FP},
    }
    shrunk = context.fit_context(
        system="S",
        messages=[{"role": "user", "content": "短问题"}],
        tool_results=[huge],
        max_chars=400,
        max_messages=5,
    )
    assert shrunk["shrunk_tool_results"] == 1
    assert shrunk["tool_results"][0]["ok"] is True
    assert shrunk["tool_results"][0]["evidence"]["fingerprint"] == KEEP_FP
    assert shrunk["tool_results"][0]["data"]["truncated"] is True
    assert shrunk["tool_results"][0]["data"]["fingerprint"] == KEEP_FP
    assert huge["data"]["rows"][0]["pad"].startswith("垫")
    assert "短问题" in shrunk["messages"][-1]["content"]

    clipped = context.fit_context(
        system="S",
        messages=[{"role": "user", "content": "HELLO" + ("X" * 800)}],
        tool_results=[],
        max_chars=80,
        max_messages=4,
    )
    assert clipped["clipped_user"] is True
    assert clipped["truncated"] is True
    text = clipped["messages"][-1]["content"]
    assert text.startswith("HELLO") or text == "…"
    assert len(text) < 800
    assert clipped["kept_chars"] <= clipped["max_chars"]


def test_unshrinkable_blob_and_system_only_overflow():
    bulky = {
        "name": "query_stats",
        "ok": True,
        "data": None,
        "evidence": {"fingerprint": KEEP_FP, "blob": "Z" * 2000},
    }
    loop = {"name": "_loop_break", "ok": False, "loop_break": True, "error": "stop", "blob": "Q" * 2000}
    fitted = context.fit_context(
        system="sys",
        messages=[{"role": "assistant", "content": "只有助手" * 100}],
        tool_results=[loop, bulky],
        max_chars=300,
        max_messages=5,
    )
    assert fitted["truncated"] is True
    assert any(tool.get("name") == "query_stats" for tool in fitted["tool_results"])
    assert fitted["dropped_tool_results"] >= 1

    only_system = context.fit_context(system="超" * 1000, messages=[], tool_results=[], max_chars=256, max_messages=4)
    assert only_system["truncated"] is True
    assert only_system["dropped_messages"] == 0
    assert only_system["messages"] == []
    assert only_system["kept_chars"] > only_system["max_chars"]

    assistants = context.fit_context(
        system="s",
        messages=[{"role": "assistant", "content": f"a{index}"} for index in range(5)],
        tool_results=[],
        max_chars=500,
        max_messages=2,
    )
    assert assistants["dropped_messages"] == 3
    assert [message["content"] for message in assistants["messages"] if not message.get("_context_note")] == ["a3", "a4"]

    tail = context.fit_context(
        system="s",
        messages=[{"role": "user", "content": LATEST}, {"role": "assistant", "content": "尾" * 40}],
        tool_results=[],
        max_chars=500,
        max_messages=1,
    )
    assert tail["dropped_messages"] == 1
    assert tail["messages"][-1]["content"] == LATEST

    blank = context.fit_context(system=None, messages=None, tool_results=None, max_chars=100, max_messages=2)
    assert blank["truncated"] is False
    assert blank["system"] == ""
    assert blank["messages"] == []


def test_tiny_tool_is_not_rewritten_when_something_else_is_over():
    tiny = {"name": "list_datasets", "ok": True, "data": {"n": 1}, "evidence": {"fingerprint": "tiny"}}
    huge = {"name": "query_stats", "ok": True, "data": "大" * 500, "evidence": {"fingerprint": KEEP_FP}}
    fitted = context.fit_context(
        system="s",
        messages=[{"role": "user", "content": "问"}],
        tool_results=[tiny, huge],
        max_chars=500,
        max_messages=4,
    )
    assert fitted["shrunk_tool_results"] >= 1
    assert fitted["kept_chars"] <= fitted["max_chars"]


def test_list_messages_keeps_the_newest_page(monkeypatch, tmp_path):
    monkeypatch.setattr(obs, "DB_PATH", tmp_path / "kernel.db")
    obs.init_db()
    sid = session.create_session()["id"]
    for index in range(60):
        session.add_message(sid, "user", f"m{index}")
    rows = session.list_messages(sid)
    assert len(rows) == 50
    assert rows[0]["content"] == "m10"
    assert rows[-1]["content"] == "m59"
    assert rows[0]["created_at"] <= rows[-1]["created_at"]
    assert session.list_messages(sid, limit=0)[-1]["content"] == "m59"


def test_shrink_each_blob_shape():
    shapes = [
        {
            "name": "describe_dataset",
            "ok": True,
            "data": {"summary": "概" * 200, "row_count": 3, "flag": True},
            "evidence": {"fingerprint": "plain"},
        },
        {
            "name": "sample_records",
            "ok": True,
            "data": ["样本" * 200, "b" * 50],
            "evidence": {"fingerprint": "list"},
        },
        {
            "name": "list_datasets",
            "ok": True,
            "data": 5,
            "arguments": {"blob": "参" * 400},
            "evidence": {"fingerprint": "num"},
        },
        {
            "name": "search_records",
            "ok": True,
            "data": None,
            "error": "错" * 300,
            "evidence": {"fingerprint": "err"},
        },
    ]
    for tool in shapes:
        fitted = context.fit_context(
            system="s",
            messages=[{"role": "user", "content": "问"}],
            tool_results=[tool],
            max_chars=220,
            max_messages=4,
        )
        assert fitted["shrunk_tool_results"] == 1
        assert fitted["tool_results"][0]["evidence"]["fingerprint"] == tool["evidence"]["fingerprint"]
        assert tool.get("data") == fitted["tool_results"][0]["data"] or fitted["tool_results"][0].get("data_truncated") is True

    stubborn = {
        "name": "list_datasets",
        "ok": True,
        "data": {"n": 1},
        "evidence": {"fingerprint": "tiny", "blob": "Z" * 800},
    }
    reverted = context.fit_context(
        system="s",
        messages=[{"role": "user", "content": "问"}],
        tool_results=[stubborn],
        max_chars=200,
        max_messages=4,
    )
    assert reverted["shrunk_tool_results"] == 0
    assert reverted["truncated"] is True
    assert reverted["tool_results"][0]["data"] == {"n": 1}

    tiny_budget = context.fit_context(
        system="",
        messages=[{"role": "user", "content": "HELLO"}],
        tool_results=[],
        max_chars=1,
        max_messages=2,
    )
    assert tiny_budget["messages"][-1]["content"] == "…"
    assert tiny_budget["clipped_user"] is True


def test_live_retry_keeps_truncation_ahead_of_retry(monkeypatch):
    monkeypatch.setattr(model_broker, "has_llm", lambda: True)
    monkeypatch.setenv("MODEL_API_KEY", "test-key")
    monkeypatch.setenv("AGENT_CONTEXT_MAX_CHARS", "500")
    monkeypatch.setattr(model_broker.time, "sleep", lambda _seconds: None)
    calls = {"n": 0}
    captured: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content.decode()))
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(500, json={"error": "nope"})
        return httpx.Response(200, json={"choices": [{"message": {"content": "第二次成功"}}]})

    _install_transport(monkeypatch, handler)
    decision = model_broker.decide(
        system="sys",
        messages=[{"role": "user", "content": "OLD" * 800}, {"role": "user", "content": LATEST}],
        allowed_tools=["list_datasets"],
        tool_results=[],
        bindings=_bindings(),
    )
    kinds = [event["type"] for event in decision["_broker_events"]]
    assert kinds[0] == "context_truncated"
    assert "live_retry" in kinds
    assert decision["type"] == "final"
    assert decision["content"] == "第二次成功"
    assert LATEST in json.dumps(captured[-1]["messages"], ensure_ascii=False)
    assert "OLDOLD" not in json.dumps(captured[-1]["messages"], ensure_ascii=False)
