#!/usr/bin/env python3
"""local-memory 记忆池 CLI。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

RUNTIME = Path(__file__).resolve().parent.parent
if str(RUNTIME) not in sys.path:
    sys.path.insert(0, str(RUNTIME))

from pool_manager import (  # noqa: E402
    backup_pool,
    clone_pool,
    create_pool,
    export_pool,
    format_pools_text,
    import_pool,
    list_pools,
    switch_pool,
)


def main() -> int:
    parser = argparse.ArgumentParser(description='local-memory pool manager')
    sub = parser.add_subparsers(dest='command', required=True)

    sub.add_parser('list', help='列出 registry 中所有 pool')

    switch_p = sub.add_parser('switch', help='切换 active pool')
    switch_p.add_argument('pool_id')

    create_p = sub.add_parser('create', help='创建新 pool')
    create_p.add_argument('pool_id')
    create_p.add_argument('--path', type=Path, default=None)
    create_p.add_argument('--chroma-collection', default='memories')
    create_p.add_argument('--set-active', action='store_true')

    clone_p = sub.add_parser('clone', help='复制 pool')
    clone_p.add_argument('source_id')
    clone_p.add_argument('dest_id')
    clone_p.add_argument('--path', type=Path, default=None)
    clone_p.add_argument('--set-active', action='store_true')

    export_p = sub.add_parser('export', help='导出 pool 目录')
    export_p.add_argument('pool_id')
    export_p.add_argument('dest', type=Path)

    import_p = sub.add_parser('import', help='从目录导入 pool')
    import_p.add_argument('source', type=Path)
    import_p.add_argument('--id', dest='pool_id', default=None)
    import_p.add_argument('--set-active', action='store_true')

    backup_p = sub.add_parser('backup', help='备份 active 或指定 pool')
    backup_p.add_argument('pool_id', nargs='?', default=None)
    backup_p.add_argument('--dest', type=Path, default=None)

    args = parser.parse_args()

    try:
        if args.command == 'list':
            print(format_pools_text(list_pools()))
        elif args.command == 'switch':
            switch_pool(args.pool_id)
            print(f'已切换 active pool → {args.pool_id}')
            print(format_pools_text())
        elif args.command == 'create':
            entry = create_pool(
                args.pool_id,
                args.path,
                chroma_collection=args.chroma_collection,
                set_active=args.set_active,
            )
            print(f'已创建 pool {entry.pool_id} → {entry.path}')
        elif args.command == 'clone':
            entry = clone_pool(
                args.source_id,
                args.dest_id,
                args.path,
                set_active=args.set_active,
            )
            print(f'已克隆 {args.source_id} → {entry.pool_id} ({entry.path})')
        elif args.command == 'export':
            dest = export_pool(args.pool_id, args.dest)
            print(f'已导出 {args.pool_id} → {dest}')
        elif args.command == 'import':
            entry = import_pool(args.source, args.pool_id, set_active=args.set_active)
            print(f'已导入 → {entry.pool_id} ({entry.path})')
        elif args.command == 'backup':
            dest = backup_pool(args.pool_id, args.dest)
            print(f'已备份 → {dest}')
    except Exception as exc:
        print(f'错误: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
