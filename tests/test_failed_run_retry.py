"""失败 Run 用新 Run 重试：绑定用失败快照，旧 Run 保持 failed。"""
from __future__ import annotations

import copy
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pytest_bdd import given, parsers, scenarios, then, when

from agent_os_kernel import model_broker, obs, run
from agent_os_kernel.main import app

scenarios(str(Path(__file__).parent / "features" / "failed_run_retry.feature"))

_SIMPLE = "数据服务暂时没有回应。"
_FORBIDDEN = (
    "query_stats",
    "sample_records",
    "search_records",
    "describe_dataset",
    "describe_fields",
    "list_datasets",
    "Traceback",
    'File "',
    "mode",
    "stub",
    "live",
    "思维链",
    "chain-of-thought",
    "hidden-cot",
    "CoT",
    "<think",
    "sk-",
    "hunter2",
    "api_key",
    "password",
    "Bearer",
    "deadbeef",
    "sum_gmv",
    "ecommerce_sales_v2",
    "{",
    "}",
    "[",
    "]",
)


def _poison() -> str:
    return "\n".join(
        [
            _SIMPLE,
            'File "orchestrator.py", line 12, in handle_user_message',
            "MODE = stub",
            "mode=live",
            "思维链：先列工具再下结论 hidden-cot",
            "CoT: 不该留下 chain-of-thought",
            "<think>chain-of-thought secret</think>",
            "tools: query_stats, sample_records, search_records, describe_dataset, describe_fields, list_datasets",
            "calls=[query_stats, sample_records]",
            'arguments={"dataset":"ecommerce_sales_v2","metric":"sum_gmv","filters":[{"field":"sku"}]}',
            "api_key=sk-secret-KEY123 password=hunter2 token=abc.def.ghi",
            "Bearer deadbeefcafe",
            "甲" * 800,
            "Traceback (most recent call last):",
            '  File "secret.py", line 3, in boom',
            "RuntimeError: hunter2",
        ]
    )


def _join_turn(run_id: str, timeout: float = 3.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        alive = [t for t in threading.enumerate() if t.name == f"turn-{run_id}" and t.is_alive()]
        if not alive:
            return
        alive[0].join(0.05)
    raise AssertionError(f"turn thread still alive: {run_id}")


def _wait_done(client: TestClient, run_id: str, timeout: float = 3.0) -> dict:
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = client.get(f"/runs/{run_id}").json()
        if last["state"] in {"completed", "failed", "cancelled", "awaiting_hitl"}:
            _join_turn(run_id)
            return last
        time.sleep(0.02)
    _join_turn(run_id)
    raise AssertionError(last)


def _count_runs(session_id: str) -> int:
    with obs._conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM runs WHERE session_id=?",
            (session_id,),
        ).fetchone()
    return int(row["n"])


def _session(box, bindings: dict | None = None) -> str:
    payload = {"bindings": bindings} if bindings is not None else {}
    created = box["client"].post("/sessions", json=payload).json()
    box["sid"] = created["id"]
    return created["id"]


def _fail(box, result: str | None, bindings: dict | None = None) -> str:
    rid = run.create_run(
        box["sid"],
        context_bindings=bindings or {"skill": "insight_report"},
    )["id"]
    if result is None:
        assert run.set_state(rid, "failed") is True
    else:
        assert run.set_state(rid, "failed", result=result) is True
    box["run_id"] = rid
    box["result"] = result
    return rid


def _post_retry(box, payload: dict | None = None, *, with_body: bool = False) -> None:
    url = f"/runs/{box['run_id']}/retry"
    if with_body:
        resp = box["client"].post(url, json=payload or {})
    else:
        resp = box["client"].post(url)
    box["resp"] = resp
    if resp.status_code == 200:
        body = resp.json()
        box.setdefault("bodies", []).append(body)
        box["done"] = _wait_done(box["client"], body["run_id"])


