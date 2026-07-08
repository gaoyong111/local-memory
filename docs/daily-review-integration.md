# 每日复盘集成（可选）

自动化复盘工作流如何读写 local-memory。

**可选模块** —— 不配置复盘，local-memory 仍完整可用。

[架构 → architecture.md](architecture.md) · [Preflight 与降级](#preflight-与降级模式)

---

## 职责分工

| 系统 | 职责 |
|------|------|
| local-memory | 记忆的存、搜、注入 |
| 每日复盘 | 从对话 / git 提炼认知，写复盘文档，反哺记忆 |

```text
┌─────────────────────────────────────────────────────────────┐
│ 运行时注入（读记忆）                                          │
│ L2 Hook · L3 MCP search_memory                              │
└────────────────────────────┬────────────────────────────────┘
                             │
┌────────────────────────────▼────────────────────────────────┐
│ local-memory 池（~/.memory/pools/default/）                 │
│ hybrid_search · add_policy · pending · mem_viewer           │
└────────────────────────────┬────────────────────────────────┘
                             │
┌────────────────────────────▼────────────────────────────────┐
│ 每日复盘（定时或手动）                                        │
│ Preflight → 采集 → 重试 pending → 写文档 → grooming         │
└─────────────────────────────────────────────────────────────┘
```

---

## 复盘用到的数据路径

| 路径 / 接口 | 用途 |
|-------------|------|
| `pending/` | add 失败 — 复盘开头可 `retry_pending` |
| `sync_pending/` | 多表同步失败 |
| MCP `add_memory` | 写入提炼后的 reference / workflow / behavior |
| MCP `run_episodic_grooming` | 复盘后批处理 episodic |
| 快照 diff | 对比复盘前后记忆状态 |

池路径：`~/.memory/pools/<active>/`（`~/.mem0` symlink 等价）。

---

## Preflight 与降级模式

复盘开始前检查基础设施：

| 检查项 | 方式 | 失败影响 |
|--------|------|----------|
| Ollama | `curl localhost:11434/api/tags` | 跳过依赖 embedding 的步骤 |
| local-memory MCP | `search_memory` / `get_all_memories` | 跳过在线写入 |
| pending 队列 | `ls pending/*.json` | 仅记录条数 |

**降级模式**：Ollama 或 MCP 不可用时，仍从 git / 会话产出复盘文档；跳过 pending 重试、记忆提取、升格、grooming；在文档中记录告警。

保证核心复盘任务不因基础设施故障完全中断。

---

## pending 重试

`add_memory` 失败时写入 `pending/`：

```json
{
  "content": "...",
  "metadata": {"category": "episodic"},
  "project": "my-app",
  "retry_count": 0,
  "created_at": "..."
}
```

MCP `retry_pending` 同时处理 `pending/` 与 `sync_pending/`。MCP 健康时复盘流程应尽早调用。

---

## 记忆提取模式

复盘会话后的典型流程：

1. 识别可持久化的事实（偏好、决策、约定）
2. 显式 category 写入：

   ```text
   add_memory(content="...", metadata='{"category":"reference"}', project="my-app")
   ```

3. 默认 verbatim 入库（无 infer）
4. 近似重复交给 E 策略

踩坑 / 决策类 → `category: episodic` → 进入 grooming 队列。

---

## 复盘后 grooming

MCP 已运行时优先：

```
run_episodic_grooming(dry_run=false)
```

避免独立脚本与 MCP 争抢 Chroma 客户端。

人在 mem_viewer 或对话中 `confirm_grooming` 确认。

---

## 会话采集注意

扫描 IDE 会话日志时：

- **Claude Code**：按 JSONL `timestamp` 过滤
- **Cursor**：无消息级 timestamp，用文件 `mtime`；排除 `/subagents/`

会话多时可并行扫描。

复盘时间范围应**增量**（上次截止点至今），勿按自然月硬切。

---

## review_helpers 部署（可选）

`scripts/review_helpers.py` 供 daily-review skill 采集 git / 会话。默认 `setup.sh` **不会**复制到 `~/.claude/skills/daily-review/scripts/`，避免覆盖本地 skill 定制；`review_helpers.py` 仍会随第 2 步进入 `~/.memory/runtime/scripts/`（运行时代码，非 skill 安装）。

启用 skill 辅助（**会覆盖**目标路径已有文件）：

```bash
INSTALL_DAILY_REVIEW_HELPERS=1 bash scripts/setup.sh
```

环境变量须为字面量 `1`（`true` / `yes` 无效）。

---

## 自建复盘工作流

最低集成：

1. 部署 local-memory 并注册 MCP
2. 触发时 Preflight
3. 采集 git / 会话 / TODO
4. MCP 健康则 `retry_pending`
5. 提炼并 `add_memory`
6. 可选：`run_episodic_grooming` + 快照 diff

本文描述的是 local-memory 暴露的**接口**，不绑定特定 skill 或 cron 实现。

---

## 相关文档

- [architecture.md](architecture.md) — 写入策略、pending 队列
- [mem-viewer-design.md](mem-viewer-design.md) — grooming UI
- [claude-code-setup.md](claude-code-setup.md) — MCP 注册
