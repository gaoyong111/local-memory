"""episodic 梳理批处理 — MCP 与 CLI 共用（复用同一 Chroma 客户端，避免多进程冲突）。"""

from __future__ import annotations

import json
import logging
from typing import Any

from add_policy import DEFAULT_CATEGORY, normalize_category
from grooming_episodic import (
    analyze_memory_grooming,
    apply_grooming_to_chroma_metadata,
    build_merge_hints,
)
from grooming_metadata import apply_grooming_pending, write_merge_hints
from hybrid_search import get_chroma_client, get_chroma_collection, hybrid_search, normalize_project

logger = logging.getLogger(__name__)


def _load_grooming_from_chroma() -> dict[str, dict]:
    from grooming_metadata import parse_grooming_fields

    try:
        client = get_chroma_client()
        col = get_chroma_collection(client)
        raw = col.get(include=['metadatas'])
    except Exception as exc:
        logger.warning('Chroma grooming metadata 读取失败: %s', exc)
        return {}

    result: dict[str, dict] = {}
    for memory_id, meta in zip(raw.get('ids') or [], raw.get('metadatas') or []):
        if memory_id:
            result[memory_id] = parse_grooming_fields(meta or {})
    return result


def _load_memories() -> list[dict]:
    from memory_sync import load_active_memories, load_active_metadata

    text_map = load_active_memories()
    meta_map = load_active_metadata()
    grooming_map = _load_grooming_from_chroma()
    rows: list[dict] = []
    for memory_id, text in text_map.items():
        meta = meta_map.get(memory_id, {})
        grooming = grooming_map.get(memory_id, {})
        category = normalize_category(meta.get('category', '') or DEFAULT_CATEGORY)
        rows.append({
            'id': memory_id,
            'text': text,
            'project': normalize_project(meta.get('project', '') or ''),
            'category': category,
            'metadata': meta,
            'grooming': grooming,
        })
    return rows


def _load_llm() -> Any | None:
    try:
        from llm_client import get_llm_client

        return get_llm_client()
    except Exception as error:
        logger.warning('LLM 不可用，使用规则兜底: %s', error)
        return None


def _update_chroma_metadata(memory_id: str, patches: dict) -> None:
    from add_policy import apply_category_metadata

    client = get_chroma_client()
    col = get_chroma_collection(client)
    raw = col.get(ids=[memory_id], include=['metadatas'])
    if not raw.get('ids'):
        raise ValueError(f'Chroma 不存在: {memory_id}')

    meta = dict((raw.get('metadatas') or [{}])[0] or {})
    for key, value in patches.items():
        if value is None:
            meta.pop(key, None)
        else:
            meta[key] = value

    apply_category_metadata(meta)
    clean: dict[str, Any] = {}
    for key, value in meta.items():
        if value is None:
            continue
        if isinstance(value, (str, int, float, bool)):
            clean[key] = value
        else:
            clean[key] = str(value)
    col.update(ids=[memory_id], metadatas=[clean])


def _select_targets(memories: list[dict], *, all_episodic: bool) -> list[dict]:
    selected: list[dict] = []
    for memory in memories:
        if memory.get('category') != DEFAULT_CATEGORY:
            continue
        grooming = memory.get('grooming') or {}
        if all_episodic:
            selected.append(memory)
            continue
        if grooming.get('pending') or not grooming.get('at'):
            selected.append(memory)
    return selected


def format_grooming_summary(summary: dict) -> str:
    return json.dumps(summary, ensure_ascii=False, indent=2)


def run_episodic_grooming(*, all_episodic: bool = False, dry_run: bool = False) -> dict:
    """分析 episodic 记忆并写入 grooming 建议（merge hints + Chroma metadata）。"""
    memories = _load_memories()
    targets = _select_targets(memories, all_episodic=all_episodic)
    llm = None if dry_run else _load_llm()

    merge_source_memories = targets if all_episodic else [
        memory for memory in memories if memory.get('category') == DEFAULT_CATEGORY
    ]
    merge_hints = build_merge_hints(merge_source_memories, hybrid_search_fn=hybrid_search)

    results: list[dict] = []
    for memory in targets:
        decision, merge_candidates = analyze_memory_grooming(
            memory,
            llm=llm,
            hybrid_search_fn=hybrid_search,
        )
        meta_patch = apply_grooming_to_chroma_metadata({}, decision, set_pending=True)
        apply_grooming_pending(meta_patch, pending=True)

        row = {
            'id': memory['id'],
            'action': decision.action,
            'reason': decision.reason,
            'target_category': decision.target_category,
            'merge_candidates': len(merge_candidates),
        }
        results.append(row)

        if dry_run:
            continue

        _update_chroma_metadata(memory['id'], meta_patch)

    if not dry_run:
        write_merge_hints(merge_hints)

    return {
        'analyzed': len(results),
        'merge_hints': len(merge_hints),
        'dry_run': dry_run,
        'results': results,
    }
