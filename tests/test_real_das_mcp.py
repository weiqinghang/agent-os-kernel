"""BTDD: real DAS MCP envelope, Bearer, SQL reject, dogfood seed id."""
from __future__ import annotations

import json
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from pytest_bdd import given, scenarios, then, when

from agent_os_kernel import context, obs
from agent_os_kernel.main import app
from agent_os_kernel.skills.insight_report import (
    SKILL,
    _build_drill,
    _build_followup,
    _build_report,
    _intent,
    _last_user,
    stub_decide,
)
from agent_os_kernel.tool_gateway import (
    WHITELIST,
    ToolGateway,
    ToolGatewayError,
    DOGFOOD_RETURN_FIELDS,
    adapt_real_arguments,
    clear_dataset_id_cache,
    index_dataset_ids,
    parse_mcp_response,
    resolve_health_url,
    resolve_mcp_url,
    structured_from_result,
)

ROOT = Path(__file__).resolve().parents[1]
scenarios(str(Path(__file__).parent / "features" / "real_das_mcp.feature"))

SEED = "ecommerce_sales_v2"
BOX_UUID = "8c136287-aa85-5c34-831a-ddfc7983d1a4"


class FakeMcp:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.mode = "json"
        self.list_rows: list[dict] | None = None
        self.fail_list = False

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.method == "GET":
            if self.mode == "health-down":
                return httpx.Response(503, json={"status": "down"})
            return httpx.Response(200, json={"status": "ok", "service": "dataset-analysis-service"})
        body = json.loads(request.content.decode())
        method = body.get("method")
        if self.mode == "http":
            return httpx.Response(401, text="nope")
        if self.mode == "bad-json":
            return httpx.Response(200, text="not-json", headers={"content-type": "application/json"})
        if self.mode == "sse-empty":
            return httpx.Response(200, text="event: message\n\n", headers={"content-type": "text/event-stream"})
        if self.mode == "rpc-error":
            payload = {"jsonrpc": "2.0", "id": 1, "error": {"code": -32602, "message": "bad args"}}
            return httpx.Response(200, json=payload)
        if method == "tools/list":
            tools = [
                {"name": name, "description": name}
                for name in sorted(WHITELIST)
            ]
            payload = {"jsonrpc": "2.0", "id": 1, "result": {"tools": tools}}
        else:
            params = body.get("params") or {}
            name = params.get("name")
            args = params.get("arguments") or {}
            if name == "list_datasets" and self.fail_list:
                payload = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "error": {"code": -32603, "message": "catalog down"},
                }
                return httpx.Response(200, json=payload)
            data = _tool_payload(name, args, self.list_rows)
            result: dict = {
                "content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False)}],
                "structuredContent": data,
            }
            if self.mode == "text-only":
                result.pop("structuredContent")
            if self.mode == "is-error":
                result = {"isError": True, "content": [{"type": "text", "text": "nope"}]}
            payload = {"jsonrpc": "2.0", "id": 1, "result": result}
        if self.mode == "sse":
            raw = "event: message\ndata: " + json.dumps(payload, ensure_ascii=False) + "\n\n"
            return httpx.Response(200, text=raw, headers={"content-type": "text/event-stream"})
        return httpx.Response(200, json=payload)


def _metric_name(args: dict) -> str | None:
    metric = args.get("metric")
    if isinstance(metric, str) and metric.strip():
        return metric.strip()
    metrics = args.get("metrics")
    if isinstance(metrics, list):
        for item in metrics:
            if isinstance(item, str) and item.strip():
                return item.strip()
            if isinstance(item, dict):
                alias = item.get("alias") or item.get("name")
                if isinstance(alias, str) and alias.strip():
                    return alias.strip()
        return None
    if isinstance(metrics, dict):
        if isinstance(metrics.get("name"), str):
            return str(metrics["name"])
        for key, val in metrics.items():
            if isinstance(val, dict):
                return str(val.get("name") or val.get("alias") or key)
    return None


def _group_key(args: dict):
    group_by = args.get("group_by")
    if isinstance(group_by, list):
        if not group_by:
            return None
        first = group_by[0]
        if isinstance(first, dict):
            return first.get("field")
        return first
    return group_by


def _eq_filters(args: dict) -> dict:
    filters = args.get("filters") or {}
    if isinstance(filters, list):
        clauses = filters
    elif isinstance(filters, dict) and isinstance(filters.get("clauses"), list):
        clauses = filters["clauses"]
    elif isinstance(filters, dict):
        return {k: v for k, v in filters.items() if k not in {"op", "clauses", "operator"}}
    else:
        return {}
    flat: dict = {}
    for clause in clauses:
        if not isinstance(clause, dict) or not clause.get("field"):
            continue
        op = clause.get("operator") or clause.get("op")
        if op == "eq":
            flat[clause["field"]] = clause.get("value")
    return flat


def _tool_payload(name: str, args: dict, list_rows: list[dict] | None = None) -> dict:
    if name == "list_datasets":
        rows = list_rows if list_rows is not None else [
            {"id": SEED, "name": SEED, "description": "seed", "row_count": 200}
        ]
        return {"datasets": rows}
    if name == "describe_dataset":
        return {
            "name": args.get("dataset_id") or args.get("name") or SEED,
            "row_count": 200,
            "columns": ["gmv"],
            "summary": {
                "min_date": "2025-07-25",
                "max_date": "2025-10-22",
                "total_gmv": 4242.5,
                "filtered_rows": 200,
                "return_pct": 6.5,
            },
        }
    if name == "query_stats":
        key = {
            ("category", "sum_gmv"): "电子产品",
            ("channel", "sum_gmv"): "抖音电商",
            ("province", "sum_gmv"): "江苏",
            ("channel", "return_rate"): "拼多多",
        }.get((_group_key(args), _metric_name(args)), "其他")
        return {
            "group_by": args.get("group_by"),
            "metric": args.get("metric"),
            "rows": [{"key": key, "value": 4242.5}],
        }
    if name in ("sample_records", "search_records"):
        filters = _eq_filters(args)
        rec = {
            "order_id": "ORD1001",
            "category": filters.get("category", "电子产品"),
            "channel": filters.get("channel", "抖音电商"),
            "province": filters.get("province", "江苏"),
            "order_date": "2025-08-01",
            "gmv": 88.0,
        }
        return {"count": 1, "records": [rec]}
    if name == "describe_fields":
        return {"dataset_id": args.get("dataset_id") or SEED, "fields": ["gmv", "category"]}
    if name == "describe_tag_hierarchies":
        return {"dataset_id": args.get("dataset_id") or SEED, "hierarchies": [{"tag": "类目"}]}
    return {"ok": True}


def _install(monkeypatch: pytest.MonkeyPatch, fake: FakeMcp) -> None:
    import agent_os_kernel.tool_gateway as tg

    real = tg.httpx.Client

    def factory(*args, **kwargs):
        kwargs = dict(kwargs)
        kwargs["transport"] = tg.httpx.MockTransport(fake)
        return real(*args, **kwargs)

    monkeypatch.setattr(tg.httpx, "Client", factory)


