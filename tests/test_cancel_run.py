"""Batch D：进行中的 Run 可以取消，收成 cancelled，人话收尾。"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pytest_bdd import given, scenarios, then, when

from agent_os_kernel import model_broker, orchestrator, run
from agent_os_kernel.main import app
from agent_os_kernel.tool_gateway import ToolGateway

scenarios(str(Path(__file__).parent / "features" / "cancel_run.feature"))

COT = "思维链不该给用户看 HIDDEN-COT-9f3a"
STOPPED = "已停止"
_TERMINAL = {"completed", "failed", "cancelled"}


def _join_turn(run_id: str, timeout: float = 3.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        alive = [t for t in threading.enumerate() if t.name == f"turn-{run_id}" and t.is_alive()]
        if not alive:
            return
        alive[0].join(0.05)
    raise AssertionError(f"turn thread still alive: {run_id}")


def _events(client: TestClient, run_id: str) -> list[dict]:
    page = client.get(f"/runs/{run_id}/events").json()
    assert page["run_id"] == run_id
    return page["events"]


def _blob(client: TestClient, run_id: str) -> str:
    snap = client.get(f"/runs/{run_id}").json()
    return json.dumps(snap, ensure_ascii=False)


@pytest.fixture
def box(monkeypatch):
    held = {
        "monkeypatch": monkeypatch,
        "gate": threading.Event(),
        "entered": threading.Event(),
        "mode": "tool",
        "decisions": 0,
        "tool_calls": 0,
        "second": False,
        "cot": COT,
    }

    def decide(**kwargs):
        del kwargs
        held["decisions"] += 1
        mode = held["mode"]
        if mode in ("decide", "boom") and held["decisions"] == 1:
            held["entered"].set()
            if not held["gate"].wait(8):
                raise TimeoutError("decide gate")
        if mode == "boom" and held["decisions"] == 1:
            raise RuntimeError(held["cot"])
        if mode == "quick":
            return {"type": "final", "content": "一份结论"}
        if held["decisions"] == 1:
            return {
                "type": "tool_calls",
                "calls": [{"name": "query_stats", "arguments": {"metric": "sum_gmv"}}],
            }
        held["second"] = True
        return {"type": "final", "content": held["cot"]}

    def fake_call(self, name, arguments=None, bindings=None):
        del self, arguments, bindings
        held["tool_calls"] += 1
        if held["mode"] == "tool":
            held["entered"].set()
            if not held["gate"].wait(8):
                raise TimeoutError("tool gate")
        return {
            "data": {"rows": [{"key": "江苏", "value": 1}]},
            "evidence": {"fingerprint": "fp-cancel"},
        }

    monkeypatch.setattr(model_broker, "decide", decide)
    monkeypatch.setattr(ToolGateway, "call", fake_call)
    with TestClient(app) as client:
        held["client"] = client
        yield held
        held["gate"].set()


def _new_session(box) -> None:
    created = box["client"].post("/sessions", json={}).json()
    box["sid"] = created["id"]


def _post_and_wait(box) -> None:
    started = time.perf_counter()
    resp = box["client"].post(
        f"/sessions/{box['sid']}/messages",
        json={"text": "请写一份洞察"},
    )
    box["elapsed"] = time.perf_counter() - started
    assert resp.status_code == 200
    box["accepted"] = resp.json()
    box["run_id"] = box["accepted"]["run_id"]
    assert box["entered"].wait(3)


def _snapshot(box) -> dict:
    return box["client"].get(f"/runs/{box['run_id']}").json()


@given("一个会停在第一次工具调用的新会话")
def given_tool(box):
    box["mode"] = "tool"
    _new_session(box)


@given("一个会停在第一次决定的新会话")
def given_decide(box):
    box["mode"] = "decide"
    _new_session(box)


@given("一个会在第一次决定后抛错的新会话")
def given_boom(box):
    box["mode"] = "boom"
    _new_session(box)


@given("一个会停在编排入口的新会话")
def given_entry(box):
    box["mode"] = "entry"
    real = orchestrator.handle_user_message

    def wrapped(session_id: str, run_id: str, text: str, resume: bool = False) -> dict:
        box["entered"].set()
        if not box["gate"].wait(8):
            raise TimeoutError("entry gate")
        return real(session_id, run_id, text, resume=resume)

    box["monkeypatch"].setattr(orchestrator, "handle_user_message", wrapped)
    _new_session(box)


@given("一个停在大纲确认的新会话")
def given_hitl(box, monkeypatch):
    monkeypatch.setenv("HITL_AUTO", "0")
    box["mode"] = "hitl"
    _new_session(box)
    accepted = box["client"].post(
        f"/sessions/{box['sid']}/messages",
        json={"text": "请基于电商销量数据写一份简短洞察报告"},
    ).json()
    assert accepted["accepted"] is True
    box["run_id"] = accepted["run_id"]
    deadline = time.time() + 3
    snap = None
    while time.time() < deadline:
        snap = _snapshot(box)
        if snap["state"] == "awaiting_hitl":
            break
        time.sleep(0.02)
    assert snap is not None and snap["state"] == "awaiting_hitl"
    box["token"] = snap["resume_token"]
    box["hitl_prompt"] = snap["result"]
    assert box["hitl_prompt"]
    assert box["hitl_prompt"] != STOPPED
    _join_turn(box["run_id"])


@given("一个已经完成的回合")
def given_done(box):
    box["mode"] = "quick"
    _new_session(box)
    resp = box["client"].post(
        f"/sessions/{box['sid']}/messages",
        json={"text": "请写一份洞察"},
    )
    assert resp.status_code == 200
    box["run_id"] = resp.json()["run_id"]
    deadline = time.time() + 3
    snap = None
    while time.time() < deadline:
        snap = _snapshot(box)
        if snap["state"] == "completed":
            break
        time.sleep(0.02)
    assert snap is not None and snap["state"] == "completed"
    assert snap["result"] == "一份结论"
    _join_turn(box["run_id"])


@when("用户发消息并等到工具进入闸门")
@when("用户发消息并等到决定进入闸门")
@when("用户发消息并等到编排入口")
def send_held(box):
    _post_and_wait(box)


@when("用户取消这个回合")
@when("用户再次取消这个回合")
def cancel(box):
    box["before"] = _snapshot(box)
    box["cancel_resp"] = box["client"].post(f"/runs/{box['run_id']}/cancel")


@when("用户取消一个不存在的回合")
def cancel_missing(box):
    box["cancel_resp"] = box["client"].post("/runs/run_missing/cancel")


@when("放开被挡住的工具调用")
@when("放开被挡住的决定")
@when("放开编排入口")
def release(box):
    box["gate"].set()


@when("用户拒绝这份确认")
def reject(box):
    resp = box["client"].post(
        f"/runs/{box['run_id']}/resume",
        json={"resume_token": box["token"], "approve": False},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "failed"
    assert body["markdown"] == "已拒绝"
    snap = _snapshot(box)
    assert snap["state"] == "failed"
    assert snap["result"] == "已拒绝"


@then("回合仍在进行")
def still_running(box):
    body = box["accepted"]
    assert box["elapsed"] < 1.0
    assert body["accepted"] is True
    assert body["run_id"].startswith("run_")
    assert body["status"] not in _TERMINAL
    snap = _snapshot(box)
    assert snap["state"] not in _TERMINAL


@then("取消被接受且状态已是已停止")
def cancel_accepted(box):
    resp = box["cancel_resp"]
    assert resp.status_code == 200
    body = resp.json()
    assert body["run_id"] == box["run_id"]
    assert body["status"] == "cancelled"
    assert body["accepted"] is True
    assert body["markdown"] == STOPPED
    assert body["result"] == STOPPED
    assert COT not in json.dumps(body, ensure_ascii=False)
    snap = _snapshot(box)
    assert snap["state"] == "cancelled"
    assert snap["result"] == STOPPED
    page = box["client"].get(f"/runs/{box['run_id']}/events").json()
    assert page["state"] == "cancelled"
    assert page["result"] == STOPPED


@then("取消被拒绝且回合维持原状")
def cancel_conflict(box):
    resp = box["cancel_resp"]
    assert resp.status_code == 409
    assert box["before"]["state"] in resp.json()["detail"]
    snap = _snapshot(box)
    assert snap["state"] == box["before"]["state"]
    assert snap["result"] == box["before"]["result"]
    assert [e["id"] for e in snap["events"]] == [e["id"] for e in box["before"]["events"]]


@then("取消接口返回 404")
def cancel_missing_status(box):
    assert box["cancel_resp"].status_code == 404


@then("这批工具事件还在且没有思维链")
def tools_kept(box):
    deadline = time.time() + 3
    events = []
    while time.time() < deadline:
        events = _events(box["client"], box["run_id"])
        if any(e["kind"] == "tool_call" for e in events):
            break
        time.sleep(0.02)
    assert any(e["kind"] == "tool_call" for e in events)
    blob = json.dumps(events, ensure_ascii=False)
    assert COT not in blob
    assert "HIDDEN-COT" not in blob
    assert box["tool_calls"] == 1


@then("编排没有进入下一步")
def no_next_step(box):
    _join_turn(box["run_id"])
    assert box["decisions"] == 1
    assert box["second"] is False
    assert box["tool_calls"] == 1


@then("没有执行工具")
def no_tools(box):
    _join_turn(box["run_id"])
    assert box["tool_calls"] == 0
    events = _events(box["client"], box["run_id"])
    assert not any(e["kind"] == "tool_call" for e in events)


@then("没有调用模型决定")
def no_decide(box):
    _join_turn(box["run_id"])
    assert box["decisions"] == 0


@then("编排线程已经退出")
def thread_gone(box):
    _join_turn(box["run_id"])


@then("轮询看到已停止且正文只有人话")
def human_line(box):
    _join_turn(box["run_id"])
    snap = _snapshot(box)
    assert snap["state"] == "cancelled"
    assert snap["result"] == STOPPED
    assert len(snap["result"]) < 40
    page = box["client"].get(f"/runs/{box['run_id']}/events").json()
    assert page["state"] == "cancelled"
    assert page["result"] == STOPPED
    blob = _blob(box["client"], box["run_id"])
    assert COT not in blob
    assert "HIDDEN-COT" not in blob
    assert "编排失败" not in (snap["result"] or "")
    msgs = box["client"].get(f"/sessions/{box['sid']}").json()["messages"]
    assert COT not in json.dumps(msgs, ensure_ascii=False)


@then("会话里只留下一句已停止")
def one_stop_line(box):
    deadline = time.time() + 2
    last = None
    while time.time() < deadline:
        last = box["client"].get(f"/sessions/{box['sid']}").json()["messages"]
        stops = [m for m in last if m["role"] == "assistant" and m["content"] == STOPPED]
        if len(stops) == 1:
            assert COT not in json.dumps(last, ensure_ascii=False)
            return
        time.sleep(0.02)
    raise AssertionError(last)


@then("迟到的完成写不回这个回合")
def late_complete_lost(box):
    _join_turn(box["run_id"])
    before = _events(box["client"], box["run_id"])
    applied = run.set_state(box["run_id"], "completed", result=COT)
    assert applied is False
    snap = _snapshot(box)
    assert snap["state"] == "cancelled"
    assert snap["result"] == STOPPED
    after = _events(box["client"], box["run_id"])
    assert [e["id"] for e in after] == [e["id"] for e in before]
    assert COT not in json.dumps(after, ensure_ascii=False)


@then("回合没有变成失败")
def not_failed(box):
    snap = _snapshot(box)
    assert snap["state"] == "cancelled"
    assert snap["result"] == STOPPED
    assert "编排失败" not in json.dumps(snap["events"], ensure_ascii=False)
    blob = json.dumps(snap["events"], ensure_ascii=False)
    assert COT not in blob


@then("回合已是拒绝")
def rejected(box):
    snap = _snapshot(box)
    assert snap["state"] == "failed"
    assert snap["result"] == "已拒绝"


@then("确认提示不再是这个回合的结果")
def prompt_replaced(box):
    snap = _snapshot(box)
    assert snap["state"] == "cancelled"
    assert snap["result"] == STOPPED
    assert snap["result"] != box["hitl_prompt"]
    assert box["hitl_prompt"] not in (snap["result"] or "")
