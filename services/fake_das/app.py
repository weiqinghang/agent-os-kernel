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

app = FastAPI(title="fake-das", version="0.2.0")


def _conn() -> sqlite3.Connection:
    if not DB_PATH.exists():
        raise HTTPException(503, f"DB missing: {DB_PATH}; run scripts/seed_ecommerce.py")
    c = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    return c


def _fingerprint(name: str, args: dict[str, Any]) -> str:
    raw = json.dumps({"name": name, "args": args}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()[:12]


def _date_clause(args: dict[str, Any]) -> tuple[str, list[Any]]:
    """Optional start/end (ISO date) → WHERE fragments on order_date."""
    parts: list[str] = []
    params: list[Any] = []
    start = args.get("start") or (args.get("time_window") or {}).get("start") if isinstance(args.get("time_window"), dict) else args.get("start")
    end = args.get("end") or (args.get("time_window") or {}).get("end") if isinstance(args.get("time_window"), dict) else args.get("end")
    # clearer:
    start = args.get("start")
    end = args.get("end")
    tw = args.get("time_window")
    if isinstance(tw, dict):
        start = start or tw.get("start")
        end = end or tw.get("end")
    if start:
        parts.append("order_date >= ?")
        params.append(str(start)[:10])
    if end:
        parts.append("order_date <= ?")
        params.append(str(end)[:10])
    return (" AND ".join(parts), params)


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
    date_sql, date_params = _date_clause(args)
    where = f" WHERE {date_sql}" if date_sql else ""
    with _conn() as c:
        row = c.execute(
            "SELECT name, description, row_count, columns FROM datasets_meta WHERE name=?",
            (name,),
        ).fetchone()
        if not row:
            raise HTTPException(404, f"dataset not found: {name}")
        stats = c.execute(
            f"""
            SELECT MIN(order_date) AS min_date, MAX(order_date) AS max_date,
                   ROUND(SUM(gmv),2) AS total_gmv, SUM(qty) AS total_qty,
                   COUNT(*) AS filtered_rows,
                   ROUND(AVG(returned)*100,2) AS return_pct
            FROM sales{where}
            """,
            date_params,
        ).fetchone()
    summary = dict(stats)
    return {
        "name": row["name"],
        "description": row["description"],
        "row_count": summary.get("filtered_rows") or row["row_count"],
        "columns": row["columns"].split(","),
        "summary": summary,
        "filters": {"start": args.get("start"), "end": args.get("end")},
    }


def _query_stats(args: dict[str, Any]) -> dict[str, Any]:
    if "sql" in args or (
        "query" in args
        and isinstance(args.get("query"), str)
        and "select" in str(args.get("query")).lower()
    ):
        raise HTTPException(400, "raw SQL not allowed; use group_by/metric/filters")
    if "sql" in args:
        raise HTTPException(400, "raw SQL not allowed")

    group_by = args.get("group_by") or "category"
    metric = args.get("metric") or "sum_gmv"
    limit = min(int(args.get("limit") or 20), 50)
    filters = args.get("filters") or {}

    if group_by not in ALLOWED_GROUP_BY:
        raise HTTPException(400, f"group_by not allowed: {group_by}")
    if metric not in ALLOWED_METRICS:
        raise HTTPException(400, f"metric not allowed: {metric}")

    where: list[str] = []
    params: list[Any] = []
    for k, v in filters.items():
        if k not in ALLOWED_GROUP_BY | {"returned"}:
            raise HTTPException(400, f"filter field not allowed: {k}")
        where.append(f"{k}=?")
        params.append(v)
    date_sql, date_params = _date_clause(args)
    if date_sql:
        where.append(date_sql)
        params.extend(date_params)
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
        "start": args.get("start"),
        "end": args.get("end"),
        "rows": [{"key": r["key"], "value": r["value"]} for r in rows],
    }


def _search_records(args: dict[str, Any]) -> dict[str, Any]:
    q = (args.get("q") or args.get("keyword") or "").strip()
    limit = min(int(args.get("limit") or 20), 50)
    field = args.get("field")
    date_sql, date_params = _date_clause(args)
    with _conn() as c:
        if field and field in ALLOWED_GROUP_BY | {"sku", "order_id"}:
            where = f"CAST({field} AS TEXT) LIKE ?"
            params: list[Any] = [f"%{q}%"]
            if date_sql:
                where += f" AND {date_sql}"
                params.extend(date_params)
            rows = c.execute(
                f"SELECT * FROM sales WHERE {where} LIMIT ?",
                (*params, limit),
            ).fetchall()
        elif q:
            where = (
                "(sku LIKE ? OR category LIKE ? OR channel LIKE ? "
                "OR province LIKE ? OR order_id LIKE ?)"
            )
            params = [f"%{q}%"] * 5
            if date_sql:
                where += f" AND {date_sql}"
                params.extend(date_params)
            rows = c.execute(
                f"SELECT * FROM sales WHERE {where} LIMIT ?",
                (*params, limit),
            ).fetchall()
        else:
            where = date_sql if date_sql else "1=1"
            rows = c.execute(
                f"SELECT * FROM sales WHERE {where} LIMIT ?",
                (*date_params, limit),
            ).fetchall()
    return {"count": len(rows), "records": [dict(r) for r in rows]}


def _sample_records(args: dict[str, Any]) -> dict[str, Any]:
    limit = min(int(args.get("limit") or 10), 20)
    filters = args.get("filters") or {}
    where: list[str] = []
    params: list[Any] = []
    for k, v in filters.items():
        if k not in ALLOWED_GROUP_BY | {"returned", "sku"}:
            raise HTTPException(400, f"filter field not allowed: {k}")
        where.append(f"{k}=?")
        params.append(v)
    date_sql, date_params = _date_clause(args)
    if date_sql:
        where.append(date_sql)
        params.extend(date_params)
    where_sql = (" WHERE " + " AND ".join(where)) if where else ""
    with _conn() as c:
        rows = c.execute(
            f"SELECT * FROM sales{where_sql} ORDER BY id LIMIT ?",
            (*params, limit),
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
    if req.method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": req.id,
            "result": {
                "tools": [{"name": n, "description": f"DAS tool {n}"} for n in sorted(WHITELIST)]
            },
        }
    if req.method == "tools/call":
        params = req.params or {}
        name = params.get("name")
        arguments = params.get("arguments") or {}
        if not name or name not in WHITELIST:
            raise HTTPException(400, f"tool not in whitelist: {name}")
        if "sql" in arguments:
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
    raise HTTPException(400, f"unknown method: {req.method}")
