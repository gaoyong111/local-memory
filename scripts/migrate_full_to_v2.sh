#!/bin/bash
# 一次性完整迁移：~/.mem0 → ~/.memory/pools/default（history/config/chroma/registry）
# 用法：cd local-memory && bash scripts/migrate_full_to_v2.sh [--dry-run]
set -euo pipefail

DRY_RUN=false
if [[ "${1:-}" == "--dry-run" ]]; then
  DRY_RUN=true
fi

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MEMORY_DIR="${MEMORY_DIR:-$HOME/.memory}"
POOL="${MEMORY_POOL_DIR:-$MEMORY_DIR/pools/default}"
SRC="${MEM0_SRC:-$HOME/.mem0}"
STAMP="$(date +%Y%m%d-%H%M)"
BACKUP="${BACKUP:-$HOME/.mem0.backup-$STAMP}"
PY="${PYTHON:-python3}"

run() {
  if $DRY_RUN; then
    echo "[dry-run] $*"
  else
    "$@"
  fi
}

echo "=== local-memory 完整迁移 ==="
echo "源: $SRC"
echo "目标池: $POOL"
echo "备份: $BACKUP"

if [[ -L "$SRC" ]]; then
  echo "错误: $SRC 已是 symlink（可能已迁移）。若需重跑，先恢复目录或设 MEM0_SRC 指向备份。" >&2
  exit 1
fi
if [[ ! -f "$SRC/active_memories.db" ]]; then
  echo "错误: $SRC/active_memories.db 不存在" >&2
  exit 1
fi

if $DRY_RUN; then
  echo "[dry-run] 将执行: 备份 → 复制池文件 → migrate_history/config/chroma → registry → symlink"
  "$PY" "$REPO_ROOT/scripts/migrate_config.py" --pool "$SRC" --dry-run
  "$PY" "$REPO_ROOT/scripts/migrate_chroma_collection.py" --pool "$SRC" --dry-run
  exit 0
fi

# 0. 备份
run cp -a "$SRC" "$BACKUP"
echo "[ok] 备份 → $BACKUP"

# 1. 准备目标池目录
run mkdir -p "$POOL/pending" "$POOL/sync_pending"

# 2. 复制数据文件（不含旧 runtime .py）
for item in \
  active_memories.db deleted_archive.db history.db lineage.jsonl \
  project_aliases.json grooming-merge-hints.json .env \
  config_local.json config_ollama.json config_newapi.json config_newapi_openai.json config.yaml; do
  if [[ -e "$SRC/$item" ]]; then
    run cp -a "$SRC/$item" "$POOL/"
  fi
done
run rm -rf "$POOL/chroma_db"
run cp -a "$SRC/chroma_db" "$POOL/"
if [[ -d "$SRC/pending" ]]; then
  run rsync -a "$SRC/pending/" "$POOL/pending/"
fi
if [[ -d "$SRC/sync_pending" ]]; then
  run rsync -a "$SRC/sync_pending/" "$POOL/sync_pending/"
fi
echo "[ok] 池文件已复制"

# 3. pool.meta（chroma 名由 migrate_chroma 更新）
if [[ ! -f "$POOL/pool.meta.json" ]]; then
  run cp -a "$SRC/pool.meta.json" "$POOL/pool.meta.json" 2>/dev/null || true
fi

# 4. 迁移脚本
"$PY" "$REPO_ROOT/scripts/migrate_history.py" --pool "$POOL"
echo "[ok] migrate_history"

"$PY" "$REPO_ROOT/scripts/migrate_config.py" --pool "$POOL"
echo "[ok] migrate_config"

"$PY" "$REPO_ROOT/scripts/migrate_chroma_collection.py" --pool "$POOL"
echo "[ok] migrate_chroma → memories"

# 5b. 清理 .env：去掉 MEM0_CONFIG，避免 dotenv 绕过 v2 config.json
if [[ -f "$POOL/.env" ]]; then
  if grep -q '^MEM0_CONFIG=' "$POOL/.env" 2>/dev/null; then
    if sed --version 2>/dev/null | grep -q GNU; then
      run sed -i '/^MEM0_CONFIG=/d' "$POOL/.env"
    else
      run sed -i '' '/^MEM0_CONFIG=/d' "$POOL/.env"
    fi
    echo "[ok] .env 已去掉 MEM0_CONFIG"
  fi
fi

# 5. 更新 pool.meta migrated_from
"$PY" - <<PY
import json
from pathlib import Path
p = Path("$POOL/pool.meta.json")
meta = json.loads(p.read_text())
meta["migrated_from"] = "~/.mem0"
meta["chroma_collection"] = "memories"
p.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n")
PY

# 6. registry → pools/default
run mkdir -p "$MEMORY_DIR"
cat > "$MEMORY_DIR/registry.json" << EOF
{
  "active_pool": "default",
  "pools": {
    "default": {
      "path": "$POOL",
      "created_at": "$(date -u +%Y-%m-%dT%H:%M:%S+00:00)",
      "note": "migrated from ~/.mem0 ($STAMP)"
    }
  }
}
EOF
echo "[ok] registry → $POOL"

# 7. symlink ~/.mem0 → pool（兼容仍读 MEM0_DIR 的脚本）
PRE="$HOME/.mem0.pre-symlink-$STAMP"
if [[ -d "$SRC" && ! -L "$SRC" ]]; then
  run mv "$SRC" "$PRE"
  run ln -s "$POOL" "$HOME/.mem0"
  echo "[ok] ~/.mem0 → symlink $POOL（旧目录 $PRE）"
fi

# 8. 部署 runtime
MEMORY_POOL_DIR="$POOL" bash "$REPO_ROOT/scripts/setup.sh"

echo ""
echo "=== 迁移完成 ==="
echo "请更新 IDE env（去掉 MEMORY_CHROMA_COLLECTION=mem0，改用 pool.meta memories）："
echo "  MEMORY_DIR=$MEMORY_DIR"
echo "  PYTHONPATH=$MEMORY_DIR/runtime"
echo "  pool .env 勿设 MEM0_CONFIG（脚本已尝试删除；保留 API key 即可）"
echo ""
echo "冒烟:"
echo "  export MEMORY_DIR=$MEMORY_DIR PYTHONPATH=$MEMORY_DIR/runtime"
echo "  python3 $MEMORY_DIR/runtime/search_context.py '测试'"
