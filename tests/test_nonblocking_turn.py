"""Batch C：回合 POST 立即返回，events 用 after_id 增量拉取。"""
from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pytest_bdd import given, scenarios, then, when

from agent_os_kernel import obs, orchestrator, run
from agent_os_kernel.main import app

scenarios(str(Path(__file__).parent / "features" / "nonblocking_turn.feature"))

_TERMINAL = {"completed", "failed", "awaiting_hitl"}


def _poll(client: TestClient, run_id: str, timeout: float = 5.0) -> dict:
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        snap = client.get(f"/runs/{run_id}").json()
        last = snap
        if snap.get("state") in _TERMINAL:
            return snap
        time.sleep(0.02)
    raise AssertionError(last)


def _hold(box, markdown: str):
    def _inner(session_id: str, run_id: str, text: str, resume: bool = False) -> dict:
        del session_id, text, resume
        obs.emit(run_id, "model_step", {"step": 0, "type": "tool_calls", "mode": "stub", "n_calls": 0})
        box["entered"].set()
        if not box["gate"].wait(8):
            run.set_state(run_id, "failed", result="gate timeout")
            return {"status": "failed", "markdown": "gate timeout"}
        run.set_state(run_id, "completed", result=markdown)
        return {"status": "completed", "markdown": markdown}

    return _inner


@pytest.fixture
def turnbox(monkeypatch):
    box = {
        "monkeypatch": monkeypatch,
        "gate": threading.Event(),
        "entered": threading.Event(),
        "expect_md": "晚到的结论",
    }
    with TestClient(app) as client:
        box["client"] = client
        yield box
        box["gate"].set()


@given("一个可编排的新会话")
def given_session(turnbox):
    created = turnbox["client"].post("/sessions", json={}).json()
    turnbox["sid"] = created["id"]


@given("一个停在确认的回合")
def given_paused(turnbox, monkeypatch):
    monkeypatch.setenv("HITL_AUTO", "0")
    created = turnbox["client"].post("/sessions", json={}).json()
    turnbox["sid"] = created["id"]
    accepted = turnbox["client"].post(
        f"/sessions/{created['id']}/messages",
        json={"text": "请基于电商销量数据写一份简短洞察报告"},
    ).json()
    assert accepted["accepted"] is True
    snap = _poll(turnbox["client"], accepted["run_id"])
    assert snap["state"] == "awaiting_hitl"
    assert snap.get("result")
    turnbox["run_id"] = accepted["run_id"]
    turnbox["token"] = snap["resume_token"]
    page = turnbox["client"].get(f"/runs/{accepted['run_id']}/events").json()
    assert page["state"] == "awaiting_hitl"
    assert page["hitl"]["resume_token"] == snap["resume_token"]
    assert page["result"] == snap["result"]
    deadline = time.time() + 2
    while time.time() < deadline:
        msgs = turnbox["client"].get(f"/sessions/{created['id']}").json()["messages"]
        if any(m["role"] == "assistant" and m["content"] == snap["result"] for m in msgs):
            return
        time.sleep(0.02)
    raise AssertionError(snap["result"])


@when("用户发消息且编排停在闸门上")
def send_held(turnbox):
    turnbox["expect_md"] = "晚到的结论"
    turnbox["monkeypatch"].setattr(
        orchestrator,
        "handle_user_message",
        _hold(turnbox, turnbox["expect_md"]),
    )
    started = time.perf_counter()
    resp = turnbox["client"].post(
        f"/sessions/{turnbox['sid']}/messages",
        json={"text": "请写一份洞察"},
    )
    turnbox["elapsed"] = time.perf_counter() - started
    assert resp.status_code == 200
    turnbox["accepted"] = resp.json()
    assert turnbox["entered"].wait(3)


@when("用户发消息且编排直接抛错")
def send_boom(turnbox):
    def boom(session_id: str, run_id: str, text: str, resume: bool = False) -> dict:
        del session_id, text, resume
        raise RuntimeError("turn-exploded")

    turnbox["monkeypatch"].setattr(orchestrator, "handle_user_message", boom)
    resp = turnbox["client"].post(
        f"/sessions/{turnbox['sid']}/messages",
        json={"text": "请写一份洞察"},
    )
    assert resp.status_code == 200
    turnbox["accepted"] = resp.json()


@when("后台在闸门内追加两条事件")
def append_two(turnbox):
    client = turnbox["client"]
    rid = turnbox["accepted"]["run_id"]
    before = client.get(f"/runs/{rid}/events").json()["events"]
    assert before
    turnbox["cursor"] = before[-1]["id"]
    obs.emit(rid, "model_step", {"step": 1, "type": "tool_calls", "mode": "stub", "n_calls": 0})
    obs.emit(rid, "tool_call", {"ok": True, "name": "query_stats"})


@when("查询不存在的事件流")
def missing_events(turnbox):
    turnbox["missing"] = turnbox["client"].get("/runs/run_missing/events")


@when("用户确认且续跑停在闸门上")
def resume_held(turnbox):
    turnbox["expect_md"] = "续跑结论"
    turnbox["entered"].clear()

    def gated(run_id: str, text: str | None = None) -> dict:
        del text
        turnbox["entered"].set()
        if not turnbox["gate"].wait(8):
            run.set_state(run_id, "failed", result="gate timeout")
            return {"status": "failed", "markdown": "gate timeout"}
        run.set_state(run_id, "completed", result=turnbox["expect_md"])
        return {"status": "completed", "markdown": turnbox["expect_md"]}

    turnbox["monkeypatch"].setattr(orchestrator, "resume_after_hitl", gated)
    started = time.perf_counter()
    resp = turnbox["client"].post(
        f"/runs/{turnbox['run_id']}/resume",
        json={"resume_token": turnbox["token"], "approve": True, "text": "请继续"},
    )
    turnbox["elapsed"] = time.perf_counter() - started
    assert resp.status_code == 200
    turnbox["accepted"] = resp.json()
    assert turnbox["entered"].wait(3)


