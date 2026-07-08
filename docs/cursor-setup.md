# Cursor 集成指南

通过 MCP 与 Hook 将 local-memory 接入 Cursor。

[架构 → architecture.md](architecture.md) · [工作区绑定 → workspace-binding.md](workspace-binding.md)

---

## 前置条件

1. 在 local-memory 仓库执行 `bash scripts/setup.sh`
2. 启动 Ollama 并拉取模型：`ollama pull bge-m3`
3. 安装依赖：`pip install -r requirements.txt`

---

## 1. 注册 MCP Server

编辑 `~/.cursor/mcp.json`（Python 路径请用绝对路径）：

```json
{
  "mcpServers": {
    "local-memory": {
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

仅某项目生效时，在项目根建 `.cursor/mcp.json`。

修改后：**Cursor Settings → MCP → Restart Servers**（或 Developer: Reload Window）。

预期 **9 个工具**，含 `run_episodic_grooming`。

---

## 2. Hook 自动注入

编辑 `~/.cursor/hooks.json`：

```json
{
  "version": 1,
  "hooks": {
    "beforeSubmitPrompt": [
      {
        "command": "MEMORY_DIR=/Users/you/.memory PYTHONPATH=/Users/you/.memory/runtime /path/to/python3 /Users/you/.memory/runtime/memory_hook.py --format cursor",
        "timeout": 20
      }
    ]
  }
}
```

每次 Agent 发消息前，Hook 对 prompt 做 hybrid_search，以 `additional_context` 注入相关记忆。

Ollama 较慢时可将 `timeout` 调到 30。

Hook 成功时上下文含 `[local-memory 自动注入的相关记忆]` 标记（CLI `search_context.py` 为 `[local-memory 相关记忆]`）。

---

## 3. 工作区绑定（可选）

项目根添加 `.cursor/memory.json` 约束读写 project 范围。详见 [workspace-binding.md](workspace-binding.md)。

Hook 从 stdin `workspace_roots[0]` 解析 workspace。

---

## 4. 会话暖启动（可选）

v2 **未内置** L1 暖启动脚本。若需在对话开头注入最近 N 条记忆，在项目 `.cursor/hooks.json` 自配 `sessionStart` hook（调用 `get_all_memories` 或自定义脚本）。

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
# Ollama 运行中
curl http://localhost:11434/api/tags

# 检索可用
export MEMORY_DIR=~/.memory PYTHONPATH=~/.memory/runtime
python3 ~/.memory/runtime/search_context.py '测试'

# Cursor Settings → MCP → local-memory 正常
# 发消息时 Hook 应注入记忆上下文
```

---

## 常见问题

**MCP server errored？**  
检查 Python 路径、`MEMORY_DIR`、依赖是否安装。迁移后 env 若仍含 `MEMORY_CHROMA_COLLECTION=mem0` 会导致向量检索失败 —— 去掉该 env。

**工具数只有 8 个？**  
注册应有 **9 个**（含 `run_episodic_grooming`）。Reload Window → 重启 MCP → 必要时 `pkill -f '.memory/runtime/mcp_server.py'` 清僵尸进程。

**Hook 不注入？**  
核对 Python 绝对路径、env 变量、timeout；确认 Ollama 已启动。Hook 用 `[local-memory 自动注入的相关记忆]` + 简洁条目；MCP `search_memory` 返回带分数的调试格式（无注入头）。

**与 Claude Code 共用数据吗？**  
是，共用 `~/.memory/pools/default/`（或 registry 当前 active pool）。

**从 mem0-local 升级？**  
见 [v2-migration.md](v2-migration.md)，验证通过后删除旧 MCP 条目。迁移完成见文档「[迁移后清理](v2-migration.md#迁移后清理可选)」。

**环境变量？**  
仅需 `MEMORY_DIR` + `PYTHONPATH`。勿设 `MEM0_*`（**v2.0.1** 起 runtime 不读取；GitHub Release 请用 ≥ v2.0.1 或 main 上 `64ff574` 之后）。
