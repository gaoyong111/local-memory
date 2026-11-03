# Changelog

## v2.1.0（未发 Release tag 前）

### Added

- Docker 部署：`Dockerfile` + `docker-compose.yml`（核心服务 / 内置 ollama 备选 / mem-viewer 三服务，profile 隔离），宿主端口 18000/18001；见 [docs/docker-deploy.md](docs/docker-deploy.md)
- MCP HTTP transport：`MEMORY_MCP_TRANSPORT=http` 启用 Streamable HTTP + Bearer 鉴权（`MEMORY_HTTP_TOKEN` 未设置拒绝启动）；stdio 本地模式不变
- embedder 环境变量覆盖：`MEMORY_EMBEDDER_BASE_URL` / `MEMORY_EMBEDDER_MODEL`，优先于池 `config.json`，容器部署免改配置文件
- mem_viewer 绑定/端口 env 化：`MEMORY_VIEWER_HOST` / `MEMORY_VIEWER_PORT`

### Changed

- SQLite 写入连接统一走 WAL + busy_timeout（`sqlite_conn.connect_db`，多客户端并发写防护）
- Chroma collection 缺失时自动创建（全新池首次写入引导）
- requirements 钉版本：`mcp==1.27.1` / `chromadb==1.5.5`（与现有数据/运行环境一致）

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
