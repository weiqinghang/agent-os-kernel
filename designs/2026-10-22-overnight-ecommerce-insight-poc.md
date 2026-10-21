# 过夜 POC：电商销量洞察报告（箱内可跑通）

- 状态：已落地 UAT PASS（2026-10-22 00:51 Asia/Shanghai）
- 目标宿主：**Grok 云电脑（box）** `/workspace`，**不**动用户 Mac
- 仓库 SoT：https://github.com/weiqinghang/agent-os-kernel
- 依据：M0 宪章、狗粮补丁（洞察报告助理 + 假/真 DAS MCP）

---

## 0. 箱内盘点（本计划撰写时）

| 项 | 现状 |
| --- | --- |
| `/workspace/designs/` | 宪章 / 狗粮补丁 / v0 架构 / simulated-research 边界稿 / `collab/` |
| `/workspace/agent-os-kernel/` | **仅** README + designs（与 designs 镜像）；**无应用代码** |
| `agent-os-kernel-init.tgz` | 同上设计包备份 |
| 真 `dataset-analysis-service` / `insight-skills` 源码 | **箱内不存在**；仅文档引用工具面 |
| Staging DAS URL | 文档提到 `t-dataset-analysis.tarsocial.com`；过夜**不依赖**外网真 DAS |
| Docker | `DOCKER_HOST=tcp://127.0.0.1:2375` 可用；镜像仅 buildkit + hello-world；**0 容器** |
| 内存 | 15 Gi 总量，可用约 **3.3 Gi**（Chrome/桌面占大头）；**禁止**再拉 Postgres/Redis 大镜像 |
| 语言 | Python 3.13 + `uv`；Node 20 / bun / go / rust 均在；fastapi **未预装**（用 uv 装） |
| SQLite | 标准库可用；优先于 Docker DB |

结论：从零在 box 上搭 **假 DAS + 内核单体 + 报告 Skill + 聊天壳**；数据集自造。

---

## 1. Goal / Non-goals

### Goal（明早可演示）

1. 箱内常驻进程：假 DAS（约 1 万行电商销量）+ Agent 内核（Session/Run/Tool Gateway/HITL 瘦实现）+ 聊天 UI。
2. 用户在聊天窗提问 → 内核开 Run → 经 Tool Gateway 调 DAS 白名单工具 → 报告 Skill 产出「结论页（3～5 发现 + 证据锚点）」→ 同线程可追问。
3. 自跑 UAT 脚本通过；服务不退出，明早可直接打开 UI。

### Non-goals

- 真 Staging DAS / 真 insight-skills 九套剧本
- Postgres、Redis、消息队列、K8s
- 任意 SQL、完整 BI、自动外发、多角色编排
- 把洞察业务名词写进内核包
- 在 Mac 上 clone / 跑服务
- 过夜强拉陈嘉树（由 Grok 在 box 直接实现 POC；事后可 PR 进仓）

---

## 2. 架构（2 进程 + 静态 UI）

```text
Browser (chat UI, static)
        │  HTTP
        ▼
┌───────────────────────────────┐
│  agent-kernel :8787           │  FastAPI 模块化单体
│  Session / Run SM / Context   │
│  Tool Gateway (MCP client)    │──JSON-RPC/HTTP──┐
│  Model Broker (1 provider)    │                 │
│  Policy(read-only) / HITL     │                 │
│  Obs (run_events JSONL/SQLite)│                 │
│  Skill: insight_report        │                 │
└───────────────────────────────┘                 │
                                                  ▼
                                    ┌─────────────────────────┐
                                    │  fake-das :8788         │
                                    │  SQLite ~10k sales rows │
                                    │  MCP tools whitelist    │
                                    └─────────────────────────┘
```

- **不**用 Docker 跑业务进程（省内存）；可选仅用 Docker 做隔离 smoke，默认 `uv run` 原生进程。
- SQLite 两库：`fake-das/data/sales.db`、`kernel/data/kernel.db`（runs / run_events / sessions）。

---

## 3. 组件

### 3.1 fake-das（伪造 dataset-analysis-service）

- **数据**：电商订单行约 **10 000** 条（SKU、类目、渠道、省份、日、销量、GMV、退货标记等）。
- **工具白名单**（对齐宪章命名，假实现）：
  - `list_datasets`
  - `describe_dataset`
  - `query_stats`（受控聚合：group_by / metric / filters；**拒**裸 SQL）
  - `search_records`（关键词/字段过滤，limit≤50）
  - `sample_records`（limit≤20）
