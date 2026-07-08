# 从 mem0-local-enhanced 升级

将已有 `~/.mem0/` 数据迁移到 local-memory v2。

**新用户**：跳过本文，直接看 [README](../README.md) 快速开始。

---

## 变更对照

| 项目 | v1 | v2 |
|------|----|----|
| 数据根目录 | `~/.mem0/` | `~/.memory/pools/default/` |
| Python 依赖 | `mem0ai` | 无 |
| Chroma collection | `mem0` | `memories` |
| 审计表 | mem0 `history` schema | 自建 `memory_events` |
| MCP 服务名 | `mem0-local` | `local-memory` |
| 运行时代码 | mem0 仓库内 | `~/.memory/runtime/` |

混合检索、写入策略、Hook、viewer 等能力不变。

---

## 一键迁移

**建议先备份（脚本也会自动备份）：**

```bash
cp -a ~/.mem0 ~/.mem0.backup-$(date +%Y%m%d)
```

**执行：**

```bash
cd local-memory
bash scripts/migrate_full_to_v2.sh
```

脚本步骤：

1. 备份 `~/.mem0` → `~/.mem0.backup-<timestamp>`
2. 复制池数据到 `~/.memory/pools/default/`
3. history → `memory_events` 迁移
4. Chroma collection `mem0` → `memories`
5. config 转为 v2 扁平格式
6. 更新 `registry.json` 指向新池
7. `~/.mem0` 改为 symlink（原目录移至 `~/.mem0.pre-symlink-<timestamp>`）
8. 调用 `setup.sh` 部署 runtime

上面的手动备份可选；脚本第 1 步会创建带时间戳备份。

---

## 迁移后检查清单

1. **更新 IDE MCP 配置** — 服务名改为 `local-memory`，env 设为：

   ```json
   {
     "MEMORY_DIR": "/Users/you/.memory",
     "PYTHONPATH": "/Users/you/.memory/runtime"
   }
   ```

   去掉 `MEMORY_CHROMA_COLLECTION=mem0` 及一切 `MEM0_*` env（**v2.0.1** 起 runtime 不再读取）。

2. **拉取新代码后重新部署 runtime**（迁移脚本已跑过 setup 则仅在更新代码后需要）：

   ```bash
   bash scripts/setup.sh
   ```

   若使用 daily-review skill 辅助脚本，需额外：`INSTALL_DAILY_REVIEW_HELPERS=1 bash scripts/setup.sh`（见 [daily-review-integration.md](daily-review-integration.md)）。

3. **冒烟**：

   ```bash
   export MEMORY_DIR=~/.memory PYTHONPATH=~/.memory/runtime
   python3 ~/.memory/runtime/search_context.py '测试'
   ```

4. **确认 MCP 工具数为 9**（含 `run_episodic_grooming`）。

5. **删除旧 MCP 条目** — 验证通过后从 IDE 配置移除 `mem0-local`。

---

## 分步脚本（按需）

完整脚本未跑或某步失败时，可单独执行：

```bash
python3 scripts/migrate_history.py --pool ~/.memory/pools/default
python3 scripts/migrate_config.py --pool ~/.memory/pools/default
python3 scripts/migrate_chroma_collection.py --pool ~/.memory/pools/default
```

支持 `--dry-run`（视脚本而定）。history 迁移保留旧 `history` 表只读，便于对比回滚。

---

## 回滚

迁移脚本结束时会打印备份与 pre-symlink 路径，优先用脚本输出的实际路径。

**第 7 步 symlink 未完成前失败：**

```bash
rm -rf ~/.memory/pools/default
cp -a ~/.mem0.backup-YYYYMMDD ~/.mem0   # 或脚本备份路径
```

**symlink 已创建：**

```bash
rm ~/.mem0
cp -a ~/.mem0.pre-symlink-YYYYMMDD ~/.mem0
rm -rf ~/.memory/pools/default          # 仅当要丢弃已迁移副本时
```

然后恢复 IDE 中旧 MCP 配置（`mem0-local`）。

---

## 迁移后清理（可选）

**v2.0.1** 运行时代码仅认 `MEMORY_*`，不再读 `MEM0_*` 或 `~/.mem0` 路径：

1. pool `.env` 去掉 `MEM0_DIR`、`MEM0_FALLBACK_CONFIG`、`MEM0_CONFIG`
2. 删除 `~/.mem0` symlink（若曾创建）
3. Chroma 孤儿 collection（`mem0_entities`、`mem0migrations`）可在确认 `memories` 完整后删除
4. v1 配置文件移至 `pools/default/legacy/` 或删除

---

## 过渡期（仅迁移脚本语境）

`migrate_full_to_v2.sh` 仍可能创建 `~/.mem0` → pool 的 symlink，便于迁移窗口内旧脚本读数据。**部署 v2.0.1 runtime 后**，IDE / cron / skill 应全部改用 `MEMORY_DIR=~/.memory`，symlink 可移除。

- `pending/`、`sync_pending/` 随池目录迁移
