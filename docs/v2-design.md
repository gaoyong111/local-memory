# 设计概览

local-memory v2 的概念模型、模块职责与关键决策。

[架构详解 → architecture.md](architecture.md) · [升级指南 → v2-migration.md](v2-migration.md)

---

## local-memory 是什么？

面向 AI 编程助手的**本地优先记忆层**，专为中文场景优化：

- **原样入库** — 写什么存什么，不经 LLM 改写，避免中文被翻译、模块名被泛化
- **混合检索** — SQLite 关键词（中文滑窗分词）+ Chroma 向量（bge-m3）RRF 融合
- **完全本地化** — SQLite + Chroma + Ollama，不依赖 mem0 库或云端记忆服务

混合检索、多表同步、写入策略、grooming 等核心能力均为自研代码，不委托给记忆框架。

技术栈：SQLite（关键词 + 审计）、Chroma（向量）、Ollama（嵌入与可选 LLM）、MCP + IDE Hook（Agent 集成）。

---

## 概念模型

```text
记忆池（Pool）                 ← 物理隔离；可切换 / 备份 / 克隆
    └── 项目（Project）         ← 逻辑标签；读写范围
            └── 记忆（Memory）   ← 单条（content + category + metadata）
```

| 概念 | 类比 | 示例 |
|------|------|------|
| 池 | 一个书柜 | `default`、`work`、`experiments` |
| 项目 | 书柜分区标签 | `my-app`、`""`（全局） |
| 工作区绑定 | 坐在哪张桌子决定能看哪些分区 | `.cursor/memory.json` |

---

## 目录布局

### 全局

```text
~/.memory/
├── registry.json           # { active_pool, pools: { id → path } }
└── runtime/                # setup.sh 部署的运行时代码
```

### 每个池

```text
~/.memory/pools/<pool-id>/
├── pool.meta.json          # pool_id、chroma_collection、config 路径
├── config.json             # embedder + llm 配置
├── .env                    # API key（不入库）
├── active_memories.db      # 关键词检索唯一数据源
├── deleted_archive.db      # 删除台账
├── history.db              # memory_events 审计表
├── chroma_db/              # 向量库（collection: memories）
├── lineage.jsonl           # 合并 / 去重事件
├── pending/                # add 失败队列
├── sync_pending/           # 同步失败队列
├── grooming-merge-hints.json
└── project_aliases.json    # 可选：basename → project 映射
```

### pool.meta.json 示例

```json
{
  "pool_id": "default",
  "created_at": "2026-07-07T00:00:00+08:00",
  "migrated_from": null,
  "chroma_collection": "memories",
  "config": "config.json"
}
```

### registry.json 示例

```json
{
  "active_pool": "default",
  "pools": {
    "default": {
      "path": "/Users/you/.memory/pools/default",
      "created_at": "2026-07-07T00:00:00+08:00"
    }
  }
}
```

registry 内 `path` 为绝对路径（setup 时展开）。

---

## 模块职责

| 模块 | 职责 |
|------|------|
| `memory_store.py` | 统一 add / get / delete 入口 |
| `memory_sync.py` | 多表写入 / 删除，失败回滚 |
| `memory_delete.py` | 带 reason 的归档删除 |
| `hybrid_search.py` | 关键词 + 向量 RRF 融合 |
| `add_policy.py` | B/D/E 写入策略 |
| `llm_client.py` | embedder + LLM，主 / 备切换 |
| `pool_manager.py` | registry、切换、备份、克隆 |
| `workspace_config.py` | 解析 `.cursor/memory.json` |
| `memory_paths.py` | 从 env / registry 解析池路径 |
| `memory_hook.py` | IDE 发消息前注入（L2） |
| `mcp_server.py` | MCP 工具面 |
| `mem_viewer.py` | Flask Web UI |
| `grooming_episodic.py` | episodic 质量建议 |
| `memory_lineage.py` | 演变事件记录 |

---

## memory_store API

### add

```python
memory_store.add(
    content: str,
    metadata: dict | None = None,
    project: str = '',
    pool: str | None = None,
    actor: str = 'mcp',
) -> AddResult
```

流程：

1. `ensure_pool_schema()`
2. `prepare_add_plan()` — B/D 策略
3. 生成 UUID memory_id
4. 嵌入 → Chroma upsert
5. `sync_active_insert()`
6. 记录 ADD 到 `memory_events`
7. `run_merge_check()` — E 策略（写入后，非写入前）
8. 失败 → `pending/`

### delete

```python
memory_store.delete(
    memory_id: str,
    reason: str,          # 必填
    actor: str = 'mcp',
    pool: str | None = None,
) -> DeleteResult
```

四表同步：active → deleted_archive → memory_events → Chroma。