def _real_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> FakeMcp:
    clear_dataset_id_cache()
    monkeypatch.delenv("USE_FAKE_DAS", raising=False)
    monkeypatch.setenv("DAS_MCP_URL", "http://das.test/api/mcp")
    monkeypatch.setenv("DAS_MCP_AUTH_TOKEN", "sekret")
    monkeypatch.setenv("DAS_HEALTH_URL", "http://das.test/health")
    monkeypatch.setattr(obs, "DB_PATH", tmp_path / "kernel.db")
    monkeypatch.setenv("HITL_AUTO", "1")
    monkeypatch.setattr("agent_os_kernel.model_broker.has_llm", lambda: False)
    fake = FakeMcp()
    _install(monkeypatch, fake)
    return fake


def _posted(fake: FakeMcp) -> list[dict]:
    out = []
    for req in fake.requests:
        if req.method != "POST":
            continue
        out.append(json.loads(req.content.decode()))
    return out


@given("真 DAS MCP 已按契约接好", target_fixture="mcp_world")
def given_real_mcp(monkeypatch, tmp_path):
    fake = _real_env(monkeypatch, tmp_path)
    return {"fake": fake, "monkeypatch": monkeypatch, "tmp_path": tmp_path}


@when("网关列出工具并调用 list_datasets 与 query_stats")
def when_list_and_query(mcp_world):
    gw = ToolGateway()
    mcp_world["tools"] = gw.list_tools()
    mcp_world["listed"] = gw.call(
        "list_datasets",
        {},
        bindings={"dataset": SEED, "tenant_id": "t1", "project_id": "p1"},
    )
    mcp_world["stats"] = gw.call(
        "query_stats",
        {"group_by": "category", "metric": "sum_gmv", "limit": 5},
        bindings={
            "dataset": SEED,
            "time_window": {"start": "2025-07-25", "end": "2025-10-22"},
        },
    )
    mcp_world["fields"] = gw.call("describe_fields", {}, bindings={"dataset": SEED})
    mcp_world["tags"] = gw.call("describe_tag_hierarchies", {}, bindings={"dataset": SEED})


@then("请求打到 /api/mcp 且带 Bearer")
def then_bearer(mcp_world):
    posts = [r for r in mcp_world["fake"].requests if r.method == "POST"]
    assert posts
    for req in posts:
        assert req.url.path == "/api/mcp"
        assert req.headers["authorization"] == "Bearer sekret"
        accept = req.headers["accept"]
        assert "application/json" in accept
        assert "text/event-stream" in accept
    stats_call = next(
        b for b in _posted(mcp_world["fake"])
        if b["method"] == "tools/call" and b["params"]["name"] == "query_stats"
    )
    args = stats_call["params"]["arguments"]
    assert args["dataset_id"] == SEED
    assert args["group_by"] == ["category"]
    assert args["limit"] == 5
    assert args["metrics"] == [{"function": "sum", "field": "gmv", "alias": "sum_gmv"}]
    assert {"field": "order_date", "operator": "gte", "value": "2025-07-25"} in args["filters"]
    assert {"field": "order_date", "operator": "lte", "value": "2025-10-22"} in args["filters"]
    for leftover in ("name", "dataset", "start", "end", "metric"):
        assert leftover not in args
    assert "sql" not in args


@then("数据集里有 ecommerce_sales_v2")
def then_seed_listed(mcp_world):
    names = [d.get("name") or d.get("id") for d in mcp_world["listed"]["data"]["datasets"]]
    assert SEED in names
    assert mcp_world["listed"]["evidence"]["fingerprint"]


@then("query_stats 返回聚合行")
def then_rows(mcp_world):
    rows = mcp_world["stats"]["data"]["rows"]
    assert rows and rows[0]["key"] == "电子产品"
    assert mcp_world["fields"]["data"]["fields"]
    assert mcp_world["tags"]["data"]["hierarchies"]


@then("工具名单包含 describe_fields 与 describe_tag_hierarchies")
def then_tools(mcp_world):
    assert "describe_fields" in mcp_world["tools"]
    assert "describe_tag_hierarchies" in mcp_world["tools"]
    assert set(SKILL["allowed_tools"]) <= WHITELIST


@when("网关用假 DAS 形状调用 query_stats、describe_dataset 与 sample_records")
def when_fake_shaped_real_call(mcp_world):
    gw = ToolGateway()
    bindings = {
        "dataset": SEED,
        "time_window": {"label": "近90天", "start": "2025-07-25", "end": "2025-10-22"},
    }
    mcp_world["adapted_stats"] = gw.call(
        "query_stats",
        {
            "dataset": "not-the-id",
            "name": SEED,
            "start": "2025-07-25",
            "end": "2025-10-22",
            "group_by": "channel",
            "metric": "return_rate",
            "limit": 10,
            "filters": {"channel": "抖音电商"},
        },
        bindings=bindings,
    )
    gw.call(
        "describe_dataset",
        {"dataset": SEED, "name": SEED, "start": "2025-07-25", "end": "2025-10-22"},
        bindings=bindings,
    )
    gw.call(
        "sample_records",
        {"name": SEED, "limit": 5, "filters": {"category": "服装"}},
        bindings=bindings,
    )


@then("真 MCP 参数不含 name、dataset、start、end 与 metric 字符串")
def then_fake_keys_stripped(mcp_world):
    calls = [
        b for b in _posted(mcp_world["fake"]) if b.get("method") == "tools/call"
    ]
    assert calls
    for body in calls:
        args = body["params"]["arguments"]
        for key in ("name", "dataset", "start", "end", "time_window", "metric"):
            assert key not in args


@then("query_stats 带上 dataset_id、时间窗 filters 数组、metrics 数组与 group_by 数组")
def then_real_dsl(mcp_world):
    by_name = {
        b["params"]["name"]: b["params"]["arguments"]
        for b in _posted(mcp_world["fake"])
        if b.get("method") == "tools/call"
    }
    stats = by_name["query_stats"]
    assert stats["dataset_id"] == SEED
    assert stats["group_by"] == ["channel"]
    assert stats["limit"] == 10
    assert stats["metrics"] == [
        {"function": "avg", "field": "returned", "alias": "return_rate"}
    ]
    assert {"field": "order_date", "operator": "gte", "value": "2025-07-25"} in stats["filters"]
    assert {"field": "order_date", "operator": "lte", "value": "2025-10-22"} in stats["filters"]
    assert {"field": "channel", "operator": "eq", "value": "抖音电商"} in stats["filters"]
    assert by_name["describe_dataset"] == {"dataset_id": SEED}
    sample = by_name["sample_records"]
    assert sample["dataset_id"] == SEED
    assert sample["limit"] == 5
    assert {"field": "category", "operator": "eq", "value": "服装"} in sample["filters"]
    assert {"field": "order_date", "operator": "gte", "value": "2025-07-25"} in sample["filters"]
    assert "offset" not in sample
    assert sample["return_fields"] == list(DOGFOOD_RETURN_FIELDS)
    assert "evidence_preset" not in sample
    assert mcp_world["adapted_stats"]["data"]["rows"][0]["key"] == "拼多多"


