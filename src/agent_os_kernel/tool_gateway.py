"""MCP client gateway — whitelist only; merge ContextBindings defaults before call.

Real DAS (default): POST JSON-RPC to ``DAS_MCP_URL`` (``…/api/mcp``) with
``Authorization: Bearer <DAS_MCP_AUTH_TOKEN>``.
Overnight fake DAS only when ``USE_FAKE_DAS=1`` (``{FAKE_DAS_URL}/mcp``, no auth).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

from . import context

# SoT for tool names. describe_* added because on-box real MCP tools/list
# exposes describe_fields and describe_tag_hierarchies (see designs/collab/DAS-LOCAL-ON-BOX.md).
WHITELIST = {
    "list_datasets",
    "describe_dataset",
    "describe_fields",
    "describe_tag_hierarchies",
    "query_stats",
    "search_records",
    "sample_records",
}

_DATASET_SCOPED = frozenset(
    {
        "describe_dataset",
        "describe_fields",
        "describe_tag_hierarchies",
        "query_stats",
        "search_records",
        "sample_records",
    }
)

# Fake-DAS argument names. Real MCP schemas set additionalProperties: false.
_FAKE_ARG_KEYS = frozenset({"name", "dataset", "start", "end", "time_window", "metric"})
_FILTER_TOOLS = frozenset({"query_stats", "search_records", "sample_records"})
_DATE_FIELD = "order_date"
_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_OPERATORS = frozenset(
    {
        "eq",
        "in",
        "gt",
        "gte",
        "lt",
        "lte",
        "between",
        "contains",
        "overlap",
        "is_null",
        "not_null",
        "text_match",
    }
)
_OP_ALIAS = {"ge": "gte", "le": "lte"}
_FUNCTIONS = frozenset({"count", "count_distinct", "sum", "avg", "min", "max"})
# Dogfood metric ids → live query_stats metrics[] items.
_METRIC_SPEC: dict[str, dict[str, str]] = {
    "sum_gmv": {"function": "sum", "field": "gmv", "alias": "sum_gmv"},
    "sum_qty": {"function": "sum", "field": "qty", "alias": "sum_qty"},
    "avg_gmv": {"function": "avg", "field": "gmv", "alias": "avg_gmv"},
    "count": {"function": "count", "alias": "count"},
    "return_rate": {"function": "avg", "field": "returned", "alias": "return_rate"},
}
# None = catalog not loaded. Empty dict = loaded, no name→id rows.
_DATASET_CATALOG: dict[str, str] | None = None

_ACCEPT = "application/json, text/event-stream"


def clear_dataset_id_cache() -> None:
    """Drop the process-local dataset_name → dataset_id catalog."""
    global _DATASET_CATALOG
    _DATASET_CATALOG = None


def _is_uuid(value: str) -> bool:
    return bool(_UUID_RE.fullmatch(value.strip()))


def _window_bounds(src: dict[str, Any]) -> tuple[Any, Any]:
    start, end = src.get("start"), src.get("end")
    window = src.get("time_window")
    if isinstance(window, dict):
        start = start or window.get("start")
        end = end or window.get("end")
    return start, end


def _raw_clauses(filters: Any) -> list[dict[str, Any]]:
    """Flat fake filters, a clause list, or a legacy {clauses|conditions} object."""
    if isinstance(filters, list):
        return [clause for clause in filters if isinstance(clause, dict)]
    if not isinstance(filters, dict):
        return []
    for key in ("clauses", "conditions"):
        nested = filters.get(key)
        if isinstance(nested, list):
            return [clause for clause in nested if isinstance(clause, dict)]
    clauses: list[dict[str, Any]] = []
    for field, value in filters.items():
        if field in {"op", "logic", "and", "or", "operator"}:
            continue
        if isinstance(value, dict):
            for op, val in value.items():
                clauses.append({"field": str(field), "op": str(op), "value": val})
        else:
            clauses.append({"field": str(field), "op": "eq", "value": value})
    return clauses


def _normalize_operator(raw: Any) -> str | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    op = _OP_ALIAS.get(raw.strip(), raw.strip())
    if op not in _OPERATORS:
        return None
    return op


def _normalize_clause(clause: dict[str, Any]) -> dict[str, Any] | None:
    field = clause.get("field")
    operator = _normalize_operator(clause.get("operator") or clause.get("op"))
    if not isinstance(field, str) or not field or operator is None:
        return None
    out: dict[str, Any] = {"field": field, "operator": operator}
    if operator in {"is_null", "not_null"}:
        return out
    if operator == "text_match":
        value = clause.get("value")
        keywords = value.get("keywords") if isinstance(value, dict) else None
        if not isinstance(keywords, list) or not keywords:
            return None
        out["value"] = value
        return out
    if "value" in clause:
        out["value"] = clause["value"]
    return out


def _filters_array(src: dict[str, Any]) -> list[dict[str, Any]] | None:
    """Live schema: filters is an array of {field, operator, value?}."""
    clauses = [item for item in (_normalize_clause(c) for c in _raw_clauses(src.get("filters"))) if item]
    seen = {(clause["field"], clause["operator"]) for clause in clauses}
    start, end = _window_bounds(src)
    if start and (_DATE_FIELD, "gte") not in seen:
        clauses.append({"field": _DATE_FIELD, "operator": "gte", "value": start})
    if end and (_DATE_FIELD, "lte") not in seen:
        clauses.append({"field": _DATE_FIELD, "operator": "lte", "value": end})
    return clauses or None


def _metric_from_name(name: str) -> dict[str, str] | None:
    spec = _METRIC_SPEC.get(name)
    if spec:
        return dict(spec)
    for fn in ("count_distinct", "sum", "avg", "min", "max", "count"):
        prefix = fn + "_"
        if name.startswith(prefix) and name[len(prefix) :]:
            item = {"function": fn, "alias": name}
            if fn != "count":
                item["field"] = name[len(prefix) :]
            return item
    if name in _FUNCTIONS:
        return {"function": name, "alias": name}
    return None


def _normalize_metric(item: dict[str, Any]) -> dict[str, Any] | None:
    fn = item.get("function") or item.get("fn")
    alias = item.get("alias") or item.get("name")
    if not isinstance(fn, str) or fn not in _FUNCTIONS:
        if isinstance(alias, str):
            return _metric_from_name(alias.strip())
        return None
    spec: dict[str, Any] = {"function": fn}
    field = item.get("field")
    if isinstance(field, str) and field and fn != "count":
        spec["field"] = field
    if isinstance(alias, str) and alias.strip() and alias.strip() != fn:
        spec["alias"] = alias.strip()
    elif fn == "count" and isinstance(alias, str) and alias.strip():
        spec["alias"] = alias.strip()
    return spec


def _metrics_array(src: dict[str, Any]) -> list[dict[str, Any]] | None:
    """Live schema: metrics is an array of {function, field?, alias?}."""
    existing = src.get("metrics")
    items: list[Any] = []
    if isinstance(existing, list) and existing:
        items = existing
    elif isinstance(existing, dict) and existing:
        if any(key in existing for key in ("function", "fn", "field", "alias", "name")):
            items = [existing]
        else:
            for key, val in existing.items():
                if isinstance(val, dict):
                    items.append({**val, "alias": val.get("alias") or val.get("name") or key})
                elif isinstance(val, str):
                    items.append(val)
    else:
        metric = src.get("metric")
        if isinstance(metric, dict) and metric:
            items = [metric]
        elif isinstance(metric, str) and metric.strip():
            items = [metric.strip()]
    out: list[dict[str, Any]] = []
    for item in items:
        if isinstance(item, str) and item.strip():
            spec = _metric_from_name(item.strip())
        elif isinstance(item, dict):
            spec = _normalize_metric(item)
        else:
            spec = None
        if spec:
            out.append(spec)
    return out or None


def _group_by_array(value: Any) -> list[Any] | None:
    """Live schema: group_by is an array of field names or {field, transform?, part?, alias?}."""
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    if not isinstance(value, list):
        return None
    out: list[Any] = []
    for item in value:
        if isinstance(item, str) and item.strip():
            out.append(item.strip())
        elif isinstance(item, dict) and isinstance(item.get("field"), str) and item["field"]:
            kept = {key: item[key] for key in ("field", "transform", "part", "alias") if key in item}
            out.append(kept)
    return out[:3] or None


def _copy_present(out: dict[str, Any], merged: dict[str, Any], keys: tuple[str, ...]) -> None:
    for key in keys:
        value = merged.get(key)
        if value is None or value == "" or value == []:
            continue
        out[key] = value


def adapt_real_arguments(name: str, merged: dict[str, Any]) -> dict[str, Any]:
    """Normalize fake-DAS / mixed args onto the live MCP schemas.

    ``filters`` is an array of ``{field, operator, value?}``. ``metrics`` is an
    array of ``{function, field?, alias?}``. ``group_by`` is an array. Fake keys
    ``name`` / ``dataset`` / ``start`` / ``end`` / ``time_window`` / string
    ``metric`` are dropped. A non-UUID ``dataset_id`` is resolved later by the
    gateway. ``USE_FAKE_DAS`` does not call this function.
    """
    dataset_id = merged.get("dataset_id") or merged.get("name") or merged.get("dataset")
    out: dict[str, Any] = {}
    if name in _DATASET_SCOPED and dataset_id:
        out["dataset_id"] = dataset_id

    if name in _FILTER_TOOLS:
        filters = _filters_array(merged)
        if filters:
            out["filters"] = filters
    if name == "query_stats":
        metrics = _metrics_array(merged)
        if metrics:
            out["metrics"] = metrics
        group_by = _group_by_array(merged.get("group_by"))
        if group_by:
            out["group_by"] = group_by
        _copy_present(out, merged, ("limit", "sort"))
    elif name == "search_records":
        _copy_present(
            out,
            merged,
            (
                "limit",
                "sort",
                "return_fields",
                "evidence_preset",
                "max_chars_per_field",
                "include_long_text",
            ),
        )
    elif name == "sample_records":
        _copy_present(
            out,
            merged,
            (
                "limit",
                "sort",
                "return_fields",
                "evidence_preset",
                "max_chars_per_field",
                "include_long_text",
                "sample_strategy",
            ),
        )
    elif name == "describe_fields":
        _copy_present(out, merged, ("field_names",))
    elif name == "describe_tag_hierarchies":
        _copy_present(out, merged, ("tree_id", "field", "field_series", "include_nodes", "limit"))
    elif name == "list_datasets":
        _copy_present(
            out,
            merged,
            (
                "query",
                "status",
                "ready_only",
                "series",
                "audience_segment",
                "include_archived",
                "include_deleted",
            ),
        )
    return out


def index_dataset_ids(payload: dict[str, Any]) -> dict[str, str]:
    """dataset_name / name → dataset_id from a list_datasets payload."""
    rows = payload.get("datasets")
    if not isinstance(rows, list):
        rows = payload.get("items")
    found: dict[str, str] = {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        dataset_id = str(row.get("dataset_id") or row.get("id") or "").strip()
        if not dataset_id:
            continue
        for key in ("dataset_name", "name", "dataset"):
            label = row.get(key)
            if isinstance(label, str) and label.strip():
                found[label.strip()] = dataset_id
    return found


class ToolGatewayError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def use_fake_das() -> bool:
    return os.getenv("USE_FAKE_DAS", "").strip() == "1"


def resolve_mcp_url() -> str:
    """Endpoint for tools/list and tools/call."""
    explicit = (os.getenv("DAS_MCP_URL") or "").strip().rstrip("/")
    if use_fake_das():
        base = (os.getenv("FAKE_DAS_URL") or "http://127.0.0.1:8788").rstrip("/")
        return f"{base}/mcp"
    if explicit:
        return explicit
    base = (os.getenv("FAKE_DAS_URL") or "http://127.0.0.1:8788").rstrip("/")
    return f"{base}/api/mcp"


def resolve_health_url() -> str:
    explicit = (os.getenv("DAS_HEALTH_URL") or "").strip()
    if explicit:
        return explicit
    parts = urlsplit(resolve_mcp_url())
    return urlunsplit((parts.scheme, parts.netloc, "/health", "", ""))


def _fingerprint(name: str, args: dict[str, Any]) -> str:
    raw = json.dumps({"name": name, "args": args}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()[:12]


def _parse_sse(text: str) -> dict[str, Any]:
    """Last JSON-RPC response in an SSE body (ignore notifications)."""
    found: dict[str, Any] | None = None
    for block in text.split("\n\n"):
        data_lines: list[str] = []
        for line in block.splitlines():
            if line.startswith("data:"):
                data_lines.append(line[5:].lstrip())
        if not data_lines:
            continue
        raw = "\n".join(data_lines).strip()
        if not raw or raw == "[DONE]":
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as e:
            raise ToolGatewayError(f"DAS SSE is not JSON: {raw[:120]}") from e
        if not isinstance(payload, dict):
            continue
        method = str(payload.get("method") or "")
        if method.startswith("notifications/"):
            continue
        if "result" in payload or "error" in payload:
            found = payload
    if found is None:
        raise ToolGatewayError("empty SSE from DAS")
    return found


def parse_mcp_response(response: httpx.Response) -> dict[str, Any]:
    if response.status_code != 200:
        raise ToolGatewayError(
            f"DAS {response.status_code}: {response.text[:200]}",
            response.status_code,
        )
    ctype = (response.headers.get("content-type") or "").lower()
    if "text/event-stream" in ctype:
        body = _parse_sse(response.text)
    else:
        try:
            body = response.json()
        except json.JSONDecodeError as e:
            raise ToolGatewayError(f"DAS body is not JSON: {response.text[:120]}") from e
    if not isinstance(body, dict):
        raise ToolGatewayError("DAS JSON-RPC body is not an object")
    if body.get("error"):
        err = body["error"]
        message = err.get("message") if isinstance(err, dict) else str(err)
        raise ToolGatewayError(f"DAS JSON-RPC error: {message}")
    return body


def structured_from_result(result: dict[str, Any]) -> dict[str, Any]:
    """MCP result → dict. Prefer structuredContent, else JSON text/json content."""
    if result.get("isError"):
        bits: list[str] = []
        for item in result.get("content") or []:
            if isinstance(item, dict) and item.get("text"):
                bits.append(str(item["text"]))
        raise ToolGatewayError("DAS tool error: " + (" ".join(bits) or "isError"))
    structured = result.get("structuredContent")
    if isinstance(structured, dict):
        return structured
    if isinstance(structured, list):
        return {"items": structured}
    for item in result.get("content") or []:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "json" and isinstance(item.get("json"), dict):
            return item["json"]
        if item.get("type") == "text" and isinstance(item.get("text"), str):
            text = item["text"].strip()
            if not text:
                continue
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                return {"text": text}
            if isinstance(parsed, dict):
                return parsed
            return {"items": parsed}
    return {}


class ToolGateway:
    def __init__(
        self,
        base_url: str | None = None,
        *,
        token: str | None = None,
        client: httpx.Client | None = None,
    ):
        # base_url, when passed, is the full MCP endpoint (tests / explicit override).
        self._mcp_url = base_url.rstrip("/") if base_url else None
        self._token = token
        self._client = client

    def mcp_url(self) -> str:
        return self._mcp_url or resolve_mcp_url()

    def auth_token(self) -> str:
        if self._token is not None:
            return self._token.strip()
        return (os.getenv("DAS_MCP_AUTH_TOKEN") or "").strip()

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": _ACCEPT,
            "Content-Type": "application/json",
        }
        token = self.auth_token()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        elif not use_fake_das():
            raise ToolGatewayError(
                "DAS_MCP_AUTH_TOKEN required for real DAS MCP",
                status_code=401,
            )
        return headers

    @contextmanager
    def _open(self) -> Iterator[httpx.Client]:
        if self._client is not None:
            yield self._client
            return
        with httpx.Client(timeout=5.0) as client:
            yield client

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self._open() as client:
            response = client.post(self.mcp_url(), json=payload, headers=self._headers())
        return parse_mcp_response(response)

    def _adapt(self, name: str, merged: dict[str, Any]) -> dict[str, Any]:
        """Fake DAS keeps name/start/end/metric. Real MCP gets the live schema."""
        if use_fake_das():
            return merged
        return adapt_real_arguments(name, merged)

    def _dataset_catalog(self) -> dict[str, str]:
        global _DATASET_CATALOG
        if _DATASET_CATALOG is not None:
            return _DATASET_CATALOG
        try:
            listed = self.call("list_datasets", {})
        except ToolGatewayError:
            _DATASET_CATALOG = {}
            return _DATASET_CATALOG
        _DATASET_CATALOG = index_dataset_ids(listed.get("data") or {})
        return _DATASET_CATALOG

    def _resolve_dataset_id(self, name: str, merged: dict[str, Any]) -> dict[str, Any]:
        """Friendly dataset_name → UUID via one cached list_datasets call."""
        if name not in _DATASET_SCOPED:
            return merged
        raw = merged.get("dataset_id")
        if not isinstance(raw, str) or not raw.strip() or _is_uuid(raw):
            return merged
        resolved = self._dataset_catalog().get(raw.strip())
        if not resolved or resolved == raw.strip():
            return merged
        return {**merged, "dataset_id": resolved}

    def call(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        *,
        bindings: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if name not in WHITELIST:
            raise ToolGatewayError(f"tool not whitelisted: {name}")
        merged = context.merge_tool_arguments(name, arguments, bindings or {})
        if "sql" in merged or any(str(k).lower() == "sql" for k in merged):
            raise ToolGatewayError("raw SQL rejected by tool gateway")
        merged = self._adapt(name, merged)
        if use_fake_das():
            if bindings:
                if bindings.get("tenant_id") and "tenant_id" not in merged:
                    merged["tenant_id"] = bindings["tenant_id"]
                if bindings.get("project_id") and "project_id" not in merged:
                    merged["project_id"] = bindings["project_id"]
        else:
            merged = self._resolve_dataset_id(name, merged)
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": merged},
        }
        body = self._post(payload)
        result = body.get("result") or {}
        if not isinstance(result, dict):
            raise ToolGatewayError("DAS result is not an object")
        structured = structured_from_result(result)
        evidence = result.get("evidence") if isinstance(result.get("evidence"), dict) else {}
        evidence = {
            **evidence,
            "tool": evidence.get("tool") or name,
            "arguments": evidence.get("arguments") or merged,
        }
        if not evidence.get("fingerprint"):
            evidence["fingerprint"] = _fingerprint(name, merged)
        return {"data": structured, "evidence": evidence}

    def list_tools(self) -> list[str]:
        payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
        body = self._post(payload)
        result = body.get("result") or {}
        tools = result.get("tools") if isinstance(result, dict) else None
        names: list[str] = []
        for tool in tools or []:
            if isinstance(tool, dict) and tool.get("name"):
                names.append(str(tool["name"]))
            elif isinstance(tool, str):
                names.append(tool)
        return names

    def probe_health(self) -> bool:
        try:
            with self._open() as client:
                response = client.get(resolve_health_url(), timeout=2.0)
            return response.status_code == 200
        except Exception:  # noqa: BLE001
            return False
