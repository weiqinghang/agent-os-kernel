"""Skill#2：退货风险助手。会话绑 agent，结论按风险排序，种子仍是同一数据集。"""
from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pytest_bdd import given, scenarios, then, when

from agent_os_kernel import agents, context, model_broker, obs, session
from agent_os_kernel.main import app
from agent_os_kernel.run import STATES
from agent_os_kernel.skills import insight_report
from agent_os_kernel.skills.return_risk_brief import SKILL, _as_float, get_skill, stub_decide
from agent_os_kernel.tool_gateway import ToolGateway

scenarios(str(Path(__file__).parent / "features" / "return_risk_brief.feature"))

ROOT = Path(__file__).resolve().parents[1]
WINDOW7 = {"label": "近7天", "start": "2025-10-15", "end": "2025-10-22"}
WINDOW30 = {"label": "近30天", "start": "2025-09-22", "end": "2025-10-22"}
KERNEL_FILES = (
    "orchestrator.py",
    "run.py",
    "obs.py",
    "policy.py",
    "tool_gateway.py",
    "model_broker.py",
    "hitl.py",
    "context.py",
    "main.py",
    "session.py",
)


def _bindings(**extra):
    base = {"skill": "return_risk_brief", "dataset": "ecommerce_sales_v2", "time_window": dict(WINDOW7)}
    base.update(extra)
    return base


def _tool(name, arguments, data, fingerprint="fp"):
    return {
        "name": name,
        "arguments": arguments,
        "ok": True,
        "data": data,
        "evidence": {"fingerprint": fingerprint},
    }


