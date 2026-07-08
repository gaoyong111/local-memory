"""写入策略 B/D/E — infer 已移除，原样入库。"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Protocol

logger = logging.getLogger(__name__)

MERGE_ADVISOR_SYSTEM = """你是记忆去重顾问。比较「新记忆」与「候选旧记忆」，判断是否语义重复。

判断标准（严格遵守）：
- DROP_NEW：新记忆和某条旧记忆描述的是**同一组事实/同一事件/同一决策**，旧记忆已完整覆盖新记忆的信息，新记忆没有任何增量信息。仅当两者核心内容高度重叠时才选 DROP_NEW。
- KEEP：以下情况一律选 KEEP：
  1. 新记忆和旧记忆虽然涉及同一主题/项目，但描述的是**不同的事实或不同的事件**
  2. 新记忆比旧记忆**更详细**或包含增量信息
  3. 两者有任何实质性的信息差异
  4. 你不确定是否真正重复

宁可多保留一条记忆，也不要误删有增量信息的记忆。

你只能输出 JSON，不得改写或生成新的记忆正文。"""

STRUCTURED_META_KEYS = ('module', 'field', 'rule')

VALID_CATEGORIES = frozenset({
    'episodic',
    'behavior',
    'workflow',
    'reference',
    'preference',
})

CATEGORY_LABELS: dict[str, str] = {
    'episodic': '踩坑/事件',
    'behavior': '行为规则',
    'workflow': '流程方法',
    'reference': '事实知识',
    'preference': '用户偏好',
}

CATEGORY_COLORS: dict[str, str] = {
    'episodic': '#9b59b6',
    'behavior': '#e74c3c',
    'workflow': '#2ecc71',
    'reference': '#3498db',
    'preference': '#f39c12',
}

DEFAULT_CATEGORY = 'episodic'

LEGACY_CATEGORY_MAP: dict[str, str] = {
    'tech-stack': 'reference',
    'api': 'reference',
    'module': 'reference',
    'architecture': 'reference',
    'dictionary': 'reference',
    'permission': 'reference',
    'state': 'reference',
}


class LlmClient(Protocol):
    def generate_response(
        self,
        messages: list[dict[str, str]],
        response_format: dict[str, str] | None = None,
    ) -> str: ...


@dataclass
class AddPlan:
    content: str
    metadata: dict[str, Any]
    run_merge_check: bool
    storage_mode: str


@dataclass
class MergeDecision:
    action: str
    target_id: str
    reason: str


def _parse_metadata(raw: str) -> dict[str, Any]:
    if not raw or not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}


def _normalize_keywords(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [part.strip() for part in re.split(r'[,，\s]+', value) if part.strip()]
    return []


def _extract_structured(meta: dict[str, Any]) -> dict[str, Any] | None:
    structured = meta.get('structured')
    if isinstance(structured, dict) and structured.get('module'):
        return structured
    if all(meta.get(key) for key in STRUCTURED_META_KEYS):
        return {key: str(meta[key]).strip() for key in STRUCTURED_META_KEYS}
    return None


def format_structured_memory(content: str, structured: dict[str, Any]) -> str:
    module = str(structured.get('module', '')).strip()
    field = str(structured.get('field', '')).strip()
    rule = str(structured.get('rule', '')).strip()
    keywords = _normalize_keywords(structured.get('keywords'))
    if not keywords:
        keywords = [module, field]
    keywords = [keyword for keyword in keywords if keyword]

    parts: list[str] = []
    if module and field:
        parts.append(f'[{module}] {field}')
    elif module:
        parts.append(f'[{module}]')

    body = rule or content.strip()
    if body:
        parts.append(f': {body}' if parts else body)

    if keywords:
        parts.append(f'（关键词: {", ".join(keywords)}）')

    formatted = ''.join(parts).strip()
    return formatted or content.strip()


def normalize_category(raw: Any) -> str:
    key = str(raw or '').strip().lower()
    if not key:
        return DEFAULT_CATEGORY
    if key in VALID_CATEGORIES:
        return key
    mapped = LEGACY_CATEGORY_MAP.get(key)
    if mapped:
        return mapped
    logger.warning('unknown category %r, fallback reference', key)
    return 'reference'


def apply_lineage_metadata(meta: dict[str, Any]) -> dict[str, Any]:
    raw = meta.get('merged_from')
    if isinstance(raw, list):
        ids = [str(item).strip() for item in raw if str(item).strip()]
        if ids:
            meta['merged_from'] = ','.join(ids)
    elif raw is not None and str(raw).strip():
        meta['merged_from'] = str(raw).strip()
    return meta


def apply_category_metadata(meta: dict[str, Any]) -> dict[str, Any]:
    raw = meta.get('category', '')
    normalized = normalize_category(raw)
    if raw and str(raw).strip().lower() != normalized:
        meta['category_raw'] = str(raw).strip()
    meta['category'] = normalized
    return meta


def apply_lang_metadata(meta: dict[str, Any], content: str) -> dict[str, Any]:
    from hybrid_search import infer_memory_lang

    meta['lang'] = infer_memory_lang(content)
    return meta


def prepare_add_plan(
    content: str,
    metadata_raw: str = '',
    project: str = '',
) -> AddPlan:
    meta = _parse_metadata(metadata_raw)
    if project:
        meta['project'] = project

    structured = _extract_structured(meta)
    if structured:
        canonical = format_structured_memory(content, structured)
        meta.pop('structured', None)
        for key in STRUCTURED_META_KEYS:
            meta.pop(key, None)
        meta['structured_json'] = json.dumps(structured, ensure_ascii=False)
        meta['module'] = str(structured.get('module', ''))
        meta['field'] = str(structured.get('field', ''))
        meta['storage_mode'] = 'structured'
        meta.setdefault('category', 'reference')
        keywords = _normalize_keywords(structured.get('keywords'))
        if keywords:
            meta['keywords'] = ','.join(keywords)
        apply_category_metadata(meta)
        apply_lineage_metadata(meta)
        apply_lang_metadata(meta, canonical)
        return AddPlan(
            content=canonical,
            metadata=meta,
            run_merge_check=True,
            storage_mode='structured',
        )

    meta['storage_mode'] = meta.get('storage_mode') or 'verbatim'
    apply_category_metadata(meta)
    apply_lineage_metadata(meta)
    apply_lang_metadata(meta, content.strip())
    from grooming_metadata import apply_grooming_pending, is_episodic_category

    if is_episodic_category(meta):
        apply_grooming_pending(meta, pending=True)
    return AddPlan(
        content=content.strip(),
        metadata=meta,
        run_merge_check=True,
        storage_mode='verbatim',
    )


def _parse_merge_response(raw: str) -> MergeDecision | None:
    text = raw.strip()
    if not text:
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r'\{[^{}]*\}', text, re.DOTALL)
        if not match:
            return None
        try:
            payload = json.loads(match.group())
        except json.JSONDecodeError:
            return None

    action = str(payload.get('action', 'KEEP')).upper()
    if action not in ('KEEP', 'DROP_NEW'):
        action = 'KEEP'
    return MergeDecision(
        action=action,
        target_id=str(payload.get('target_id', '') or ''),
        reason=str(payload.get('reason', '') or ''),
    )


def advise_merge(
    llm: LlmClient,
    new_memory_id: str,
    new_text: str,
    candidates: list[dict[str, Any]],
) -> MergeDecision:
    if not candidates:
        return MergeDecision(action='KEEP', target_id='', reason='无相似候选')

    candidate_lines = []
    for item in candidates:
        candidate_lines.append(
            f"- id={item.get('id', '')} score={item.get('score', 0):.2f} text={item.get('text', '')}"
        )

    user_prompt = f"""新记忆 ID: {new_memory_id}