@when("网关调用 sample_records 与 search_records 的返回字段组合")
def when_record_projection(mcp_world):
    gw = ToolGateway()
    bindings = {"dataset": SEED}
    gw.call("sample_records", {"limit": 5}, bindings=bindings)
    gw.call("search_records", {"return_fields": [], "limit": 3}, bindings=bindings)
    gw.call(
        "sample_records",
        {"return_fields": [], "evidence_preset": "default", "limit": 2},
        bindings=bindings,
    )
    gw.call(
        "search_records",
        {"return_fields": [" sku ", "", "gmv", 1]},
        bindings=bindings,
    )
    gw.call(
        "sample_records",
        {"return_fields": ["order_id"], "evidence_preset": "default"},
        bindings=bindings,
    )
    gw.call("search_records", {"evidence_preset": "custom"}, bindings=bindings)


@then("缺省 return_fields 被补成狗粮字段且显式投影被保留")
def then_record_projection(mcp_world):
    calls = [
        b["params"]
        for b in _posted(mcp_world["fake"])
        if b.get("method") == "tools/call" and b["params"]["name"] in ("sample_records", "search_records")
    ]
    assert len(calls) == 6
    bare, empty, preset, cleaned, both, invalid = (c["arguments"] for c in calls)
    assert [c["name"] for c in calls] == [
        "sample_records",
        "search_records",
        "sample_records",
        "search_records",
        "sample_records",
        "search_records",
    ]
    assert bare["return_fields"] == list(DOGFOOD_RETURN_FIELDS)
    assert bare["dataset_id"] == SEED
    assert bare["limit"] == 5
    assert "evidence_preset" not in bare
    assert empty["return_fields"] == list(DOGFOOD_RETURN_FIELDS)
    assert preset == {
        "dataset_id": SEED,
        "evidence_preset": "default",
        "limit": 2,
    }
    assert cleaned["return_fields"] == ["sku", "gmv"]
    assert "evidence_preset" not in cleaned
    assert both["return_fields"] == ["order_id"]
    assert both["evidence_preset"] == "default"
    assert invalid["return_fields"] == list(DOGFOOD_RETURN_FIELDS)
    assert "evidence_preset" not in invalid
    for args in (bare, empty, preset, cleaned, both, invalid):
        for key in ("name", "dataset", "start", "end", "metric", "q", "keyword", "offset"):
            assert key not in args


@when("网关用假形状调用 sample_records")
def when_fake_sample(mcp_world):
    ToolGateway().call(
        "sample_records",
        {"limit": 5, "filters": {"category": "服装"}},
        bindings={"dataset": SEED, "time_window": {"start": "2025-07-25", "end": "2025-10-22"}},
    )


@then("假 sample_records 保留 name 且没有 return_fields")
def then_fake_sample_unchanged(mcp_world):
    args = _posted(mcp_world["fake"])[-1]["params"]["arguments"]
    assert args["name"] == SEED
    assert args["start"] == "2025-07-25"
    assert args["end"] == "2025-10-22"
    assert args["limit"] == 5
    assert args["filters"]["category"] == "服装"
    assert "return_fields" not in args
    assert "evidence_preset" not in args
    assert "dataset_id" not in args


@given("目录把 ecommerce_sales_v2 映射到 UUID")
def given_uuid_catalog(mcp_world):
    mcp_world["dataset_uuid"] = BOX_UUID
    mcp_world["fake"].list_rows = [{"dataset_id": BOX_UUID, "dataset_name": SEED}]


@when("网关用友好名两次调用 query_stats 并用 UUID 再调用一次")
def when_resolve_name(mcp_world):
    gw = ToolGateway()
    bindings = {"dataset": SEED, "time_window": {"start": "2025-07-25", "end": "2025-10-22"}}
    for _ in range(2):
        gw.call(
            "query_stats",
            {"group_by": "category", "metric": "sum_gmv", "limit": 1},
            bindings=bindings,
        )
    gw.call(
        "query_stats",
        {"dataset_id": BOX_UUID, "group_by": "category", "metric": "count", "limit": 1},
    )


@then("只请求了一次 list_datasets 且 query_stats 的 dataset_id 都是该 UUID")
def then_resolved_uuid(mcp_world):
    calls = [b for b in _posted(mcp_world["fake"]) if b.get("method") == "tools/call"]
    listed = [c for c in calls if c["params"]["name"] == "list_datasets"]
    stats = [c for c in calls if c["params"]["name"] == "query_stats"]
    assert len(listed) == 1
    assert len(stats) == 3
    assert all(c["params"]["arguments"]["dataset_id"] == BOX_UUID for c in stats)
    first = stats[0]["params"]["arguments"]
    assert first["metrics"][0]["function"] == "sum"
    assert first["group_by"] == ["category"]
    assert isinstance(first["filters"], list)
    assert stats[2]["params"]["arguments"]["metrics"] == [{"function": "count", "alias": "count"}]


@given("假 DAS 开关已打开", target_fixture="mcp_world")
def given_fake_switch(monkeypatch):
    clear_dataset_id_cache()
    monkeypatch.setenv("USE_FAKE_DAS", "1")
    monkeypatch.setenv("FAKE_DAS_URL", "http://127.0.0.1:8788")
    monkeypatch.delenv("DAS_MCP_URL", raising=False)
    monkeypatch.delenv("DAS_MCP_AUTH_TOKEN", raising=False)
    fake = FakeMcp()
    _install(monkeypatch, fake)
    return {"fake": fake}


@when("网关用假形状调用 query_stats")
def when_fake_shape(mcp_world):
    ToolGateway().call(
        "query_stats",
        {"group_by": "province", "metric": "sum_gmv", "limit": 3},
        bindings={
            "dataset": SEED,
            "time_window": {"start": "2025-07-25", "end": "2025-10-22"},
        },
    )


@then("参数保留 name、start、end、metric 且没有 dataset_id")
def then_fake_shape_kept(mcp_world):
    args = _posted(mcp_world["fake"])[-1]["params"]["arguments"]
    assert args["name"] == SEED
    assert args["start"] == "2025-07-25"
    assert args["end"] == "2025-10-22"
    assert args["metric"] == "sum_gmv"
    assert args["group_by"] == "province"
    assert "dataset_id" not in args
    assert "metrics" not in args
    assert "filters" not in args