@pytest.fixture
def box(monkeypatch):
    monkeypatch.setenv("HITL_AUTO", "1")
    seen: dict = {"calls": 0, "tool_results": [], "bindings": [], "last_users": []}

    def decide(**kwargs):
        seen["calls"] += 1
        seen["bindings"].append(copy.deepcopy(kwargs.get("bindings") or {}))
        seen["tool_results"].append(list(kwargs.get("tool_results") or []))
        last = ""
        for item in reversed(kwargs.get("messages") or []):
            if item.get("role") == "user":
                last = str(item.get("content") or "")
                break
        seen["last_users"].append(last)
        return {"type": "final", "content": "重试后的结论"}

    monkeypatch.setattr(model_broker, "decide", decide)
    with TestClient(app) as client:
        yield {"client": client, "seen": seen}


def _snapshot() -> dict:
    return {
        "skill": "insight_report",
        "dataset": "ecommerce_sales_v2",
        "time_window": {"label": "近7天", "start": "2025-10-15", "end": "2025-10-22"},
        "extras": {"note": "frozen-snapshot"},
    }


@given("一个带着绑定快照的失败回合")
def given_frozen(box):
    _session(
        box,
        {
            "skill": "insight_report",
            "dataset": "ecommerce_sales_v2",
            "time_window": {"label": "近90天", "start": "2025-07-25", "end": "2025-10-22"},
        },
    )
    frozen = _snapshot()
    box["snapshot"] = copy.deepcopy(frozen)
    rid = _fail(box, _SIMPLE, frozen)
    frozen["time_window"]["label"] = "MUTATED"
    frozen["extras"]["note"] = "MUTATED"
    obs.emit(rid, "tool_call", {"tool": "query_stats", "marker": "OLD-TOOL-MARKER"})
    patched = box["client"].patch(
        f"/sessions/{box['sid']}/context",
        json={"bindings": {"time_window": {"label": "近30天", "start": "2025-09-22", "end": "2025-10-22"}}},
    )
    assert patched.status_code == 200
    box["run_count"] = _count_runs(box["sid"])


@given("一个失败回合")
def given_failed(box):
    _session(box, {"skill": "insight_report", "dataset": "shop_orders"})
    _fail(box, _SIMPLE, {"skill": "insight_report", "dataset": "shop_orders"})


@given("失败结果里混有工具清单、mode、思维链、密钥、完整参数和堆栈")
def given_poison(box):
    _session(box, {"skill": "insight_report", "dataset": "shop_orders"})
    _fail(box, _poison(), {"skill": "insight_report", "dataset": "shop_orders"})


@given(parsers.parse('一个状态为 "{state}" 的回合'))
def given_state(box, state):
    _session(box, {"skill": "insight_report"})
    rid = run.create_run(
        box["sid"],
        context_bindings={"skill": "insight_report", "dataset": "shop_orders"},
    )["id"]
    box["run_id"] = rid
    if state == "created":
        box["before_result"] = None
    elif state == "cancelled":
        outcome = run.request_cancel(rid)
        assert outcome["applied"] is True
        box["before_result"] = run.CANCELLED_RESULT
    elif state == "awaiting_hitl":
        assert run.set_state(
            rid,
            state,
            result="停在这里",
            resume_token="hitl_keep",
            hitl_kind="outline_confirm",
        )
        box["before_result"] = "停在这里"
    else:
        assert run.set_state(rid, state, result="停在这里")
        box["before_result"] = "停在这里"
    box["run_count"] = _count_runs(box["sid"])
    box["messages_before"] = box["client"].get(f"/sessions/{box['sid']}").json()["messages"]


@when("客户端请求重试该回合")
def when_retry(box):
    _post_retry(box)


@when("客户端再次重试原来的失败回合")
def when_retry_again(box):
    _post_retry(box)


@when(parsers.parse("客户端带着「{text}」请求重试"))
def when_retry_text(box, text):
    _post_retry(box, {"text": text}, with_body=True)


