#!/usr/bin/env python3
"""统一记忆注入 Hook：Claude Code (UserPromptSubmit) + Cursor (beforeSubmitPrompt)"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

from hybrid_search import (
    HOOK_RESULTS_HEADER,
    detect_project,
    format_results_lines,
    hybrid_search,
)
from memory_paths import resolve_runtime_dir
from workspace_config import workspace_runtime


def write_recall_debug(
    query: str,
    project: str,
    results: list[dict],
    dropped: list[dict],
) -> None:
    """MEMORY_HOOK_DEBUG=1 时写最近一次召回快照（可观测性，失败不影响 hook）。"""
    try:
        debug_path = resolve_runtime_dir() / 'last_recall.json'
        os.makedirs(debug_path.parent, exist_ok=True)
        payload = {
            'ts': time.strftime('%Y-%m-%dT%H:%M:%S'),
            'query': query[:200],
            'project': project,
            'injected': [
                {
                    'id': item.get('id', ''),
                    'rank': item.get('rank'),
                    'score': round(float(item.get('score', 0) or 0), 6),
                    'source': item.get('source', ''),
                    'recency_bonus': round(float(item.get('recency_bonus', 0) or 0), 6),
                }
                for item in results
            ],
            'dropped': dropped[:50],
        }
        with open(debug_path, 'w', encoding='utf-8') as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
    except Exception:
        pass


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
    debug_enabled = os.getenv('MEMORY_HOOK_DEBUG', '') == '1'
    diagnostics: list[dict] = []
    with workspace_runtime(cwd) as config:
        project = detect_project(cwd, workspace_aliases=config.aliases or None)
        results = hybrid_search(
            query,
            project=project,
            max_results=5,
            read_projects=config.read_projects,
            diagnostics_out=diagnostics if debug_enabled else None,
        )

    if debug_enabled:
        write_recall_debug(query, project, results, diagnostics)

    if not results:
        print('{}' if hook_format == 'cursor' else json.dumps({}))
        return

    context = format_results_lines(results, header=HOOK_RESULTS_HEADER)

    if hook_format == 'cursor':
        output_cursor(context)
    else:
        output_claude(context)


if __name__ == '__main__':
    main()