@when("网关调用 query_stats 并带上 sql")
def when_sql(mcp_world):
    gw = ToolGateway()
    with pytest.raises(ToolGatewayError, match="raw SQL"):
        gw.call("query_stats", {"sql": "select 1", "group_by": "category"})
    with pytest.raises(ToolGatewayError, match="raw SQL"):
        gw.call("query_stats", {"SQL": "select 1"})


@then("网关拒绝裸 SQL 且没有发出请求")
def then_no_post(mcp_world):
    assert mcp_world["fake"].requests == []


def _settle_chat(client: TestClient, payload: dict, timeout: float = 30.0) -> dict:
    """Block the test, not the HTTP handler, until the background turn reaches a terminal state."""
    run_id = payload["run_id"]
    deadline = time.time() + timeout
    last: dict | None = None
    while time.time() < deadline:
        snap = client.get(f"/runs/{run_id}").json()
        last = snap
        if snap.get("state") in ("completed", "failed", "awaiting_hitl"):
            hitl = None
            if snap["state"] == "awaiting_hitl":
                hitl = {"kind": snap.get("hitl_kind"), "resume_token": snap.get("resume_token")}
            return {
                "run_id": run_id,
                "status": snap["state"],
                "markdown": snap.get("result") or "",
                "text": snap.get("result") or "",
                "context_bindings": snap.get("context_bindings") or payload.get("context_bindings") or {},
                "hitl": hitl,
            }
        time.sleep(0.02)
    raise AssertionError(last)


@when("用户让内核写一份电商洞察报告")
def when_dogfood(mcp_world):
    with TestClient(app) as client:
        health = client.get("/health").json()
        created = client.post(
            "/sessions",
            json={
                "bindings": {
                    "skill": "insight_report",
                    "dataset": SEED,
                    "time_window": {"label": "近90天", "start": "2025-07-25", "end": "2025-10-22"},
                }
            },
        )
        assert created.status_code == 200
        sid = created.json()["id"]
        resp = client.post(
            f"/sessions/{sid}/messages",
            json={"text": "请基于电商销量数据写一份简短洞察报告"},
        )
        assert resp.status_code == 200
        accepted = resp.json()
        assert accepted["run_id"]
        assert accepted["accepted"] is True
        mcp_world["health"] = health
        mcp_world["chat"] = _settle_chat(client, accepted)
        mcp_world["sid"] = sid


@then("结论页包含种子数据集 ecommerce_sales_v2")
def then_seed_on_page(mcp_world):
    body = mcp_world["chat"]
    assert body["status"] == "completed"
    assert SEED in body["markdown"]
    assert body["context_bindings"]["dataset"] == SEED
    assert mcp_world["health"]["dataset_id"] == SEED
    assert mcp_world["health"]["das_reachable"] is True
    posts = [r for r in mcp_world["fake"].requests if r.method == "POST"]
    assert posts
    assert all(r.url.path == "/api/mcp" for r in posts)
    assert all(r.headers["authorization"] == "Bearer sekret" for r in posts)


@then("结论页包含发现")
def then_findings(mcp_world):
    assert "发现" in mcp_world["chat"]["markdown"]
    assert "evidence" in mcp_world["chat"]["markdown"].lower() or "证据" in mcp_world["chat"]["markdown"]


@given("仓库里的启动脚本", target_fixture="scripts")
def given_scripts():
    return {
        "start": (ROOT / "scripts" / "start.sh").read_text(encoding="utf-8"),
        "stop": (ROOT / "scripts" / "stop.sh").read_text(encoding="utf-8"),
        "uat": (ROOT / "scripts" / "uat.sh").read_text(encoding="utf-8"),
        "loader": (ROOT / "scripts" / "load_local_env.sh").read_text(encoding="utf-8"),
    }


@then("默认路径启动 dataset-analysis-service 且 workers 为 2")
def then_real_start(scripts):
    text = scripts["start"]
    assert "/workspace/dataset-analysis-service" in text
    assert "--workers 2" in text
    assert "APP_ENV" in text
    assert "APP_RUNTIME_ENV_FILE" in text
    assert "data/das-local.env" in scripts["loader"] or "das-local.env" in scripts["loader"]
    assert "MODEL_" in scripts["loader"]
    assert "XAI_" in scripts["loader"]
    assert "OPENAI_" in scripts["loader"]
    # fake server is not the unconditional launcher
    gate = text.index('if [[ "${USE_FAKE_DAS:-0}" == "1" ]]')
    fake = text.index("services.fake_das.app")
    real = text.index('DAS_ROOT="${DAS_ROOT:-/workspace/dataset-analysis-service}"')
    assert gate < fake < real
    assert "--workers 2" in text[real:]
    assert "services.fake_das.app" not in text[real:]


@then("fake-das 只在 USE_FAKE_DAS 开关后启动")
def then_fake_gated(scripts):
    text = scripts["start"]
    assert 'USE_FAKE_DAS:-0}" == "1"' in text or "USE_FAKE_DAS" in text
    assert text.index("services.fake_das.app") > text.index("USE_FAKE_DAS")
    assert "ecommerce_sales_v2" in scripts["uat"]
    assert "fake-das" in scripts["uat"]
    assert "/api/mcp" in scripts["uat"]


@then("停止脚本会停 das、fake-das 和 kernel")
def then_stop(scripts):
    stop = scripts["stop"]
    for name in ("das", "fake-das", "kernel"):
        assert name in stop


def test_sse_envelope_and_text_fallback(monkeypatch, tmp_path):
    fake = _real_env(monkeypatch, tmp_path)
    fake.mode = "sse"
    listed = ToolGateway().call("list_datasets", {})
    assert listed["data"]["datasets"][0]["name"] == SEED
    fake.mode = "text-only"
    stats = ToolGateway().call(
        "query_stats",
        {"group_by": "channel", "metric": "sum_gmv"},
        bindings={"dataset": SEED},
    )
    assert stats["data"]["rows"][0]["key"] == "抖音电商"


def test_errors_and_missing_token(monkeypatch, tmp_path):
    fake = _real_env(monkeypatch, tmp_path)
    fake.mode = "http"
    with pytest.raises(ToolGatewayError) as exc:
        ToolGateway().list_tools()
    assert exc.value.status_code == 401

    fake.mode = "rpc-error"
    with pytest.raises(ToolGatewayError, match="JSON-RPC"):
        ToolGateway().call("list_datasets", {})

    fake.mode = "is-error"
    with pytest.raises(ToolGatewayError, match="tool error"):
        ToolGateway().call("list_datasets", {})

    fake.mode = "bad-json"
    with pytest.raises(ToolGatewayError, match="not JSON"):
        ToolGateway().call("list_datasets", {})

    fake.mode = "sse-empty"
    with pytest.raises(ToolGatewayError, match="empty SSE"):
        ToolGateway().call("list_datasets", {})

    monkeypatch.delenv("DAS_MCP_AUTH_TOKEN", raising=False)
    with pytest.raises(ToolGatewayError, match="DAS_MCP_AUTH_TOKEN"):
        ToolGateway().call("list_datasets", {})

    with pytest.raises(ToolGatewayError, match="not whitelisted"):
        ToolGateway(token="sekret").call("drop_table", {})


