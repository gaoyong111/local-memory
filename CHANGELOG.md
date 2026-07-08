# Changelog

## v2.0.1（未发 Release tag 前：main @ `64ff574` 起）

### Breaking

- 运行时代码**不再读取** `MEM0_*` 环境变量（`MEM0_DIR`、`MEM0_CONFIG`、`MEM0_KW_REL_RATIO` 等）
- 路径解析仅认 `MEMORY_DIR` + registry；移除 `~/.mem0` fallback

### Changed

- 环境变量统一为 `MEMORY_*`（见 [v2-design.md](docs/v2-design.md#环境变量)）
- `review_helpers` 快照文件命名：`memory-snapshot-*.json`（仍可读旧 `mem0-snapshot-*`）
- 文档：迁移后清理步骤、IDE setup FAQ 同步

## v2.0.0

- 初始公开发布：local-memory v2 本地化记忆栈（SQLite + Chroma + Ollama，9 个 MCP 工具）
- 从 mem0-local-enhanced 迁移路径见 [v2-migration.md](docs/v2-migration.md)