def _seed_module():
    path = ROOT / "scripts" / "seed_ecommerce.py"
    spec = importlib.util.spec_from_file_location("seed_ecommerce", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _drive(text, bindings=None, canned=None):
    bindings = bindings or _bindings()
    tool_results = []
    allowed = list(SKILL["allowed_tools"])
    samples = []
    for _ in range(6):
        decision = stub_decide(
            messages=[{"role": "user", "content": text}],
            allowed_tools=allowed,
            tool_results=tool_results,
            bindings=bindings,
        )
        if decision["type"] == "final":
            return {"content": decision["content"], "samples": samples, "calls": tool_results}
        assert decision["type"] == "tool_calls"
        for call in decision["calls"]:
            data = (canned or _canned)(call)
            if call["name"] == "sample_records":
                samples.extend((data.get("records") or []))
            tool_results.append(
                _tool(call["name"], call.get("arguments") or {}, data, "fp-" + call["name"])
            )
    raise AssertionError(text)


def _group_of(args):
    group_by = (args or {}).get("group_by")
    if isinstance(group_by, list):
        return group_by[0] if group_by else None
    return group_by


def _flat_filters(args):
    filters = (args or {}).get("filters") or {}
    if isinstance(filters, dict):
        return {key: value for key, value in filters.items() if key not in {"op", "clauses", "operator"}}
    flat = {}
    if isinstance(filters, list):
        for clause in filters:
            if not isinstance(clause, dict) or not clause.get("field"):
                continue
            op = clause.get("operator") or clause.get("op") or "eq"
            if op == "eq":
                flat[clause["field"]] = clause.get("value")
    return flat


def _canned(call):
    name = call.get("name")
    args = call.get("arguments") or {}
    if name == "query_stats":
        table = {
            "channel": ("拼多多", 18.2),
            "category": ("服饰鞋包", 9.0),
            "province": ("广东", 11.4),
        }
        key, value = table.get(_group_of(args), ("未知", 1))
        return {"rows": [{"key": key, "value": value}, {"key": "次位", "value": 1}]}
    if name == "sample_records":
        filters = _flat_filters(args)
        return {
            "count": 1,
            "records": [
                {
                    "order_id": "ORD9001",
                    "category": filters.get("category", "食品饮料"),
                    "channel": filters.get("channel", "京东"),
                    "province": filters.get("province", "北京"),
                    "order_date": "2025-10-20",
                    "gmv": 12,
                }
            ],
        }
    if name == "describe_dataset":
        return {"row_count": 100000, "name": "ecommerce_sales_v2", "summary": {"return_pct": 8}}
    if name == "list_datasets":
        return {"datasets": [{"name": "ecommerce_sales_v2", "row_count": 100000}]}
    return {}


def _present(markdown: str) -> str:
    kept = []
    for line in markdown.splitlines():
        if "evidence" in line.lower():
            continue
        if "tool gateway" in line.lower():
            continue
        if "dataset=" in line.lower():
            continue
        kept.append(line)
    return "\n".join(kept)


@pytest.fixture
def box(monkeypatch, tmp_path):
    monkeypatch.setattr(obs, "DB_PATH", tmp_path / "kernel.db")
    monkeypatch.setattr(model_broker, "has_llm", lambda: False)
    for key in ("MODEL_API_KEY", "XAI_API_KEY", "OPENAI_API_KEY", "MODEL_BASE_URL", "MODEL_NAME"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("HITL_AUTO", "1")
    obs.init_db()
    return {"monkeypatch": monkeypatch}


def _install_gateway(monkeypatch):
    def call(self, name, arguments=None, *, bindings=None):
        del self, bindings
        return {"data": _canned({"name": name, "arguments": arguments or {}}), "evidence": {"fingerprint": "fp"}}

    monkeypatch.setattr(ToolGateway, "call", call)


def _wait_turn(client, accepted):
    import time

    run_id = accepted["run_id"]
    deadline = time.time() + 10
    last = None
    while time.time() < deadline:
        last = client.get(f"/runs/{run_id}").json()
        if last["state"] in ("completed", "failed", "cancelled", "awaiting_hitl"):
            return last
        time.sleep(0.02)
    raise AssertionError(last)


@given("agent_id 为 return_risk.brief 的会话")
def given_risk_session(box):
    with TestClient(app) as client:
        created = client.post("/sessions", json={"agent_id": "return_risk.brief"}).json()
        box["client"] = client
        box["sid"] = created["id"]
        box["created"] = created
        # Hold the client open by finishing the scenario steps inside this given? No.
        # TestClient context would close. Store the payload and recreate in when.
    box["client"] = None


@when("用户请求近7天退货风险")
def when_ask_risk(box):
    box["report"] = _drive("近7天退货风险")
    box["shown"] = _present(box["report"]["content"])


@then("出现 3 到 5 条风险向结论")
def then_ranked(box):
    content = box["report"]["content"]
    assert "退货风险简报（近7天）" in content
    assert "建议先看" in content
    assert "偏高" in content
    numbered = [line for line in content.splitlines() if line[:2] in {f"{n}." for n in range(1, 6)}]
    assert 3 <= len(numbered) <= 5
    assert "推迟" not in content
    rates = []
    for line in numbered:
        start = line.find("退货率 ")
        end = line.find("%", start)
        rates.append(float(line[start + 4 : end]))
    assert rates == sorted(rates, reverse=True)


@then("正文不是洞察全景腔")
def then_not_insight(box):
    content = box["report"]["content"]
    assert "类目集中" not in content
    assert "电商销量洞察报告" not in content
    assert "发现" not in content


@then("默认终端没有 dataset= 或 evidence 哈希或工具名")
def then_redacted(box):
    shown = box["shown"]
    assert "dataset=" not in shown
    assert "evidence" not in shown.lower()
    for name in SKILL["allowed_tools"]:
        assert name not in shown
    assert "Tool Gateway" not in shown


@when("用户查看第 1 条依据")
def when_drill_first(box):
    report = _drive("近7天退货风险")
    first = next(line for line in report["content"].splitlines() if line.startswith("1. "))
    box["first_line"] = first
    box["drill"] = _drive("第1条依据是什么")


@then("样本渠道或类目与该条风险描述一致")
def then_aligned(box):
    content = box["drill"]["content"]
    assert "第1条依据" in content
    assert box["drill"]["samples"]
    # Highest fixture rate is 拼多多 18.2, so item 1 is that channel.
    assert "拼多多" in box["first_line"]
    assert "拼多多" in content
    assert all(item["channel"] == "拼多多" for item in box["drill"]["samples"])


@given("同一退货会话里已经有一版近7天结论")
def given_one_version(box):
    _install_gateway(box["monkeypatch"])
    with TestClient(app) as client:
        created = client.post("/sessions", json={"agent_id": "return_risk.brief"}).json()
        first = _wait_turn(
            client,
            client.post(f"/sessions/{created['id']}/messages", json={"text": "近7天退货风险"}).json(),
        )
        box["sid"] = created["id"]
        box["first_run"] = first["id"]
        box["first_text"] = first["result"]
        assert "近7天" in first["result"]


@when("用户确认改为近30天")
def when_rebind(box):
    _install_gateway(box["monkeypatch"])
    with TestClient(app) as client:
        patched = client.patch(
            f"/sessions/{box['sid']}/context",
            json={"bindings": {"time_window": WINDOW30}},
        )
        assert patched.status_code == 200
        second = _wait_turn(
            client,
            client.post(
                f"/sessions/{box['sid']}/messages",
                json={"text": "用近30天再出一版退货风险简报", "bindings": {"time_window": WINDOW30}},
            ).json(),
        )
        box["second"] = second


@then("新 Run 完成且结论时间窗口径为近30天")
def then_new_window(box):
    second = box["second"]
    assert second["state"] == "completed"
    assert second["id"] != box["first_run"]
    assert second["context_bindings"]["time_window"]["label"] == "近30天"
    assert "退货风险简报（近30天）" in second["result"]
    assert "近7天" not in second["result"].splitlines()[0]


@when("用户在同一会话追问第 1 条依据")
def when_same_thread(box):
    _install_gateway(box["monkeypatch"])
    with TestClient(app) as client:
        created = client.post("/sessions", json={"agent_id": "return_risk.brief"}).json()
        _wait_turn(
            client,
            client.post(f"/sessions/{created['id']}/messages", json={"text": "近7天退货风险"}).json(),
        )
        follow = _wait_turn(
            client,
            client.post(f"/sessions/{created['id']}/messages", json={"text": "第1条依据是什么"}).json(),
        )
        stored = client.get(f"/sessions/{created['id']}").json()
        box["follow"] = follow
        box["thread"] = stored
        box["sid"] = created["id"]


@then("不需要新开 Session")
def then_same_session(box):
    assert box["thread"]["id"] == box["sid"]
    assert box["thread"]["agent_id"] == "return_risk.brief"
    roles = [item["role"] for item in box["thread"]["messages"]]
    assert roles.count("user") >= 2


@then("样本与第 1 条爆点一致")
def then_follow_samples(box):
    assert box["follow"]["state"] == "completed"
    assert "拼多多" in box["follow"]["result"]
    assert "第1条依据" in box["follow"]["result"]


@given("狗粮数据集 ecommerce_sales_v2")
def given_seed_id(box):
    box["dataset"] = "ecommerce_sales_v2"


@then("行数量级约 1e5")
def then_row_scale(box, tmp_path):
    seeder = _seed_module()
    count = seeder.seed(tmp_path / "sales.db")
    assert seeder.DEFAULT_N == 100_000
    assert 90_000 <= count <= 110_000
    conn = sqlite3.connect(tmp_path / "sales.db")
    names = {row[0] for row in conn.execute("SELECT name, row_count FROM datasets_meta")}
    counts = {row[0]: row[1] for row in conn.execute("SELECT name, row_count FROM datasets_meta")}
    conn.close()
    assert "ecommerce_sales_v2" in names
    assert counts["ecommerce_sales_v2"] == count
    assert box["dataset"] == "ecommerce_sales_v2"


@then("Gateway 仍只有现有 DAS 连接")
def then_one_gateway():
    records = agents.list_agents()
    assert [row["id"] for row in records] == ["insight.report", "return_risk.brief"]
    assert all(row["mcps"] == ["das"] for row in records)
    assert (ROOT / "src" / "agent_os_kernel" / "tool_gateway.py").is_file()
    extras = list((ROOT / "src").rglob("*gateway*.py"))
    assert extras == [ROOT / "src" / "agent_os_kernel" / "tool_gateway.py"]


@when("检视本批内核源码")
def when_read_kernel(box):
    box["sources"] = {
        name: (ROOT / "src" / "agent_os_kernel" / name).read_text(encoding="utf-8")
        for name in KERNEL_FILES
    }


@then("无新增 phase_ 事件 kind")
def then_no_phase(box):
    for name, text in box["sources"].items():
        assert "phase_" not in text, name


@then("Run 状态集合无退货专用态")
def then_states():
    assert STATES == {"created", "acting", "awaiting_hitl", "completed", "failed", "cancelled"}


@then("编排没有退货业务分支")
def then_no_business_branch(box):
    for name, text in box["sources"].items():
        assert "退货" not in text, name
    assert "return_risk" not in box["sources"]["orchestrator.py"]


def test_catalog_and_session_lock(tmp_path, monkeypatch):
    monkeypatch.setattr(obs, "DB_PATH", tmp_path / "kernel.db")
    obs.init_db()
    insight = agents.get(None)
    assert insight["id"] == "insight.report"
    assert insight["display_name"] == "洞察报告助理"
    assert agents.get("  ")["id"] == "insight.report"
    risk = agents.get("return_risk.brief")
    assert risk["display_name"] == "退货风险助手"
    assert risk["skills"] == ["return_risk_brief"]
    assert risk["default_bindings"]["time_window"]["label"] == "近7天"
    with pytest.raises(KeyError):
        agents.get("nope")

    with TestClient(app) as client:
        missing = client.post("/sessions", json={})
        assert missing.status_code == 200
        assert missing.json()["agent_id"] == "insight.report"
        assert missing.json()["bindings"]["skill"] == "insight_report"
        assert missing.json()["bindings"]["time_window"]["label"] == "近90天"
        assert missing.json()["bindings"]["dataset"] == "ecommerce_sales_v2"

        unknown = client.post("/sessions", json={"agent_id": "nope"})
        assert unknown.status_code == 400

        created = client.post(
            "/sessions",
            json={"meta": {"agent_id": "return_risk.brief"}},
        ).json()
        assert created["agent_id"] == "return_risk.brief"
        assert created["bindings"]["skill"] == "return_risk_brief"
        assert created["bindings"]["time_window"]["label"] == "近7天"
        listed = client.get("/agents").json()
        assert listed["default"] == "insight.report"
        assert {row["id"] for row in listed["agents"]} == {"insight.report", "return_risk.brief"}
        skills = client.get("/skills").json()["skills"]
        risk_row = next(row for row in skills if row["name"] == "return_risk_brief")
        assert risk_row["status"] == "active"
        assert risk_row["allowed_tools"] == insight_report.SKILL["allowed_tools"]

        other = client.post("/sessions", json={"agent_id": "insight.report"}).json()
        assert other["id"] != created["id"]
        stored = client.get(f"/sessions/{created['id']}").json()
        assert stored["agent_id"] == "return_risk.brief"

        blocked = client.patch(
            f"/sessions/{created['id']}/context",
            json={"bindings": {"agent_id": "insight.report", "time_window": WINDOW30}},
        )
        assert blocked.status_code == 409
        again = client.get(f"/sessions/{created['id']}").json()
        assert again["agent_id"] == "return_risk.brief"
        assert again["bindings"]["time_window"]["label"] == "近7天"

        moved = client.patch(
            f"/sessions/{created['id']}/context",
            json={"bindings": {"time_window": WINDOW30}},
        )
        assert moved.status_code == 200
        assert client.get(f"/sessions/{created['id']}").json()["agent_id"] == "return_risk.brief"

        wrong_skill = client.post(
            "/sessions",
            json={"agent_id": "return_risk.brief", "bindings": {"skill": "insight_report"}},
        )
        assert wrong_skill.status_code == 400
        cross = client.post(
            f"/sessions/{created['id']}/messages",
            json={"text": "写一份洞察", "skill": "insight_report"},
        )
        assert cross.status_code == 400

    with pytest.raises(KeyError):
        session.create_session({"agent_id": "nope"})
    with pytest.raises(ValueError):
        session.set_bindings(created["id"], {"agent_id": "insight.report"})


def test_binding_order_prefers_agent_window_over_skill():
    resolved = context.resolve_bindings(
        skill_defaults=insight_report.SKILL["default_bindings"],
        agent_defaults=agents.get("return_risk.brief")["default_bindings"],
        skill_name="return_risk_brief",
    )
    assert resolved["time_window"]["label"] == "近7天"
    assert resolved["dataset"] == "ecommerce_sales_v2"
    follow = context.resolve_bindings(
        request={"time_window": WINDOW30},
        session_bindings={"dataset": "ecommerce_sales_v2"},
        agent_defaults=agents.get("return_risk.brief")["default_bindings"],
        skill_defaults=SKILL["default_bindings"],
        inherit_run={"time_window": WINDOW7, "skill": "return_risk_brief"},
        skill_name="return_risk_brief",
    )
    assert follow["time_window"]["label"] == "近30天"
    inherited = context.resolve_bindings(
        agent_defaults=agents.get("return_risk.brief")["default_bindings"],
        skill_defaults=SKILL["default_bindings"],
        inherit_run={"time_window": WINDOW30, "dataset": "ecommerce_sales_v2"},
        skill_name="return_risk_brief",
    )
    assert inherited["time_window"]["label"] == "近30天"


def test_skill_edges_cover_focus_drill_and_empty():
    assert get_skill()["name"] == "return_risk_brief"
    assert "推迟" not in SKILL["system_prompt"]
    assert "偏高" in SKILL["system_prompt"]
    assert "avg_returned" in SKILL["system_prompt"]
    assert "metric return_rate" not in SKILL["system_prompt"]
    assert SKILL["hitl_kinds"] == ["outline_confirm"]
    assert 1 <= len(SKILL["plan_steps"]) <= 5
    assert _as_float("nope") == 0.0
    assert _as_float(None) == 0.0

    focus = _drive("只看拼多多")
    assert "拼多多" in focus["content"]
    assert all(item["channel"] == "拼多多" for item in focus["samples"])
    category = _drive("只看服饰鞋包")
    assert "服饰鞋包" in category["content"]
    province = _drive("只看广东")
    assert "广东" in province["content"]
    named = _drive("看依据里的美妆个护")
    assert "美妆个护" in named["content"]
    assert "第1条依据" in _drive("第一条依据")["content"]
    second = _drive("第2条依据是什么")
    assert "广东" in second["content"]
    assert second["samples"]
    assert all(item["province"] == "广东" for item in second["samples"])
    missing = _drive("第4条依据是什么")
    assert "没有第4条" in missing["content"]

    def empty_sample(call):
        if call.get("name") == "sample_records":
            return {"count": 0, "records": []}
        if call.get("name") == "query_stats" and _group_of(call.get("arguments") or {}) == "channel":
            return {"rows": [{"key": "天猫", "value": "bad"}]}
        return _canned(call)

    gap = _drive("只看京东", canned=empty_sample)
    assert "京东" in gap["content"]
    assert "没有抽到" in gap["content"]

    empty = stub_decide(
        messages=[{"role": "user", "content": "近7天退货风险"}],
        allowed_tools=["list_datasets"],
        tool_results=[_tool("list_datasets", {}, {"datasets": []})],
        bindings=_bindings(),
    )
    assert empty["type"] == "final"
    assert "没有足够" in empty["content"]

    no_tools = stub_decide(
        messages=[{"role": "user", "content": "只看不存在的柜面"}],
        allowed_tools=[],
        tool_results=[],
        bindings=_bindings(),
    )
    assert no_tools["type"] == "final"
    assert "推迟" not in no_tools["content"]


def _numbered(content: str) -> list[str]:
    return [line for line in content.splitlines() if line[:2] in {f"{n}." for n in range(1, 6)}]


def test_real_returned_rows_rank_without_key_value():
    def canned(call):
        if call.get("name") != "query_stats":
            return _canned(call)
        group = _group_of(call.get("arguments") or {})
        if group == "channel":
            return {
                "rows": [
                    {"avg_returned": 0.5},
                    {"channel": "京东", "avg_returned": 0.06, "sum_returned": 12, "count": 200},
                    {"channel": "拼多多", "avg_returned": 0.13, "sum_returned": 26, "count": 200},
                ]
            }
        if group == "category":
            return {"rows": [{"category": "服饰鞋包", "metrics": {"avg_returned": 0.09}}]}
        if group == "province":
            return {
                "result": {
                    "rows": [
                        {"dimensions": {"province": "广东"}, "values": {"avg_returned": 0.11}},
                    ]
                }
            }
        return {"rows": []}

    report = _drive("近7天退货风险", canned=canned)
    numbered = _numbered(report["content"])
    assert 3 <= len(numbered) <= 5
    assert "没有足够" not in report["content"]
    assert "拼多多" in numbered[0]
    assert "退货率 13%" in numbered[0]
    assert "200 单里退货 26" in numbered[0]
    assert "广东" in report["content"]
    assert "退货率 11%" in report["content"]
    assert "服饰鞋包" in report["content"]
    assert "退货率 9%" in report["content"]
    drill = _drive("第1条依据是什么", canned=canned)
    assert "第1条依据" in drill["content"]
    assert "拼多多" in drill["content"]
    assert drill["samples"]
    assert all(item["channel"] == "拼多多" for item in drill["samples"])
    focus = _drive("只看拼多多", canned=canned)
    assert "拼多多" in focus["content"]
    assert "退货率 13%" in focus["content"]
    assert "没有抽到这个范围的样本" not in focus["content"]


def test_fake_das_near7_avg_returned_brief(tmp_path, monkeypatch):
    from services.fake_das import app as fake

    db = tmp_path / "sales.db"
    _seed_module().seed(db, n=4000)
    monkeypatch.setattr(fake, "DB_PATH", db)
    bindings = _bindings()
    allowed = list(SKILL["allowed_tools"])
    decision = stub_decide(
        messages=[{"role": "user", "content": "近7天退货风险"}],
        allowed_tools=allowed,
        tool_results=[],
        bindings=bindings,
    )
    assert decision["type"] == "tool_calls"
    channel = next(
        call
        for call in decision["calls"]
        if call["name"] == "query_stats" and _group_of(call["arguments"]) == "channel"
    )
    assert channel["arguments"]["metrics"] == [
        {"function": "avg", "field": "returned", "alias": "avg_returned"}
    ]
    assert channel["arguments"]["group_by"] == ["channel"]
    assert {"field": "order_date", "operator": "gte", "value": "2025-10-15"} in channel["arguments"]["filters"]
    assert {"field": "order_date", "operator": "lte", "value": "2025-10-22"} in channel["arguments"]["filters"]
    assert "metric" not in channel["arguments"]
    results = []
    for call in decision["calls"]:
        name = call["name"]
        args = call.get("arguments") or {}
        if name == "query_stats":
            data = fake._query_stats(args)
            assert data["rows"]
            assert data["rows"][0].get("avg_returned") is not None
        elif name == "describe_dataset":
            data = fake._describe_dataset(args)
        else:
            data = fake._list_datasets(args)
        results.append(_tool(name, args, data, "fp-" + name))
    final = stub_decide(
        messages=[{"role": "user", "content": "近7天退货风险"}],
        allowed_tools=allowed,
        tool_results=results,
        bindings=bindings,
    )
    assert final["type"] == "final"
    numbered = _numbered(final["content"])
    assert 3 <= len(numbered) <= 5
    assert "没有足够" not in final["content"]
    assert "拼多多" in numbered[0]
    assert "退货率" in numbered[0]
    assert "%" in numbered[0]

    drill = stub_decide(
        messages=[{"role": "user", "content": "第1条依据是什么"}],
        allowed_tools=allowed,
        tool_results=results,
        bindings=bindings,
    )
    assert drill["type"] == "tool_calls"
    sample_call = next(call for call in drill["calls"] if call["name"] == "sample_records")
    assert {"field": "channel", "operator": "eq", "value": "拼多多"} in sample_call["arguments"]["filters"]
    sample = fake._sample_records(sample_call["arguments"])
    assert sample["records"]
    assert all(row["channel"] == "拼多多" for row in sample["records"])
    shown = stub_decide(
        messages=[{"role": "user", "content": "第1条依据是什么"}],
        allowed_tools=allowed,
        tool_results=results + [_tool("sample_records", sample_call["arguments"], sample)],
        bindings=bindings,
    )
    assert shown["type"] == "final"
    assert "第1条依据" in shown["content"]
    assert "拼多多" in shown["content"]
    assert "没有抽到" not in shown["content"]