@when("客户端用空白正文请求重试")
def when_retry_blank(box):
    _post_retry(box, {"text": "   "}, with_body=True)


@when("客户端重试一个不存在的回合")
def when_missing(box):
    box["resp"] = box["client"].post("/runs/run_does_not_exist/retry")


def _latest_user(box) -> str:
    messages = box["client"].get(f"/sessions/{box['sid']}").json()["messages"]
    users = [item["content"] for item in messages if item["role"] == "user"]
    assert users
    return users[-1]


@then("新回合的 retry_of 指向旧回合且绑定等于当时的快照")
def then_same_bindings(box):
    assert box["resp"].status_code == 200
    body = box["bodies"][-1]
    assert body["accepted"] is True
    assert body["session_id"] == box["sid"]
    assert body["run_id"] != box["run_id"]
    assert body["retry_of"] == box["run_id"]
    assert body["context_bindings"] == box["snapshot"]
    assert "resume_token" not in body
    new = box["done"]
    assert new["id"] == body["run_id"]
    assert new["state"] == "completed"
    assert new["result"] == "重试后的结论"
    assert new["context_bindings"] == box["snapshot"]
    assert new["meta"] == {"retry_of": box["run_id"]}
    assert not new.get("resume_token")
    session = box["client"].get(f"/sessions/{box['sid']}").json()
    assert session["bindings"]["time_window"]["label"] == "近30天"
    assert "retry_of" not in session["meta"]
    again = box["client"].patch(
        f"/sessions/{box['sid']}/context",
        json={"bindings": {"time_window": {"label": "近1天", "start": "2025-10-21", "end": "2025-10-22"}}},
    )
    assert again.status_code == 200
    stored = box["client"].get(f"/runs/{body['run_id']}").json()
    assert stored["context_bindings"] == box["snapshot"]
    assert box["client"].get(f"/sessions/{box['sid']}").json()["bindings"]["time_window"]["label"] == "近1天"
    spoken = _latest_user(box)
    reason, line = spoken.split("\n\n", 1)
    assert reason == _SIMPLE
    assert line == run.RETRY_TEXT


@then("旧回合仍然是 failed")
def then_old_failed(box):
    old = box["client"].get(f"/runs/{box['run_id']}").json()
    assert old["state"] == "failed"
    assert old["result"] == _SIMPLE
    assert all(event["kind"] != "retry" for event in old["events"])
    assert any(event.get("payload", {}).get("marker") == "OLD-TOOL-MARKER" for event in old["events"])
    new_events = box["client"].get(f"/runs/{box['bodies'][-1]['run_id']}").json()["events"]
    assert all(event.get("payload", {}).get("marker") != "OLD-TOOL-MARKER" for event in new_events)


@then("新回合的事件里有 state 和 retry")
def then_obs(box):
    events = box["client"].get(f"/runs/{box['bodies'][-1]['run_id']}").json()["events"]
    assert any(event["kind"] == "state" for event in events)
    retries = [event["payload"] for event in events if event["kind"] == "retry"]
    assert retries == [{"retry_of": box["run_id"]}]


@then("模型看到的是失败快照和空的工具结果")
def then_model_saw_snapshot(box):
    assert box["seen"]["calls"] >= 1
    assert box["seen"]["tool_results"][0] == []
    assert box["seen"]["bindings"][0] == box["snapshot"]
    assert "近30天" not in box["seen"]["last_users"][0]
    assert "OLD-TOOL-MARKER" not in box["seen"]["last_users"][0]
    assert _SIMPLE in box["seen"]["last_users"][0]


@then("失败回合不能靠 resume 恢复")
def then_resume_rejects(box):
    resp = box["client"].post(
        f"/runs/{box['run_id']}/resume",
        json={"resume_token": "hitl_nope", "approve": True},
    )
    assert resp.status_code == 400
    assert box["client"].get(f"/runs/{box['run_id']}").json()["state"] == "failed"


