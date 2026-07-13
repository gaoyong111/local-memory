# 架构详解

local-memory 如何存储、检索与注入记忆。

[设计概览 → v2-design.md](v2-design.md) · [Web UI → mem-viewer-design.md](mem-viewer-design.md) · [English → README_EN.md](../README_EN.md)

---

## 系统分层

```text
┌─────────────────────────────────────────────────────────┐
│  IDE 集成层                                              │
│  memory_hook.py · mcp_server.py · mem_viewer.py         │
└────────────────────────┬────────────────────────────────┘
                         │
┌────────────────────────▼────────────────────────────────┐
│  策略层                                                  │
│  add_policy (B/D/E) · grooming · memory_lineage         │
└────────────────────────┬────────────────────────────────┘
                         │
┌────────────────────────▼────────────────────────────────┐
│  服务层                                                  │
│  memory_store · llm_client · pool_manager                 │
│  workspace_config · hybrid_search · memory_sync           │
└────────────────────────┬────────────────────────────────┘
                         │
┌────────────────────────▼────────────────────────────────┐
│  存储层（按池隔离）                                       │
│  active_memories.db · Chroma · history.db               │
│  deleted_archive.db · lineage.jsonl                     │
└─────────────────────────────────────────────────────────┘
```

---

## 存储模型

每个**记忆池**是自包含目录，池内按职责拆分：

| 存储 | 职责 | 参与检索？ |
|------|------|-----------|
| `active_memories.db` | 活跃记忆（正文 + project/category/lang） | **是** — 关键词路 |
| Chroma（collection: `memories`） | 向量 + metadata | **是** — 向量路 |
| `history.db` → `memory_events` | CRUD 审计 | 否 |
| `deleted_archive.db` | 删除台账（reason / 快照 / actor） | 否（提供 deleted_ids 过滤） |
| `lineage.jsonl` | 合并 / 去重 / grooming 事件 | 否 |
| `pending/` | add 失败重试队列 | 否 |
| `sync_pending/` | 多表同步失败队列 | 否 |

**写入路径**：Ollama 嵌入 → Chroma upsert → 同步 `active_memories` → 写入 `memory_events` → 可选 E 策略去重。

**删除路径**：active / archive / history / Chroma 四表事务同步；任一步失败回滚或写入 `sync_pending/`。

向量写入须带 Ollama 预计算 embedding（`upsert(embeddings=...)`）。勿对 Chroma 使用裸 `col.add(documents=...)`，否则会触发内置 ONNX 模型下载而非走 Ollama。

---

## 写入策略（B / D / E）

所有记忆**原样入库（verbatim）**，入库时不经 LLM 推断改写。这是中文记忆可靠性的核心设计 —— 避免模块名被泛化、中文被翻译成英文。

### B — 分类标签

`metadata.category` 仅作标签，不改变存储模式。

| category | 典型用途 |
|----------|----------|
| `reference` | 事实、技术约定、领域知识 |
| `preference` | 用户偏好 |
| `workflow` | 可复用流程 / 方法论 |
| `behavior` | 行为规则 |
| `episodic` | 踩坑、决策、事件（留空时默认） |

历史非标标签写入时规范化为 `reference`。

### D — 结构化格式化

含明确模块 / 字段 / 规则的技术信息，格式化为关键词密度更高的模板后入库：

```text
[userService] loginType: 字符串（关键词: 登录, API）
```

Chroma metadata 仅支持标量，嵌套 `structured` dict 序列化为 `structured_json` 字符串。

### E — LLM 去重

每次写入后自动去重检查：

1. 关键词分数 ≥ 15.0 → 候选集
2. Token Jaccard ≥ 0.5 → 精筛
3. LLM 决策 `KEEP` 或 `DROP_NEW`

原则：**宁多勿删**。仅当旧记忆已完整覆盖同一组事实时才 `DROP_NEW`。

LLM 不可用时写入仍成功，去重跳过（默认 `KEEP`）。

---

## 混合检索

针对中文 query 做了专门优化（滑窗分词、子序列弱匹配、中文 query 排除 `lang=en` 向量结果）。

```text
用户 query
  │
  ├─ ① 向量检索（Chroma + Ollama bge-m3）
  │     中文 query 排除 lang=en；相对阈值 top1−0.10
  │     → top-50 vec_rank_map
  │
  ├─ ② 关键词检索（active_memories.db）
  │     2–4 字滑窗 + TF cap=3 + 条件子序列
  │     相对截断 score < top1×0.25
  │     → top-50 kw_rank
  │
  └─ ③ 加权 RRF 融合
        rrf = 1/(K+vec_rank) + 0.5·1/(K+kw_rank)；K=15
        project 匹配 +0.005；preference 类 +0.008
        配额：project 前 3 直保 + 全局保底 2
```

**返回条数**：

