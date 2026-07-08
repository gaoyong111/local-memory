# local-memory

**面向 AI 编程助手的本地持久化记忆系统 —— 中文友好，原样入库。**

在本地存储中文事实、偏好与流程知识；通过 IDE Hook 自动注入上下文，或通过 MCP 按需检索。数据不出本机，不依赖 mem0 库 —— 底层为 SQLite、Chroma 与 Ollama embedding。

核心设计：**写入什么存什么**，不经 LLM 推断改写，避免中文被翻译、模块名被泛化；混合检索针对中文 query 优化（滑窗分词 + bge-m3 向量 + RRF 融合）。

[English → README_EN.md](README_EN.md)

---

## 为什么用 local-memory？

大多数 AI 助手在会话之间会「失忆」。local-memory 为 Agent 提供**可持久化、可检索**的记忆层，完全运行在本地：

| 能力 | 说明 |
|------|------|
| **混合检索** | SQLite 关键词 + Chroma 向量（bge-m3），加权 RRF 融合 |
| **原样入库** | 写入什么存什么，入库时不经 LLM 改写 |
| **写入策略** | 分类标签、结构化格式化、写入时 LLM 去重 |
| **多记忆池** | 工作 / 个人 / 实验场景物理隔离 |
| **工作区绑定** | 仓库级读写权限（`.cursor/memory.json`） |
| **IDE 集成** | Cursor / Claude Code 的 MCP 与 Hook |
| **Web UI** | `mem_viewer` 浏览、编辑、episodic 梳理 |

---

## 快速开始

### 前置条件

