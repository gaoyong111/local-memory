# Changelog

## v2.2.0（未发 Release tag 前）

### Added

- **Agent 身份与池绑定**：`~/.memory/registry.json` 新增 `agents` 段；`add_memory` 支持 `agent` 参数（未登记身份拒绝写入，路由到绑定池，metadata 留 `written_by` + `audited=false`，显式传 `audited=true` 也会被拦下）与 `target_pool` 跨池写入（须在该 agent `allowed_pools` 白名单内）；`retry_pending` 按 pending 条目 `metadata.agent` 反查池路由
- **跨池迁移**：MCP 工具 `transfer_memory`（总数 9 → 10），`move` = 保真迁移（原 id/时间戳/向量/grooming 元数据）+ 源池留 1 条 TRANSFER 审计痕，`copy` = 双存
- **池参数**：`search_memory` / `delete_memory` / `confirm_grooming` 均加可选 `pool`，操作非活跃池不再依赖 `switch_pool`（根治多会话 active_pool 并发互踩）
- **写入脱敏**：`src/redact.py` 9 类凭据模式（API key / token / 密码 / 私钥等）写入时统一替换为 `[REDACTED:label]`，在 `memory_store.add` 收口；同时修复 pending 降级队列明文落盘敏感内容的泄漏
- **recency 半衰期回血**：`active_memories` 加 `last_accessed_at` 列（`memory_sync` 提供批量 load/touch），命中记忆 bonus=0.006×0.5^(days/30)（半衰期 30 天），长期未用的记忆排序自然回血
- **召回可观测**：hook 输出新增 `diagnostics_out`；`MEMORY_HOOK_DEBUG=1` 写 `last_recall.json` 召回快照（失败不影响 hook）
- mem_viewer：池动态切换、未审计/有演变 badge、审计确认 API、非活跃池只读护栏（写路由 403）、全量列表折叠 + URL 筛选持久化

### Fixed

- **向量分数负值**：`embed_text` 由 Ollama 旧端点 `/api/embeddings`（未归一化向量，norm≈26.7 使 l2 距离爆炸、score 出 -347 级负分，相对截断失效）换为 `/api/embed`（归一化向量）；l2 与余弦单调等价，排序/RRF 不受影响
- chroma documents 主写入路径为空：`_chroma_upsert` 补传 `documents=[content]`，与 `transfer_memory` 行为一致
- `get_chroma_client` 按池路径缓存（原进程级单例跨池污染）
- merge 重校验失败时清除死 merge hint，避免反复撞同一失效目标
- grooming delete 建议倾向性反转：宁可提 delete 待用户确认，也不埋掉可疑条目

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
