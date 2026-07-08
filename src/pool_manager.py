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
