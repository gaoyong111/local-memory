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
│ Preflight → 采集 → pending重试 → 写文档 → 进化提取 → diff → grooming      │
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
| 快照 diff | 对比复盘前后记忆状态（**进化提取之后**执行，确保本次新增认知纳入 diff） |

池路径：`~/.memory/pools/<active>/`。

---

## Preflight 与降级模式

复盘开始前检查基础设施：

| 检查项 | 方式 | 失败影响 |
|--------|------|----------|
| Ollama | `curl localhost:11434/api/tags` | 跳过依赖 embedding 的步骤 |
| local-memory MCP | `search_memory` / `get_all_memories` | 跳过在线写入 |
| pending 队列 | `ls pending/*.json` | 仅记录条数 |
| 权限漂移 | `sync_permissions.py --check` | drift 时执行 sync 修复并在「配置变更记录」标注 |

**降级模式**：Ollama 或 MCP 不可用时，仍从 git / 会话产出复盘文档；跳过 pending 重试、记忆提取、升格、grooming；在文档中记录告警并给出修复命令。会话中途基础设施恢复时补跑被跳过环节（进化提取 → diff → 升格 → grooming），而非整篇放弃。

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

**失败处理**：LLM 主端点不可用 / Chroma 异常 / MCP 中断时**直接跳过，不硬跑**；复盘主体结束后 AI 重试一次，仍失败则告知用户。跳过原因记入复盘文档（「基础设施告警」或 grooming 小节）；主 LLM 降级告警记忆 `source=llm-degradation-alert` 会写入 pending 供复盘可见（本地 qwen 降级已移除，2026-08-11）。

---

## 会话采集注意

扫描 IDE 会话日志时：

- **Claude Code**：按 JSONL `timestamp` 过滤
- **Cursor**：无消息级 timestamp，用文件 `mtime`；排除 `/subagents/`

会话多时可并行扫描。

复盘时间范围应**增量**（上次截止点至今），勿按自然月硬切。

---

## review_helpers 部署（可选）

`scripts/review_helpers.py` 供 daily-review skill 采集 git / 会话 / 工具统计。默认 `setup.sh` **不会**复制到 `~/.claude/skills/daily-review/scripts/`，避免覆盖本地 skill 定制；`review_helpers.py` 仍会随第 2 步进入 `~/.memory/runtime/scripts/`（运行时代码，非 skill 安装）。

**子命令**（均走 `python3`，don't ask 模式下比复合 Bash 更可靠）：

| 子命令 | 用途 |
|--------|------|
| `check-missed-run` | 漏跑检测（工作日化：工作日 09:00 期望一次复盘，周末/节假日不产生期望）+ 返回扫描起点 |
| `renewal-due` | cron 续期到期判断（阈值 = 3 天 + 区间内周末/节假日天数，节假日读 `~/daily-reviews/holidays.yaml`，每年初手动更新） |
| `list-sessions` | Claude/Cursor 会话清单 |
| `git-log` | 扫描 `~/Desktop/h5_release/` git 提交 |
| `tool-stats` | 工具调用次数与授权方式 |
| `diff` / `snapshot` | local-memory 快照与对比（diff 在进化提取**之后**） |
| `record-scan-end` | 写入下次扫描起点（2026-07-30 起写复盘结束时刻 now，上次执行窗口不重扫；终点只前进不后退） |
| `log-cron-renewal` | cron 续期日志（含 lastFiredAt） |

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
3. 采集 git / 会话 / TODO（`review_helpers.py git-log`、`list-sessions`、`tool-stats`）
4. MCP 健康则 `retry_pending`
5. 提炼并 `add_memory`（进化提取）
6. `diff --baseline latest` 对比记忆变化
7. 可选：`run_episodic_grooming` + `snapshot`

本文描述的是 local-memory 暴露的**接口**，不绑定特定 skill 或 cron 实现。

---

## 相关文档

- [architecture.md](architecture.md) — 写入策略、pending 队列
- [mem-viewer-design.md](mem-viewer-design.md) — grooming UI
- [claude-code-setup.md](claude-code-setup.md) — MCP 注册
