"""local-memory 运行时路径 — MEMORY_DIR / pool registry。"""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Iterator

DEFAULT_USER = os.getenv('MEMORY_USER_ID', 'default-user')

_workspace_pool: ContextVar[str | None] = ContextVar('workspace_pool', default=None)


@contextmanager
def workspace_pool_scope(pool_id: str | None) -> Iterator[None]:
    if not pool_id:
        yield
        return
    token = _workspace_pool.set(pool_id)
    try:
        yield
    finally:
        _workspace_pool.reset(token)


def _expand(path: str | Path) -> Path:
    return Path(path).expanduser().resolve()


def resolve_memory_root() -> Path:
    """数据根：MEMORY_DIR → ~/.memory。"""
    if os.environ.get('MEMORY_DIR'):
        return _expand(os.environ['MEMORY_DIR'])
    return _expand('~/.memory')


def load_registry(root: Path | None = None) -> dict[str, Any]:
    root = root or resolve_memory_root()
    registry_path = root / 'registry.json'
    if not registry_path.is_file():
        return {'active_pool': 'default', 'pools': {}}
    with registry_path.open(encoding='utf-8') as handle:
        return json.load(handle)


def resolve_pool_path(pool_id: str | None = None) -> Path:
    """pool_id=None → ContextVar → env MEMORY_POOL → registry.active_pool → 'default'。"""
    root = resolve_memory_root()
    registry_path = root / 'registry.json'

    if pool_id is None:
        pool_id = _workspace_pool.get()

    if pool_id is None:
        if registry_path.exists():
            registry = load_registry(root)
            pool_id = os.environ.get('MEMORY_POOL') or registry.get('active_pool', 'default')
            entry = (registry.get('pools') or {}).get(pool_id) or {}
            entry_path = entry.get('path')
            if entry_path:
                return _expand(entry_path)
        else:
            pool_id = os.environ.get('MEMORY_POOL') or 'default'

    if pool_id is not None and registry_path.exists():
        registry = load_registry(root)
        entry = (registry.get('pools') or {}).get(pool_id) or {}
        entry_path = entry.get('path')
        if entry_path:
            return _expand(entry_path)

    pools_path = root / 'pools' / (pool_id or 'default')
    return pools_path.resolve()


def resolve_runtime_dir() -> Path:
    return _expand(os.environ.get('MEMORY_RUNTIME', '~/.memory/runtime'))


def resolve_pool_file(
    rel: str,
    env_key: str | None = None,
    pool_path: Path | None = None,
) -> Path:
    if env_key and os.environ.get(env_key):
        return _expand(os.environ[env_key])
    if pool_path is None:
        pool_path = resolve_pool_path()
    return (pool_path / rel).resolve()


def get_chroma_collection_name(pool_path: Path | None = None) -> str:
    if os.environ.get('MEMORY_CHROMA_COLLECTION'):
        return os.environ['MEMORY_CHROMA_COLLECTION']
    if pool_path is None:
        pool_path = resolve_pool_path()
    meta_file = pool_path / 'pool.meta.json'
    if meta_file.is_file():
        meta = json.loads(meta_file.read_text(encoding='utf-8'))
        if meta.get('chroma_collection'):
            return meta['chroma_collection']
    return 'memories'


def resolve_config_path(pool_path: Path | None = None) -> Path:
    if os.environ.get('MEMORY_CONFIG'):
        return _expand(os.environ['MEMORY_CONFIG'])
    if pool_path is None:
        pool_path = resolve_pool_path()
    for name in ('config.json', 'config_local.json'):
        candidate = pool_path / name
        if candidate.is_file():
            return candidate.resolve()
    return (pool_path / 'config.json').resolve()


def get_chroma_collection(client: Any, pool_path: Path | None = None) -> Any:
    return client.get_collection(get_chroma_collection_name(pool_path))


# 池内路径常量（模块级 lazy 解析）
def chroma_db_path() -> Path:
    return resolve_pool_file('chroma_db', 'MEMORY_CHROMA_PATH', None)


def history_db_path() -> Path:
    return resolve_pool_file('history.db', 'MEMORY_HISTORY_DB', None)


def active_db_path() -> Path:
    return resolve_pool_file('active_memories.db', 'MEMORY_ACTIVE_DB', None)


def deleted_db_path() -> Path:
    return resolve_pool_file('deleted_archive.db', 'MEMORY_DELETED_DB', None)


def lineage_path() -> Path:
    return resolve_pool_file('lineage.jsonl')


def pending_dir() -> Path:
    return resolve_pool_file('pending')


def sync_pending_dir() -> Path:
    return resolve_pool_file('sync_pending')


def merge_hints_path() -> Path:
    return resolve_pool_file('grooming-merge-hints.json')


def project_aliases_path() -> Path:
    env = os.environ.get('MEMORY_PROJECT_ALIASES')
    if env:
        return _expand(env)
    return resolve_pool_file('project_aliases.json')


# 延迟绑定字符串路径（兼容 port 代码中的 ACTIVE_DB 等常量）
class _LazyPath:
    def __init__(self, getter):
        self._getter = getter

    def __str__(self) -> str:
        return str(self._getter())

    def __fspath__(self) -> str:
        return str(self._getter())


ACTIVE_DB = _LazyPath(active_db_path)
HISTORY_DB = _LazyPath(history_db_path)
DELETED_DB = _LazyPath(deleted_db_path)
CHROMA_DB_PATH = _LazyPath(chroma_db_path)
LINEAGE_PATH = _LazyPath(lineage_path)
PENDING_DIR = _LazyPath(pending_dir)
SYNC_PENDING_DIR = _LazyPath(sync_pending_dir)
MERGE_HINTS_PATH = _LazyPath(merge_hints_path)
CONFIG_PATH = _LazyPath(resolve_config_path)
PROJECT_ALIASES_PATH = _LazyPath(project_aliases_path)
MEMORY_DIR = _LazyPath(resolve_pool_path)
