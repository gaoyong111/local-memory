# Changelog

## v2.0.1（未发 Release tag 前：main @ `64ff574` 起）

### Breaking

- 运行时代码仅认 `MEMORY_*` 环境变量与 `~/.memory` 数据根（不再读取旧版 `MEM0_*` / `~/.mem0`）

### Changed

- 环境变量统一为 `MEMORY_*`（见 [v2-design.md](docs/v2-design.md#环境变量)）
- `review_helpers` 快照文件命名：`memory-snapshot-*.json`
- 文档：移除面向新用户的迁移指南；沿革见 [history.md](docs/history.md)，归档迁移材料见 [docs/legacy/](docs/legacy/)

## v2.0.0

- 初始公开发布：local-memory v2 本地化记忆栈（SQLite + Chroma + Ollama，9 个 MCP 工具）
- 项目沿革见 [history.md](docs/history.md)
