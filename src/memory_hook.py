#!/usr/bin/env python3
"""统一记忆注入 Hook：Claude Code (UserPromptSubmit) + Cursor (beforeSubmitPrompt)"""

from __future__ import annotations

import argparse
import json
import os
import sys

from hybrid_search import (
    detect_project,
    format_results_lines,
    hybrid_search,
)
from workspace_config import workspace_runtime


def extract_prompt(stdin_data: dict) -> str:
    for key in ('prompt', 'message', 'content', 'user_message', 'text'):
        value = stdin_data.get(key, '')
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, list):
            parts: list[str] = []
            for item in value:
                if isinstance(item, dict):
                    part = item.get('content', item.get('text', ''))
                    if part:
                        parts.append(str(part))
                elif isinstance(item, str):
                    parts.append(item)
            if parts:
                return '\n'.join(parts).strip()
    return ''


def resolve_cwd(stdin_data: dict) -> str:
    roots = stdin_data.get('workspace_roots') or []
    if isinstance(roots, list):
        for root in roots:
            if isinstance(root, str) and root.strip():
                return root.strip()
    return (
        stdin_data.get('cwd', '')
        or os.getenv('CLAUDE_PROJECT_DIR', '')
        or os.getcwd()
    )


def output_claude(context: str) -> None:
    print(json.dumps({
        'hookSpecificOutput': {
            'hookEventName': 'UserPromptSubmit',
            'additionalContext': context,
        },
    }, ensure_ascii=False))


def output_cursor(context: str) -> None:
    print(json.dumps({'additional_context': context}, ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser(description='local-memory hybrid search hook')
    parser.add_argument(
        '--format',
        choices=('claude', 'cursor', 'auto'),
        default='auto',
        help='输出格式：claude=Claude Code，cursor=Cursor IDE',
    )
    args = parser.parse_args()

    stdin_raw = sys.stdin.read()
    try:
        stdin_data = json.loads(stdin_raw) if stdin_raw.strip() else {}
    except json.JSONDecodeError:
        stdin_data = {}

    hook_format = args.format
    if hook_format == 'auto':
        hook_format = 'cursor' if os.getenv('CURSOR_HOOK', '') == '1' else 'claude'

    query = extract_prompt(stdin_data)
    if not query:
        print('{}' if hook_format == 'cursor' else json.dumps({}))
        return

    cwd = resolve_cwd(stdin_data)
    with workspace_runtime(cwd) as config:
        project = detect_project(cwd, workspace_aliases=config.aliases or None)
        results = hybrid_search(
            query,
            project=project,
            max_results=5,
            read_projects=config.read_projects,
        )

    if not results:
        print('{}' if hook_format == 'cursor' else json.dumps({}))
        return

    header = '[local-memory 自动注入的相关记忆]'
    context = format_results_lines(results, header=header)

    if hook_format == 'cursor':
        output_cursor(context)
    else:
        output_claude(context)


if __name__ == '__main__':
    main()
