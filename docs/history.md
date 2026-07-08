# 项目沿革

local-memory 是当前维护的本地记忆栈。本文仅记录**历史背景**，不含安装或迁移步骤（新用户请从 [README](../README.md) 开始）。

## 前身：mem0-local-enhanced

| 时期 | 项目 | 说明 |
|------|------|------|
| v1 | [mem0-local-enhanced](https://github.com/gaoyong111/mem0-local-enhanced)（已归档） | 在开源 `mem0ai` 库之上叠加混合检索、写入策略与 MCP；数据目录惯例为 `~/.mem0/` |
| v2 | **local-memory**（本仓库） | 去掉 `mem0ai` 依赖，自研 SQLite + Chroma + Ollama 栈；数据根为 `~/.memory/` |

v1→v2 的能力（混合检索、原样入库、category 标签、episodic grooming 等）在 v2 中保留；存储模型与运行时路径不同。

## 设计演变要点

- **Chroma collection**：v1 常用名 `mem0` → v2 固定为 `memories`（见 `pool.meta.json`）
- **配置格式**：v1 嵌套 mem0 风格 JSON → v2 扁平 `config.json`（v1 示例见 [configs/legacy/](../configs/legacy/)）
- **MCP 服务名**：v1 `mem0-local` → v2 `local-memory`
- **环境变量**：v2 仅使用 `MEMORY_*`（`MEMORY_DIR`、`MEMORY_POOL` 等，见 [v2-design.md](v2-design.md#环境变量)）

## 归档材料

仅供查阅早期仓库与一次性迁移记录，**新安装无需阅读**：

- [docs/legacy/v1-migration-archive.md](legacy/v1-migration-archive.md) — 旧版迁移说明（已停用）
- [scripts/legacy/](../scripts/legacy/) — 旧版迁移脚本（已停用）
- [configs/legacy/](../configs/legacy/) — v1 配置格式示例
