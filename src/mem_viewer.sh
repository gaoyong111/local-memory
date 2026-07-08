#!/bin/bash
# local-memory 记忆可视化 Web UI
# 用法:
#   bash ~/.memory/runtime/mem_viewer.sh
#       → 默认 registry active pool，展示全部 project
#   bash ~/.memory/runtime/mem_viewer.sh /path/to/your/project
#       → 额外加载该 repo 的 workspace write 软警告（不限制 read / 不绑 pool）

RUNTIME_DIR="${MEMORY_RUNTIME:-$HOME/.memory/runtime}"
export MEMORY_DIR="${MEMORY_DIR:-$HOME/.memory}"
export PYTHONPATH="${PYTHONPATH:-$RUNTIME_DIR}:$PYTHONPATH"

if [[ -n "${1:-}" ]]; then
  if ! cd "$1" 2>/dev/null; then
    echo "错误: 目录不存在: $1" >&2
    exit 1
  fi
  export WORKSPACE_ROOT="$(pwd)"
else
  unset WORKSPACE_ROOT
fi

python3 "$RUNTIME_DIR/mem_viewer.py"
