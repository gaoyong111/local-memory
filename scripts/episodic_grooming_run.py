#!/usr/bin/env python3
"""episodic 梳理批处理 CLI — 逻辑在 episodic_grooming_batch（MCP 优先时用 run_episodic_grooming 工具）。"""

from __future__ import annotations

import argparse
import os
import sys

_RUNTIME = os.getenv('PYTHONPATH', os.path.expanduser('~/.memory/runtime'))
_SRC = os.path.join(os.path.dirname(__file__), '..', 'src')


def _prepend_sys_path(*paths: str) -> None:
    for path in paths:
        if path and path not in sys.path:
            sys.path.insert(0, path)


for _part in _RUNTIME.split(':'):
    _prepend_sys_path(_part.strip())
_prepend_sys_path(_SRC)

from episodic_grooming_batch import format_grooming_summary, run_episodic_grooming  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description='episodic grooming 批处理')
    parser.add_argument('--all-episodic', action='store_true', help='分析全部 episodic')
    parser.add_argument('--dry-run', action='store_true', help='只打印不写库')
    args = parser.parse_args()
    summary = run_episodic_grooming(all_episodic=args.all_episodic, dry_run=args.dry_run)
    if args.dry_run:
        for row in summary.get('results') or []:
            import json

            print(json.dumps(row, ensure_ascii=False))
    print(format_grooming_summary(summary))


if __name__ == '__main__':
    main()