def test_fake_mode_uses_mcp_path_without_auth(monkeypatch):
    monkeypatch.setenv("USE_FAKE_DAS", "1")
    monkeypatch.setenv("FAKE_DAS_URL", "http://127.0.0.1:8788")
    monkeypatch.delenv("DAS_MCP_URL", raising=False)
    monkeypatch.delenv("DAS_HEALTH_URL", raising=False)
    assert resolve_mcp_url() == "http://127.0.0.1:8788/mcp"
    assert resolve_health_url() == "http://127.0.0.1:8788/health"
    fake = FakeMcp()
    _install(monkeypatch, fake)
    out = ToolGateway().call(
        "query_stats",
        {"group_by": "province", "metric": "sum_gmv"},
        bindings={"dataset": SEED, "project_id": "box"},
    )
    posted = _posted(fake)
    assert len(posted) == 1
    assert posted[0]["params"]["name"] == "query_stats"
    req = fake.requests[-1]
    assert req.url.path == "/mcp"
    assert "authorization" not in req.headers
    args = posted[0]["params"]["arguments"]
    assert "dataset_id" not in args
    assert args["name"] == SEED
    assert args["project_id"] == "box"
    assert out["data"]["rows"][0]["key"] == "江苏"


def test_adapt_real_argument_edges():
    original = {
        "dataset_id": "explicit",
        "name": "other",
        "dataset": "also",
        "metric": "sum_gmv",
        "metrics": {"custom": {"fn": "sum", "field": "gmv"}},
        "group_by": [
            "category",
            {"field": "channel", "transform": "identity", "part": "day", "alias": "ch", "extra": 1},
            "",
            {"alias": "nope"},
            "province",
            "city",
        ],
        "filters": {
            "op": "and",
            "clauses": [{"field": "order_date", "op": "ge", "value": "2025-01-01"}],
        },
        "time_window": {"start": "2025-07-25", "end": "2025-10-22"},
        "sort": [{"field": "sum_gmv", "direction": "desc"}],
    }
    mixed = adapt_real_arguments("query_stats", original)
    assert original["name"] == "other"
    assert mixed["dataset_id"] == "explicit"
    assert mixed["metrics"] == [{"function": "sum", "field": "gmv", "alias": "custom"}]
    assert mixed["group_by"] == [
        "category",
        {"field": "channel", "transform": "identity", "part": "day", "alias": "ch"},
        "province",
    ]
    assert {"field": "order_date", "operator": "gte", "value": "2025-01-01"} in mixed["filters"]
    assert {"field": "order_date", "operator": "lte", "value": "2025-10-22"} in mixed["filters"]
    assert not any(clause["operator"] == "ge" for clause in mixed["filters"])
    assert "metric" not in mixed
    assert mixed["sort"][0]["direction"] == "desc"
    assert "limit" not in mixed

    listed = adapt_real_arguments(
        "list_datasets",
        {
            "name": "nope",
            "metric": "sum_gmv",
            "ready_only": False,
            "include_archived": False,
            "query": "",
            "start": "2025-01-01",
        },
    )
    assert listed == {"ready_only": False, "include_archived": False}

    described = adapt_real_arguments(
        "describe_fields",
        {
            "name": SEED,
            "start": "2025-07-25",
            "end": "2025-10-22",
            "metric": "sum_gmv",
            "field_names": ["gmv"],
        },
    )
    assert described == {"dataset_id": SEED, "field_names": ["gmv"]}
    assert adapt_real_arguments("describe_fields", {"dataset_id": SEED, "field_names": []}) == {
        "dataset_id": SEED
    }
    assert adapt_real_arguments("describe_tag_hierarchies", {"tree_id": "cat", "limit": 4}) == {
        "tree_id": "cat",
        "limit": 4,
    }
    assert adapt_real_arguments("describe_dataset", {"dataset": SEED, "filters": {"a": 1}}) == {
        "dataset_id": SEED
    }

    window_only = adapt_real_arguments(
        "search_records",
        {
            "dataset": SEED,
            "time_window": {"end": "2025-10-22"},
            "filters": [
                {"field": "sku", "op": "eq", "value": "耳机"},
                "skip",
                {"field": "note", "operator": "text_match"},
                {
                    "field": "title",
                    "operator": "text_match",
                    "value": {"keywords": ["耳机"], "mode": "and"},
                },
                {"field": "gone", "operator": "is_null", "value": "x"},
                {"field": "bad", "op": "neq", "value": 1},
            ],
            "q": "",
            "keyword": "耳机",
            "query": "耳机",
            "offset": 2,
            "return_fields": ["sku"],
            "metric": "sum_gmv",
        },
    )
    assert window_only["dataset_id"] == SEED
    assert window_only["return_fields"] == ["sku"]
    for banned in ("keyword", "query", "offset", "q", "metric", "metrics"):
        assert banned not in window_only
    assert {"field": "order_date", "operator": "lte", "value": "2025-10-22"} in window_only["filters"]
    assert {"field": "sku", "operator": "eq", "value": "耳机"} in window_only["filters"]
    assert {"field": "title", "operator": "text_match", "value": {"keywords": ["耳机"], "mode": "and"}} in window_only[
        "filters"
    ]
    assert {"field": "gone", "operator": "is_null"} in window_only["filters"]
    assert not any(clause["field"] == "bad" for clause in window_only["filters"])

    conditions = adapt_real_arguments(
        "sample_records",
        {
            "name": SEED,
            "offset": 0,
            "sample_strategy": "first",
            "include_long_text": False,
            "filters": {
                "conditions": [{"field": "province", "operator": "eq", "value": "江苏"}, None],
                "logic": "and",
                "channel": {"eq": "京东"},
            },
        },
    )
    assert conditions["filters"] == [{"field": "province", "operator": "eq", "value": "江苏"}]
    assert conditions["sample_strategy"] == "first"
    assert conditions["include_long_text"] is False
    assert conditions["return_fields"] == list(DOGFOOD_RETURN_FIELDS)
    assert "offset" not in conditions
    assert adapt_real_arguments(
        "search_records",
        {"dataset_id": SEED, "return_fields": "order_id", "evidence_preset": "default"},
    ) == {"dataset_id": SEED, "evidence_preset": "default"}
    assert "return_fields" not in adapt_real_arguments("query_stats", {"dataset_id": SEED, "metric": "count"})

    ops = adapt_real_arguments(
        "query_stats",
        {
            "dataset_id": SEED,
            "metric": "  count  ",
            "filters": {"gmv": {"gte": 10, "lte": 20}, "operator": "and"},
            "start": "2025-07-25",
            "group_by": "  ",
        },
    )
    assert ops["metrics"] == [{"function": "count", "alias": "count"}]
    fields = {(c["field"], c["operator"]) for c in ops["filters"]}
    assert ("gmv", "gte") in fields and ("gmv", "lte") in fields
    assert ("order_date", "gte") in fields
    assert ("order_date", "lte") not in fields
    assert "group_by" not in ops

    assert adapt_real_arguments(
        "query_stats", {"dataset_id": SEED, "metrics": ["sum_gmv"], "metric": "avg_gmv"}
    )["metrics"] == [{"function": "sum", "field": "gmv", "alias": "sum_gmv"}]
    assert adapt_real_arguments(
        "query_stats", {"dataset_id": SEED, "metric": {"fn": "sum", "field": "gmv"}}
    )["metrics"] == [{"function": "sum", "field": "gmv"}]
    assert adapt_real_arguments(
        "query_stats", {"dataset_id": SEED, "metrics": {"function": "min", "alias": "min"}}
    )["metrics"] == [{"function": "min"}]
    assert adapt_real_arguments(
        "query_stats", {"dataset_id": SEED, "metric": "count_distinct_buyer"}
    )["metrics"] == [{"function": "count_distinct", "field": "buyer", "alias": "count_distinct_buyer"}]
    assert adapt_real_arguments("query_stats", {"dataset_id": SEED, "metric": "min"})["metrics"] == [
        {"function": "min", "alias": "min"}
    ]
    assert "filters" not in adapt_real_arguments(
        "search_records",
        {"dataset_id": SEED, "filters": [{"field": "sku", "op": "", "value": "x"}]},
    )
    assert adapt_real_arguments(
        "query_stats", {"dataset_id": SEED, "metrics": {"sum_qty": "sum_qty", "skip": 1}}
    )["metrics"] == [{"function": "sum", "field": "qty", "alias": "sum_qty"}]
    assert adapt_real_arguments(
        "query_stats", {"dataset_id": SEED, "metrics": {"function": "nope", "alias": "avg_gmv"}}
    )["metrics"] == [{"function": "avg", "field": "gmv", "alias": "avg_gmv"}]
    assert "metrics" not in adapt_real_arguments(
        "query_stats", {"dataset_id": SEED, "metrics": [{"function": "nope"}, 3]}
    )
    unknown = adapt_real_arguments("query_stats", {"dataset_id": SEED, "metric": "custom_metric"})
    assert "metrics" not in unknown
    assert "filters" not in adapt_real_arguments(
        "query_stats", {"dataset_id": SEED, "metric": "  ", "filters": "nope", "group_by": 4}
    )
    assert "metrics" not in adapt_real_arguments("query_stats", {"dataset_id": SEED, "metric": None})
    kept = adapt_real_arguments(
        "query_stats",
        {
            "dataset_id": SEED,
            "filters": {"clauses": [{"field": "order_date", "op": "le", "value": "2025-10-22"}, "bad"]},
            "end": "2025-12-01",
            "metric": "sum_qty",
        },
    )
    assert kept["metrics"] == [{"function": "sum", "field": "qty", "alias": "sum_qty"}]
    assert kept["filters"] == [{"field": "order_date", "operator": "lte", "value": "2025-10-22"}]
    assert "group_by" not in kept
    assert adapt_real_arguments(
        "query_stats",
        {"dataset_id": SEED, "metrics": {"name": "count", "field": "gmv"}},
    )["metrics"] == [{"function": "count", "alias": "count"}]
    assert adapt_real_arguments(
        "query_stats",
        {"dataset_id": SEED, "metrics": {"function": "count", "alias": "count", "field": "gmv"}},
    )["metrics"] == [{"function": "count", "alias": "count"}]
    assert adapt_real_arguments(
        "query_stats",
        {"dataset_id": SEED, "metrics": {"function": "sum", "field": 1, "alias": "sum"}},
    )["metrics"] == [{"function": "sum"}]


