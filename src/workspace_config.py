"""仓库级 workspace 配置：读写 project 权限、pool 绑定、detect aliases。"""

from __future__ import annotations

import json
import logging
import os
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from memory_paths import load_registry, resolve_memory_root, workspace_pool_scope

logger = logging.getLogger(__name__)

_CONFIG_CANDIDATES = (
    ('.cursor', 'memory.json'),
    ('.memory', 'workspace.json'),
)


@dataclass
class WorkspaceConfig:
    pool: str | None = None
    read_projects: list[str] | None = None
    write_projects: list[str] | None = None
    aliases: dict[str, str] = field(default_factory=dict)
    source_path: str | None = None

    @property
    def is_configured(self) -> bool:
        return self.source_path is not None


def merge_aliases(pool_aliases: dict[str, str], workspace_aliases: dict[str, str] | None) -> dict[str, str]:
    merged = dict(pool_aliases or {})
    if workspace_aliases:
        merged.update({str(k): str(v) for k, v in workspace_aliases.items()})
    return merged


def resolve_pool_id(config: WorkspaceConfig) -> str | None:
    """MEMORY_POOL env → config.pool → None（走 registry active）。"""
    env_pool = os.environ.get('MEMORY_POOL')
    if env_pool:
        return env_pool
    if config.pool:
        return config.pool
    return None


def _parse_access_field(raw: Any) -> list[str] | None:
    if raw is None:
        return None
    if not isinstance(raw, list):
        return None
    return [str(item) if item is not None else '' for item in raw]


def _parse_config_file(path: Path) -> WorkspaceConfig:
    data = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(data, dict):
        raise ValueError(f'workspace 配置必须是 JSON object: {path}')
    access = data.get('access') if isinstance(data.get('access'), dict) else {}
    detect = data.get('detect') if isinstance(data.get('detect'), dict) else {}
    aliases_raw = detect.get('aliases') if isinstance(detect.get('aliases'), dict) else {}
    aliases = {str(k): str(v) for k, v in aliases_raw.items()}
    pool = data.get('pool')
    return WorkspaceConfig(
        pool=str(pool) if pool else None,
        read_projects=_parse_access_field(access.get('read')) if 'access' in data else None,
        write_projects=_parse_access_field(access.get('write')) if 'access' in data else None,
        aliases=aliases,
        source_path=str(path),
    )


def load_workspace_config(cwd: str | None = None) -> WorkspaceConfig:
    """从 cwd 向上查找 .cursor/memory.json 或 .memory/workspace.json（§2.1）。"""
    start = Path(cwd or os.getcwd()).expanduser().resolve()
    current = start
    while True:
        for parts in _CONFIG_CANDIDATES:
            candidate = current.joinpath(*parts)
            if candidate.is_file():
                try:
                    return _parse_config_file(candidate)
                except (json.JSONDecodeError, OSError, ValueError) as exc:
                    logger.warning('workspace 配置解析失败 %s: %s', candidate, exc)
                    return WorkspaceConfig(source_path=str(candidate))
        if (current / '.git').exists():
            break
        parent = current.parent
        if parent == current:
            break
        current = parent
    return WorkspaceConfig()


def filter_by_read_access(results: list[dict[str, Any]], read_projects: list[str] | None) -> list[dict[str, Any]]:
    if read_projects is None:
        return results
    if read_projects == []:
        return []
    if '*' in read_projects:
        return results
    allowed = {str(item) for item in read_projects}
    filtered: list[dict[str, Any]] = []
    for item in results:
        project = str(item.get('project', '') or '')
        if project in allowed:
            filtered.append(item)
    return filtered


def filter_records_by_read_access(records: list[Any], read_projects: list[str] | None) -> list[Any]:
    if read_projects is None:
        return records
    if read_projects == []:
        return []
    if '*' in read_projects:
        return records
    allowed = {str(item) for item in read_projects}
    return [record for record in records if str(getattr(record, 'project', '') or '') in allowed]


def check_write_access(project: str, write_projects: list[str] | None) -> str | None:
    normalized = str(project or '')
    if write_projects is None:
        return None
    if write_projects == []:
        return f"write to project '{normalized or 'global'}' outside configured write list (write list is empty)"
    if '*' in write_projects:
        return None
    if normalized in {str(item) for item in write_projects}:
        return None
    label = normalized or 'global'
    return f"write to project '{label}' outside configured write list"


def log_workspace_warning(message: str) -> None:
    logger.warning(message)
    try:
        log_path = resolve_memory_root() / 'workspace_config.log'
        with log_path.open('a', encoding='utf-8') as handle:
            handle.write(message + '\n')
    except OSError:
        pass


@contextmanager
def workspace_runtime(cwd: str | None) -> Iterator[WorkspaceConfig]:
    """加载 workspace 配置并应用 pool 作用域（若有）。"""
    config = load_workspace_config(cwd) if cwd else WorkspaceConfig()
    pool_id = resolve_pool_id(config) if cwd else None
    with workspace_pool_scope(pool_id):
        yield config


def active_pool_label() -> str:
    registry = load_registry()
    return str(registry.get('active_pool') or 'default')