@when("放开编排闸门")
def release_gate(turnbox):
    turnbox["gate"].set()


@when("用户拒绝确认")
def reject(turnbox):
    turnbox["rejected"] = turnbox["client"].post(
        f"/runs/{turnbox['run_id']}/resume",
        json={"resume_token": turnbox["token"], "approve": False},
    )


@then("接口已经返回 run_id 且回合还没完成")
def accepted_fast(turnbox):
    body = turnbox["accepted"]
    assert turnbox["elapsed"] < 1.0
    assert body["run_id"].startswith("run_")
    assert body["accepted"] is True
    assert "markdown" not in body
    assert body["status"] not in _TERMINAL
    snap = turnbox["client"].get(f"/runs/{body['run_id']}").json()
    assert snap["state"] not in ("completed", "failed")
    msgs = turnbox["client"].get(f"/sessions/{turnbox['sid']}").json()["messages"]
    assert any(m["role"] == "user" and m["content"] == "请写一份洞察" for m in msgs)
    assert not any(m["role"] == "assistant" for m in msgs)
    whole = turnbox["client"].get(f"/runs/{body['run_id']}/events").json()
    assert whole["events"]
    assert all(e["id"] > 0 for e in whole["events"])


@then("用 after_id 只看到新事件")
def only_new(turnbox):
    client = turnbox["client"]
    rid = turnbox["accepted"]["run_id"]
    cursor = turnbox["cursor"]
    page = client.get(f"/runs/{rid}/events", params={"after_id": cursor, "limit": 1}).json()
    assert page["state"] not in ("completed", "failed")
    assert len(page["events"]) == 1
    assert page["events"][0]["id"] > cursor
    assert page["events"][0]["kind"] == "model_step"
    nxt = page["events"][0]["id"]
    rest = client.get(f"/runs/{rid}/events", params={"after_id": nxt}).json()
    assert [e["kind"] for e in rest["events"]] == ["tool_call"]
    done = client.get(f"/runs/{rid}/events", params={"after_id": rest["events"][-1]["id"]}).json()
    assert done["events"] == []
    assert done["run_id"] == rid
    assert done["state"] not in ("completed", "failed")


@then("轮询到完成的结论")
def saw_conclusion(turnbox):
    rid = turnbox["accepted"]["run_id"]
    snap = _poll(turnbox["client"], rid)
    assert snap["state"] == "completed"
    assert snap["result"] == turnbox["expect_md"]


@then("会话里留下助手正文")
def assistant_stored(turnbox):
    deadline = time.time() + 2
    last = None
    while time.time() < deadline:
        last = turnbox["client"].get(f"/sessions/{turnbox['sid']}").json()["messages"]
        if any(m["role"] == "assistant" and m["content"] == turnbox["expect_md"] for m in last):
            return
        time.sleep(0.02)
    raise AssertionError(last)


@then("轮询到失败且原因含抛错短句")
def saw_failure(turnbox):
    snap = _poll(turnbox["client"], turnbox["accepted"]["run_id"])
    assert snap["state"] == "failed"
    assert "turn-exploded" in (snap.get("result") or "")
    msgs = turnbox["client"].get(f"/sessions/{turnbox['sid']}").json()["messages"]
    assert not any(m["role"] == "assistant" for m in msgs)


@then("事件接口返回 404")
def missing_status(turnbox):
    assert turnbox["missing"].status_code == 404


@then("负数 after_id 和非法 limit 被拒绝")
def bad_cursor(turnbox):
    rid = turnbox["accepted"]["run_id"]
    client = turnbox["client"]
    assert client.get(f"/runs/{rid}/events", params={"after_id": -1}).status_code == 400
    assert client.get(f"/runs/{rid}/events", params={"limit": 0}).status_code == 400
    assert client.get(f"/runs/{rid}/events", params={"limit": 501}).status_code == 400


@then("续跑接口已经返回且状态不再是等待确认")
def resume_fast(turnbox):
    body = turnbox["accepted"]
    assert turnbox["elapsed"] < 1.0
    assert body["accepted"] is True
    assert body["run_id"] == turnbox["run_id"]
    assert "markdown" not in body
    assert body["status"] == "acting"
    snap = turnbox["client"].get(f"/runs/{turnbox['run_id']}").json()
    assert snap["state"] == "acting"


@then("立刻再确认一次会被拒绝")
def second_resume_rejected(turnbox):
    again = turnbox["client"].post(
        f"/runs/{turnbox['run_id']}/resume",
        json={"resume_token": turnbox["token"], "approve": True},
    )
    assert again.status_code == 400


@then("拒绝接口当场返回已拒绝")
def rejected_now(turnbox):
    resp = turnbox["rejected"]
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "failed"
    assert body["markdown"] == "已拒绝"
    assert body["run_id"] == turnbox["run_id"]
    snap = turnbox["client"].get(f"/runs/{turnbox['run_id']}").json()
    assert snap["state"] == "failed"
    assert snap["result"] == "已拒绝"