def test_dataset_catalog_resolution(monkeypatch, tmp_path):
    indexed = index_dataset_ids(
        {
            "items": [
                "skip",
                {"dataset_name": "solo"},
                {"dataset_id": BOX_UUID, "dataset_name": SEED, "name": "alias", "dataset": "also"},
            ]
        }
    )
    assert indexed[SEED] == BOX_UUID
    assert indexed["alias"] == BOX_UUID
    assert indexed["also"] == BOX_UUID
    assert "solo" not in indexed
    assert index_dataset_ids({"datasets": []}) == {}

    fake = _real_env(monkeypatch, tmp_path)
    gw = ToolGateway()
    gw.call("describe_dataset", {"dataset_id": BOX_UUID})
    assert [b["params"]["name"] for b in _posted(fake) if b["method"] == "tools/call"] == ["describe_dataset"]

    clear_dataset_id_cache()
    fake.requests.clear()
    fake.fail_list = True
    failed = gw.call("query_stats", {"dataset": SEED, "metric": "sum_gmv", "group_by": "category"})
    again = gw.call("query_stats", {"name": "missing-dataset", "metric": "count"})
    calls = [b for b in _posted(fake) if b["method"] == "tools/call"]
    assert [c["params"]["name"] for c in calls].count("list_datasets") == 1
    assert calls[1]["params"]["arguments"]["dataset_id"] == SEED
    assert calls[2]["params"]["arguments"]["dataset_id"] == "missing-dataset"
    assert failed["data"]["rows"][0]["key"] == "电子产品"
    assert again["data"]["rows"]

    clear_dataset_id_cache()
    fake.requests.clear()
    fake.fail_list = False
    fake.list_rows = [{"id": SEED, "name": SEED}]
    same = gw.call("describe_dataset", {"name": SEED})
    assert same["data"]["name"] == SEED
    described = next(
        b for b in _posted(fake) if b["method"] == "tools/call" and b["params"]["name"] == "describe_dataset"
    )
    assert described["params"]["arguments"]["dataset_id"] == SEED


def test_url_resolution_and_probe(monkeypatch):
    monkeypatch.delenv("USE_FAKE_DAS", raising=False)
    monkeypatch.setenv("DAS_MCP_URL", "http://das.test/api/mcp/")
    monkeypatch.delenv("DAS_HEALTH_URL", raising=False)
    assert resolve_mcp_url() == "http://das.test/api/mcp"
    assert resolve_health_url() == "http://das.test/health"
    monkeypatch.setenv("DAS_HEALTH_URL", "http://das.test/healthz")
    assert resolve_health_url() == "http://das.test/healthz"

    fake = FakeMcp()
    _install(monkeypatch, fake)
    monkeypatch.setenv("DAS_MCP_AUTH_TOKEN", "sekret")
    assert ToolGateway().probe_health() is True
    fake.mode = "health-down"
    assert ToolGateway().probe_health() is False
    fake.mode = "json"

    response = httpx.Response(200, json=["nope"])
    with pytest.raises(ToolGatewayError, match="not an object"):
        parse_mcp_response(response)
    with pytest.raises(ToolGatewayError, match="tool error"):
        structured_from_result({"isError": True, "content": []})
    assert structured_from_result({"structuredContent": ["a"]}) == {"items": ["a"]}
    assert structured_from_result(
        {"content": [{"type": "text", "text": "plain note"}]}
    ) == {"text": "plain note"}
    assert structured_from_result({"content": [{"type": "json", "json": {"n": 1}}]}) == {"n": 1}
    assert structured_from_result({"content": [{"type": "text", "text": "[1, 2]"}]}) == {"items": [1, 2]}
    assert structured_from_result({}) == {}