| 入口 | max |
|------|-----|
| Hook L2（`memory_hook.py`） | 5 |
| MCP `search_memory` | 8 |
| mem_viewer 搜索面板 | 8 |
| mem_viewer `/api/similar` | 5 |

MCP 输出含 `kw=` / `vec=` / `kw_rank` / `vec_rank` / `rrf=` —— keyword 分不是 0～1 语义相似度。

### 项目检测

`detect_project(cwd)` 推断 project 标签：

1. workspace aliases（`.cursor/memory.json`）
2. 池级 `project_aliases.json`
3. 目录 basename（Desktop 等泛化目录返回空 = 全局）

---

## 记忆注入三层

| 层 | 触发 | 行为 |
|----|------|------|
| L1 | 可选 `sessionStart` hook（用户自配） | 最近 N 条，不看 query |
| L2 | `beforeSubmitPrompt` / `UserPromptSubmit` | 每条消息 hybrid_search |
| L3 | Agent 调用 MCP `search_memory` | 按需检索 |

L2/L3 共用 `hybrid_search.py`。有 workspace 配置时 L2/L3 应用 read 过滤；mem_viewer 不过滤（展示 active pool 全部 project）。

---

## MCP 工具（共 9 个）

| 工具 | 说明 |
|------|------|
| `add_memory` | 添加，走 B/D/E；遵守 workspace write 范围 |
| `search_memory` | 混合检索；遵守 workspace read 范围 |
| `get_all_memories` | 列出全部；可选 project 过滤 |
| `delete_memory` | 按 ID 删除；**reason 必填** |
| `retry_pending` | 重试 `pending/` 与 `sync_pending/` |
| `run_episodic_grooming` | 批处理 episodic 梳理 |
| `confirm_grooming` | 确认 grooming，清 `grooming_pending` |
| `list_pools` | 列出所有记忆池 |
| `switch_pool` | 切换活跃记忆池 |

---

## episodic 人机梳理

episodic 不自动删 / 合 / 升。AI 写建议，人在 mem_viewer 或对话中确认。

| 字段 | 含义 |
|------|------|
| `grooming_pending=1` | 待确认（新 episodic 自动打上） |
| `grooming_action` | `keep` / `delete` / `promote` |
| `grooming_target_category` | promote 目标 category |

merge 建议存于 `grooming-merge-hints.json`（当次 ephemeral）。采纳后 metadata 写 `merged_from`，并追加 `lineage.jsonl`。

MCP 已运行时优先 `run_episodic_grooming`（避免 Chroma 多进程冲突）。

---

## 失败恢复

| 队列 | 触发 | 重试 |
|------|------|------|
| `pending/` | add 失败（Ollama 未启动等） | MCP `retry_pending` |
| `sync_pending/` | 多表同步部分失败 | `retry_pending`（含 sync 重试） |

重试 ≥3 次仍失败需人工处理。

---

## LLM 配置

池 `config.json` 定义 embedder 与 LLM：

```json
{
  "embedder": { "provider": "ollama", "model": "bge-m3", "base_url": "..." },
  "llm": { "provider": "openai_compatible", "model": "...", "api_key_env": "..." },
  "fallback_llm": { "provider": "ollama", "model": "qwen2.5:7b", "base_url": "..." }
}
```

- **嵌入**：始终本地 Ollama bge-m3（中文效果好、无 API 费用）
- **LLM**：仅用于 E 策略去重与 episodic grooming
- 主 LLM 失败 → 自动切 `fallback_llm`

MCP 启动探活 LLM；失败仅 warning —— 关键词检索仍可用。

---

## 已知限制

- Chroma metadata 仅标量；嵌套 dict 须 JSON 字符串
- Chroma 不在 SQLite 事务内；极端失败可能留 `sync_pending/`
- Hook 默认 timeout 20s；Ollama 慢时可调大
- Ollama 未启动：MCP 可启动，关键词路部分可用，向量 add/search 不可用
- 记忆量极大（数千条+）时可评估 SQLite FTS5 / rerank
- 每日复盘 `memory diff` 须在**进化提取**（`add_memory`）之后执行，否则本次新增认知不进 diff；详见 [daily-review-integration.md](daily-review-integration.md)
- `review_helpers.py` 有三处部署：`local-memory/scripts/`（源码）、`~/.memory/runtime/scripts/`（setup 默认）、`~/.claude/skills/daily-review/scripts/`（`INSTALL_DAILY_REVIEW_HELPERS=1`）；改 helper 后须同步 skill 路径（复盘 cron 实际调用处）

---

## 相关文档

- [v2-design.md](v2-design.md) — 概念模型、模块职责
- [workspace-binding.md](workspace-binding.md) — 仓库级读写权限
- [cursor-setup.md](cursor-setup.md) / [claude-code-setup.md](claude-code-setup.md) — IDE 接入
- [daily-review-integration.md](daily-review-integration.md) — 可选复盘工作流
