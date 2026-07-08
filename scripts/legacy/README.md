# 归档：v1 迁移脚本（已停用）

本目录脚本用于 **mem0-local-enhanced → local-memory v2** 的一次性数据迁移，仅作历史留存。

**新用户请勿运行。** 安装与配置见仓库根目录 [README.md](../../README.md)。

| 脚本 | 原用途 |
|------|--------|
| `migrate_full_to_v2.sh` | 完整目录迁移 |
| `migrate_chroma_collection.py` | Chroma collection 重命名 |
| `migrate_config.py` | v1 配置转 v2 |
| `migrate_history.py` | history 表 → memory_events |

说明文档：[docs/legacy/v1-migration-archive.md](../../docs/legacy/v1-migration-archive.md)