- 传输：HTTP JSON-RPC（MCP tools/list + tools/call 子集即可；内核只认这 5 个 name）。
- 默认上限：timeout 5s；单次返回行数硬顶。

### 3.2 agent-kernel

八层**包目录切开、同进程**：

| 包 | POC 行为 |
| --- | --- |
| orchestrator | Session 消息 → 创建/resume Run |
| runtime | `created→acting⇄awaiting_hitl→completed\|failed` |
| context | 消息 + 工具摘要/指纹；不存 sample 全量 |
| tool_gateway | MCP 客户端；白名单；拒未知/`sql` |
| model_broker | 读 `MODEL_API_KEY`（优先现有 xAI/OpenAI 环境）；无 key 则 **deterministic stub** 仍能出模板报告（UAT 双模式） |
| policy | 只读；外发类需 `final_confirm`（POC 可演示 HITL 一次） |
| observability | `run_events` append |
| hitl | kinds: `user_input` / `outline_confirm` / `segment_confirm` / `final_confirm`；带 `resume_token` |

### 3.3 Skill：`insight_report`（内联精简）

剧本（可写死 YAML/Python）：

1. 澄清选题（可自动跳过：默认「近 90 天电商销量洞察」）
2. `list`/`describe` → `query_stats` 若干（Top 类目、渠道、省份、退货率、周趋势）
3. 拼结论页 Markdown：3～5 发现，每条带 `evidence:` 查询指纹
4. 可选 `outline_confirm` HITL（POC 默认 auto-approve 开关 `HITL_AUTO=1`，UAT 另测人工路径）
5. 追问：同 Session 新 Run 或 resume，再调 DAS

### 3.4 UI

- 单页：`ui/chat/index.html`（原生 JS fetch），由 kernel 挂静态目录。
- 能力：建 Session、发消息、展示助手 Markdown、HITL 确认按钮、展示 run_id / 状态。

### 3.5 Seed / UAT

- `scripts/seed_ecommerce.py` → 写 10k 行
- `scripts/uat.sh`：健康检查 → 工具冒烟 → 端到端聊天 → 断言结论页含证据锚点 → 追问一轮

---

## 4. 目录布局

在 **`/workspace/agent-os-kernel`** 扩仓（设计已在此）；过夜产物也落这里，便于晨间 `git push` / Cloud Agent PR。

```text
/workspace/agent-os-kernel/
  designs/                          # 已有 + 本计划副本可选
  README.md
  pyproject.toml                    # uv workspace 或单项目
  services/
    fake_das/
      app.py                        # FastAPI + MCP endpoints
      seed.py
      data/sales.db                 # gitignore
  src/
    agent_os_kernel/
      __init__.py
      main.py                       # :8787
      session.py
      run.py
      context.py
      tool_gateway.py
      model_broker.py
      policy.py
      obs.py
      hitl.py
      skills/
        insight_report.py
  ui/
    chat/
      index.html
      app.js
  scripts/
    seed_ecommerce.py
    start.sh
    stop.sh
    uat.sh
  data/
    kernel.db                       # gitignore
  .env.example
```

备用：若怕污染仓根，可先 `/workspace/poc/` 实现再 `rsync` 进 `agent-os-kernel`；**默认直接写仓内**。

---

## 5. 有序建造步骤 + 验收点

| # | 步骤 | 验收 |
| --- | --- | --- |
| 1 | `uv init` + 依赖：fastapi, uvicorn, httpx, pydantic；可选 mcp SDK | `uv sync` 成功 |
| 2 | 实现 seed → SQLite 10 000 行 | `SELECT COUNT(*)` = 10000 |
| 3 | fake-das 五工具 + 拒 SQL | curl `tools/call` query_stats OK；sql → 4xx |
| 4 | kernel：Session/Run/事件持久化 | POST session + run 状态机单测 |
| 5 | Tool Gateway 连 :8788 白名单 | kernel 调 `list_datasets` 成功 |
| 6 | insight_report Skill + Model Broker | 无 UI 时 CLI/API 出结论页 Markdown |
| 7 | 静态聊天 UI | 浏览器打开 `http://127.0.0.1:8787/` 可聊 |
| 8 | HITL 路径（auto 关） | 一次 awaiting_hitl → resume → completed |
| 9 | `scripts/uat.sh` 全绿 | exit 0；日志落 `data/uat-last.log` |
| 10 | `start.sh` 后台常驻 + 写 pid | 明早 `curl` 两端口仍 200 |
| 11 | （可选）开 GitHub Issue「Overnight POC」+ 推分支 PR | 不挡演示 |

