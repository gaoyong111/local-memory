# Legacy 配置示例（mem0-local-enhanced / v1）

**新用户请使用上级目录的 v2 示例**（`configs/config_ollama.example.json`、`configs/config_api.example.json`）。

本目录保留旧版 mem0 嵌套格式，仅供：

- 对照 v1 → v2 字段差异
- `scripts/legacy/migrate_config.py` 迁移参考

| 文件 | 说明 |
|------|------|
| `config_ollama.example.json` | v1 Ollama + Chroma `mem0` collection |
| `config_api.example.json` | v1 远程 LLM + 内联 `api_key`（不推荐） |

v2 扁平格式见 [v2-design.md](../../docs/v2-design.md#配置格式)。
