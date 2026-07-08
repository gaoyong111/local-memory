#!/bin/bash
# local-memory v2 一键部署 — cp 到 ~/.memory/runtime/，不覆盖已有 pool 数据
# 用法：bash scripts/setup.sh
# 可选：INSTALL_DAILY_REVIEW_HELPERS=1 bash scripts/setup.sh  # 复制 review_helpers 到 daily-review skill

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
MEMORY_DIR="${MEMORY_DIR:-$HOME/.memory}"
RUNTIME_DIR="${MEMORY_RUNTIME:-$MEMORY_DIR/runtime}"
POOL_DIR="${MEMORY_POOL_DIR:-$MEMORY_DIR/pools/default}"
# 过渡期：若 ~/.mem0 已有数据，默认池指向它
if [ -z "$MEMORY_POOL_DIR" ] && [ -f "$HOME/.mem0/active_memories.db" ]; then
    POOL_DIR="$HOME/.mem0"
fi
SKILL_SCRIPTS="$HOME/.claude/skills/daily-review/scripts"

echo "=== local-memory 部署 ==="
echo "运行时代码: $RUNTIME_DIR"
echo "默认数据池: $POOL_DIR"

mkdir -p "$RUNTIME_DIR" "$RUNTIME_DIR/scripts" "$POOL_DIR/pending" "$POOL_DIR/sync_pending"

# 1. 复制源码（cp，非 symlink）
cp "$REPO_ROOT/src/"*.py "$RUNTIME_DIR/"
cp "$REPO_ROOT/src/mem_viewer.sh" "$RUNTIME_DIR/"
chmod +x "$RUNTIME_DIR/mem_viewer.sh"
echo "[1/5] 源码已 cp 到 runtime"

# 2. 辅助脚本 + 配置模板（runtime 供 pool_manager 新建池时 seed）
cp "$REPO_ROOT/scripts/"*.py "$RUNTIME_DIR/scripts/" 2>/dev/null || true
template_copied=0
for template in \
    "$REPO_ROOT/configs/config_ollama.example.json" \
    "$REPO_ROOT/src/project_aliases.example.json"; do
    if [ ! -f "$template" ]; then
        echo "警告: 模板缺失，跳过 cp: $template" >&2
        continue
    fi
    cp "$template" "$RUNTIME_DIR/"
    template_copied=$((template_copied + 1))
done
if [ ! -f "$RUNTIME_DIR/config_ollama.example.json" ]; then
    echo "错误: config_ollama.example.json 未部署到 runtime，pool_cli create 将无法 seed config" >&2
    exit 1
fi
echo "[2/5] scripts 已 cp；config 模板 ${template_copied}/2"

# 3. daily-review skill 辅助（默认跳过，避免覆盖用户本地 skill）
if [ "${INSTALL_DAILY_REVIEW_HELPERS:-0}" = "1" ]; then
    if [ -f "$REPO_ROOT/scripts/review_helpers.py" ]; then
        mkdir -p "$SKILL_SCRIPTS"
        cp "$REPO_ROOT/scripts/review_helpers.py" "$SKILL_SCRIPTS/"
        echo "[3/5] review_helpers.py 已 cp 到 $SKILL_SCRIPTS"
    else
        echo "[3/5] 跳过 review_helpers（源文件不存在）"
    fi
else
    echo "[3/5] 跳过 review_helpers（需 INSTALL_DAILY_REVIEW_HELPERS=1 启用）"
fi

# 4. greenfield / 迁移后池结构（不覆盖已有数据；不盲目覆盖 registry）
if [ ! -f "$MEMORY_DIR/registry.json" ]; then
    cat > "$MEMORY_DIR/registry.json" << EOF
{
  "active_pool": "default",
  "pools": {
    "default": {
      "path": "$POOL_DIR",
      "created_at": "$(date -u +%Y-%m-%dT%H:%M:%S+00:00)"
    }
  }
}
EOF
    echo "[4/5] registry.json 已创建"
else
    echo "[4/5] registry.json 已存在，跳过（避免覆盖 migrate_full_to_v2.sh 结果）"
fi

if [ ! -f "$POOL_DIR/pool.meta.json" ]; then
    cat > "$POOL_DIR/pool.meta.json" << EOF
{
  "pool_id": "default",
  "created_at": "$(date -u +%Y-%m-%dT%H:%M:%S+00:00)",
  "migrated_from": null,
  "chroma_collection": "memories",
  "config": "config.json"
}
EOF
fi

if [ ! -f "$POOL_DIR/config.json" ]; then
    if [ -f "$REPO_ROOT/configs/config_ollama.example.json" ]; then
        cp "$REPO_ROOT/configs/config_ollama.example.json" "$POOL_DIR/config.json"
    fi
fi

if [ ! -f "$POOL_DIR/project_aliases.json" ] && [ -f "$REPO_ROOT/src/project_aliases.example.json" ]; then
    cp "$REPO_ROOT/src/project_aliases.example.json" "$POOL_DIR/project_aliases.json"
fi

if [ ! -f "$POOL_DIR/.env" ] && [ -f "$REPO_ROOT/.env.example" ]; then
    cp "$REPO_ROOT/.env.example" "$POOL_DIR/.env"
fi

touch "$POOL_DIR/active_memories.db" "$POOL_DIR/history.db" "$POOL_DIR/deleted_archive.db" 2>/dev/null || true
mkdir -p "$POOL_DIR/chroma_db"
echo "[4/5] pool 目录就绪"

# 5. 依赖提示
echo "[5/5] 请确保已安装: pip install -r $REPO_ROOT/requirements.txt"

echo ""
echo "=== 部署完成 ==="
echo ""
echo "标准 v2 布局（迁移后）："
echo "  export MEMORY_DIR=$MEMORY_DIR"
echo "  export PYTHONPATH=$RUNTIME_DIR"
echo "  # collection 读 pool.meta.json（通常 memories），勿设 MEMORY_CHROMA_COLLECTION=mem0"
echo ""
echo "MCP 配置示例 (~/.cursor/mcp.json)："
echo "  \"local-memory\": {"
echo "    \"command\": \"python3\","
echo "    \"args\": [\"$RUNTIME_DIR/mcp_server.py\"],"
echo "    \"env\": {"
echo "      \"MEMORY_DIR\": \"$MEMORY_DIR\","
echo "      \"PYTHONPATH\": \"$RUNTIME_DIR\""
echo "    }"
echo "  }"
echo ""
echo "冒烟: PYTHONPATH=$RUNTIME_DIR MEMORY_DIR=$MEMORY_DIR python3 $RUNTIME_DIR/search_context.py '测试'"
echo ""