每步失败不进入下一步；内存吃紧时优先杀闲置 Chrome 标签，**仍不**引入 Postgres。

---

## 6. UAT 脚本大纲

```bash
# scripts/uat.sh（伪代码）
set -euo pipefail
BASE_K=http://127.0.0.1:8787
BASE_D=http://127.0.0.1:8788

curl -sf "$BASE_D/health" | grep ok
curl -sf "$BASE_K/health" | grep ok

# DAS 工具
curl -sf -X POST "$BASE_D/mcp" -d '{"method":"tools/call","params":{"name":"list_datasets"}}' | grep ecommerce

# 拒 SQL
code=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$BASE_D/mcp" -d '{"method":"tools/call","params":{"name":"query_stats","arguments":{"sql":"select 1"}}}')
test "$code" != "200"

# E2E 报告
SID=$(curl -sf -X POST "$BASE_K/sessions" | jq -r .id)
RESP=$(curl -sf -X POST "$BASE_K/sessions/$SID/messages" -d '{"text":"请基于电商销量数据写一份简短洞察报告"}')
echo "$RESP" | grep -E '发现|证据|evidence'
echo "$RESP" | grep -E 'GMV|类目|渠道|退货'

# 追问
RESP2=$(curl -sf -X POST "$BASE_K/sessions/$SID/messages" -d '{"text":"哪个省份 GMV 最高？"}')
echo "$RESP2" | grep -E '省|GMV'

echo "UAT PASS"
```

人工晨检：打开 UI → 同问两句 → 看 run_events。

---

## 7. 明早启动命令

```bash
export DOCKER_HOST=tcp://127.0.0.1:2375   # 仅若要用 docker；默认不用
cd /workspace/agent-os-kernel
./scripts/start.sh
# 等价手动：
#   uv run uvicorn services.fake_das.app:app --host 127.0.0.1 --port 8788 &
#   uv run uvicorn agent_os_kernel.main:app --host 127.0.0.1 --port 8787 &

# 健康检查
curl -s http://127.0.0.1:8788/health
curl -s http://127.0.0.1:8787/health

# UI
# 浏览器打开 http://127.0.0.1:8787/

# 重跑 UAT
./scripts/uat.sh

# 停止
./scripts/stop.sh
```

日志：`/workspace/agent-os-kernel/data/fake-das.log`、`kernel.log`。

---

## 8. 推荐技术栈（拍板）

| 层 | 选择 | 理由 |
| --- | --- | --- |
| 语言 | **Python 3.13 + uv** | 箱内就绪；FastAPI 适合双服务；SQLite 零运维 |
| DB | **SQLite** | 内存/磁盘友好；10k 行无压力 |
| MCP | HTTP JSON-RPC 子集 | 不必上完整 SDK 也能验 Gateway |
| UI | 静态 HTML+JS | 无 Node 构建、省内存 |
| 模型 | env 密钥真实调用；无密钥 stub | 保证 UAT 可绿 |
| 容器 | **默认不用** | 可用内存仅 ~3 Gi |

---

## 9. 风险

| 风险 | 缓解 |
| --- | --- |
| 内存 ~3 Gi 可用 | 双进程原生 Python；不拉 DB 镜像；控制 uvicorn workers=1 |
| 无真 DAS 契约细节 | 按宪章五工具自洽；接口做成可替换适配器 |
| 无 LLM key | stub 模式仍出结构化报告；有 key 再切真模型 |
| 宪章「批准前不拉嘉树」 | 本 POC 由 Grok 在 box 落地；不冒充嘉树；事后 Issue/PR 标明 overnight dogfood |
| 进程过夜被杀 | `start.sh` nohup + pid；UAT 后写 `STATUS.md` |

---

## 10. 协作与仓

- Issue = SoT：执行中可开 `Overnight e-commerce insight POC` Issue（非阻塞）。
- 设计仍写 `/workspace/designs/`；代码主路径 `/workspace/agent-os-kernel`。
- `collab/events.jsonl` 记 kickoff；重大里程碑再 append。
- **不**提前点对点吵队友，除非阻塞（如缺唯一密钥且 stub 不可接受——本计划 stub 可接受）。

---

## 11. 成功标准（明早）

- [x] `fake-das` + `kernel` 在 box 监听 8788/8787
- [x] SQLite 含 ≈10k 电商行
- [x] 聊天 UI 可要报告并追问
- [x] 报告含 DAS 证据锚点（非纯臆造）
- [x] `uat.sh` PASS
- [x] 本计划文件存在且与实现大体一致