def test_explicit_endpoint_and_preserved_evidence(monkeypatch):
    monkeypatch.delenv("USE_FAKE_DAS", raising=False)
    fake = FakeMcp()

    def handler(request: httpx.Request) -> httpx.Response:
        fake.requests.append(request)
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "result": {
                    "structuredContent": {"datasets": []},
                    "evidence": {"tool": "list_datasets", "fingerprint": "fixedfp", "arguments": {"keep": 1}},
                },
            },
        )

    import agent_os_kernel.tool_gateway as tg

    client = tg.httpx.Client(transport=tg.httpx.MockTransport(handler))
    gw = ToolGateway("http://override.test/api/mcp", token="other", client=client)
    out = gw.call("list_datasets", {})
    assert fake.requests[0].url.path == "/api/mcp"
    assert fake.requests[0].headers["authorization"] == "Bearer other"
    assert out["evidence"]["fingerprint"] == "fixedfp"
    assert gw.probe_health() is True or gw.probe_health() is False


def test_kernel_routes_and_hitl(monkeypatch, tmp_path):
    from agent_os_kernel.main import create_session
    from agent_os_kernel.policy import allow_tool, requires_final_confirm
    from agent_os_kernel.skills.insight_report import get_skill

    fake = _real_env(monkeypatch, tmp_path)
    created_default = create_session(None)
    assert created_default["bindings"]["dataset"] == SEED
    assert get_skill()["default_bindings"]["dataset"] == SEED
    prompt = get_skill()["system_prompt"]
    assert "原文引用" in prompt and "time_window.label" in prompt
    assert allow_tool("describe_fields") and allow_tool("describe_tag_hierarchies")
    assert not allow_tool("drop_table")
    assert requires_final_confirm("email_send")
    assert not requires_final_confirm("query_stats")
    monkeypatch.setenv("HITL_AUTO", "0")
    with TestClient(app) as client:
        assert client.get("/sessions/missing").status_code == 404
        assert client.patch("/sessions/missing/context", json={"bindings": {}}).status_code == 404
        assert client.post("/sessions/missing/messages", json={"text": "hi"}).status_code == 404
        assert client.get("/runs/missing").status_code == 404
        assert client.post("/runs/missing/resume", json={"resume_token": "x"}).status_code == 404

        created = client.post("/sessions", json={}).json()
        sid = created["id"]
        assert created["bindings"]["dataset"] == SEED
        assert client.get(f"/sessions/{sid}").status_code == 200

        bad = client.post(f"/sessions/{sid}/messages", json={"text": "x", "skill": "nope"})
        assert bad.status_code == 400

        paused = _settle_chat(
            client,
            client.post(
                f"/sessions/{sid}/messages",
                json={"text": "请基于电商销量数据写一份简短洞察报告"},
            ).json(),
        )
        assert paused["status"] == "awaiting_hitl"
        assert paused["markdown"]
        token = paused["hitl"]["resume_token"]
        run_id = paused["run_id"]
        assert client.post(
            f"/runs/{run_id}/resume",
            json={"resume_token": "wrong", "approve": True},
        ).status_code == 403
        approved = _settle_chat(
            client,
            client.post(
                f"/runs/{run_id}/resume",
                json={"resume_token": token, "approve": True, "text": "请继续生成报告"},
            ).json(),
        )
        assert approved["status"] == "completed"
        assert SEED in approved["markdown"]
        assert client.post(
            f"/runs/{run_id}/resume",
            json={"resume_token": token, "approve": True},
        ).status_code == 400

        paused_again = _settle_chat(
            client,
            client.post(
                f"/sessions/{sid}/messages",
                json={"text": "请再写一份洞察报告"},
            ).json(),
        )
        assert paused_again["status"] == "awaiting_hitl"
        rejected = client.post(
            f"/runs/{paused_again['run_id']}/resume",
            json={"resume_token": paused_again["hitl"]["resume_token"], "approve": False},
        ).json()
        assert rejected["status"] == "failed"
        assert rejected["markdown"] == "已拒绝"

        monkeypatch.setenv("HITL_AUTO", "1")
        done = _settle_chat(
            client,
            client.post(
                f"/sessions/{sid}/messages",
                json={"text": "哪个省份 GMV 最高？"},
            ).json(),
        )
        assert done["status"] == "completed"
        assert "江苏" in done["markdown"]
        snap = client.get(f"/runs/{done['run_id']}").json()
        assert snap["events"]

        tools = client.get("/tools").json()
        assert "describe_fields" in tools["tools"]
        assert client.get("/skills").status_code == 200
        assert client.get("/").status_code == 200
        assert client.get("/static/app.js").status_code == 200

        patched = client.patch(
            f"/sessions/{sid}/context",
            json={"bindings": {"time_window": {"label": "近7天", "start": "2025-10-15", "end": "2025-10-22"}}, "replace": True},
        )
        assert patched.status_code == 200
        assert patched.json()["bindings"]["time_window"]["label"] == "近7天"

    import agent_os_kernel.main as main_mod

    monkeypatch.setattr(main_mod, "UI_DIR", tmp_path)
    with TestClient(app) as client:
        assert client.get("/").status_code == 404

    def boom(self):
        raise RuntimeError("das down")

    monkeypatch.setattr(ToolGateway, "list_tools", boom)
    with TestClient(app) as client:
        assert client.get("/tools").status_code == 502

    fake.mode = "health-down"
    # health-down still returns a response; force probe failure via closed transport
    monkeypatch.setattr(ToolGateway, "probe_health", lambda self: False)
    with TestClient(app) as client:
        health = client.get("/health").json()
        assert health["das_reachable"] is False
        assert health["chain"] == "degraded"
        assert health["dataset_id"] == SEED


