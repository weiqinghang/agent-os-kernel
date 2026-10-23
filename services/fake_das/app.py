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


def _clause_list(args: dict[str, Any]) -> list[dict[str, Any]]:
    filters = args.get("filters") or {}
    if isinstance(filters, list):
        return [clause for clause in filters if isinstance(clause, dict)]
    if not isinstance(filters, dict):
        return []
    nested = filters.get("clauses") if isinstance(filters.get("clauses"), list) else filters.get("conditions")
    if isinstance(nested, list):
        return [clause for clause in nested if isinstance(clause, dict)]
    clauses: list[dict[str, Any]] = []
    for key, value in filters.items():
        if key in {"op", "logic", "and", "or", "operator", "clauses", "conditions"}:
            continue
        if isinstance(value, dict):
            for op, val in value.items():
                clauses.append({"field": str(key), "operator": str(op), "value": val})
        else:
            clauses.append({"field": str(key), "operator": "eq", "value": value})
    return clauses


def _date_bounds(args: dict[str, Any]) -> tuple[Any, Any]:
    start = args.get("start")
    end = args.get("end")
    window = args.get("time_window")
    if isinstance(window, dict):
        start = start or window.get("start")
        end = end or window.get("end")
    for clause in _clause_list(args):
        if clause.get("field") != "order_date":
            continue
        op = clause.get("operator") or clause.get("op")
        value = clause.get("value")
        if op in {"gte", "ge"} and start is None:
            start = value
        elif op in {"lte", "le"} and end is None:
            end = value
        elif op == "between" and isinstance(value, (list, tuple)) and len(value) >= 2:
            start = start or value[0]
            end = end or value[1]
    return start, end


def _date_clause(args: dict[str, Any]) -> tuple[str, list[Any]]:
    """Optional start/end or order_date filters → WHERE fragments."""
    parts: list[str] = []
    params: list[Any] = []
    start, end = _date_bounds(args)
    if start:
        parts.append("order_date >= ?")
        params.append(str(start)[:10])
    if end:
        parts.append("order_date <= ?")
        params.append(str(end)[:10])
    return (" AND ".join(parts), params)


def _eq_pairs(args: dict[str, Any], allowed: set[str]) -> list[tuple[str, Any]]:
    pairs: list[tuple[str, Any]] = []
    for clause in _clause_list(args):
        field = clause.get("field")
        if not isinstance(field, str) or not field or field == "order_date":
            continue
        op = clause.get("operator") or clause.get("op") or "eq"
        if op != "eq":
            continue
        if field not in allowed:
            raise HTTPException(400, f"filter field not allowed: {field}")
        pairs.append((field, clause.get("value")))
    return pairs


def _group_name(args: dict[str, Any]) -> str:
    group_by = args.get("group_by") or "category"
    if isinstance(group_by, list):
        group_by = group_by[0] if group_by else "category"
    if isinstance(group_by, dict):
        group_by = group_by.get("field") or "category"
    return str(group_by)


def _metric_choice(args: dict[str, Any]) -> tuple[str, str, bool]:
    """Return metric id, SQL expression, and whether the row should use avg_returned."""
    metric_sql = {
        "sum_gmv": "ROUND(SUM(gmv),2)",
        "sum_qty": "SUM(qty)",
        "count": "COUNT(*)",
        "avg_gmv": "ROUND(AVG(gmv),2)",
        "return_rate": "ROUND(AVG(returned)*100,2)",
    }
    metric = args.get("metric")
    if isinstance(metric, str) and metric.strip():
        name = metric.strip()
        if name not in metric_sql:
            raise HTTPException(400, f"metric not allowed: {name}")
        return name, metric_sql[name], False
    metrics = args.get("metrics")
    if isinstance(metrics, list):
        for item in metrics:
            if isinstance(item, str) and item.strip() in metric_sql:
                return item.strip(), metric_sql[item.strip()], False
            if not isinstance(item, dict):
                continue
            function = item.get("function")
            field = item.get("field")
            alias = item.get("alias")
            if field == "return_rate":
                field = "returned"
                if function not in {"avg", "sum"}:
                    function = "avg"
            if field == "returned" and function == "avg":
                return "avg_returned", "AVG(returned)", True
            if field == "returned" and function == "sum":
                return "sum_returned", "SUM(returned)", True
            if function == "count":
                return "count", "COUNT(*)", True
            if isinstance(alias, str) and alias in metric_sql:
                return alias, metric_sql[alias], False
            if isinstance(function, str) and isinstance(field, str) and f"{function}_{field}" in metric_sql:
                name = f"{function}_{field}"
                return name, metric_sql[name], False
        raise HTTPException(400, "metric not allowed")
    return "sum_gmv", metric_sql["sum_gmv"], False


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

    group_by = _group_name(args)
    metric, metric_sql, as_ratio = _metric_choice(args)
    limit = min(int(args.get("limit") or 20), 50)
    if group_by not in ALLOWED_GROUP_BY:
        raise HTTPException(400, f"group_by not allowed: {group_by}")

    where: list[str] = []
    params: list[Any] = []
    for field, value in _eq_pairs(args, ALLOWED_GROUP_BY | {"returned"}):
        where.append(f"{field}=?")
        params.append(value)
    date_sql, date_params = _date_clause(args)
    if date_sql:
        where.append(date_sql)
        params.extend(date_params)
    where_sql = (" WHERE " + " AND ".join(where)) if where else ""

    sql = (
        f"SELECT {group_by} AS key, {metric_sql} AS value "
        f"FROM sales{where_sql} GROUP BY {group_by} "
        f"ORDER BY value DESC LIMIT ?"
    )
    params.append(limit)
    with _conn() as c:
        rows = c.execute(sql, params).fetchall()
    shaped: list[dict[str, Any]] = []
    for row in rows:
        item: dict[str, Any] = {"key": row["key"], "value": row["value"], group_by: row["key"]}
        if as_ratio:
            item["avg_returned"] = row["value"]
        shaped.append(item)
    return {
        "group_by": group_by,
        "metric": metric,
        "filters": args.get("filters") or {},
        "start": args.get("start"),
        "end": args.get("end"),
        "rows": shaped,
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
    where: list[str] = []
    params: list[Any] = []
    for field, value in _eq_pairs(args, ALLOWED_GROUP_BY | {"returned", "sku"}):
        where.append(f"{field}=?")
        params.append(value)
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
