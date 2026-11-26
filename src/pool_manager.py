"""记忆池 registry 管理：list / switch / create / clone / import / export / backup。"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from memory_paths import _expand, load_registry, resolve_memory_root, resolve_pool_path, resolve_runtime_dir


@dataclass
class PoolEntry:
    pool_id: str
    path: Path
    active: bool
    created_at: str = ''
    note: str = ''
    chroma_collection: str = 'memories'

    def to_dict(self) -> dict[str, Any]:
        return {
            'pool_id': self.pool_id,
            'path': str(self.path),
            'active': self.active,
            'created_at': self.created_at,
            'note': self.note,
            'chroma_collection': self.chroma_collection,
        }


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S+00:00')


def registry_file(root: Path | None = None) -> Path:
    return (root or resolve_memory_root()) / 'registry.json'


def save_registry(registry: dict[str, Any], root: Path | None = None) -> Path:
    path = registry_file(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', encoding='utf-8') as handle:
        json.dump(registry, handle, ensure_ascii=False, indent=2)
        handle.write('\n')
    return path


def _pool_path_from_entry(pool_id: str, entry: dict[str, Any], root: Path) -> Path:
    raw = entry.get('path')
    if raw:
        return _expand(raw)
    return (root / 'pools' / pool_id).resolve()


def _read_pool_meta(pool_path: Path) -> dict[str, Any]:
    meta_file = pool_path / 'pool.meta.json'
    if not meta_file.is_file():
        return {}
    try:
        return json.loads(meta_file.read_text(encoding='utf-8'))
    except (json.JSONDecodeError, OSError):
        return {}


def list_pools(root: Path | None = None) -> list[PoolEntry]:
    root = root or resolve_memory_root()
    registry = load_registry(root)
    active_id = registry.get('active_pool', 'default')
    pools = registry.get('pools') or {}
    entries: list[PoolEntry] = []
    for pool_id, item in pools.items():
        if not isinstance(item, dict):
            continue
        pool_path = _pool_path_from_entry(pool_id, item, root)
        meta = _read_pool_meta(pool_path)
        entries.append(
            PoolEntry(
                pool_id=pool_id,
                path=pool_path,
                active=(pool_id == active_id),
                created_at=str(item.get('created_at') or meta.get('created_at') or ''),
                note=str(item.get('note') or ''),
                chroma_collection=str(meta.get('chroma_collection') or 'memories'),
            )
        )
    entries.sort(key=lambda row: (not row.active, row.pool_id))
    return entries


def get_active_pool_id(root: Path | None = None) -> str:
    registry = load_registry(root or resolve_memory_root())
    return str(registry.get('active_pool') or 'default')


def _resolve_pool_id(pool_id: str | None, root: Path | None = None) -> str:
    return pool_id or get_active_pool_id(root)


def _get_pool_path(pool_id: str, root: Path | None = None) -> Path:
    root = root or resolve_memory_root()
    registry = load_registry(root)
    pools = registry.get('pools') or {}
    if pool_id not in pools:
        raise ValueError(f'pool 不存在: {pool_id}')
    path = _pool_path_from_entry(pool_id, pools[pool_id], root)
    if not path.is_dir():
        raise ValueError(f'pool 路径不存在: {path}')
    return path


def switch_pool(pool_id: str, root: Path | None = None) -> PoolEntry:
    root = root or resolve_memory_root()
    registry = load_registry(root)
    pools = registry.get('pools') or {}
    if pool_id not in pools:
        raise ValueError(f'pool 不存在: {pool_id}')
    _get_pool_path(pool_id, root)
    registry['active_pool'] = pool_id
    save_registry(registry, root)
    return list_pools(root)[0]


def _seed_config(pool_path: Path, template_pool: Path | None = None) -> None:
    target = pool_path / 'config.json'
    if target.is_file():
        return
    if template_pool and (template_pool / 'config.json').is_file():
        shutil.copy2(template_pool / 'config.json', target)
        return
    example = resolve_runtime_dir() / 'config_ollama.example.json'
    if example.is_file():
        shutil.copy2(example, target)


def init_pool_directory(
    pool_path: Path,
    pool_id: str,
    *,
    chroma_collection: str = 'memories',
    migrated_from: str | None = None,
    template_pool: Path | None = None,
) -> None:
    pool_path.mkdir(parents=True, exist_ok=True)
    for name in ('pending', 'sync_pending', 'chroma_db'):
        (pool_path / name).mkdir(exist_ok=True)
    for name in ('active_memories.db', 'history.db', 'deleted_archive.db'):
        db_file = pool_path / name
        if not db_file.exists():
            db_file.touch()
    meta_file = pool_path / 'pool.meta.json'
    if not meta_file.is_file():
        meta = {
            'pool_id': pool_id,
            'created_at': _now_iso(),
            'migrated_from': migrated_from,
            'chroma_collection': chroma_collection,
            'config': 'config.json',
        }
        meta_file.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    _seed_config(pool_path, template_pool)
    aliases = pool_path / 'project_aliases.json'
    example = resolve_runtime_dir() / 'project_aliases.example.json'
    if not aliases.is_file() and example.is_file():
        shutil.copy2(example, aliases)


def register_pool(
    pool_id: str,
    pool_path: Path,
    root: Path | None = None,
    *,
    note: str = '',
    set_active: bool = False,
) -> PoolEntry:
    root = root or resolve_memory_root()
    registry = load_registry(root)
    pools = registry.setdefault('pools', {})
    if pool_id in pools:
        raise ValueError(f'pool 已存在: {pool_id}')
    abs_path = pool_path.expanduser().resolve()
    pools[pool_id] = {
        'path': str(abs_path),
        'created_at': _now_iso(),
    }
    if note:
        pools[pool_id]['note'] = note
    if set_active or not registry.get('active_pool'):
        registry['active_pool'] = pool_id
    save_registry(registry, root)
    for entry in list_pools(root):
        if entry.pool_id == pool_id:
            return entry
    raise RuntimeError(f'注册后未找到 pool: {pool_id}')


def create_pool(
    pool_id: str,
    path: Path | None = None,
    root: Path | None = None,
    *,
    chroma_collection: str = 'memories',
    set_active: bool = False,
    template_pool: Path | None = None,
) -> PoolEntry:
    root = root or resolve_memory_root()
    registry = load_registry(root)
    if pool_id in (registry.get('pools') or {}):
        raise ValueError(f'pool 已存在: {pool_id}')
    pool_path = (path or (root / 'pools' / pool_id)).expanduser().resolve()
    if pool_path.exists() and any(pool_path.iterdir()):
        raise ValueError(f'目标目录非空: {pool_path}')
    if template_pool is None:
        try:
            template_pool = resolve_pool_path()
        except Exception:
            template_pool = None
    init_pool_directory(
        pool_path,
        pool_id,
        chroma_collection=chroma_collection,
        template_pool=template_pool,
    )
    return register_pool(pool_id, pool_path, root, set_active=set_active)


def clone_pool(
    source_id: str,
    dest_id: str,
    dest_path: Path | None = None,
    root: Path | None = None,
    *,
    set_active: bool = False,
) -> PoolEntry:
    root = root or resolve_memory_root()
    src_path = _get_pool_path(source_id, root)
    dest = (dest_path or (root / 'pools' / dest_id)).expanduser().resolve()
    if dest.exists() and any(dest.iterdir()):
        raise ValueError(f'目标目录非空: {dest}')
    shutil.copytree(src_path, dest, symlinks=True)
    meta_file = dest / 'pool.meta.json'
    meta = _read_pool_meta(dest)
    meta.update({
        'pool_id': dest_id,
        'created_at': _now_iso(),
        'migrated_from': str(src_path),
        'cloned_from': source_id,
    })
    meta_file.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return register_pool(dest_id, dest, root, note=f'clone from {source_id}', set_active=set_active)


def export_pool(pool_id: str, dest: Path, root: Path | None = None) -> Path:
    src = _get_pool_path(_resolve_pool_id(pool_id, root), root)
    dest = dest.expanduser().resolve()
    if dest.exists():
        raise ValueError(f'导出目标已存在: {dest}')
    shutil.copytree(src, dest, symlinks=True)
    return dest


def import_pool(source: Path, pool_id: str | None = None, root: Path | None = None, *, set_active: bool = False) -> PoolEntry:
    root = root or resolve_memory_root()
    src = source.expanduser().resolve()
    if not src.is_dir():
        raise ValueError(f'导入源不是目录: {src}')
    if not (src / 'pool.meta.json').is_file() and not (src / 'active_memories.db').is_file():
        raise ValueError(f'导入源缺少 pool 标记文件: {src}')
    meta = _read_pool_meta(src)
    pid = pool_id or str(meta.get('pool_id') or src.name)
    registry = load_registry(root)
    if pid in (registry.get('pools') or {}):
        raise ValueError(f'pool 已存在: {pid}')
    dest = (root / 'pools' / pid).resolve()
    if dest.exists() and any(dest.iterdir()):
        raise ValueError(f'目标目录非空: {dest}')
    shutil.copytree(src, dest, symlinks=True)
    if not (dest / 'pool.meta.json').is_file():
        init_pool_directory(dest, pid, migrated_from=str(src))
    return register_pool(pid, dest, root, note=f'import from {src}', set_active=set_active)


def backup_pool(pool_id: str | None = None, dest: Path | None = None, root: Path | None = None) -> Path:
    root = root or resolve_memory_root()
    pid = _resolve_pool_id(pool_id, root)
    src = _get_pool_path(pid, root)
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    if dest is None:
        backup_root = root / 'backups'
        backup_root.mkdir(parents=True, exist_ok=True)
        dest = backup_root / f'{pid}-{stamp}'
    else:
        dest = dest.expanduser().resolve()
    if dest.exists():
        raise ValueError(f'备份目标已存在: {dest}')
    shutil.copytree(src, dest, symlinks=True)
    return dest


def format_pools_text(pools: list[PoolEntry] | None = None, root: Path | None = None) -> str:
    rows = pools if pools is not None else list_pools(root)
    if not rows:
        return 'registry 中无 pool'
    lines = [f'active: {get_active_pool_id(root)}', '']
    for row in rows:
        mark = '*' if row.active else ' '
        lines.append(
            f'{mark} {row.pool_id}\n'
            f'    path: {row.path}\n'
            f'    chroma: {row.chroma_collection}'
            + (f'\n    note: {row.note}' if row.note else '')
        )
    return '\n'.join(lines)


def transfer_memory(
    memory_id: str,
    source_pool: str,
    target_pool: str,
    root: Path | None = None,
    *,
    mode: str = 'move',
    reason: str = '',
    actor: str = 'mcp',
) -> dict[str, Any]:
    """跨池转移单条记忆。

    copy：写目标池（原 id/时间戳/向量/元数据），源池不动。
    move：写目标池后物理删源池 sqlite+chroma，并删源池关联痕
    （history.db 该 id 全部事件行、lineage.jsonl 该 id 全部旧条目、archive 不写行）；
    源池 lineage 追加 1 条 TRANSFER 事件作审计痕。
    """
    import sqlite3

    from memory_paths import workspace_pool_scope
    from memory_lineage import record_event

    root = root or resolve_memory_root()
    registry = load_registry(root)
    pools = registry.get('pools') or {}
    if source_pool not in pools:
        raise ValueError(f'源池不存在: {source_pool}')
    if target_pool not in pools:
        raise ValueError(f'目标池不存在: {target_pool}')
    if source_pool == target_pool:
        raise ValueError('源池与目标池相同')
    mode = (mode or 'move').strip().lower()
    if mode not in ('move', 'copy'):
        raise ValueError(f'不支持的 mode: {mode}（仅 move/copy）')
    memory_id = (memory_id or '').strip()
    if not memory_id:
        raise ValueError('memory_id 不能为空')

    def _pool_db(pool_id: str, name: str) -> Path:
        return Path(pools[pool_id]['path']) / name

    def _chroma_col(pool_id: str):
        # 走 hybrid_search 的按 path 缓存 client，避免与同进程其他 chroma 实例 settings 冲突
        from hybrid_search import get_chroma_client
        from memory_paths import get_chroma_collection
        with workspace_pool_scope(pool_id):
            return get_chroma_collection(get_chroma_client())

    # ---- 读源 ----
    with workspace_pool_scope(source_pool):
        src_conn = sqlite3.connect(str(_pool_db(source_pool, 'active_memories.db')))
        try:
            row = src_conn.execute(
                'SELECT memory_id, content, project, category, lang, created_at, updated_at '
                'FROM active_memories WHERE memory_id = ?',
                (memory_id,),
            ).fetchone()
        finally:
            src_conn.close()
        if not row:
            raise ValueError(f'源池 {source_pool} 无此记忆: {memory_id}')
        src_raw = _chroma_col(source_pool).get(ids=[memory_id], include=['metadatas', 'embeddings'])
        if not src_raw.get('ids'):
            raise ValueError(f'源池 {source_pool} chroma 无此记忆: {memory_id}')
        src_meta = dict((src_raw.get('metadatas') or [{}])[0] or {})
        embs = src_raw.get('embeddings')
        src_emb = None if embs is None or len(embs) == 0 else embs[0]
        content = row[1]

    # ---- 写目标 ----
    with workspace_pool_scope(target_pool):
        dst_conn = sqlite3.connect(str(_pool_db(target_pool, 'active_memories.db')))
        try:
            if dst_conn.execute('SELECT 1 FROM active_memories WHERE memory_id = ?', (memory_id,)).fetchone():
                raise ValueError(f'目标池 {target_pool} 已存在同 id 记忆: {memory_id}')
            dst_conn.execute(
                'INSERT INTO active_memories (memory_id, content, project, category, lang, created_at, updated_at) '
                'VALUES (?,?,?,?,?,?,?)',
                (memory_id, content, row[2] or '', row[3] or '', row[4] or 'zh', row[5], row[6]),
            )
            dst_conn.commit()
        finally:
            dst_conn.close()
        col = _chroma_col(target_pool)
        add_kwargs = {'ids': [memory_id], 'documents': [content], 'metadatas': [src_meta]}
        if src_emb is not None:
            add_kwargs['embeddings'] = [src_emb]
        col.add(**add_kwargs)

    # ---- move：物理删源 + 删关联痕 + 审计痕 ----
    if mode == 'move':
        with workspace_pool_scope(source_pool):
            conn = sqlite3.connect(str(_pool_db(source_pool, 'active_memories.db')))
            try:
                conn.execute('DELETE FROM active_memories WHERE memory_id = ?', (memory_id,))
                conn.commit()
            finally:
                conn.close()
            _chroma_col(source_pool).delete(ids=[memory_id])

            hist_path = _pool_db(source_pool, 'history.db')
            hist = sqlite3.connect(str(hist_path))
            try:
                deleted_events = hist.execute(
                    'DELETE FROM memory_events WHERE memory_id = ?', (memory_id,)
                ).rowcount
                hist.commit()
            finally:
                hist.close()

            lineage_path = _pool_db(source_pool, 'lineage.jsonl')
            removed_lineage = 0
            if lineage_path.is_file():
                kept = []
                for raw in lineage_path.read_text(encoding='utf-8').splitlines():
                    try:
                        entry = json.loads(raw)
                    except Exception:
                        kept.append(raw)
                        continue
                    if entry.get('memory_id') == memory_id:
                        removed_lineage += 1
                    else:
                        kept.append(raw)
                if removed_lineage:
                    lineage_path.write_text('\n'.join(kept) + '\n', encoding='utf-8')

            record_event(
                'TRANSFER',
                memory_id,
                note=f'transfer {source_pool} -> {target_pool} (mode={mode})'
                     + (f'; reason: {reason}' if reason else '')
                     + f'; purged: history={deleted_events}, lineage={removed_lineage}',
                actor=actor,
            )

    return {
        'memory_id': memory_id,
        'source_pool': source_pool,
        'target_pool': target_pool,
        'mode': mode,
        'content_length': len(content or ''),
        'had_embedding': src_emb is not None,
    }
