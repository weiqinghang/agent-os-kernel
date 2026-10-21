"""Fake DAS: HTTP MCP subset over SQLite ecommerce sales."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "services" / "fake_das" / "data" / "sales.db"

ALLOWED_GROUP_BY = {"category", "channel", "province", "sku", "order_date"}
ALLOWED_METRICS = {"sum_gmv", "sum_qty", "count", "avg_gmv", "return_rate"}
WHITELIST = {
    "list_datasets",
    "describe_dataset",
    "query_stats",
    "search_records",
    "sample_records",
}

app = FastAPI(title="fake-das", version="0.1.0")


def _conn() -> sqlite3.Connection:
    if not DB_PATH.exists():
        raise HTTPException(503, f"DB missing: {DB_PATH}; run scripts/seed_ecommerce.py")
    c = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    return c


def _fingerprint(name: str, args: dict[str, Any]) -> str:
    raw = json.dumps({"name": name, "args": args}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()[:12]


@app.get("/health")
def health() -> dict[str, Any]:
    ok = DB_PATH.exists()
    n = 0
    if ok:
        with _conn() as c:
            n = c.execute("SELECT COUNT(*) FROM sales").fetchone()[0]
    return {"status": "ok" if ok else "degraded", "rows": n, "service": "fake-das"}


class McpRequest(BaseModel):
    jsonrpc: str = "2.0"
    id: int | str | None = 1
    method: str
    params: dict[str, Any] = Field(default_factory=dict)


def _list_datasets(_args: dict[str, Any]) -> dict[str, Any]:
    with _conn() as c:
        rows = c.execute("SELECT name, description, row_count FROM datasets_meta").fetchall()
    return {
        "datasets": [
            {"name": r["name"], "description": r["description"], "row_count": r["row_count"]}
            for r in rows
        ]
    }


def _describe_dataset(args: dict[str, Any]) -> dict[str, Any]:
    name = args.get("name") or args.get("dataset") or "ecommerce_sales"
    with _conn() as c:
        row = c.execute(
            "SELECT name, description, row_count, columns FROM datasets_meta WHERE name=?",
            (name,),
        ).fetchone()
        if not row:
            raise HTTPException(404, f"dataset not found: {name}")
        stats = c.execute(
            """
            SELECT MIN(order_date) AS min_date, MAX(order_date) AS max_date,
                   ROUND(SUM(gmv),2) AS total_gmv, SUM(qty) AS total_qty,
                   ROUND(AVG(returned)*100,2) AS return_pct
            FROM sales
            """
        ).fetchone()
    return {
        "name": row["name"],
        "description": row["description"],
        "row_count": row["row_count"],
        "columns": row["columns"].split(","),
        "summary": dict(stats),
    }


def _query_stats(args: dict[str, Any]) -> dict[str, Any]:
    # Reject raw SQL
    if "sql" in args or "query" in args and isinstance(args.get("query"), str) and (
        "select" in str(args.get("query")).lower()
    ):
        raise HTTPException(400, "raw SQL not allowed; use group_by/metric/filters")
    if any(k in args for k in ("sql",)):
        raise HTTPException(400, "raw SQL not allowed")

    group_by = args.get("group_by") or "category"
    metric = args.get("metric") or "sum_gmv"
    limit = min(int(args.get("limit") or 20), 50)
    filters = args.get("filters") or {}

    if group_by not in ALLOWED_GROUP_BY:
        raise HTTPException(400, f"group_by not allowed: {group_by}")
    if metric not in ALLOWED_METRICS:
        raise HTTPException(400, f"metric not allowed: {metric}")

    where = []
    params: list[Any] = []
    for k, v in filters.items():
        if k not in ALLOWED_GROUP_BY | {"returned"}:
            raise HTTPException(400, f"filter field not allowed: {k}")
        where.append(f"{k}=?")
        params.append(v)
    where_sql = (" WHERE " + " AND ".join(where)) if where else ""

    metric_sql = {
        "sum_gmv": "ROUND(SUM(gmv),2)",
        "sum_qty": "SUM(qty)",
        "count": "COUNT(*)",
        "avg_gmv": "ROUND(AVG(gmv),2)",
        "return_rate": "ROUND(AVG(returned)*100,2)",
    }[metric]

    sql = (
        f"SELECT {group_by} AS key, {metric_sql} AS value "
        f"FROM sales{where_sql} GROUP BY {group_by} "
        f"ORDER BY value DESC LIMIT ?"
    )
    params.append(limit)
    with _conn() as c:
        rows = c.execute(sql, params).fetchall()
    return {
        "group_by": group_by,
        "metric": metric,
        "filters": filters,
        "rows": [{"key": r["key"], "value": r["value"]} for r in rows],
    }


def _search_records(args: dict[str, Any]) -> dict[str, Any]:
    q = (args.get("q") or args.get("keyword") or "").strip()
    limit = min(int(args.get("limit") or 20), 50)
    field = args.get("field")
    with _conn() as c:
        if field and field in ALLOWED_GROUP_BY | {"sku", "order_id"}:
            rows = c.execute(
                f"SELECT * FROM sales WHERE CAST({field} AS TEXT) LIKE ? LIMIT ?",
                (f"%{q}%", limit),
            ).fetchall()
        elif q:
            rows = c.execute(
                """
                SELECT * FROM sales
                WHERE sku LIKE ? OR category LIKE ? OR channel LIKE ?
                   OR province LIKE ? OR order_id LIKE ?
                LIMIT ?
                """,
                (f"%{q}%", f"%{q}%", f"%{q}%", f"%{q}%", f"%{q}%", limit),
            ).fetchall()
        else:
            rows = c.execute("SELECT * FROM sales LIMIT ?", (limit,)).fetchall()
    return {"count": len(rows), "records": [dict(r) for r in rows]}


def _sample_records(args: dict[str, Any]) -> dict[str, Any]:
    limit = min(int(args.get("limit") or 10), 20)
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM sales ORDER BY id LIMIT ?",
            (limit,),
        ).fetchall()
    return {"count": len(rows), "records": [dict(r) for r in rows]}


HANDLERS = {
    "list_datasets": _list_datasets,
    "describe_dataset": _describe_dataset,
    "query_stats": _query_stats,
    "search_records": _search_records,
    "sample_records": _sample_records,
}


@app.post("/mcp")
def mcp(req: McpRequest) -> dict[str, Any]:
    method = req.method
    params = req.params or {}

    if method == "tools/list":
        tools = [
            {"name": n, "description": f"DAS tool {n}"}
            for n in sorted(WHITELIST)
        ]
        return {"jsonrpc": "2.0", "id": req.id, "result": {"tools": tools}}

    if method == "tools/call":
        name = params.get("name")
        arguments = params.get("arguments") or {}
        if not name or name not in WHITELIST:
            raise HTTPException(400, f"tool not in whitelist: {name}")
        # Explicit SQL rejection at call level
        if isinstance(arguments, dict) and (
            "sql" in arguments
            or (
                "query" in arguments
                and isinstance(arguments.get("query"), str)
                and "select" in arguments["query"].lower()
            )
        ):
            raise HTTPException(400, "raw SQL not allowed")
        try:
            result = HANDLERS[name](arguments)
        except HTTPException:
            raise
        except Exception as e:  # noqa: BLE001
            raise HTTPException(500, str(e)) from e
        fp = _fingerprint(name, arguments)
        return {
            "jsonrpc": "2.0",
            "id": req.id,
            "result": {
                "content": [{"type": "json", "json": result}],
                "structuredContent": result,
                "evidence": {"tool": name, "fingerprint": fp, "arguments": arguments},
            },
        }

    raise HTTPException(400, f"unsupported method: {method}")
