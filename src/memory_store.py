"""统一记忆 CRUD — P1 核心入口。"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from add_policy import prepare_add_plan, run_merge_check
from hybrid_search import detect_project, hybrid_search, normalize_project
from llm_client import embed_text, get_llm_client
from memory_lineage import parse_merged_from, record_merge_result
from memory_paths import DEFAULT_USER, pending_dir
from memory_sync import (
    ensure_active_schema,
    ensure_history_schema,
    record_memory_event,
    sync_active_insert,
)

logger = logging.getLogger(__name__)


@dataclass
class AddResult:
    memory_id: str
    content: str
    project: str
    event: str
    storage_mode: str
    merge_note: str | None = None
    dropped: bool = False


@dataclass
class MemoryRecord:
    memory_id: str
    content: str
    project: str
    category: str
    lang: str
    created_at: str
    updated_at: str


@dataclass
class DeleteResult:
    memory_id: str
    ok: bool
    detail: str = ''


def ensure_pool_schema(*, pool: str | None = None) -> None:
    del pool
    ensure_active_schema()
    ensure_history_schema()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sanitize_chroma_metadata(meta: dict[str, Any]) -> dict[str, Any]:
    clean: dict[str, Any] = {}
    for key, value in meta.items():
        if value is None:
            continue
        if isinstance(value, (str, int, float, bool)):
            clean[key] = value
        else:
            clean[key] = str(value)
    return clean


def _chroma_upsert(memory_id: str, content: str, metadata: dict[str, Any]) -> None:
    from hybrid_search import get_chroma_client, get_chroma_collection

    now = _utc_now_iso()
    chroma_meta = _sanitize_chroma_metadata({
        **metadata,
        'data': content,
        'user_id': DEFAULT_USER,
        'created_at': now,
        'updated_at': now,
        'hash': hashlib.md5(content.encode('utf-8')).hexdigest(),
        'role': 'user',
    })
    embedding = embed_text(content)
    col = get_chroma_collection(get_chroma_client())
    col.upsert(ids=[memory_id], embeddings=[embedding], metadatas=[chroma_meta])


def _write_pending(content: str, metadata: dict, project: str) -> str:
    pdir = str(pending_dir())
    os.makedirs(pdir, exist_ok=True)
    slug = content[:20].replace(' ', '_').replace('/', '_')
    filename = f'{slug}_{int(time.time())}.json'
    filepath = os.path.join(pdir, filename)
    payload = {
        'content': content,
        'metadata': metadata,
        'project': project,
        'retry_count': 0,
        'created_at': time.strftime('%Y-%m-%dT%H:%M:%S'),
    }
    with open(filepath, 'w', encoding='utf-8') as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    return filepath


def add(
    content: str,
    *,
    metadata: dict | None = None,
    project: str = '',
    pool: str | None = None,
    actor: str = 'mcp',
) -> AddResult:
    del pool
    ensure_pool_schema()

    metadata_raw = json.dumps(metadata or {}, ensure_ascii=False)
    effective_project = project or detect_project()
    plan = prepare_add_plan(content, metadata_raw, effective_project)
    memory_id = str(uuid.uuid4())

    try:
        _chroma_upsert(memory_id, plan.content, plan.metadata)
    except Exception:
        logger.exception('add 失败（Chroma/embedding）')
        raise

    proj = str(plan.metadata.get('project', '') or effective_project or '')
    cat = str(plan.metadata.get('category', '') or '')
    lang = str(plan.metadata.get('lang', '') or 'zh')

    sync_result = sync_active_insert(memory_id, plan.content, project=proj, category=cat, lang=lang)
    if not sync_result.ok:
        logger.error('active 同步失败 %s: %s', memory_id, sync_result.detail)
        try:
            from hybrid_search import get_chroma_client, get_chroma_collection

            get_chroma_collection(get_chroma_client()).delete(ids=[memory_id])
        except Exception:
            logger.exception('回滚 Chroma 失败 %s', memory_id)
        raise RuntimeError(f'active 同步失败: {sync_result.detail}')

    record_memory_event(
        'ADD',
        memory_id,
        new_content=plan.content,
        project=proj,
        category=cat,
        actor=actor,
    )

    merge_note: str | None = None
    dropped = False
    if plan.run_merge_check:
        llm = get_llm_client()

        def _delete_cb(mid: str, reason: str) -> None:
            delete(mid, reason, actor='system', source='merge_dedup')

        merge_note = run_merge_check(
            llm,
            memory_id,
            plan.content,
            normalize_project(proj),
            delete_memory=_delete_cb,
            hybrid_search_fn=hybrid_search,
        )
        if merge_note and '已删除' in merge_note:
            dropped = True
            return AddResult(
                memory_id=memory_id,
                content=plan.content,
                project=proj,
                event='DROP_NEW',
                storage_mode=plan.storage_mode,
                merge_note=merge_note,
                dropped=True,
            )

    merged_sources = parse_merged_from(plan.metadata)
    if merged_sources:
        record_merge_result(
            memory_id,
            merged_sources,
            category=cat,
            content_preview=plan.content,
            actor=actor,
        )

    return AddResult(
        memory_id=memory_id,
        content=plan.content,
        project=proj,
        event='ADD',
        storage_mode=plan.storage_mode,
        merge_note=merge_note,
    )


def get_all_memories(
    *,
    project: str | None = None,
    pool: str | None = None,
) -> list[MemoryRecord]:
    del pool
    from memory_sync import get_active_record, load_active_memories, migrate_active_if_needed

    migrate_active_if_needed()
    text_map = load_active_memories()
    records: list[MemoryRecord] = []
    for memory_id, text in text_map.items():
        row = get_active_record(memory_id) or {}
        proj = str(row.get('project', '') or '')
        if project is not None and normalize_project(project) != normalize_project(proj):
            continue
        records.append(
            MemoryRecord(
                memory_id=memory_id,
                content=text,
                project=proj,
                category=str(row.get('category', '') or ''),
                lang=str(row.get('lang', '') or 'zh'),
                created_at=str(row.get('created_at', '') or ''),
                updated_at=str(row.get('updated_at', '') or ''),
            )
        )
    return records


def get_by_id(memory_id: str, *, pool: str | None = None) -> MemoryRecord | None:
    del pool
    from memory_sync import get_active_record

    row = get_active_record(memory_id)
    if not row:
        return None
    return MemoryRecord(
        memory_id=memory_id,
        content=str(row.get('content', '') or ''),
        project=str(row.get('project', '') or ''),
        category=str(row.get('category', '') or ''),
        lang=str(row.get('lang', '') or 'zh'),
        created_at=str(row.get('created_at', '') or ''),
        updated_at=str(row.get('updated_at', '') or ''),
    )


def delete(
    memory_id: str,
    reason: str,
    *,
    actor: str = 'mcp',
    source: str = '',
    pool: str | None = None,
) -> DeleteResult:
    del pool
    ensure_pool_schema()
    from memory_delete import archive_delete
    from memory_sync import SyncError

    try:
        archive_delete(memory_id, reason, actor=actor, source=source or actor)
        return DeleteResult(memory_id=memory_id, ok=True, detail='deleted')
    except SyncError as error:
        return DeleteResult(memory_id=memory_id, ok=False, detail=str(error))
    except ValueError as error:
        if 'not found' in str(error).lower() or '不存在' in str(error):
            return DeleteResult(memory_id=memory_id, ok=True, detail='already_gone')
        raise


def retry_pending_entry(payload: dict[str, Any]) -> AddResult:
    """重试单条 pending（verbatim，忽略 use_infer）。"""
    payload.pop('use_infer', None)
    meta = payload.get('metadata') or {}
    project = payload.get('project', '') or str(meta.get('project', '') or '')
    return add(
        payload.get('content', ''),
        metadata=meta,
        project=project,
        actor='retry_pending',
    )
