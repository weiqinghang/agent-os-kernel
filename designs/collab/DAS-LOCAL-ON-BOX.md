# Real DAS on Grok box (local)

Default `scripts/start.sh` boots the real `dataset-analysis-service` and the kernel.
Overnight `services/fake_das` starts only when `USE_FAKE_DAS=1`.

Cutover restored 2026-10-22 after P0′ (#4 + #6) had put the gateway back on unauthenticated `{FAKE_DAS_URL}/mcp`.
Staging / remote Postgres stays out of scope. Do not point this box at remote staging PG or S3.

## Paths

- DAS code: `/workspace/dataset-analysis-service` (override with `DAS_ROOT`)
- DAS local env (gitignored): `/workspace/dataset-analysis-service/.env`
  - Force-load with `APP_RUNTIME_ENV_FILE` pointing at that file (do **not** load `app/runtime/develop.env` on the box)
- Kernel DAS env (gitignored): `data/das-local.env` next to the kernel repo
- Kernel model env (gitignored): `.env` — `scripts/load_local_env.sh` exports only `MODEL_*` / `XAI_*` / `OPENAI_*`
- Seed dataset id: `ecommerce_sales_v2` (file on the DAS tree: `var/seed_dataset_id.txt` when present)
- SQLite DB: `/workspace/dataset-analysis-service/var/local.db`
- Uploads: `/workspace/dataset-analysis-service/var/uploads/`

## Ports

- DAS: `127.0.0.1:8788` (uvicorn `--workers 2` so MCP→REST self-calls do not deadlock)
- Kernel: `127.0.0.1:8787`

## Env var names (values live only in the gitignored files above)

| Var | Where | Purpose |
| --- | --- | --- |
| `APP_ENV=local` | DAS `.env` | SQLite + auto-schema |
| `DATABASE_URL` | DAS `.env` | `sqlite+pysqlite:///./var/local.db` |
| `DATASET_UPLOAD_ROOT` | DAS `.env` | `./var/uploads` |
| `DATASET_SOURCE_STORAGE_BACKEND=local` | DAS `.env` | no S3 |
| `WEB_ADMIN_PASSWORD` | DAS `.env` | UI/REST session gate |
| `MCP_AUTH_TOKEN` | DAS `.env` | Bearer for `/api/mcp` |
| `MCP_CALLER_ID` | DAS `.env` | audit caller id |
| `DATASET_API_BASE_URL` | DAS `.env` | `http://127.0.0.1:8788` (MCP self-calls) |
| `DAS_MCP_URL` | kernel `data/das-local.env` | `http://127.0.0.1:8788/api/mcp` |
| `DAS_MCP_AUTH_TOKEN` | kernel `data/das-local.env` | same token as `MCP_AUTH_TOKEN` |
| `DAS_HEALTH_URL` | kernel `data/das-local.env` | `http://127.0.0.1:8788/health` |
| `FAKE_DAS_URL` | kernel (compat) | `http://127.0.0.1:8788` base; health falls back to `{origin}/health` |
| `USE_FAKE_DAS` | process | `1` selects overnight fake `/mcp` with no Bearer |

## Start / stop

```bash
# preferred: real DAS + kernel. Loads data/das-local.env and model keys from .env.
./scripts/start.sh
./scripts/stop.sh    # das.pid, fake-das.pid, and kernel.pid

# overnight fake only
USE_FAKE_DAS=1 ./scripts/start.sh
```

Manual DAS only:

```bash
cd /workspace/dataset-analysis-service
set -a; source .env; set +a
export APP_ENV=local
export APP_RUNTIME_ENV_FILE="$PWD/.env"
.venv/bin/python -m uvicorn app:app --host 127.0.0.1 --port 8788 --workers 2
```

## MCP contract the kernel speaks

`ToolGateway` POSTs JSON-RPC to `DAS_MCP_URL` (`/api/mcp`):

- `Authorization: Bearer <DAS_MCP_AUTH_TOKEN>` (required unless `USE_FAKE_DAS=1`)
- `Accept: application/json, text/event-stream`
- methods: `tools/list`, `tools/call`
- success body is either one JSON object or SSE `data:` lines
- tool payload is `result.structuredContent` when it is an object; otherwise JSON inside `result.content[]`
- missing `evidence.fingerprint` is filled by the gateway (sha256 prefix) so the conclusion page still has an anchor
- raw SQL (`sql` / `SQL` argument) is rejected before the HTTP call
- `USE_FAKE_DAS=1` forwards fake-DAS arguments unchanged (`name`, `start`, `end`, string `metric`). It does not resolve dataset names.
- real MCP (`additionalProperties: false`, live `tools/list`) is normalized in `adapt_real_arguments` before POST:

  | Incoming (model / bindings / fake shape) | Real argument |
  | --- | --- |
  | `dataset_id`, else `name`, else `dataset` | `dataset_id`. A non-UUID is resolved once via `list_datasets` (`dataset_name` / `name` → `dataset_id`) and cached. A value that is already a UUID is sent as-is. Bindings may stay `ecommerce_sales_v2`. |
  | `start` / `end` or `time_window.start` / `end` | `filters[]` item `{field: "order_date", operator: "gte"\|"lte", value}` |
  | flat `filters` `{field: value}` | `filters[]` item `{field, operator: "eq", value}` |
  | `filters` list, or legacy `{clauses\|conditions}` | same array; `op` is renamed to `operator` (`ge`→`gte`, `le`→`lte`) |
  | string `metric` | `metrics[]` item `{function, field?, alias}` |
  | `sum_gmv` / `sum_qty` / `avg_gmv` | `{function: sum\|avg, field: gmv\|qty, alias: <id>}` |
  | `count` | `{function: "count", alias: "count"}` (no field) |
  | `return_rate` | `{function: "avg", field: "returned", alias: "return_rate"}`（旧指标名；退货简报改走下一行） |
  | `metrics` 里 `field: "returned"` 且 `function: "avg"` / `"sum"`，或 `function: "return_rate"`，或 `field: "return_rate"` | `{function: "avg"\|"sum", field: "returned", alias: "avg_returned"\|"sum_returned"}`。DAS 没有名为 `return_rate` 的指标。 |
  | `group_by` string | `[group_by]`; a list is kept and truncated to 3 |
  | `name`, `dataset`, `start`, `end`, `time_window`, string `metric` | stripped |
  | `sample_records` / `search_records` with no `return_fields` and no `evidence_preset` | `return_fields`: `order_id`, `sku`, `category`, `channel`, `province`, `order_date`, `qty`, `gmv`, `returned` |
  | `evidence_preset: "default"` | kept; published default evidence fields are used, and the dogfood list is not injected |
  | non-empty `return_fields` | kept (blank items dropped) |

  `filters` / `metrics` / `group_by` are arrays. They are not `{op, clauses}` objects and not a dict keyed by metric id. `query_stats`, `search_records`, and `sample_records` take the filters array. Only `query_stats` takes `metrics` and `group_by`. `describe_dataset` takes `dataset_id` only. Keys outside the live tool schema are not forwarded. Live `sample_records` / `search_records` reject a missing or empty `return_fields` (`return_fields_required`) unless `evidence_preset` is `"default"` and the dataset published `default_evidence_fields`. An empty list is also rejected (`minItems: 1`).

Whitelist (on-box `tools/list` exposes the two describe tools, so they are allow-listed):

- `list_datasets`
- `describe_dataset`
- `describe_fields`
- `describe_tag_hierarchies`
- `query_stats`
- `search_records`
- `sample_records`

Dogfood seed id is `ecommerce_sales_v2` (same id for the insight assistant and the return-risk assistant). Chat session defaults and both skills bind that id. `scripts/uat.sh` follows `/health`: `service=fake-das` keeps SQLite numeric checks; otherwise it asserts the seed id over `/api/mcp` and does not read `sales.db`.

Target scale is about **100_000** rows on that same id. Do not mint a new dataset id and do not add an MCP.

Reseed the fake DAS (replaces `services/fake_das/data/sales.db`):

```bash
uv run python scripts/seed_ecommerce.py
# default --n 100000; same id ecommerce_sales_v2
# curl -s http://127.0.0.1:8788/health   → "rows" near 100000
```

`--ensure-alias` does not change the row count. A fake db that is still ~1e4 or ~200 rows has not been reseeded.

Real DAS on this box still imports **`.xlsx` only** into the existing published id (`POST /datasets/import-jobs`, then profile → mappings → publish). The box seed script lives in the dataset-analysis-service tree. Point it at `ecommerce_sales_v2` and load about 1e5 rows there. This repo's `scripts/seed_ecommerce.py` is the fake-DAS path and the row-count contract; it does not open a second dataset.

## Smoke

```bash
curl -s http://127.0.0.1:8788/health
# POST $DAS_MCP_URL
# Authorization: Bearer <token from das-local.env — do not commit>
# body: tools/list, or tools/call list_datasets / query_stats
```

## Seed / upload notes

- Import accepts **`.xlsx` only** via `POST /datasets/import-jobs` (cookie after `/ui/login`).
- After profile → patch field mappings with `permissions` + `index_strategy=btree` → confirm → `ddl-plan` → `publish` → **`POST /datasets/{id}/indexes`**.
- Without indexes, `query_readiness.ready=false` and `ready_only=true` hides the dataset; `group_by` / metrics fail.
- Dogfood seed: `ecommerce_sales_v2`, about 100_000 rows after `scripts/seed_ecommerce.py` (fake) or an in-place xlsx republish (real DAS). Same id, no new MCP.
- `order_date` must be published as `data_type=date` so `gte` / `lte` filters validate. A text-typed duplicate of the same column fails those operators. The box seed script lives in the dataset-analysis-service tree (patched there); `scripts/seed_ecommerce.py` in this repo only fills the SQLite fake DAS and stores ISO date strings.

## Blockers / later

- Staging/prod need PostgreSQL + real secrets; do not point this box at remote staging PG/S3.
- `services/fake_das/` stays in tree for `USE_FAKE_DAS=1` and for the overnight reference path.