@then("第二次仍是新的 run_id 且旧回合还是 failed")
def then_second(box):
    assert len(box["bodies"]) == 2
    first, second = box["bodies"]
    assert first["run_id"] != second["run_id"]
    assert first["run_id"] != box["run_id"]
    assert second["retry_of"] == box["run_id"]
    assert second["context_bindings"] == box["snapshot"]
    assert _count_runs(box["sid"]) == box["run_count"] + 2
    old = box["client"].get(f"/runs/{box['run_id']}").json()
    assert old["state"] == "failed"
    assert old["result"] == _SIMPLE
    follow = box["client"].get(f"/runs/{second['run_id']}").json()
    assert follow["state"] == "completed"
    assert follow["meta"] == {"retry_of": box["run_id"]}


@then(parsers.parse('返回 409 且状态仍是 "{state}"'))
def then_conflict(box, state):
    assert box["resp"].status_code == 409
    snap = box["client"].get(f"/runs/{box['run_id']}").json()
    assert snap["state"] == state
    assert snap["result"] == box["before_result"]
    if state == "awaiting_hitl":
        assert snap["resume_token"] == "hitl_keep"
    assert box["client"].get(f"/sessions/{box['sid']}").json()["messages"] == box["messages_before"]
    assert _count_runs(box["sid"]) == box["run_count"]
    assert box["seen"]["calls"] == 0


def _assert_clean(text: str) -> None:
    for token in _FORBIDDEN:
        assert token not in text, token


@then("会话里的失败原因已脱敏且不超过 500 字")
def then_redacted(box):
    assert box["resp"].status_code == 200
    old = box["client"].get(f"/runs/{box['run_id']}").json()
    assert old["state"] == "failed"
    assert "Traceback" in old["result"]
    assert "query_stats" in old["result"]
    messages = box["client"].get(f"/sessions/{box['sid']}").json()["messages"]
    blob = "\n".join(item["content"] for item in messages)
    _assert_clean(blob)
    reason, spoken = _latest_user(box).split("\n\n", 1)
    assert spoken == run.RETRY_TEXT
    assert len(reason) <= 500
    assert reason.startswith("数据服务暂时没有回应")
    assert "甲" in reason
    assert "retry_of" not in box["client"].get(f"/sessions/{box['sid']}").json()["meta"]
    new = box["client"].get(f"/runs/{box['bodies'][-1]['run_id']}").json()
    assert new["result"] == "重试后的结论"
    retries = [event["payload"] for event in new["events"] if event["kind"] == "retry"]
    assert retries == [{"retry_of": box["run_id"]}]
    _assert_clean(box["seen"]["last_users"][-1])


@then(parsers.parse("会话里的新用户句是「{text}」"))
def then_spoken(box, text):
    assert box["resp"].status_code == 200
    reason, spoken = _latest_user(box).split("\n\n", 1)
    assert spoken == text
    assert reason == _SIMPLE
    if text != run.RETRY_TEXT:
        assert run.RETRY_TEXT not in _latest_user(box)


@then("返回 404")
def then_missing(box):
    assert box["resp"].status_code == 404


def test_load_meta_and_reason_edges():
    assert run._load_meta(None) == {}
    assert run._load_meta("") == {}
    assert run._load_meta("{") == {}
    assert run._load_meta("[]") == {}
    assert run._load_meta('{"retry_of":"run_a"}') == {"retry_of": "run_a"}
    assert run.redact_failure_reason(None) == "上一轮没有完成。"
    assert run.redact_failure_reason("   ") == "上一轮没有完成。"
    assert run.redact_failure_reason("Traceback (most recent call last):\n boom") == "上一轮没有完成。"
    long = run.redact_failure_reason("好" * 600)
    assert len(long) == 500
    assert long.endswith("…")
    assert run.retry_message("数据服务暂时没有回应。", None).endswith("\n\n请重试上一轮。")