MCP、viewer、E 去重全部收敛到这两个函数，无双路径。

---

## 配置格式

v2 扁平 config（无 mem0 字段）：

```json
{
  "embedder": {
    "provider": "ollama",
    "model": "bge-m3",
    "base_url": "http://localhost:11434"
  },
  "llm": {
    "provider": "openai_compatible",
    "model": "glm-5.1",
    "base_url": "https://your-api.example/v1",
    "api_key_env": "NEWAPI_KEY"
  },
  "fallback_llm": {
    "provider": "ollama",
    "model": "qwen2.5:7b",
    "base_url": "http://localhost:11434"
  }
}
```

LLM provider 支持：`ollama`、`openai_compatible`、`anthropic`。

密钥从池 `.env` 经 `api_key_env` 加载。示例：`configs/config_ollama.example.json`、`configs/config_api.example.json`（v1 mem0 格式见 `configs/legacy/`）。

`config_api.example.json` 仅 LLM 走远程 API；**embedder 仍须本地 Ollama**（混合检索依赖向量）。密钥变量名见各示例中的 `api_key_env`，对应写入池目录 `.env`。

---

## 工作区绑定

可选仓库级配置（详见 [workspace-binding.md](workspace-binding.md)）：

```json
{
  "_schema": "workspace-v1",
  "pool": "default",
  "access": {
    "read": ["my-app", ""],
    "write": ["my-app"]
  }
}
```

- 控制 Hook / MCP 可读写的 **project** 列表
- write 越权：软警告，仍写入
- 无文件 → 向后兼容（不限制范围）
- mem_viewer 不过滤 read，展示 active pool 全部

查找顺序：`.cursor/memory.json` → `.memory/workspace.json` → 无。

---

## 池操作

| 操作 | CLI | MCP |
|------|-----|-----|
| 列出 | `pool_cli.py list` | `list_pools` |
| 切换 | `pool_cli.py switch <id>` | `switch_pool` |
| 创建 | `pool_cli.py create <id>` | — |
| 备份 | `pool_cli.py backup <id>` | — |
| 克隆 | `pool_cli.py clone <src> <dst>` | — |

切换更新 `registry.active_pool`。若 MCP env 设了 `MEMORY_CHROMA_COLLECTION` 会覆盖 `pool.meta.json` —— 迁移后去掉旧 env。

---

## 设计决策

| 议题 | 决定 | 理由 |
|------|------|------|
| 移除 mem0 | 是 | 核心逻辑已是自研；mem0 只增加依赖 |
| 原样入库 | 永久 | infer 导致信息丢失、中文变英文、记忆碎片化 |
| 关键词数据源 | 仅 `active_memories.db` | 审计表不参与检索；删除由 archive 过滤 |
| Chroma collection | `memories` | 与 mem0 命名切割 |
| 删除 reason | 必填 | 审计与 grooming 可追溯 |
| E 去重时机 | 写入后 | 与 v1 一致；DROP_NEW 时回滚删除 |
| write 越权 | 软警告 + 仍写 | 避免多项目场景静默丢数据 |
| grooming | 人机协作 | AI 建议、人确认；episodic 不自动删 |

---

## 环境变量

| 变量 | 用途 | 默认 |
|------|------|------|
| `MEMORY_DIR` | 数据根目录 | `~/.memory` |
| `PYTHONPATH` | 运行时代码路径 | `~/.memory/runtime` |
| `MEMORY_POOL` | 覆盖 active pool | registry |
| `MEMORY_CHROMA_COLLECTION` | 覆盖 collection 名 | pool.meta |
| `MEMORY_USER_ID` | 用户 ID | `default-user` |
| `MEMORY_KW_REL_RATIO` | keyword 相对截断比例 | `0.25` |
| `MEMORY_VECTOR_REL_MARGIN` | 向量相对阈值 | `0.10` |
| `MEMORY_FALLBACK_CONFIG` | 无 `fallback_llm` 时的兜底配置文件 | 无 |
| `MEMORY_PROJECT_ALIASES` | project 别名 JSON 路径 | pool 内 `project_aliases.json` |

> **Breaking（v2.0.1）**：运行时代码**不再读取** `MEM0_*` 环境变量（commit `64ff574`）。迁移完成后 pool `.env` 与 IDE 配置仅保留 `MEMORY_*`。GitHub Release **v2.0.0** 仍含旧别名；请使用 **≥ v2.0.1** 或 main 最新代码。

新环境只需 `MEMORY_DIR` + `PYTHONPATH`。

---

## 历史说明

local-memory v2 是 **mem0-local-enhanced** 的继任者。v1 在 mem0 库之上叠加了混合检索与写入策略；v2 去掉该依赖，用户可见能力不变。已有数据升级见 [v2-migration.md](v2-migration.md)。
