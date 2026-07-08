# 工作区绑定（Workspace Binding）

仓库级记忆读写范围配置。

[设计背景 → v2-design.md](v2-design.md) · [架构 → architecture.md](architecture.md)

---

## 要解决什么问题

同时开多个项目时，Agent 应该只能**看到和写入**当前仓库相关的记忆，而不是整个记忆库。

v2 在现有 `project` 标签之上，增加仓库级配置文件实现读写边界。

---

## 配置文件位置

**推荐**：`<repo>/.cursor/memory.json`

**备选**：`<repo>/.memory/workspace.json`（与 IDE 无关的通用位置）

两者都不存在时，行为与 v1 相同（不限制范围）。

### 查找规则

`load_workspace_config(cwd)` 从 `cwd` 向上遍历，直到：

1. 找到配置文件 → 加载并停止
2. 到达 git 根（含 `.git`）→ 视为无配置
3. 到达文件系统根 → 视为无配置

monorepo 子包各自维护配置时，以**离 cwd 最近**的为准。同目录两个文件都存在时，`.cursor/memory.json` 优先。

---

## Schema

```json
{
  "_schema": "workspace-v1",
  "pool": "default",
  "access": {
    "read": ["my-app", ""],
    "write": ["my-app"]
  },
  "detect": {
    "aliases": {
      "my-app": "my-app"
    }
  }
}
```

### 字段说明

| 字段 | 必填 | 说明 |
|------|------|------|
| `pool` | 否 | 使用的记忆池；默认 registry `active_pool` |
| `access.read` | 否 | search / hook / get_all 允许的 project 列表 |
| `access.write` | 否 | add 允许的 project 列表 |
| `detect.aliases` | 否 | cwd basename → project id（覆盖池级同名 key） |

### project 特殊值

| 值 | 含义 |
|----|------|
| `""` | 全局记忆 |
| `"my-app"` | 项目级记忆 |
| `"*"` | 所有 project（等同无限制） |
| `[]` | **read**：结果恒空；**write**：每次 add 均软警告 |

---

## 默认行为

| 配置状态 | read | write |
|----------|------|-------|
| 无文件 | active pool 全部 project | 全部 |
| 有文件但 `access` 为空 | 全部 | 全部 |
| 显式 `read` / `write` 列表 | 过滤 | 过滤 |

---

## 写入权限

`add_memory` 目标 project 不在 `access.write` 内时：

- MCP 响应中记录**软警告**
- 写入**仍执行**

避免 Agent 误判 project 时静默丢数据。可在日志或 mem_viewer 中审计警告。

---

## 读取权限

应用于：

- MCP `search_memory`、`get_all_memories`
- Hook L2（`memory_hook.py`）

**不应用于**：

- `mem_viewer` — 设计上展示 active pool 全部 project
- MCP 在 `cwd` 为空且无法推断 workspace 时

### Hook cwd 来源

优先级：stdin `workspace_roots[0]`（Cursor）→ `cwd` 参数 → `CLAUDE_PROJECT_DIR` → `os.getcwd()`。

---

## 池覆盖

workspace 配置中指定 `pool` 时，MCP / Hook 从该池读数据，而非 registry `active_pool`。

mem_viewer 始终用 registry `active_pool`（或 `MEMORY_POOL` env），忽略 workspace 池绑定。

---

## 示例：多项目

**项目 A**（`.cursor/memory.json`）：

```json
{
  "_schema": "workspace-v1",
  "pool": "default",
  "access": {
    "read": ["project-a", ""],
    "write": ["project-a"]
  }
}
```

**项目 B**：

```json
{
  "_schema": "workspace-v1",
  "access": {
    "read": ["project-b", ""],
    "write": ["project-b"]
  }
}
```

全局记忆（`""`）两边可读；各自只写自己的 project。

---

## 提交到仓库

`.cursor/memory.json` 可安全提交 —— 只有项目名和权限规则，无密钥。clone 后团队成员继承相同记忆范围。

本仓库示例：[.cursor/memory.json](../.cursor/memory.json)。

---

## 故障排查

| 现象 | 检查 |
|------|------|
| Hook 无记忆注入 | `access.read` 是否漏了需要的 project；全局记忆加 `""` |
| MCP 搜不到但 viewer 有数据 | workspace read 过滤生效；核对 `cwd` 与配置 |
| 写入 project 不对 | `detect.aliases` 与 `detect_project(cwd)` basename |
| 池不对 | `pool` 字段 vs registry `active_pool` |
