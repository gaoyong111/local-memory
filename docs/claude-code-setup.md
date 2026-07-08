# Claude Code 集成指南

通过 MCP 与 Hook 将 local-memory 接入 Claude Code。

[架构 → architecture.md](architecture.md) · [工作区绑定 → workspace-binding.md](workspace-binding.md)

---

## 前置条件

1. 在 local-memory 仓库执行 `bash scripts/setup.sh`
2. 启动 Ollama 并拉取模型：`ollama pull bge-m3`
3. 安装依赖：`pip install -r requirements.txt`

---

## 1. 注册 MCP Server

### 全局注册（推荐）

编辑 `~/.claude.json`：

```json
{
  "mcpServers": {
    "local-memory": {
      "type": "stdio",
      "command": "/path/to/python3",
      "args": ["/Users/you/.memory/runtime/mcp_server.py"],
      "env": {
        "MEMORY_DIR": "/Users/you/.memory",
        "PYTHONPATH": "/Users/you/.memory/runtime"
      }
    }
  }
}
```

### CLI 方式

```
/mcp add local-memory -- /path/to/python3 /Users/you/.memory/runtime/mcp_server.py
```

仍需在 env 中补全 `MEMORY_DIR` 与 `PYTHONPATH`。

预期 **9 个工具**，含 `run_episodic_grooming`。

---

## 2. Hook 自动注入

编辑 `~/.claude/settings.json`：

```json
{
  "hooks": {
    "UserPromptSubmit": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "MEMORY_DIR=/Users/you/.memory PYTHONPATH=/Users/you/.memory/runtime /path/to/python3 /Users/you/.memory/runtime/memory_hook.py --format claude",
            "timeout": 20,
            "statusMessage": "检索 local-memory 相关记忆"
          }
        ]
      }
    ]
  }
}
```

`hook_search.py` 为兼容入口，内部委托 `memory_hook.py --format claude`。

---

## 3. MCP 权限

在 permissions 策略中允许 MCP 工具。示例 allow 列表：

```yaml
allow:
  mcp:
    - mcp__local-memory__search_memory
    - mcp__local-memory__get_all_memories
    - mcp__local-memory__add_memory
    - mcp__local-memory__delete_memory
    - mcp__local-memory__retry_pending
    - mcp__local-memory__run_episodic_grooming
    - mcp__local-memory__confirm_grooming
    - mcp__local-memory__list_pools
    - mcp__local-memory__switch_pool
```

若使用 permissions sync 工作流，改 yaml 后执行 sync 脚本；勿手改会被 sync 整段覆盖的 settings permissions。

---

## 4. 工作区绑定（可选）

项目根添加 `.cursor/memory.json` 或 `.memory/workspace.json`。详见 [workspace-binding.md](workspace-binding.md)。

---

## 5. 每日复盘（可选）

local-memory 可衔接自动化复盘流程。见 [daily-review-integration.md](daily-review-integration.md)。

---

## MCP 工具一览（共 9 个）

| 工具 | 用途 |
|------|------|
| `add_memory` | 添加记忆，支持 category metadata，写入时去重 |
| `search_memory` | 混合检索，可选 project 过滤 |
| `get_all_memories` | 列出记忆，可选 project 过滤 |
| `delete_memory` | 按 ID 删除（reason 必填） |
| `retry_pending` | 重试 pending / sync 队列 |
| `run_episodic_grooming` | 批处理 episodic 梳理 |
| `confirm_grooming` | 确认 grooming 建议 |
| `list_pools` | 列出所有记忆池 |
| `switch_pool` | 切换活跃记忆池 |

---

## 验证

```bash
bash scripts/setup.sh
curl http://localhost:11434/api/tags
export MEMORY_DIR=~/.memory PYTHONPATH=~/.memory/runtime
python3 ~/.memory/runtime/search_context.py '测试'
```

确认 MCP 工具可调用（**9 个**，含 `run_episodic_grooming`），发消息时有记忆注入；Hook 上下文含 `[local-memory 自动注入的相关记忆]`（CLI 搜索为 `[local-memory 相关记忆]`）。MCP `search_memory` 为带 id/kw/vec/rrf 的调试格式，与 Hook 简洁条目不同。

---

## 常见问题

**MCP 启动失败？**  
Ollama 未运行或池路径错误。确认 `MEMORY_DIR` 与 registry 一致。迁移后去掉 `MEMORY_CHROMA_COLLECTION=mem0`。

**还要设 `MEM0_DIR` 吗？**  
不需要。仅用 `MEMORY_DIR` + `PYTHONPATH`。`~/.mem0` symlink 仅供旧脚本兼容。

**写入时中文变英文？**  
v2 原样入库，无 infer 改写。

**add 失败数据丢了？**  
进入 active pool 的 `pending/`，用 MCP `retry_pending` 重试。

**与 Cursor 共用数据吗？**  
是，同一默认池。

**从 mem0-local 升级？**  
见 [v2-migration.md](v2-migration.md)。