新记忆正文:
{new_text}

候选旧记忆:
{chr(10).join(candidate_lines)}

请输出 JSON:
{{"action":"KEEP"|"DROP_NEW","target_id":"<旧记忆ID或空>","reason":"<简短中文>"}}
"""
    try:
        response = llm.generate_response(
            messages=[
                {'role': 'system', 'content': MERGE_ADVISOR_SYSTEM},
                {'role': 'user', 'content': user_prompt},
            ],
            response_format={'type': 'json_object'},
        )
        decision = _parse_merge_response(response)
        if decision:
            return decision
    except Exception as error:
        logger.warning('merge advisor failed: %s', error)

    return MergeDecision(action='KEEP', target_id='', reason='合并顾问不可用，保留新记忆')


def _token_overlap_ratio(new_text: str, old_text: str) -> float:
    new_tokens = set(re.findall(r'\w+', new_text.lower()))
    old_tokens = set(re.findall(r'\w+', old_text.lower()))
    if not new_tokens or not old_tokens:
        return 0.0
    intersection = new_tokens & old_tokens
    union = new_tokens | old_tokens
    return len(intersection) / len(union)


def run_merge_check(
    llm: LlmClient,
    memory_id: str,
    text: str,
    project: str,
    *,
    delete_memory: Any,
    hybrid_search_fn: Any,
    min_keyword_score: float = 15.0,
    min_overlap: float = 0.5,
) -> str | None:
    results = hybrid_search_fn(text, project=project, max_results=6)
    candidates = [
        item for item in results
        if item.get('id') != memory_id and (item.get('score') or 0) >= min_keyword_score
    ]
    candidates = [
        item for item in candidates
        if _token_overlap_ratio(text, item.get('text', '')) >= min_overlap
    ]
    if not candidates:
        return None

    decision = advise_merge(llm, memory_id, text, candidates)
    if decision.action != 'DROP_NEW':
        return None

    target = decision.target_id or candidates[0].get('id', '')
    drop_reason = f'去重：与 {target} 重复。{decision.reason or ""}'.strip()
    try:
        delete_memory(memory_id, drop_reason)
    except Exception as error:
        logger.warning('drop duplicate failed %s: %s', memory_id, error)
        return None

    try:
        from memory_lineage import record_dedup_drop

        record_dedup_drop(
            memory_id,
            target,
            note=decision.reason,
            content_preview=text,
        )
    except Exception as error:
        logger.warning('lineage record failed: %s', error)

    return f'去重：新记忆已删除（与 {target} 重复）。{decision.reason}'
