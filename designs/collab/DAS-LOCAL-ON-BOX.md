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
- `USE_FAKE_DAS=1` forwards fake-DAS arguments unchanged (`name`, `start`, `end`, string `metric`)
- real MCP (`additionalProperties: false`) is normalized in `adapt_real_arguments` before POST:

  | Incoming (model / bindings / fake shape) | Real argument |
  | --- | --- |
  | `dataset_id`, else `name`, else `dataset` | `dataset_id` (explicit `dataset_id` wins) |
  | `start` / `end` or `time_window.start` / `end` | `filters.clauses[]` on `order_date` with `gte` / `lte` |
  | flat `filters` `{field: value}` | `filters = {op: "and", clauses: [{field, op: "eq", value}]}` |
  | existing `filters.clauses` or `filters.conditions` | kept, then the time window is appended if missing |
  | string `metric` (`sum_gmv`, `sum_qty`, `avg_gmv`, `count`, `return_rate`) | `metrics = {<id>: {name, fn, field?}}` |
  | `metrics` already an object or list | kept; the string `metric` is dropped |
  | `group_by`, `limit` on `query_stats` | passed through |
  | `name`, `dataset`, `start`, `end`, `time_window`, string `metric` | stripped so they are not additional properties |

  `describe_dataset` / `describe_fields` / `describe_tag_hierarchies` send `dataset_id` only. `query_stats`, `search_records`, and `sample_records` also send the `filters` object (and `query_stats` sends `metrics`).

Whitelist (on-box `tools/list` exposes the two describe tools, so they are allow-listed):

- `list_datasets`
- `describe_dataset`
- `describe_fields`
- `describe_tag_hierarchies`
- `query_stats`
- `search_records`
- `sample_records`

Dogfood seed id is `ecommerce_sales_v2` (200 rows imported from the overnight sales extract). Chat session default and `insight_report` default bindings use that id. `scripts/uat.sh` follows `/health`: `service=fake-das` keeps SQLite numeric checks; otherwise it asserts the seed id over `/api/mcp` and does not read `sales.db`.

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
- Dogfood seed: `ecommerce_sales_v2`.

## Blockers / later

- Staging/prod need PostgreSQL + real secrets; do not point this box at remote staging PG/S3.
- `services/fake_das/` stays in tree for `USE_FAKE_DAS=1` and for the overnight reference path.