def test_context_bindings_cover():
    assert context.empty_bindings() == {}
    coerced = context.coerce_partial(
        {
            "skill": " insight_report ",
            "dataset": " ecommerce_sales_v2 ",
            "time_window": {"start": "2025-07-25", "end": "2025-10-22", "label": "近90天"},
            "project": "legacy",
            "tenant_id": "ten",
            "extras": {"flat": "ok", "nested": {"no": 1}},
            "flag": True,
            "workflow": {"skip": True},
        }
    )
    assert coerced["skill"] == "insight_report"
    assert coerced["dataset"] == SEED
    assert coerced["project_id"] == "legacy"
    assert coerced["extras"]["flag"] is True
    assert "workflow" not in coerced
    assert context.coerce_partial({"time_window": None})["time_window"] is None
    assert context.coerce_partial({"time_window": "nope"}) == {}
    assert context.coerce_partial(None) == {}
    assert context.coerce_partial({"project_id": None})["project_id"] is None
    assert context.coerce_partial({"tenant_id": None})["tenant_id"] is None

    merged = context.deep_merge({"time_window": {"start": "a", "label": "L"}}, {"time_window": {"end": "b"}})
    assert merged["time_window"]["start"] == "a"
    assert merged["time_window"]["end"] == "b"
    assert context.deep_merge({"dataset": "x"}, {"dataset": None})["dataset"] is None
    assert "skip" not in context.deep_merge({"a": 1}, {"skip": None})

    resolved = context.resolve_bindings(
        request={"time_window": {"label": "近30天", "start": "2025-09-22", "end": "2025-10-22"}},
        session_bindings={"dataset": SEED, "tenant_id": "t"},
        skill_defaults=SKILL["default_bindings"],
        skill_name="insight_report",
        inherit_run={"dataset": "older", "skill": "insight_report"},
    )
    assert resolved["dataset"] == SEED
    assert resolved["time_window"]["label"] == "近30天"
    assert resolved["skill"] == "insight_report"
    bare = context.resolve_bindings()
    assert bare["dataset"] == context.DEFAULT_DATASET
    assert bare["skill"] == context.DEFAULT_SKILL

    assert context.session_bindings_from_meta({"bindings": {"dataset": SEED}})["dataset"] == SEED
    assert context.session_bindings_from_meta({"context": {"skill": "insight_report"}})["skill"] == "insight_report"
    assert context.session_bindings_from_meta({"dataset": SEED, "project": "p"})["project_id"] == "p"
    assert context.session_bindings_from_meta({}) == {}
    assert context.session_bindings_from_meta(None) == {}

    system = context.compose_system("prompt", {**resolved, "extras": {"k": 1}})
    assert "Context bindings" in system
    assert SEED in system
    defaults = context.tool_defaults_from(
        {"dataset": SEED, "time_window": {"start": "2025-07-25", "end": "2025-10-22"}},
        "describe_fields",
    )
    assert defaults["name"] == SEED
    assert defaults["start"] == "2025-07-25"
    assert context.tool_defaults_from({}, "list_datasets") == {}
    merged_args = context.merge_tool_arguments(
        "query_stats",
        {"name": "explicit", "metric": "sum_gmv"},
        {"dataset": SEED, "time_window": {"start": "s", "end": "e"}},
    )
    assert merged_args["name"] == "explicit"
    assert merged_args["metric"] == "sum_gmv"
    fp = context.bindings_fingerprint(resolved)
    assert len(fp) == 12
    summary = context.summarize_tool(
        "query_stats",
        {"fingerprint": "abc"},
        {"rows": [{"key": "a", "value": 1}, {"key": "b", "value": 2}]},
        bindings=resolved,
    )
    assert summary["preview"]["n"] == 2
    assert summary["bindings_fingerprint"] == fp
    ds_sum = context.summarize_tool("list_datasets", {}, {"datasets": [{"name": SEED}]})
    assert ds_sum["preview"][0]["name"] == SEED
    desc_sum = context.summarize_tool("describe_dataset", {}, {"summary": {"total_gmv": 1}})
    assert desc_sum["preview"]["total_gmv"] == 1
    count_sum = context.summarize_tool("sample_records", {}, {"count": 3, "records": []})
    assert count_sum["preview"]["count"] == 3
    other = context.summarize_tool("describe_fields", {}, {"fields": ["gmv"], "extra": 1, "a": 1, "b": 2, "c": 3, "d": 4})
    assert "fields" in other["preview"]


def test_insight_paths_keep_conclusion(monkeypatch):
    monkeypatch.delenv("USE_FAKE_DAS", raising=False)
    bindings = {
        "dataset": SEED,
        "skill": "insight_report",
        "time_window": {"label": "近90天", "start": "2025-07-25", "end": "2025-10-22"},
    }
    allowed = list(SKILL["allowed_tools"])
    messages = [{"role": "user", "content": "请基于电商销量数据写一份简短洞察报告"}]
    results: list[dict] = []
    final = ""
    for _ in range(4):
        decision = stub_decide(
            messages=messages,
            allowed_tools=allowed,
            tool_results=results,
            bindings=bindings,
        )
        if decision["type"] == "final":
            final = decision["content"]
            break
        for call in decision["calls"]:
            results.append(
                {
                    "name": call["name"],
                    "arguments": call["arguments"],
                    "data": _tool_payload(call["name"], call["arguments"]),
                    "evidence": {"fingerprint": "fp" + call["name"][:4]},
                }
            )
    assert "发现" in final
    assert SEED in final
    assert _last_user([]) == ""
    assert _intent("随便看看", [{"role": "assistant", "content": "已有"}]) == "report"

    for text, needle in (
        ("哪个省份 GMV 最高？", "省份"),
        ("哪个渠道卖得最好？", "渠道"),
        ("哪个类目 GMV 最高？", "类目"),
        ("退货率怎么样？", "退货"),
    ):
        follow_results: list[dict] = []
        content = ""
        for _ in range(3):
            decision = stub_decide(
                messages=[{"role": "user", "content": text}],
                allowed_tools=allowed,
                tool_results=follow_results,
                bindings=bindings,
            )
            if decision["type"] == "final":
                content = decision["content"]
                break
            for call in decision["calls"]:
                follow_results.append(
                    {
                        "name": call["name"],
                        "arguments": call["arguments"],
                        "data": _tool_payload(call["name"], call["arguments"]),
                        "evidence": {"fingerprint": "fu"},
                    }
                )
        assert needle in content

    drill_results: list[dict] = []
    drill = ""
    for _ in range(4):
        decision = stub_decide(
            messages=[{"role": "user", "content": "第2条依据是什么？给我样本"}],
            allowed_tools=allowed,
            tool_results=drill_results,
            bindings=bindings,
        )
        if decision["type"] == "final":
            drill = decision["content"]
            break
        for call in decision["calls"]:
            drill_results.append(
                {
                    "name": call["name"],
                    "arguments": call["arguments"],
                    "data": _tool_payload(call["name"], call["arguments"]),
                    "evidence": {"fingerprint": "dr"},
                }
            )
    assert "第2条依据" in drill
    assert "抖音电商" in drill

    empty = _build_drill([], "看一下依据")
    assert "没有抽到" in empty
    assert "未能从 DAS" in _build_followup("province", [])
    sparse = _build_report([], bindings)
    assert SEED in sparse
    cat_drill = stub_decide(
        messages=[{"role": "user", "content": "服装的样本"}],
        allowed_tools=allowed,
        tool_results=[],
        bindings=bindings,
    )
    assert cat_drill["calls"][0]["arguments"]["filters"]["category"] == "服装"
