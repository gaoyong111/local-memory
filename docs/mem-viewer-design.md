# mem_viewer 设计规格

本地 Web UI：浏览、检索、编辑记忆，支持 episodic 人机梳理。

[架构 → architecture.md](architecture.md) · [工作区绑定 → workspace-binding.md](workspace-binding.md)

---

## 技术栈

| 层 | 选型 |
|----|------|
| 后端 | Flask（`mem_viewer.py`） |
| 前端 | 内嵌 HTML + vis.js Network |
| 向量 | Ollama bge-m3（编辑正文时 re-embed） |
| 数据 | Chroma + `active_memories.db` + `lineage.jsonl` |

---

## 启动

```bash
# 默认：registry active pool，展示全部 project
bash ~/.memory/runtime/mem_viewer.sh

# 可选：传入仓库根，对手动 add/update 做 workspace write 软警告
bash ~/.memory/runtime/mem_viewer.sh /path/to/your/project
```

环境变量：`MEMORY_DIR`、`PYTHONPATH=~/.memory/runtime`。Chroma collection 读 `pool.meta.json`（默认 `memories`）。

先部署最新代码：`bash scripts/setup.sh`

---

## 功能

- **图谱视图** — 节点按 category 上色；边来自 `merged_from`
- **混合检索** — 与 MCP `search_memory` 同算法（max 8）
- **CRUD** — 新增、编辑、删除（删除必填 reason）
- **演变时间线** — 单条记忆的合并 / 去重 / 删除历史
- **episodic 梳理** — 展示 AI 建议；确认 keep / promote / merge

---

## 与工作区绑定的关系

| 能力 | mem_viewer 行为 |
|------|----------------|
| read 过滤 | **不应用** — 展示 active pool 全部 project |
| pool 绑定 | **不应用** — 用 registry active pool |
| write 软警告 | 设 `WORKSPACE_ROOT`（或脚本传 repo 路径）时，对手动 add/update 检测 |

---

## 检索条数

| 入口 | max | 说明 |
|------|-----|------|
| 搜索面板 `/search` | 8 | 与 MCP 相同 |
| 相似预警 `/api/similar` | 5 | 写入前查重 |
| Hook L2 | 5 | 非 viewer 功能 |

---

## HTTP API

| 路由 | 方法 | 说明 |
|------|------|------|
| `/` | GET | 主页面（图谱 + 侧栏） |
| `/search` | GET | 混合检索 `?q=&project=` |
| `/api/timeline/<id>` | GET | 演变时间线 |
| `/api/similar` | GET | 相似记忆 `?q=&project=` |
| `/api/add` | POST | 新增（JSON body） |
| `/api/update/<id>` | POST | 编辑；正文变更时 re-embed |
| `/api/grooming/confirm/<id>` | POST | 确认保留，清 `grooming_pending` |
| `/api/grooming/promote/<id>` | POST | 采纳 promote |
| `/api/grooming/merge/<source_id>` | POST | 合并（当场 hybrid_search 重校验） |
| `/delete/<id>` | POST | 删除（reason 必填） |

---

## episodic 梳理 UI

`grooming_pending=1` 的记忆进入待确认队列。

| 操作 | 效果 |
|------|------|
| 确认保留 | `grooming_pending=0`；保留 action 元数据 |
| Promote | 按建议改 category |
| 合并 | 检索重验目标，写 `merged_from`，删 source |
| 删除 | 标准归档删除 |

批处理建议：MCP `run_episodic_grooming` 或 `scripts/episodic_grooming_run.py`。

---

## category 配色

| category | 含义 |
|----------|------|
| `episodic` | 踩坑 / 事件 |
| `behavior` | 行为规则 |
| `workflow` | 流程方法 |
| `reference` | 事实知识 |
| `preference` | 用户偏好 |

---

## 故障排查

| 现象 | 处理 |
|------|------|
| 图谱为空 | 池内无记忆，或 Ollama 未启动无法 embed |
| 与 MCP 搜索结果不同 | viewer 不做 workspace read 过滤 |
| 编辑后搜不到 | 正文变更触发 re-embed，等待 Ollama |
| grooming 队列过期 | 重新跑 `run_episodic_grooming` |