- Python 3.10+
- 本地 [Ollama](https://ollama.com)（`bge-m3` 做 embedding；去重/grooming 可选 LLM）
- `pip install -r requirements.txt`

### 安装

```bash
git clone https://github.com/gaoyong111/local-memory.git
cd local-memory
pip install -r requirements.txt
bash scripts/setup.sh
```

`setup.sh` 会部署运行时代码到 `~/.memory/runtime/`，并创建默认池。全新环境下池位于 `~/.memory/pools/default/`，同时复制示例 `config.json` 与 `.env`。

> **本机仍有旧版 `~/.mem0/`？** 若存在 `~/.mem0/active_memories.db` 且尚未迁移，`setup.sh` 可能将默认池指向 `~/.mem0`。请先完成 [v2 迁移](docs/v2-migration.md)，或强制 greenfield：`MEMORY_POOL_DIR=~/.memory/pools/default bash scripts/setup.sh`。

### 配置 LLM / embedder（可选）

编辑 setup 已复制的池配置，或覆盖为示例：

```bash
cp configs/config_ollama.example.json ~/.memory/pools/default/config.json
# 远程 LLM：configs/config_api.example.json（embedder 仍须本地 Ollama）
#   echo 'NEWAPI_KEY=your-key' >> ~/.memory/pools/default/.env
ollama pull bge-m3   # 全本地或 config_api 均需 bge-m3 做向量检索
```

### 冒烟测试

```bash
export MEMORY_DIR=~/.memory PYTHONPATH=~/.memory/runtime
python3 ~/.memory/runtime/search_context.py '测试'
```

### 接入 IDE

| IDE | 文档 |
|-----|------|
| Cursor | [docs/cursor-setup.md](docs/cursor-setup.md) |
| Claude Code | [docs/claude-code-setup.md](docs/claude-code-setup.md) |

在 MCP 中注册 `local-memory`，指向 `~/.memory/runtime/mcp_server.py`；可选配置 Hook 实现发消息前自动注入记忆。

---

## 架构概览

```text
 IDE 层           Hook (L2) · MCP · mem_viewer
       │
 策略层           add_policy (B/D/E) · grooming · lineage
       │
 服务层           memory_store · llm_client · pool_manager · workspace_config
       │
 存储层           active_memories.db · Chroma · history · deleted_archive
```

- **记忆池（Pool）** — 自包含目录，含独立数据库与 Chroma collection
- **项目（Project）** — 记忆的逻辑标签（`""` = 全局，`"my-app"` = 项目级）
- **工作区绑定** — 可选的仓库级读写范围配置

详见 [docs/architecture.md](docs/architecture.md) · [docs/v2-design.md](docs/v2-design.md)

---

## MCP 工具（共 9 个）

| 工具 | 说明 |
|------|------|
| `add_memory` | 添加记忆，支持 category metadata，写入时去重 |
| `search_memory` | 混合检索，可选 project 过滤 |
| `get_all_memories` | 列出记忆，可选 project 过滤 |
| `delete_memory` | 按 ID 删除（reason 必填） |
| `retry_pending` | 重试失败的写入 / 同步 |
| `run_episodic_grooming` | 批处理 episodic 质量梳理 |
| `confirm_grooming` | 确认 grooming 建议 |
| `list_pools` | 列出所有记忆池 |
| `switch_pool` | 切换活跃记忆池 |

---

## 文档索引

| 文档 | 内容 |
|------|------|
| [architecture.md](docs/architecture.md) | 写入策略、混合检索、存储模型 |
| [v2-design.md](docs/v2-design.md) | 概念模型、模块职责、设计决策 |
| [workspace-binding.md](docs/workspace-binding.md) | 仓库级读写权限 |
| [mem-viewer-design.md](docs/mem-viewer-design.md) | Web UI 规格 |
| [cursor-setup.md](docs/cursor-setup.md) | Cursor MCP + Hook |
| [claude-code-setup.md](docs/claude-code-setup.md) | Claude Code MCP + Hook |
| [daily-review-integration.md](docs/daily-review-integration.md) | 可选：每日复盘（`INSTALL_DAILY_REVIEW_HELPERS=1` 部署辅助脚本） |
| [v2-migration.md](docs/v2-migration.md) | 从 mem0-local-enhanced 升级 |

---

## 池管理

```bash
export MEMORY_DIR=~/.memory PYTHONPATH=~/.memory/runtime
python3 ~/.memory/runtime/scripts/pool_cli.py list
python3 ~/.memory/runtime/scripts/pool_cli.py create my-pool
python3 ~/.memory/runtime/scripts/pool_cli.py switch default
python3 ~/.memory/runtime/scripts/pool_cli.py backup default
```

也可通过 MCP `list_pools` / `switch_pool` 操作。

---

## mem_viewer

本地 Web UI，用于浏览与编辑记忆：

```bash
bash ~/.memory/runtime/mem_viewer.sh
# 可选：传入仓库根目录，启用手动写入时的 workspace 软警告
bash ~/.memory/runtime/mem_viewer.sh /path/to/your/project
```

---

## 开发

[![Tests](https://github.com/gaoyong111/local-memory/actions/workflows/test.yml/badge.svg)](https://github.com/gaoyong111/local-memory/actions/workflows/test.yml)

```bash
pip install -r requirements-dev.txt
export MEMORY_DIR=~/.memory PYTHONPATH=src
python3 -m unittest discover -s tests -v
```

修改 `src/` 或 `configs/` 后重新部署（`pool_manager` 等从 runtime 读配置模板，不 redeploy 会导致 `pool_cli create` 无法 seed）：

```bash
bash scripts/setup.sh
```

---

## 从 mem0-local-enhanced 升级

若曾使用 [mem0-local-enhanced](https://github.com/gaoyong111/mem0-local-enhanced)，数据在 `~/.mem0/`：

```bash
bash scripts/migrate_full_to_v2.sh
```

回滚与分步说明见 [docs/v2-migration.md](docs/v2-migration.md)。全新安装可跳过。

---

## 数据目录

```text
~/.memory/
├── registry.json              # 活跃池注册表
├── runtime/                   # 部署的运行时代码
└── pools/
    └── default/
        ├── pool.meta.json
        ├── config.json
        ├── active_memories.db
        ├── history.db
        ├── deleted_archive.db
        ├── chroma_db/
        ├── pending/
        └── sync_pending/
```

数据全部在本地。备份池目录或使用 `pool_cli.py backup` 即可。

---

## License

[MIT](LICENSE)
