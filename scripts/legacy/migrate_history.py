#!/usr/bin/env python3
"""history.db：mem0 history 表 → memory_events（原地迁移，保留旧表）。"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent.parent / 'src'
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from memory_sync import _HISTORY_SCHEMA  # noqa: E402


def _normalize_event(raw: str) -> str:
    event = (raw or '').strip().upper()
    if event in ('ADD', 'UPDATE', 'DELETE'):
        return event
    return 'ADD'


def _load_active_meta(pool: Path) -> dict[str, tuple[str, str]]:
    active_db = pool / 'active_memories.db'
    if not active_db.is_file():
        return {}
    conn = sqlite3.connect(active_db)
    try:
        rows = conn.execute(
            'SELECT memory_id, project, category FROM active_memories'
        ).fetchall()
    finally:
        conn.close()
    return {mid: (proj or '', cat or '') for mid, proj, cat in rows if mid}


def migrate(pool: Path, *, dry_run: bool = False) -> dict:
    history_db = pool / 'history.db'
    if not history_db.is_file():
        raise FileNotFoundError(f'history.db 不存在: {history_db}')

    conn = sqlite3.connect(history_db)
    try:
        if not conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='history'"
        ).fetchone():
            raise RuntimeError('未找到 mem0 history 表')

        old_count = conn.execute('SELECT COUNT(*) FROM history').fetchone()[0]
        existing = 0
        if conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='memory_events'"
        ).fetchone():
            existing = conn.execute('SELECT COUNT(*) FROM memory_events').fetchone()[0]
            if existing > 0 and existing >= old_count:
                return {
                    'skipped': True,
                    'existing_events': existing,
                    'history_rows': old_count,
                    'reason': 'memory_events 已覆盖 history 行数',
                }
            if existing > 0 and existing < old_count:
                raise RuntimeError(
                    f'检测到部分迁移（memory_events={existing}，history={old_count}）。'
                    '请先备份并清空 memory_events 表后重试，或从 history.db.mem0-backup 恢复。'
                )

        if dry_run:
            return {'dry_run': True, 'history_rows': old_count, 'existing_events': existing}

        backup = history_db.with_name(history_db.name + '.mem0-backup')
        if not backup.exists():
            shutil.copy2(history_db, backup)

        conn.executescript(_HISTORY_SCHEMA)
        conn.commit()
    finally:
        conn.close()

    active_meta = _load_active_meta(pool)
    log_path = pool / 'migration.history.log'
    migrated = 0
    errors: list[str] = []

    conn = sqlite3.connect(history_db)
    try:
        rows = conn.execute(
            """
            SELECT memory_id, old_memory, new_memory, event, created_at, actor_id, role
            FROM history
            ORDER BY created_at ASC
            """
        ).fetchall()

        for memory_id, old_memory, new_memory, event, created_at, actor_id, role in rows:
            if not memory_id:
                errors.append(f'skip row without memory_id event={event}')
                continue
            ev = _normalize_event(event)
            if ev == 'ADD':
                old_content = None
                new_content = new_memory or old_memory
            elif ev == 'UPDATE':
                old_content = old_memory
                new_content = new_memory
            else:
                old_content = old_memory or new_memory
                new_content = None

            project, category = active_meta.get(memory_id, ('', ''))
            actor = (actor_id or role or 'system').strip() or 'system'
            if actor in ('mem_viewer', 'viewer'):
                actor = 'viewer'

            try:
                conn.execute(
                    """
                    INSERT INTO memory_events (
                        memory_id, event, old_content, new_content,
                        project, category, actor, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        memory_id,
                        ev,
                        old_content,
                        new_content,
                        project or None,
                        category or None,
                        actor,
                        created_at or datetime.now(timezone.utc).isoformat(),
                    ),
                )
                migrated += 1
            except sqlite3.Error as exc:
                errors.append(f'{memory_id}/{ev}: {exc}')

        conn.commit()
        new_count = conn.execute('SELECT COUNT(*) FROM memory_events').fetchone()[0]
    finally:
        conn.close()

    if errors:
        log_path.write_text('\n'.join(errors) + '\n', encoding='utf-8')

    return {
        'history_rows': old_count,
        'migrated': migrated,
        'memory_events_rows': new_count,
        'backup': str(backup),
        'errors': len(errors),
        'log': str(log_path) if errors else '',
    }


def main() -> None:
    parser = argparse.ArgumentParser(description='迁移 history.db → memory_events')
    parser.add_argument('--pool', required=True, help='池目录，如 ~/.memory/pools/default')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    pool = Path(args.pool).expanduser().resolve()
    result = migrate(pool, dry_run=args.dry_run)
    print(result)


if __name__ == '__main__':
    main()
