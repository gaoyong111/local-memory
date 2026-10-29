"""local-memory 记忆可视化 Web UI — Flask + vis.js Network 图谱驱动"""

import hashlib
import json
import os
import sqlite3
import sys
import time
import urllib.request
import uuid
from collections import defaultdict
from datetime import datetime, timezone

# 运行时代码在 ~/.memory/runtime/；数据路径走 MEMORY_DIR，勿把 pool 目录插入 sys.path
_RUNTIME_DIR = os.path.dirname(os.path.abspath(__file__))
if _RUNTIME_DIR not in sys.path:
    sys.path.insert(0, _RUNTIME_DIR)

from memory_paths import ACTIVE_DB, HISTORY_DB
from hybrid_search import (
    detect_project,
    extract_keywords,
    get_chroma_client,
    get_chroma_collection,
    hybrid_search,
    infer_memory_lang,
    normalize_project,
)
from add_policy import (  # noqa: E402
    CATEGORY_COLORS,
    CATEGORY_LABELS,
    DEFAULT_CATEGORY,
    VALID_CATEGORIES,
    apply_category_metadata,
    apply_lang_metadata,
    normalize_category,
    prepare_add_plan,
)
from grooming_episodic import revalidate_merge_target  # noqa: E402
from grooming_metadata import (  # noqa: E402
    clear_grooming_pending,
    clear_grooming_suggestion,
    is_grooming_pending,
    merge_hint_for_source,
    parse_grooming_fields,
    read_merge_hints,
    remove_merge_hint,
)
from memory_lineage import build_timeline, parse_merged_from, record_event, record_merge_result  # noqa: E402

# 配置
HOST = 'localhost'
PORT = 8765
DEFAULT_USER = os.getenv('MEMORY_USER_ID', 'default-user')
VIEWER_WORKSPACE_ROOT = os.getenv('WORKSPACE_ROOT') or None
# 与 mcp_server.py DEFAULT_MAX_RESULTS 保持一致，便于对比检索效果
MCP_SEARCH_MAX_RESULTS = 8
_chroma_collection = None
_viewer_workspace_config = None


def _bootstrap_viewer_workspace():
    """可选加载 workspace（仅 write 软警告 + detect aliases）；不绑 pool、不应用 read 过滤。"""
    global _viewer_workspace_config
    from workspace_config import WorkspaceConfig, load_workspace_config

    if not VIEWER_WORKSPACE_ROOT:
        _viewer_workspace_config = WorkspaceConfig()
        return _viewer_workspace_config

    _viewer_workspace_config = load_workspace_config(VIEWER_WORKSPACE_ROOT)
    return _viewer_workspace_config


def _viewer_workspace():
    if _viewer_workspace_config is None:
        return _bootstrap_viewer_workspace()
    return _viewer_workspace_config

# 项目颜色映射（固定 8 色，超出后循环）
_PROJECT_COLORS = [
    '#e74c3c', '#3498db', '#2ecc71', '#f39c12',
    '#9b59b6', '#1abc9c', '#e67e22', '#34495e',
]
_GLOBAL_COLOR = '#95a5a6'


def _load_chroma_grooming_fields() -> dict[str, dict]:
    """从 Chroma 读取 grooming_* metadata（active_memories 不含这些字段）。"""
    try:
        col = _get_chroma_collection()
        result = col.get(include=['metadatas'])
    except Exception:
        return {}

    grooming_map: dict[str, dict] = {}
    ids = result.get('ids') or []
    metas = result.get('metadatas') or []
    for memory_id, meta in zip(ids, metas):
        if not meta:
            continue
        grooming_map[memory_id] = parse_grooming_fields(meta)
    return grooming_map


def load_all_memories() -> list[dict]:
    """从 active_memories + Chroma 加载全部活跃记忆。"""
    from memory_sync import load_active_memories, load_active_metadata, migrate_active_if_needed

    migrate_active_if_needed()
    text_map = load_active_memories()
    metadata_map = load_active_metadata()
    grooming_map = _load_chroma_grooming_fields()
    merge_hints_payload = read_merge_hints()

    created_at_map: dict[str, str] = {}
    conn = sqlite3.connect(str(ACTIVE_DB))
    try:
        for memory_id, created_at, updated_at in conn.execute(
            'SELECT memory_id, created_at, updated_at FROM active_memories'
        ):
            created_at_map[memory_id] = created_at or updated_at or ''
    finally:
        conn.close()

    update_count_map: dict[str, int] = {}
    conn = sqlite3.connect(str(HISTORY_DB))
    try:
        if conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='memory_events'"
        ).fetchone():
            update_rows = conn.execute(
                "SELECT memory_id, count(*) FROM memory_events WHERE event = 'UPDATE' GROUP BY memory_id"
            ).fetchall()
        elif conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='history'"
        ).fetchone():
            update_rows = conn.execute(
                "SELECT memory_id, count(*) FROM history WHERE event = 'UPDATE' AND is_deleted = 0 GROUP BY memory_id"
            ).fetchall()
        else:
            update_rows = []
        update_count_map = {row[0]: row[1] for row in update_rows if row[0]}
    finally:
        conn.close()

    memories = []
    for memory_id, text in text_map.items():
        meta = metadata_map.get(memory_id, {})
        # 归一化 project="全局" → ""，统一灰色显示
        raw_project = meta.get('project', '')
        project = '' if raw_project == '全局' else raw_project
        raw_category = str(meta.get('category', '') or '')
        normalized_category = normalize_category(raw_category)
        grooming = grooming_map.get(memory_id, parse_grooming_fields({}))
        merge_hint = merge_hint_for_source(memory_id, merge_hints_payload)
        memories.append({
            'id': memory_id,
            'text': text,
            'project': project,
            'category': normalized_category,
            'category_raw': raw_category,
            'category_label': CATEGORY_LABELS.get(normalized_category, normalized_category),
            'metadata': meta,
            'created_at': created_at_map.get(memory_id, ''),
            'update_count': update_count_map.get(memory_id, 0),
            'grooming': grooming,
            'merge_hint': merge_hint,
        })

    return memories


def _load_chroma_metadata() -> dict[str, dict]:
    """从 ChromaDB 加载 memory_id -> metadata 映射。"""
    try:
        col = _get_chroma_collection()
        result = col.get(include=['metadatas'])
    except Exception:
        return {}

    metadata_map: dict[str, dict] = {}
    ids = result.get('ids') or []
    metas = result.get('metadatas') or []
    for memory_id, meta in zip(ids, metas):
        if not meta:
            continue
        metadata_map[memory_id] = {
            'project': str(meta.get('project', '') or ''),
            'category': str(meta.get('category', '') or ''),
            'storage_mode': str(meta.get('storage_mode', '') or ''),
            'module': str(meta.get('module', '') or ''),
            'field': str(meta.get('field', '') or ''),
            'keywords': str(meta.get('keywords', '') or ''),
        }
    return metadata_map


def assign_project_colors(memories: list[dict]) -> dict[str, str]:
    """为每个 project 分配颜色。"""
    projects = sorted(set(m['project'] for m in memories if m['project']))
    color_map = {}
    for index, project in enumerate(projects):
        color_map[project] = _PROJECT_COLORS[index % len(_PROJECT_COLORS)]
    return color_map


def compute_thickness(memories: list[dict], edges: list[dict]) -> dict[str, dict]:
    """计算每条记忆的三种厚度：变更厚度(update_count)、连接厚度(edge_count)、重复厚度(repetition_count)。
    返回 {memory_id: {change, connection, repetition, shape, size, borderWidth}}。"""
    # 变更厚度（直接从 update_count 字段取）
    # 连接厚度（统计每个节点的边数）
    edge_count: dict[str, int] = defaultdict(int)
    for edge in edges:
        edge_count[edge['from']] += 1
        edge_count[edge['to']] += 1

    # 重复厚度：统计每条记忆与其他记忆的关键词重叠≥30%的数量
    word_sets = {}
    for m in memories:
        words = set(extract_keywords(m['text']))
        keywords_meta = m['metadata'].get('keywords', '')
        if keywords_meta:
            words.update(keywords_meta.lower().split(','))
        word_sets[m['id']] = words

    repetition_count: dict[str, int] = defaultdict(int)
    for i in range(len(memories)):
        for j in range(i + 1, len(memories)):
            id_a, id_b = memories[i]['id'], memories[j]['id']
            words_a, words_b = word_sets[id_a], word_sets[id_b]
            if not words_a or not words_b:
                continue
            overlap = len(words_a & words_b)
            min_len = min(len(words_a), len(words_b))
            ratio = overlap / min_len if min_len else 0
            if ratio >= 0.3:
                repetition_count[id_a] += 1
                repetition_count[id_b] += 1

    # 映射到视觉属性
    thickness_map: dict[str, dict] = {}
    for m in memories:
        update_count_val = m.get('update_count', 0)
        conn_count = edge_count.get(m['id'], 0)
        rep_count = repetition_count.get(m['id'], 0)

        # 变更厚度 → 形状：0次=dot, 1次=diamond, 2+次=star
        if update_count_val >= 2:
            shape = 'star'
        elif update_count_val == 1:
            shape = 'diamond'
        else:
            shape = 'dot'

        # 重复厚度 → 大小：基础15，每个重复+3，上限40
        size = min(15 + rep_count * 3, 40)

        # 光晕：重复≥3时启用 shadow
        shadow = rep_count >= 3

        thickness_map[m['id']] = {
            'change': update_count_val,
            'connection': conn_count,
            'repetition': rep_count,
            'shape': shape,
            'size': size,
            'shadow': shadow,
        }

    return thickness_map


from datetime import datetime, timedelta  # noqa: E402


def compute_edges(memories: list[dict]) -> list[dict]:
    """计算图谱边：关键词重叠 ≥40%（内容关联）+ 24小时内创建（时间邻近）。
    内容边用实线蓝色，时间边用虚线灰色。"""
    edges = []
    edge_index: dict[str, dict] = {}

    def add_edge(source_id: str, target_id: str, edge_type: str, weight: int):
        key = f'{source_id}-{target_id}' if source_id < target_id else f'{target_id}-{source_id}'
        if key not in edge_index:
            edge_index[key] = {'from': source_id, 'to': target_id, 'type': edge_type, 'weight': weight}
        elif edge_type == 'keyword':
            # 关键词边优先，覆盖时间边
            edge_index[key]['type'] = 'keyword'
            edge_index[key]['weight'] = weight

    # 关键词重叠
    word_sets = {}
    for m in memories:
        words = set(extract_keywords(m['text']))
        keywords_meta = m['metadata'].get('keywords', '')
        if keywords_meta:
            words.update(keywords_meta.lower().split(','))
        word_sets[m['id']] = words

    for i in range(len(memories)):
        for j in range(i + 1, len(memories)):
            id_a, id_b = memories[i]['id'], memories[j]['id']
            words_a, words_b = word_sets[id_a], word_sets[id_b]
            if not words_a or not words_b:
                continue
            overlap = len(words_a & words_b)
            min_len = min(len(words_a), len(words_b))
            ratio = overlap / min_len if min_len else 0
            if ratio >= 0.4:
                add_edge(id_a, id_b, 'keyword', overlap)

    # 时间链：按创建时间排序，每条记忆只连到紧邻的前一条（同项目内）
    # 形成时间流，像串珠子，不是毛线团
    project_sorted = defaultdict(list)
    for m in memories:
        created = m.get('created_at', '')
        if created and m['project']:
            try:
                ts = datetime.fromisoformat(created.replace('Z', '+00:00'))
                project_sorted[m['project']].append((ts, m['id']))
            except (ValueError, TypeError):
                pass

    for project, items in project_sorted.items():
        items.sort(key=lambda x: x[0])
        for idx in range(1, len(items)):
            key = f'{items[idx-1][1]}-{items[idx][1]}' if items[idx-1][1] < items[idx][1] else f'{items[idx][1]}-{items[idx-1][1]}'
            if key not in edge_index:
                edge_index[key] = {'from': items[idx-1][1], 'to': items[idx][1], 'type': 'time', 'weight': 1}

    for edge_data in edge_index.values():
        is_keyword = edge_data['type'] == 'keyword'
        edges.append({
            'from': edge_data['from'],
            'to': edge_data['to'],
            'width': min(edge_data['weight'], 5) if is_keyword else 1,
            'color': {'color': '#3498db' if is_keyword else '#2c3e50', 'highlight': '#3498db'},
            'dashes': not is_keyword,
            'edgeType': edge_data['type'],
        })

    return edges


def _get_chroma_collection():
    """进程内单例 Chroma collection，复用 hybrid_search 共享客户端。"""
    global _chroma_collection
    if _chroma_collection is not None:
        return _chroma_collection
    _chroma_collection = get_chroma_collection(get_chroma_client())
    return _chroma_collection


def _sanitize_chroma_metadata(meta: dict) -> dict:
    """Chroma metadata 仅支持标量类型。"""
    clean: dict = {}
    for key, value in meta.items():
        if value is None:
            continue
        if isinstance(value, (str, int, float, bool)):
            clean[key] = value
        else:
            clean[key] = str(value)
    return clean


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def embed_text(text: str) -> list[float]:
    """Ollama embedding，用于正文更新后重嵌 Chroma。"""
    from llm_client import embed_text as _embed

    return _embed(text)


def _write_access_warning(project: str) -> str | None:
    from workspace_config import check_write_access, log_workspace_warning

    warning = check_write_access(normalize_project(project), _viewer_workspace().write_projects)
    if warning:
        log_workspace_warning(warning)
    return warning


def _write_access_warning_for_memory(
    memory_id: str,
    body_project: object | None = None,
    *,
    has_project: bool = False,
) -> str | None:
    """update 时未传 project 则按记忆当前 project 做 write 检测。"""
    if has_project:
        return _write_access_warning(str(body_project or ''))
    from memory_sync import get_active_record

    old = get_active_record(memory_id)
    if not old:
        raise ValueError(f'记忆不存在: {memory_id}')
    return _write_access_warning(str(old.get('project', '') or ''))


def add_memory_from_viewer(content: str, *, project: str = '', category: str = 'episodic') -> dict:
    """viewer 新增记忆：统一走 memory_store.add（含 E-dedup）。"""
    from memory_store import add as store_add

    content = (content or '').strip()
    if not content:
        raise ValueError('正文不能为空')

    project = normalize_project(project)
    category = normalize_category(category or DEFAULT_CATEGORY)
    if category not in VALID_CATEGORIES:
        raise ValueError(f'无效 category: {category}')

    write_warning = _write_access_warning(project)
    result = store_add(
        content,
        metadata={'category': category},
        project=project,
        actor='mem_viewer',
    )
    if result.dropped:
        payload = {'ok': False, 'dropped': True, 'detail': result.merge_note or '去重删除'}
        if write_warning:
            payload['warning'] = write_warning
        return payload
    payload = {
        'ok': True,
        'id': result.memory_id,
        'content': result.content,
        'project': normalize_project(result.project),
        'category': category,
    }
    if write_warning:
        payload['warning'] = write_warning
    return payload


def update_memory_content(
    memory_id: str,
    content: str,
    *,
    project: str | None = None,
    category: str | None = None,
) -> dict:
    """viewer 正文扩写：SQLite + history UPDATE + Chroma 重嵌。"""
    from memory_sync import get_active_record, sync_active_update_content

    content = (content or '').strip()
    if not content:
        raise ValueError('正文不能为空')

    old = get_active_record(memory_id)
    if not old:
        raise ValueError(f'记忆不存在: {memory_id}')

    new_project = normalize_project(project) if project is not None else normalize_project(old.get('project', ''))
    new_category = (
        normalize_category(category)
        if category is not None
        else normalize_category(old.get('category', '') or 'episodic')
    )
    if new_category not in VALID_CATEGORIES:
        raise ValueError(f'无效 category: {new_category}')

    lang = infer_memory_lang(content)
    sync_active_update_content(
        memory_id,
        content,
        project=new_project,
        category=new_category,
        lang=lang,
    )

    col = _get_chroma_collection()
    result = col.get(ids=[memory_id], include=['metadatas'])
    ids = result.get('ids') or []
    if not ids:
        raise ValueError(f'Chroma 中不存在: {memory_id}')

    old_meta = dict((result.get('metadatas') or [{}])[0] or {})
    new_meta = dict(old_meta)
    new_meta['data'] = content
    new_meta['project'] = new_project
    new_meta['category'] = new_category
    new_meta['updated_at'] = _utc_now_iso()
    new_meta['hash'] = hashlib.md5(content.encode('utf-8')).hexdigest()
    apply_category_metadata(new_meta)
    apply_lang_metadata(new_meta, content)
    clear_grooming_suggestion(new_meta)

    embedding = embed_text(content)
    col.update(ids=[memory_id], embeddings=[embedding], metadatas=[_sanitize_chroma_metadata(new_meta)])

    record_event(
        'UPDATE',
        memory_id,
        category=new_category,
        note='viewer 正文扩写',
        content_preview=content,
        actor='mem_viewer',
    )
    return {
        'changed': True,
        'content': content,
        'project': new_project,
        'category': new_category,
        'category_label': CATEGORY_LABELS.get(new_category, new_category),
        'category_raw': str(new_meta.get('category_raw', '') or ''),
    }


def _read_chroma_meta(memory_id: str) -> dict:
    col = _get_chroma_collection()
    result = col.get(ids=[memory_id], include=['metadatas'])
    ids = result.get('ids') or []
    if not ids:
        raise ValueError(f'记忆不存在: {memory_id}')
    return dict((result.get('metadatas') or [{}])[0] or {})


def _write_chroma_meta(memory_id: str, meta: dict) -> None:
    col = _get_chroma_collection()
    col.update(ids=[memory_id], metadatas=[_sanitize_chroma_metadata(meta)])


def confirm_grooming_memory(memory_id: str) -> dict:
    """确认保留：仅清除 grooming_pending。"""
    meta = _read_chroma_meta(memory_id)
    if not is_grooming_pending(meta):
        return {'changed': False, 'grooming': parse_grooming_fields(meta)}

    clear_grooming_pending(meta)
    _write_chroma_meta(memory_id, meta)
    record_event(
        'GROOMING',
        memory_id,
        note='viewer 确认保留（清除待确认标记）',
        actor='mem_viewer',
    )
    return {'changed': True, 'grooming': parse_grooming_fields(meta)}


def execute_grooming_promote(memory_id: str) -> dict:
    """采纳升类建议。"""
    meta = _read_chroma_meta(memory_id)
    grooming = parse_grooming_fields(meta)
    target = grooming.get('target_category', '')
    if grooming.get('action') != 'promote' or not target:
        raise ValueError('当前记忆无 promote 建议')

    return update_memory_metadata(memory_id, category=target)


def execute_grooming_merge(source_id: str, hinted_target_id: str = '') -> dict:
    """合并（重校验）：删除 source，target 追加 merged_from。"""
    from memory_delete import archive_delete
    from memory_sync import SyncError, get_active_record

    source = get_active_record(source_id)
    if not source:
        raise ValueError(f'源记忆不存在: {source_id}')

    source_text = str(source.get('content', '') or '')
    project = normalize_project(str(source.get('project', '') or ''))
    validated = revalidate_merge_target(
        source_id,
        source_text,
        project,
        hinted_target_id,
        hybrid_search_fn=hybrid_search,
    )
    if not validated:
        # hint 指向的目标已不存在时，重校验必然反复失败——清掉死 hint 避免用户反复撞墙
        remove_merge_hint(source_id)
        raise ValueError('重校验未找到合适合并目标（死 hint 已清除），请手动处理')

    target_id = str(validated.get('id', '') or '')
    if not target_id or target_id == source_id:
        raise ValueError('合并目标无效')

    target_record = get_active_record(target_id)
    if not target_record:
        raise ValueError(f'目标记忆不存在: {target_id}')

    target_meta = _read_chroma_meta(target_id)
    merged_sources = parse_merged_from(target_meta)
    if source_id not in merged_sources:
        merged_sources.append(source_id)
    target_meta['merged_from'] = ','.join(merged_sources)
    apply_category_metadata(target_meta)
    _write_chroma_meta(target_id, target_meta)

    reason = f'grooming 合并至 {target_id[:8]}…（overlap={validated.get("overlap", 0):.2f}）'
    try:
        archive_delete(
            source_id,
            reason,
            actor='mem_viewer',
            source='grooming_merge',
        )
    except SyncError as error:
        raise ValueError(f'删除源记忆同步失败: {error}') from error

    record_merge_result(
        target_id,
        [source_id],
        category=str(target_record.get('category', '') or ''),
        note=reason,
        content_preview=source_text,
        actor='mem_viewer',
    )
    remove_merge_hint(source_id)
    return {
        'ok': True,
        'source_id': source_id,
        'target_id': target_id,
        'overlap': validated.get('overlap', 0),
        'target_changed': target_id != hinted_target_id,
    }


from flask import Flask, jsonify, render_template_string, request, url_for  # noqa: E402

app = Flask(__name__)

# 本地静态文件（src/static/vendor/），替代原 unpkg CDN（2026-09-02：CDN 加载失败改本地）
VIS_JS_STATIC = 'vendor/vis-network.min.js'

HTML_TEMPLATE = '''<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<title>local-memory 记忆图谱</title>
<style>
  * { margin:0; padding:0; box-sizing:border-box; }
  body { font-family: -apple-system, 'PingFang SC', sans-serif; background:#1a1a2e; color:#e0e0e0; display:flex; flex-direction:column; height:100vh; }

  .toolbar { display:flex; align-items:center; gap:12px; padding:12px 16px; background:#16213e; border-bottom:1px solid #0f3460; }
  .toolbar input[type=text] { flex:1; min-width:120px; padding:8px 12px; border-radius:6px; border:1px solid #0f3460; background:#1a1a2e; color:#e0e0e0; font-size:14px; }
  .toolbar input[type=text]:focus { outline:none; border-color:#3498db; }
  .toolbar select { padding:8px 12px; border-radius:6px; border:1px solid #0f3460; background:#1a1a2e; color:#e0e0e0; font-size:14px; }
  .toolbar button { padding:8px 16px; border-radius:6px; border:none; cursor:pointer; font-size:14px; transition:opacity .2s; }
  .toolbar button:hover { opacity:0.8; }
  .btn-search { background:#3498db; color:#fff; }
  .btn-reset { background:#e74c3c; color:#fff; }
  .btn-add { background:#2ecc71; color:#fff; white-space:nowrap; }

  .modal-overlay { position:fixed; inset:0; background:rgba(0,0,0,0.55); display:none; align-items:center; justify-content:center; z-index:100; }
  .modal-overlay.visible { display:flex; }
  .modal-card { width:min(560px, 92vw); max-height:85vh; overflow-y:auto; background:#16213e; border:1px solid #0f3460; border-radius:10px; padding:20px; }
  .modal-title { font-size:18px; font-weight:600; color:#3498db; margin-bottom:16px; }
  .modal-field { margin-bottom:12px; }
  .modal-field label { display:block; font-size:12px; color:#7f8c8d; margin-bottom:6px; }
  .modal-field textarea { width:100%; min-height:140px; padding:10px 12px; border-radius:6px; border:1px solid #0f3460; background:#1a1a2e; color:#e0e0e0; font-size:14px; line-height:1.5; resize:vertical; }
  .modal-field input, .modal-field select { width:100%; padding:8px 12px; border-radius:6px; border:1px solid #0f3460; background:#1a1a2e; color:#e0e0e0; font-size:14px; }
  .similar-warn { margin-top:8px; padding:10px; border-radius:6px; background:#1a1a2e; border:1px solid #f39c12; font-size:12px; color:#f39c12; display:none; }
  .similar-warn.visible { display:block; }
  .similar-item { margin-top:6px; color:#bdc3c7; line-height:1.4; cursor:pointer; }
  .similar-item:hover { color:#3498db; }
  .modal-actions { display:flex; gap:10px; justify-content:flex-end; margin-top:16px; }
  .modal-actions button { padding:8px 16px; border-radius:6px; border:none; cursor:pointer; font-size:14px; }
  .btn-cancel { background:#7f8c8d; color:#fff; }
  .btn-primary { background:#2ecc71; color:#fff; }
  .detail-textarea { width:100%; min-height:120px; padding:8px 10px; border-radius:6px; border:1px solid #0f3460; background:#1a1a2e; color:#e0e0e0; font-size:14px; line-height:1.6; resize:vertical; display:none; }
  .detail-textarea.visible { display:block; }
  .detail-actions { display:flex; gap:8px; margin-top:8px; flex-wrap:wrap; }
  .btn-edit-content { background:#3498db; color:#fff; padding:6px 14px; border:none; border-radius:6px; cursor:pointer; font-size:13px; }
  .btn-save-content { background:#2ecc71; color:#fff; padding:6px 14px; border:none; border-radius:6px; cursor:pointer; font-size:13px; display:none; }
  .btn-save-content.visible { display:inline-block; }
  .btn-cancel-content { background:#7f8c8d; color:#fff; padding:6px 14px; border:none; border-radius:6px; cursor:pointer; font-size:13px; display:none; }
  .btn-cancel-content.visible { display:inline-block; }

  .grooming-section { border:1px solid #f39c12; border-radius:8px; padding:12px; background:#1a1a2e; }
  .grooming-pending-badge { display:inline-block; padding:2px 8px; border-radius:4px; background:#4a3a20; color:#f39c12; font-size:11px; margin-left:6px; }
  .grooming-action { display:inline-block; padding:2px 8px; border-radius:4px; font-size:12px; font-weight:600; margin-bottom:8px; }
  .grooming-action.delete { background:#4a2020; color:#e74c3c; }
  .grooming-action.promote { background:#1e3a4a; color:#3498db; }
  .grooming-action.keep { background:#2c3e2c; color:#2ecc71; }
  .grooming-action.merge { background:#3d3520; color:#f39c12; }
  .grooming-reason { font-size:13px; line-height:1.5; color:#e0e0e0; white-space:pre-wrap; margin-bottom:10px; }
  .grooming-meta { font-size:11px; color:#7f8c8d; margin-bottom:10px; }
  .grooming-actions { display:flex; gap:8px; flex-wrap:wrap; }
  .grooming-actions button { padding:6px 12px; border:none; border-radius:6px; cursor:pointer; font-size:12px; }
  .btn-grooming-confirm { background:#2ecc71; color:#fff; }
  .btn-grooming-promote { background:#3498db; color:#fff; }
  .btn-grooming-delete { background:#e74c3c; color:#fff; }
  .btn-grooming-merge { background:#f39c12; color:#1a1a2e; }

  .search-results { padding:8px 16px; background:#16213e; border-bottom:1px solid #0f3460; max-height:220px; overflow-y:auto; display:none; }
  .search-results.visible { display:block; }
  .search-results-head { font-size:12px; color:#7f8c8d; margin-bottom:8px; }
  .search-result-item { display:flex; gap:10px; align-items:flex-start; padding:8px 10px; margin-bottom:6px; border-radius:6px; background:#1a1a2e; cursor:pointer; border:1px solid transparent; }
  .search-result-item:hover { border-color:#3498db; }
  .search-result-item.dimmed { opacity:0.45; }
  .search-result-rank { font-size:12px; color:#3498db; min-width:28px; font-weight:600; }
  .search-result-score { font-size:12px; color:#f39c12; min-width:110px; white-space:nowrap; }
  .search-result-text { font-size:13px; color:#e0e0e0; line-height:1.5; flex:1; }
  .search-result-meta { font-size:11px; color:#95a5a6; margin-top:4px; }

  .main { display:flex; flex:1; overflow:hidden; }
  #graph-container { flex:1; }
  #detail-panel { width:320px; background:#16213e; border-left:1px solid #0f3460; overflow:hidden; display:none; flex-direction:column; }
  #detail-panel.visible { display:flex; }
  #detail-panel-body { flex:1; overflow-y:auto; padding:16px; min-height:0; scrollbar-width:thin; scrollbar-color:#0f3460 transparent; }
  #detail-panel-body::-webkit-scrollbar { width:6px; }
  #detail-panel-body::-webkit-scrollbar-thumb { background:#0f3460; border-radius:3px; }
  #detail-panel-footer { flex-shrink:0; padding:12px 16px 16px; border-top:1px solid #0f3460; background:#16213e; }

  #memory-list { width:240px; background:#16213e; border-left:1px solid #0f3460; overflow-y:auto; display:flex; flex-direction:column; min-height:0; scrollbar-width:thin; scrollbar-color:#0f3460 transparent; }
  #memory-list.collapsed { width:38px; overflow:hidden; }
  #memory-list::-webkit-scrollbar { width:6px; }
  #memory-list::-webkit-scrollbar-thumb { background:#0f3460; border-radius:3px; }
  .memory-list-head { display:flex; align-items:center; gap:6px; padding:12px 12px 10px; font-size:12px; color:#7f8c8d; border-bottom:1px solid #0f3460; flex-shrink:0; cursor:pointer; user-select:none; }
  .memory-list-head:hover { background:#1a1a2e; }
  #memory-list.collapsed .memory-list-head { justify-content:center; padding:12px 0; }
  #memory-list.collapsed #memory-list-title { display:none; }
  #memory-list.collapsed #memory-list-body { display:none; }
  .memory-list-toggle { font-size:12px; color:#95a5a6; margin-left:auto; }
  #memory-list.collapsed .memory-list-toggle { margin-left:0; }
  .memory-list-item { padding:10px 12px; border-bottom:1px solid #0f3460; cursor:pointer; transition:background .15s; }
  .memory-list-item:hover { background:#1a1a2e; }
  .memory-list-item.active { background:#0f3460; }
  .memory-list-text { font-size:12px; color:#e0e0e0; line-height:1.5; margin-bottom:4px; }
  .memory-list-meta { font-size:11px; color:#95a5a6; display:flex; align-items:center; gap:6px; flex-wrap:wrap; }
  .memory-list-meta .grooming-action { margin-bottom:0; padding:1px 6px; font-size:10px; }
  .memory-list-meta .grooming-pending-badge { margin-left:0; }
  .memory-list-empty { padding:16px 12px; font-size:12px; color:#7f8c8d; }

  .detail-title { font-size:16px; font-weight:600; margin-bottom:12px; color:#3498db; }
  .detail-section { margin-bottom:16px; }
  .detail-label { font-size:12px; color:#7f8c8d; margin-bottom:4px; }
  .detail-text { font-size:14px; line-height:1.6; white-space:pre-wrap; }
  .detail-meta { font-size:12px; color:#95a5a6; }
  .timeline-hint { font-size:11px; color:#7f8c8d; margin-bottom:4px; }
  .timeline-list { list-style:none; padding:0; margin:4px 0 0; }
  .merge-sources { margin-top:4px; }
  .merge-sources-empty { font-size:12px; color:#95a5a6; }
  .merge-sources-title { font-size:12px; color:#7f8c8d; margin-bottom:8px; }
  .merge-source-node { margin-bottom:10px; border:1px solid #0f3460; border-radius:6px; background:#1a1a2e; overflow:hidden; }
  .merge-source-node.nested { margin-left:12px; margin-top:8px; border-style:dashed; }
  .merge-source-head { display:flex; align-items:center; gap:8px; flex-wrap:wrap; padding:8px 10px; background:#16213e; font-size:12px; color:#bdc3c7; }
  .merge-source-badge { display:inline-block; padding:2px 6px; border-radius:4px; font-size:11px; font-weight:600; }
  .merge-source-badge.active { background:#1e4620; color:#2ecc71; }
  .merge-source-badge.deleted { background:#4a2020; color:#e74c3c; }
  .merge-source-badge.history { background:#3d3520; color:#f39c12; }
  .merge-source-badge.missing { background:#2c2c2c; color:#95a5a6; }
  .merge-source-id { color:#7f8c8d; font-family:monospace; }
  .merge-source-meta { font-size:11px; color:#7f8c8d; width:100%; }
  .merge-source-locate { padding:2px 8px; border:none; border-radius:4px; background:#3498db; color:#fff; font-size:11px; cursor:pointer; }
  .merge-source-locate:hover { opacity:0.85; }
  .merge-source-body { padding:8px 10px; font-size:12px; line-height:1.5; color:#e0e0e0; white-space:pre-wrap; word-break:break-word; }
  .merge-source-children { padding:0 8px 8px; }
  .timeline-item { border-left:2px solid #3498db; padding:6px 0 6px 10px; margin-bottom:8px; }
  .timeline-item .tl-head { font-size:12px; color:#3498db; margin-bottom:4px; }
  .timeline-item .tl-body { font-size:12px; color:#bdc3c7; line-height:1.5; white-space:pre-wrap; }
  .timeline-link { color:#f39c12; cursor:pointer; text-decoration:underline; }
  .detail-edit-row { display:flex; flex-direction:column; gap:8px; margin-bottom:8px; }
  .detail-edit-row input, .detail-edit-row select { width:100%; padding:6px 10px; border-radius:6px; border:1px solid #0f3460; background:#1a1a2e; color:#e0e0e0; font-size:13px; }
  .detail-edit-row input:focus, .detail-edit-row select:focus { outline:none; border-color:#3498db; }
  .btn-save { margin-top:4px; padding:6px 14px; background:#2ecc71; color:#fff; border:none; border-radius:6px; cursor:pointer; font-size:13px; }
  .btn-save:hover { opacity:0.8; }
  .btn-save:disabled { opacity:0.5; cursor:not-allowed; }
  .btn-delete { width:100%; padding:8px 16px; background:#e74c3c; color:#fff; border:none; border-radius:6px; cursor:pointer; font-size:13px; }
  .btn-delete:hover { opacity:0.8; }

  .stats { padding:8px 16px; background:#16213e; border-top:1px solid #0f3460; font-size:12px; color:#7f8c8d; text-align:center; }
  .legend { display:inline; margin-left:16px; }
  .legend span { margin-right:8px; }

  .legend-panel { position:absolute; bottom:40px; left:16px; padding:12px 16px; background:#16213e; border:1px solid #0f3460; border-radius:8px; font-size:12px; color:#e0e0e0; z-index:10; max-width:280px; }
  .legend-panel h4 { margin:0 0 8px; font-size:13px; color:#3498db; }
  .legend-item { display:flex; align-items:center; gap:8px; margin-bottom:6px; }
  .legend-dot { width:14px; height:14px; border-radius:50%; flex-shrink:0; }
  .legend-shape { width:12px; height:12px; flex-shrink:0; background:#e0e0e0; }
  .legend-shape.dot { border-radius:50%; }
  .legend-shape.diamond { border-radius:2px; transform:rotate(45deg) scale(0.7); }
  .legend-shape.star { clip-path:polygon(50% 0%, 61% 35%, 98% 35%, 68% 57%, 79% 91%, 50% 70%, 21% 91%, 32% 57%, 2% 35%, 39% 35%); }

  #toast-container { position:fixed; top:16px; right:16px; z-index:200; display:flex; flex-direction:column; gap:8px; }
  .toast { padding:10px 16px; border-radius:6px; font-size:13px; color:#fff; max-width:420px; word-break:break-all; box-shadow:0 2px 8px rgba(0,0,0,0.35); animation:toast-in .2s ease-out; }
  .toast.success { background:#2ecc71; }
  .toast.error { background:#e74c3c; }
  .toast.info { background:#3498db; }
  @keyframes toast-in { from { transform:translateX(20px); opacity:0; } to { transform:translateX(0); opacity:1; } }

  #review-panel { position:fixed; inset:0; background:rgba(0,0,0,0.6); display:none; align-items:center; justify-content:center; z-index:90; }
  #review-panel.visible { display:flex; }
  #review-card { width:min(860px, 94vw); max-height:90vh; background:#16213e; border:1px solid #0f3460; border-radius:10px; display:flex; flex-direction:column; overflow:hidden; }
  #review-head { display:flex; align-items:center; gap:10px; padding:14px 18px; border-bottom:1px solid #0f3460; flex-shrink:0; }
  #review-progress { font-size:13px; color:#95a5a6; }
  #review-title { font-size:15px; font-weight:600; color:#3498db; flex:1; }
  #review-close { background:none; border:none; color:#95a5a6; font-size:18px; cursor:pointer; }
  #review-body { flex:1; overflow-y:auto; padding:16px 18px; min-height:0; }
  .review-meta { font-size:12px; color:#95a5a6; margin-bottom:10px; }
  .review-text-box { border:1px solid #0f3460; border-radius:6px; padding:10px 12px; font-size:13px; line-height:1.6; white-space:pre-wrap; word-break:break-word; background:#1a1a2e; margin-bottom:12px; max-height:280px; overflow-y:auto; }
  .review-reason { font-size:13px; color:#f39c12; line-height:1.5; white-space:pre-wrap; margin-bottom:12px; }
  .review-compare { display:flex; gap:10px; margin-bottom:12px; }
  .review-compare-col { flex:1; min-width:0; }
  .review-compare-label { font-size:12px; color:#7f8c8d; margin-bottom:4px; }
  .review-compare-label b { color:#e0e0e0; }
  .review-compare-text { border:1px solid #0f3460; border-radius:6px; padding:8px 10px; font-size:12px; line-height:1.5; white-space:pre-wrap; word-break:break-word; background:#1a1a2e; max-height:220px; overflow-y:auto; }
  #review-foot { display:flex; gap:8px; padding:12px 18px; border-top:1px solid #0f3460; flex-shrink:0; flex-wrap:wrap; align-items:center; }
  #review-foot .spacer { flex:1; }
  #review-foot button { padding:8px 14px; border:none; border-radius:6px; cursor:pointer; font-size:13px; }
  .rv-keep { background:#2ecc71; color:#fff; }
  .rv-delete { background:#e74c3c; color:#fff; }
  .rv-promote { background:#3498db; color:#fff; }
  .rv-merge { background:#f39c12; color:#1a1a2e; }
  .rv-skip { background:#7f8c8d; color:#fff; }
  #review-delete-reason { width:100%; padding:8px 10px; border-radius:6px; border:1px solid #0f3460; background:#1a1a2e; color:#e0e0e0; font-size:12px; margin-top:8px; }

  #merge-compare-panel { position:fixed; inset:0; background:rgba(0,0,0,0.6); display:none; align-items:center; justify-content:center; z-index:95; }
  #merge-compare-panel.visible { display:flex; }
  #merge-compare-card { width:min(860px, 94vw); max-height:85vh; background:#16213e; border:1px solid #f39c12; border-radius:10px; display:flex; flex-direction:column; overflow:hidden; }
  #merge-compare-title { font-size:15px; font-weight:600; color:#f39c12; padding:14px 18px; border-bottom:1px solid #0f3460; flex-shrink:0; }
  #merge-compare-body { flex:1; overflow-y:auto; padding:16px 18px; min-height:0; }
  #merge-compare-foot { display:flex; gap:8px; justify-content:flex-end; padding:12px 18px; border-top:1px solid #0f3460; flex-shrink:0; }
  #merge-compare-foot button { padding:8px 14px; border:none; border-radius:6px; cursor:pointer; font-size:13px; }

  .list-check { margin-right:6px; cursor:pointer; flex-shrink:0; }
  #batch-bar { display:none; align-items:center; gap:10px; padding:8px 12px; background:#0f3460; border-bottom:1px solid #0f3460; font-size:12px; flex-shrink:0; flex-wrap:wrap; }
  #batch-bar.visible { display:flex; }
  #batch-bar button { padding:4px 10px; border:none; border-radius:4px; cursor:pointer; font-size:12px; }
  .batch-keep { background:#2ecc71; color:#fff; }
  .batch-promote { background:#3498db; color:#fff; }
  .batch-clear { background:#7f8c8d; color:#fff; }
</style>
</head>
<body>

<div id="toast-container"></div>

<div id="review-panel">
  <div id="review-card">
    <div id="review-head">
      <span id="review-progress"></span>
      <span id="review-title"></span>
      <button id="review-close" onclick="closeReview()" title="关闭 (Esc)">×</button>
    </div>
    <div id="review-body"></div>
    <div id="review-foot"></div>
  </div>
</div>

<div id="merge-compare-panel" onclick="onMergeCompareBackdrop(event)">
  <div id="merge-compare-card" onclick="event.stopPropagation()">
    <div id="merge-compare-title"></div>
    <div id="merge-compare-body"></div>
    <div id="merge-compare-foot">
      <button class="rv-skip" onclick="closeMergeCompare()">关闭</button>
    </div>
  </div>
</div>

<div class="toolbar">
  <input type="text" id="search-input" placeholder="搜索记忆..." />
  <select id="project-filter">
    <option value="">全部项目</option>
    {% for project in projects %}
    <option value="{{ project }}">{{ project }}</option>
    {% endfor %}
  </select>
  <select id="category-filter">
    <option value="">全部分类</option>
    {% for cat, label in category_items %}
    <option value="{{ cat }}">{{ label }}</option>
    {% endfor %}
  </select>
  <select id="grooming-filter">
    <option value="">全部记忆</option>
    <option value="review">待处理 ({{ grooming_counts.review }})</option>
    <option value="pending">待确认 pending ({{ grooming_counts.pending }})</option>
    <option value="delete">建议删除 ({{ grooming_counts.delete }})</option>
    <option value="promote">建议升类 ({{ grooming_counts.promote }})</option>
    <option value="merge">建议合并 ({{ grooming_counts.merge }})</option>
    <option value="keep">建议保留 ({{ grooming_counts.keep }})</option>
  </select>
  <button class="btn-search" onclick="doSearch()">搜索</button>
  <button class="btn-add" onclick="openAddModal()">+ 新增记忆</button>
  <button id="btn-review-queue" onclick="openReview()">待处理队列 ({{ grooming_counts.review }})</button>
  <button class="btn-reset" onclick="resetGraph()">重置</button>
</div>

<div id="add-modal" class="modal-overlay" onclick="onAddModalBackdrop(event)">
  <div class="modal-card" onclick="event.stopPropagation()">
    <div class="modal-title">新增记忆</div>
    <div class="modal-field">
      <label>正文（中文完整句，可含 Why / How to apply）</label>
      <textarea id="add-content" placeholder="例如：5月19号下大雨我没带伞，下次梅雨季节记得随身带伞。" oninput="scheduleSimilarCheck()"></textarea>
      <div id="add-similar" class="similar-warn"></div>
    </div>
    <div class="modal-field">
      <label>项目（留空 = 全局）</label>
      <input type="text" id="add-project" placeholder="留空 = 全局" oninput="scheduleSimilarCheck()" />
    </div>
    <div class="modal-field">
      <label>分类</label>
      <select id="add-category">
        {% for cat, label in category_items %}
        <option value="{{ cat }}">{{ label }}</option>
        {% endfor %}
      </select>
    </div>
    <div class="modal-actions">
      <button class="btn-cancel" onclick="closeAddModal()">取消</button>
      <button class="btn-primary" id="btn-add-save" onclick="saveNewMemory()">保存</button>
    </div>
  </div>
</div>

<div id="search-results" class="search-results">
  <div class="search-results-head" id="search-results-head"></div>
  <div id="search-results-list"></div>
</div>

<div class="legend-panel">
  <h4>分类颜色</h4>
  {% for cat, label in category_items %}
  <div class="legend-item">
    <div class="legend-dot" style="background:{{ category_colors[cat] }}"></div>
    <span>{{ label }}</span>
  </div>
  {% endfor %}
  <hr style="border-color:#0f3460;margin:8px 0" />
  <h4>节点形状</h4>
  <div class="legend-item">
    <div class="legend-shape dot"></div>
    <span>无变更</span>
  </div>
  <div class="legend-item">
    <div class="legend-shape diamond"></div>
    <span>1次更新</span>
  </div>
  <div class="legend-item">
    <div class="legend-shape star"></div>
    <span>2+次更新</span>
  </div>
  <hr style="border-color:#0f3460;margin:8px 0" />
  <div class="legend-item">
    <span style="font-size:16px">⬤</span> vs <span style="font-size:10px">●</span>
    <span>节点大小 = 重复提及</span>
  </div>
  <div class="legend-item">
    <div class="legend-dot" style="background:#95a5a6;box-shadow:0 0 8px #e0e0e0"></div>
    <span>光晕 = 重复≥3次</span>
  </div>
</div>

<div class="main">
  <div id="graph-container"></div>
  <div id="memory-list">
    <div class="memory-list-head" onclick="toggleMemoryList()" title="点击收起/展开">
      <span id="memory-list-title">记忆列表</span>
      <span id="memory-list-toggle" class="memory-list-toggle">▾</span>
    </div>
    <div id="batch-bar">
      <label><input type="checkbox" id="batch-select-all" class="list-check" onchange="toggleSelectAll(this.checked)" />全选</label>
      <button class="batch-keep" onclick="batchConfirm()">批量确认保留</button>
      <button class="batch-promote" onclick="batchPromote()">批量升类</button>
      <button class="batch-clear" onclick="clearSelection()">取消选择</button>
    </div>
    <div id="memory-list-body"></div>
  </div>
  <div id="detail-panel">
    <div id="detail-panel-body">
    <div class="detail-title" id="detail-title"></div>
    <div class="detail-section">
      <div class="detail-label">记忆正文</div>
      <div class="detail-text" id="detail-text"></div>
      <textarea class="detail-textarea" id="edit-content"></textarea>
      <div class="detail-actions">
        <button class="btn-edit-content" id="btn-edit-content" onclick="startEditContent()">编辑正文</button>
        <button class="btn-save-content" id="btn-save-content" onclick="saveContent()">保存正文</button>
        <button class="btn-cancel-content" id="btn-cancel-content" onclick="cancelEditContent()">取消</button>
      </div>
    </div>
    <div class="detail-section">
      <div class="detail-label">项目 / 分类</div>
      <div class="detail-edit-row">
        <input type="text" id="edit-project" placeholder="留空 = 全局" />
        <select id="edit-category">
          {% for cat, label in category_items %}
          <option value="{{ cat }}">{{ label }}</option>
          {% endfor %}
        </select>
      </div>
      <div class="detail-meta" id="detail-created"></div>
      <button class="btn-save" id="btn-save" onclick="saveMetadata()">保存</button>
    </div>
    <div class="detail-section" id="grooming-section" style="display:none">
      <div class="detail-label">AI 梳理建议 <span id="grooming-pending-badge" class="grooming-pending-badge" style="display:none">待确认</span></div>
      <div class="grooming-section">
        <div id="grooming-action-badge"></div>
        <div class="grooming-reason" id="grooming-reason"></div>
        <div class="grooming-meta" id="grooming-meta"></div>
        <div class="grooming-actions" id="grooming-actions"></div>
      </div>
    </div>
    <div class="detail-section">
      <div class="detail-label">演变时间线</div>
      <div class="detail-meta timeline-hint">合并来源：展示并入本条的原记忆正文；若来源本身由合并产生会继续展开</div>
      <div id="detail-ancestors" class="merge-sources"></div>
      <ul class="timeline-list" id="detail-timeline"></ul>
    </div>
    <div class="detail-section">
      <div class="detail-label">Metadata</div>
      <div class="detail-meta" id="detail-meta"></div>
    </div>
    <div class="detail-section">
      <div class="detail-label">厚度指标</div>
      <div class="detail-meta" id="detail-thickness"></div>
    </div>
    <div class="detail-section">
      <div class="detail-label">ID</div>
      <div class="detail-meta" id="detail-id"></div>
    </div>
    </div>
    <div id="detail-panel-footer">
      <button class="btn-delete" id="btn-delete" onclick="deleteMemory()">删除此记忆</button>
    </div>
  </div>
</div>

<div class="stats" id="stats">
  共 <span id="total-count">0</span> 条记忆
  <span class="legend">●无变更 ◆1次更新 ★2+次更新 | 大=重复提及多 | 光晕=重复≥3</span>
</div>

<script src="{{ vis_js_src }}"></script>
<script>
  const nodesData = {{ nodes_json | safe }};
  const edgesData = {{ edges_json | safe }};
  const memoriesMap = {{ memories_map_json | safe }};
  const thicknessMap = {{ thickness_json | safe }};
  const mergeHintsPayload = {{ merge_hints_json | safe }};
  let selectedId = null;
  let memoryListCollapsed = false;
  const deletedNodeIds = new Set();
  let lastSearchResults = [];
  let lastSearchPayload = null;
  const MCP_SEARCH_MAX_RESULTS = {{ mcp_search_max_results }};

  function showToast(message, type, duration) {
    let container = document.getElementById('toast-container');
    if (!container) {
      container = document.createElement('div');
      container.id = 'toast-container';
      document.body.appendChild(container);
    }
    const toast = document.createElement('div');
    toast.className = 'toast ' + (type || 'info');
    toast.textContent = message;
    container.appendChild(toast);
    setTimeout(() => toast.remove(), duration || 3200);
  }

  function syncGroomingUi() {
    updateGroomingFilterLabel();
    const btn = document.getElementById('btn-review-queue');
    if (btn) btn.textContent = '待处理队列 (' + computeGroomingCounts().review + ')';
    onFilterChange();
  }

  const nodes = new vis.DataSet(nodesData);
  const edges = new vis.DataSet(edgesData);
  // 保存节点原始颜色，用于重置还原
  const originalNodeColors = {};
  nodesData.forEach(n => {
    originalNodeColors[n.id] = (n.color && n.color.background) ? n.color.background : n.color;
  });
  const container = document.getElementById('graph-container');
  const options = {
    nodes: { shape: 'dot', font: { size: 11, color: '#e0e0e0' }, borderWidth: 1, borderWidthSelected: 3 },
    edges: { color: { color: '#0f3460', highlight: '#3498db', hover: '#2980b9' }, width: 1, smooth: { type: 'continuous' } },
    physics: { barnesHut: { gravitationalConstant: -2000, centralGravity: 0.3, springLength: 100, springConstant: 0.04 }, stabilization: { iterations: 100 } },
    interaction: { hover: true, tooltipDelay: 200, navigationButtons: true, keyboard: true },
  };
  const network = new vis.Network(container, { nodes, edges }, options);

  network.on('click', function(params) {
    if (params.nodes.length > 0) {
      selectedId = params.nodes[0];
      showDetail(selectedId);
    } else {
      hideDetail();
    }
  });

  function showDetail(id) {
    cancelEditContent();
    const mem = memoriesMap[id];
    if (!mem) return;
    const thick = thicknessMap[id] || {};
    document.getElementById('detail-title').textContent = mem.project ? '[' + mem.project + ']' : '[全局]';
    document.getElementById('detail-text').textContent = mem.text;
    document.getElementById('edit-project').value = mem.project || '';
    document.getElementById('edit-category').value = mem.category || 'episodic';
    document.getElementById('detail-created').textContent =
      '创建: ' + (mem.created_at || '未知') +
      (mem.category_raw && mem.category_raw !== mem.category ? ' | 原分类: ' + mem.category_raw : '');
    document.getElementById('detail-meta').textContent = JSON.stringify(mem.metadata, null, 2);
    const changeEmoji = thick.change >= 2 ? '★' : thick.change === 1 ? '◆' : '●';
    document.getElementById('detail-thickness').textContent =
      changeEmoji + ' 变更:' + thick.change + '  |  连接:' + thick.connection + '  |  重复:' + thick.repetition;
    document.getElementById('detail-id').textContent = id;
    document.getElementById('detail-panel').classList.add('visible');
    renderGroomingPanel(mem);
    loadTimeline(id);
    updateMemoryListHighlight();
  }

  function renderGroomingPanel(mem) {
    const section = document.getElementById('grooming-section');
    const badge = document.getElementById('grooming-pending-badge');
    const actionEl = document.getElementById('grooming-action-badge');
    const reasonEl = document.getElementById('grooming-reason');
    const metaEl = document.getElementById('grooming-meta');
    const actionsEl = document.getElementById('grooming-actions');

    const grooming = mem.grooming || {};
    const mergeHint = mem.merge_hint || null;
    const hasSuggestion = grooming.action || grooming.reason || mergeHint;
    if (!hasSuggestion && !grooming.pending) {
      section.style.display = 'none';
      return;
    }

    section.style.display = '';
    badge.style.display = grooming.pending ? 'inline-block' : 'none';

    let actionHtml = '';
    if (grooming.action) {
      actionHtml += '<span class="grooming-action ' + grooming.action + '">' +
        (grooming.action_label || grooming.action) + '</span> ';
    }
    if (mergeHint) {
      actionHtml += '<span class="grooming-action merge">合并 → ' + (mergeHint.target_id || '').slice(0, 8) + '...</span>';
    }
    actionEl.innerHTML = actionHtml;

    const reasonParts = [];
    if (grooming.reason) reasonParts.push(grooming.reason);
    if (mergeHint && mergeHint.reason) reasonParts.push('[当次合并] ' + mergeHint.reason);
    reasonEl.textContent = reasonParts.join('\\n\\n') || '暂无建议说明';

    const metaParts = [];
    if (grooming.at) metaParts.push('建议时间: ' + grooming.at);
    if (grooming.target_category) metaParts.push('升类目标: ' + grooming.target_category);
    if (mergeHintsPayload.generated_at) metaParts.push('合并报告: ' + mergeHintsPayload.generated_at);
    metaEl.textContent = metaParts.join(' · ');

    actionsEl.innerHTML = '';
    if (grooming.pending) {
      actionsEl.innerHTML += '<button class="btn-grooming-confirm" onclick="confirmGroomingKeep()">确认保留</button>';
    }
    if (grooming.action === 'promote' && grooming.target_category) {
      actionsEl.innerHTML += '<button class="btn-grooming-promote" onclick="applyGroomingPromote()">采纳升类</button>';
    }
    if (grooming.action === 'delete') {
      actionsEl.innerHTML += '<button class="btn-grooming-delete" onclick="applyGroomingDelete()">采纳删除</button>';
    }
    if (mergeHint && mergeHint.target_id) {
      actionsEl.innerHTML += '<button class="btn-grooming-merge" onclick="applyGroomingMerge()">合并（重校验）</button>';
      actionsEl.innerHTML += '<button class="btn-grooming-confirm" onclick="openMergeCompare(selectedId)">对照查看</button>';
      actionsEl.innerHTML += '<button class="btn-grooming-confirm" onclick="jumpToMemory(\\'' + mergeHint.target_id + '\\')">定位目标</button>';
    }
  }

  function countPendingGrooming() {
    return Object.values(memoriesMap).filter(mem => mem.grooming && mem.grooming.pending).length;
  }

  function computeGroomingCounts() {
    const counts = { review: 0, pending: 0, delete: 0, promote: 0, keep: 0, merge: 0 };
    Object.values(memoriesMap).forEach(mem => {
      const g = mem.grooming || {};
      const hint = mem.merge_hint;
      const action = g.action || '';
      if (g.pending) counts.pending += 1;
      if (g.pending || action === 'delete' || action === 'promote' || hint) counts.review += 1;
      if (action === 'delete' || action === 'promote' || action === 'keep') counts[action] += 1;
      if (hint) counts.merge += 1;
    });
    return counts;
  }

  function updateGroomingFilterLabel() {
    const select = document.getElementById('grooming-filter');
    const counts = computeGroomingCounts();
    const labels = {
      review: '待处理',
      pending: '待确认 pending',
      delete: '建议删除',
      promote: '建议升类',
      merge: '建议合并',
      keep: '建议保留',
    };
    Object.keys(labels).forEach(value => {
      const option = select.querySelector('option[value="' + value + '"]');
      if (option) option.textContent = labels[value] + ' (' + counts[value] + ')';
    });
  }

  function confirmGroomingKeep() {
    if (!selectedId) return;
    const btn = document.querySelector('#grooming-actions .btn-grooming-confirm');
    if (btn) {
      btn.disabled = true;
      btn.textContent = '确认中...';
    }
    fetch('/api/grooming/confirm/' + encodeURIComponent(selectedId), { method: 'POST' })
      .then(r => r.json())
      .then(result => {
        if (!result.ok) {
          showToast('确认失败: ' + (result.error || '未知错误'), 'error');
          if (btn) {
            btn.disabled = false;
            btn.textContent = '确认保留';
          }
          return;
        }
        const mem = memoriesMap[selectedId];
        if (mem) {
          mem.grooming = result.grooming || mem.grooming || {};
          mem.grooming.pending = false;
          const bg = originalNodeColors[selectedId] || '#95a5a6';
          nodes.update({
            id: selectedId,
            borderWidth: 1,
            color: { background: bg, border: bg, highlight: { background: bg, border: '#f39c12' } },
          });
        }
        syncGroomingUi();
        if (mem) renderGroomingPanel(mem);
        showToast('已确认保留', 'success');
      })
      .catch(err => {
        console.error('confirm grooming failed', err);
        showToast('确认失败，请重试', 'error');
        if (btn) {
          btn.disabled = false;
          btn.textContent = '确认保留';
        }
      });
  }

  function applyGroomingPromote() {
    if (!selectedId) return;
    if (!confirm('确认采纳升类建议？')) return;
    fetch('/api/grooming/promote/' + encodeURIComponent(selectedId), { method: 'POST' })
      .then(r => r.json())
      .then(result => {
        if (!result.ok) {
          showToast('升类失败: ' + (result.error || '未知错误'), 'error');
          return;
        }
        const mem = memoriesMap[selectedId];
        if (mem) {
          mem.category = result.category || mem.category;
          mem.category_label = result.category_label || mem.category_label;
          mem.category_raw = result.category_raw || '';
          mem.grooming = { pending: false, action: '', action_label: '', reason: '', target_category: '', at: mem.grooming && mem.grooming.at || '' };
          const nodeColor = {{ category_colors_json | safe }}[mem.category] || '{{ global_color }}';
          nodes.update({
            id: selectedId,
            color: { background: nodeColor, border: nodeColor, highlight: { background: nodeColor, border: '#f39c12' } },
            borderWidth: 1,
          });
          originalNodeColors[selectedId] = nodeColor;
          renderGroomingPanel(mem);
          showDetail(selectedId);
        }
        syncGroomingUi();
        showToast('已升类为 ' + (result.category_label || result.category), 'success');
      })
      .catch(err => {
        console.error('promote failed', err);
        showToast('升类失败，请重试', 'error');
      });
  }

  function removeMemoryFromView(id) {
    deletedNodeIds.add(id);
    nodes.remove(id);
    const relatedEdges = edges.getIds().filter(eid => {
      const edge = edges.get(eid);
      return edge.from === id || edge.to === id;
    });
    edges.remove(relatedEdges);
    delete memoriesMap[id];
    hideDetail();
    updateStats();
    renderMemoryList();
  }

  function applyGroomingDelete() {
    if (!selectedId) return;
    const mem = memoriesMap[selectedId];
    const aiReason = (mem && mem.grooming && mem.grooming.reason)
      ? ('采纳 AI 删除建议: ' + mem.grooming.reason)
      : '采纳 AI 删除建议';
    const input = prompt('删除原因（必填，可修改）：', aiReason);
    if (input === null) return;
    const reason = input.trim();
    if (!reason) {
      showToast('必须填写删除原因', 'error');
      return;
    }
    if (!confirm('确认删除此记忆？\\n' + reason)) return;
    fetch('/delete/' + selectedId, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ reason: reason }),
    })
      .then(r => r.json())
      .then(result => {
        if (result.ok) {
          removeMemoryFromView(selectedId);
          syncGroomingUi();
        } else {
          showToast('删除失败: ' + result.error, 'error');
        }
      });
  }

  function applyGroomingMerge() {
    if (!selectedId) return;
    const mem = memoriesMap[selectedId];
    const targetId = mem && mem.merge_hint ? mem.merge_hint.target_id : '';
    if (!confirm('合并前将重新校验目标，确认继续？')) return;
    fetch('/api/grooming/merge/' + encodeURIComponent(selectedId), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ target_id: targetId }),
    })
      .then(r => r.json())
      .then(result => {
        if (!result.ok) {
          showToast('合并失败: ' + (result.error || '未知错误'), 'error');
          return;
        }
        const srcId = result.source_id || selectedId;
        const tgtMem = memoriesMap[result.target_id];
        if (tgtMem) {
          tgtMem.merge_hint = null;
          tgtMem.grooming = tgtMem.grooming || {};
          tgtMem.grooming.pending = false;
        }
        removeMemoryFromView(srcId);
        syncGroomingUi();
        showToast('已合并至 ' + String(result.target_id).slice(0, 8) + '…' + (result.target_changed ? '（目标已变更）' : ''), 'success');
        if (tgtMem) jumpToMemory(result.target_id);
      })
      .catch(err => {
        console.error('merge failed', err);
        showToast('合并失败，请重试', 'error');
      });
  }

  function openMergeCompare(sourceId) {
    const src = memoriesMap[sourceId];
    if (!src) return;
    const hint = src.merge_hint;
    if (!hint || !hint.target_id) {
      showToast('该记忆无合并建议', 'info');
      return;
    }
    const target = memoriesMap[hint.target_id];
    const panel = document.getElementById('merge-compare-panel');
    document.getElementById('merge-compare-title').textContent =
      '合并对照 · overlap=' + (hint.overlap != null ? Number(hint.overlap).toFixed(2) : '?');
    document.getElementById('merge-compare-body').innerHTML =
      '<div class="review-compare">' +
        '<div class="review-compare-col">' +
          '<div class="review-compare-label"><b>源（将被删除）</b> ' + sourceId.slice(0, 8) + '…</div>' +
          '<div class="review-compare-text">' + escapeHtml(src.text) + '</div>' +
        '</div>' +
        '<div class="review-compare-col">' +
          '<div class="review-compare-label"><b>目标（保留）</b> ' + (hint.target_id || '').slice(0, 8) + '…' +
          (target ? '' : '（不在当前图谱，可能已删除）') + '</div>' +
          '<div class="review-compare-text">' + (target ? escapeHtml(target.text) : '（无法加载目标正文）') + '</div>' +
        '</div>' +
      '</div>' +
      (hint.reason ? '<div class="review-reason">AI: ' + escapeHtml(hint.reason) + '</div>' : '');
    const foot = document.getElementById('merge-compare-foot');
    foot.innerHTML =
      '<button class="btn-grooming-confirm" onclick="closeMergeCompare();jumpToMemory(\\'' + hint.target_id + '\\')">定位目标</button>' +
      '<button class="rv-merge" onclick="closeMergeCompare();applyGroomingMergeFrom(\\'' + sourceId + '\\')">合并（重校验）</button>';
    panel.classList.add('visible');
  }

  function closeMergeCompare() {
    document.getElementById('merge-compare-panel').classList.remove('visible');
  }

  function onMergeCompareBackdrop(event) {
    if (event.target.id === 'merge-compare-panel') closeMergeCompare();
  }

  function applyGroomingMergeFrom(sourceId) {
    const prev = selectedId;
    selectedId = sourceId;
    applyGroomingMerge();
    selectedId = prev;
  }

  function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text == null ? '' : String(text);
    return div.innerHTML;
  }

  function reviewQueueIds() {
    return Object.values(memoriesMap)
      .filter(mem => {
        const g = mem.grooming || {};
        return g.pending || g.action === 'delete' || g.action === 'promote' || mem.merge_hint;
      })
      .sort((a, b) => {
        const rank = mem => {
          const g = mem.grooming || {};
          if (g.action === 'delete') return 0;
          if (mem.merge_hint) return 1;
          if (g.action === 'promote') return 2;
          return 3;
        };
        return rank(a) - rank(b) || String(a.created_at || '').localeCompare(String(b.created_at || ''));
      })
      .map(mem => mem.id);
  }

  let reviewQueue = [];
  let reviewIndex = 0;

  function openReview() {
    reviewQueue = reviewQueueIds();
    if (!reviewQueue.length) {
      showToast('当前无待处理记忆', 'info');
      return;
    }
    reviewIndex = 0;
    document.getElementById('review-panel').classList.add('visible');
    renderReviewCard();
  }

  function closeReview() {
    document.getElementById('review-panel').classList.remove('visible');
    reviewQueue = [];
    reviewIndex = 0;
  }

  function renderReviewCard() {
    reviewQueue = reviewQueue.filter(id => memoriesMap[id]);
    if (reviewIndex >= reviewQueue.length) {
      document.getElementById('review-body').innerHTML =
        '<div style="text-align:center;padding:40px 0;color:#2ecc71;font-size:15px">✓ 队列已清空</div>';
      document.getElementById('review-foot').innerHTML =
        '<span class="spacer"></span><button class="rv-skip" onclick="closeReview()">关闭</button>';
      document.getElementById('review-progress').textContent = '';
      document.getElementById('review-title').textContent = '';
      syncGroomingUi();
      return;
    }

    const id = reviewQueue[reviewIndex];
    const mem = memoriesMap[id];
    const g = mem.grooming || {};
    const hint = mem.merge_hint || null;

    document.getElementById('review-progress').textContent =
      (reviewIndex + 1) + ' / ' + reviewQueue.length;
    document.getElementById('review-title').textContent =
      '[' + (mem.project || '全局') + '] ' + (mem.category || 'episodic');

    let html = '<div class="review-meta">' + id + ' · 创建: ' + (mem.created_at || '未知');
    if (g.action) html += ' · AI: <span class="grooming-action ' + g.action + '">' + (g.action_label || g.action) + '</span>';
    if (g.pending) html += ' <span class="grooming-pending-badge">待确认</span>';
    html += '</div>';
    html += '<div class="review-text-box">' + escapeHtml(mem.text) + '</div>';
    if (g.reason) html += '<div class="review-reason">AI 建议: ' + escapeHtml(g.reason) + '</div>';
    if (g.action === 'promote' && g.target_category) {
      html += '<div class="review-meta">升类目标: ' + escapeHtml(g.target_category) + '</div>';
    }

    const needCompare = (hint && hint.target_id) || (g.action === 'delete');
    if (needCompare) {
      const compareId = hint && hint.target_id ? hint.target_id : '';
      const target = compareId ? memoriesMap[compareId] : null;
      html += '<div class="review-compare">' +
        '<div class="review-compare-col">' +
          '<div class="review-compare-label"><b>' + (hint ? '源（合并将删除）' : '本条') + '</b> ' + id.slice(0, 8) + '…</div>' +
          '<div class="review-compare-text">' + escapeHtml(mem.text) + '</div>' +
        '</div>';
      if (hint && hint.target_id) {
        html += '<div class="review-compare-col">' +
          '<div class="review-compare-label"><b>目标（保留）</b> ' + hint.target_id.slice(0, 8) + '…' +
          (target ? '' : '（不在图谱，可能已删除）') + '</div>' +
          '<div class="review-compare-text">' + (target ? escapeHtml(target.text) : '（无法加载）') + '</div>' +
        '</div>';
      } else {
        html += '<div class="review-compare-col">' +
          '<div class="review-compare-label"><b>AI 删除理由</b></div>' +
          '<div class="review-compare-text">' + escapeHtml(g.reason || '无') + '</div>' +
        '</div>';
      }
      html += '</div>';
      if (hint && hint.reason) html += '<div class="review-reason">合并建议: ' + escapeHtml(hint.reason) + '</div>';
    }

    document.getElementById('review-body').innerHTML = html;

    const foot = document.getElementById('review-foot');
    let footHtml = '';
    if (g.pending) footHtml += '<button class="rv-keep" onclick="reviewKeep()">确认保留</button>';
    if (g.action === 'promote' && g.target_category) footHtml += '<button class="rv-promote" onclick="reviewPromote()">采纳升类</button>';
    if (g.action === 'delete') footHtml += '<button class="rv-delete" onclick="reviewDelete()">采纳删除</button>';
    if (hint && hint.target_id) {
      footHtml += '<button class="rv-merge" onclick="reviewMerge()">合并（重校验）</button>';
      footHtml += '<button class="rv-skip" onclick="closeReview();jumpToMemory(\\'' + hint.target_id + '\\')">定位目标</button>';
    }
    footHtml += '<span class="spacer"></span>';
    footHtml += '<button class="rv-skip" onclick="reviewSkip()">跳过</button>';
    foot.innerHTML = footHtml;
  }

  function reviewAdvance() {
    reviewIndex += 1;
    renderReviewCard();
  }

  function reviewSkip() {
    reviewAdvance();
  }

  function reviewKeep() {
    const id = reviewQueue[reviewIndex];
    fetch('/api/grooming/confirm/' + encodeURIComponent(id), { method: 'POST' })
      .then(r => r.json())
      .then(result => {
        if (!result.ok) {
          showToast('确认失败: ' + (result.error || '未知错误'), 'error');
          return;
        }
        const mem = memoriesMap[id];
        if (mem) {
          mem.grooming = result.grooming || mem.grooming || {};
          mem.grooming.pending = false;
          const bg = originalNodeColors[id] || '#95a5a6';
          nodes.update({ id: id, borderWidth: 1, color: { background: bg, border: bg, highlight: { background: bg, border: '#f39c12' } } });
        }
        showToast('已确认保留', 'success');
        reviewAdvance();
      })
      .catch(() => showToast('确认失败，请重试', 'error'));
  }

  function reviewPromote() {
    const id = reviewQueue[reviewIndex];
    fetch('/api/grooming/promote/' + encodeURIComponent(id), { method: 'POST' })
      .then(r => r.json())
      .then(result => {
        if (!result.ok) {
          showToast('升类失败: ' + (result.error || '未知错误'), 'error');
          return;
        }
        const mem = memoriesMap[id];
        if (mem) {
          mem.category = result.category || mem.category;
          mem.category_label = result.category_label || mem.category_label;
          mem.category_raw = result.category_raw || '';
          mem.grooming = { pending: false, action: '', action_label: '', reason: '', target_category: '', at: '' };
          const nodeColor = {{ category_colors_json | safe }}[mem.category] || '{{ global_color }}';
          nodes.update({ id: id, color: { background: nodeColor, border: nodeColor, highlight: { background: nodeColor, border: '#f39c12' } }, borderWidth: 1 });
          originalNodeColors[id] = nodeColor;
        }
        showToast('已升类为 ' + (result.category_label || result.category), 'success');
        reviewAdvance();
      })
      .catch(() => showToast('升类失败，请重试', 'error'));
  }

  function reviewDelete() {
    const id = reviewQueue[reviewIndex];
    const mem = memoriesMap[id];
    const aiReason = (mem && mem.grooming && mem.grooming.reason) ? ('采纳 AI 删除建议: ' + mem.grooming.reason) : '采纳 AI 删除建议';
    const input = prompt('删除原因（必填，可修改）：', aiReason);
    if (input === null) return;
    const reason = input.trim();
    if (!reason) {
      showToast('必须填写删除原因', 'error');
      return;
    }
    if (!confirm('确认删除？\\n' + reason)) return;
    fetch('/delete/' + encodeURIComponent(id), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ reason: reason }),
    })
      .then(r => r.json())
      .then(result => {
        if (!result.ok) {
          showToast('删除失败: ' + result.error, 'error');
          return;
        }
        removeMemoryFromView(id);
        showToast('已删除', 'success');
        reviewAdvance();
      })
      .catch(() => showToast('删除失败，请重试', 'error'));
  }

  function reviewMerge() {
    const id = reviewQueue[reviewIndex];
    const mem = memoriesMap[id];
    const targetId = mem && mem.merge_hint ? mem.merge_hint.target_id : '';
    if (!confirm('合并前将重新校验目标，确认继续？')) return;
    fetch('/api/grooming/merge/' + encodeURIComponent(id), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ target_id: targetId }),
    })
      .then(r => r.json())
      .then(result => {
        if (!result.ok) {
          showToast('合并失败: ' + (result.error || '未知错误'), 'error');
          return;
        }
        removeMemoryFromView(result.source_id || id);
        const tgtMem = memoriesMap[result.target_id];
        if (tgtMem) {
          tgtMem.merge_hint = null;
          tgtMem.grooming = tgtMem.grooming || {};
          tgtMem.grooming.pending = false;
        }
        showToast('已合并至 ' + String(result.target_id).slice(0, 8) + '…', 'success');
        reviewAdvance();
      })
      .catch(() => showToast('合并失败，请重试', 'error'));
  }

  document.addEventListener('keydown', function(e) {
    if (e.key !== 'Escape') return;
    const mergePanel = document.getElementById('merge-compare-panel');
    if (mergePanel.classList.contains('visible')) {
      closeMergeCompare();
      return;
    }
    const reviewPanel = document.getElementById('review-panel');
    if (reviewPanel.classList.contains('visible')) {
      closeReview();
    }
  });

  function actionLabel(action) {
    const labels = {
      ADD: '创建',
      UPDATE: '内容修正',
      DELETE: '删除',
      DEDUP_DROP: '去重删除',
      CATEGORY_CHANGE: '分类变更',
      GROOMING: '梳理',
    };
    return labels[action] || action;
  }

  function renderTimelineEvents(container, events) {
    container.innerHTML = '';
    if (!events || events.length === 0) {
      container.innerHTML = '<li class="timeline-item"><div class="tl-body">暂无演变记录</div></li>';
      return;
    }
    events.forEach(ev => {
      const li = document.createElement('li');
      li.className = 'timeline-item';
      const sources = (ev.source_ids || []).filter(Boolean);
      const sourceText = sources.length ? ('来源: ' + sources.join(', ')) : '';
      const targetText = ev.target_id ? ('保留: ' + ev.target_id) : '';
      li.innerHTML =
        '<div class="tl-head">' + (ev.ts || '未知时间') + ' · ' + actionLabel(ev.action) +
        (ev.origin ? ' (' + ev.origin + ')' : '') + '</div>' +
        '<div class="tl-body">' +
        (ev.note ? ev.note + '\\n' : '') +
        (sourceText ? sourceText + '\\n' : '') +
        (targetText ? targetText + '\\n' : '') +
        (ev.content_preview || '') +
        '</div>';
      container.appendChild(li);
    });
  }

  function renderMergeSourceNode(source, nested) {
    const wrap = document.createElement('div');
    wrap.className = 'merge-source-node' + (nested ? ' nested' : '');

    const status = source.status || 'missing';
    const badgeClass = ['active', 'deleted', 'history', 'missing'].includes(status) ? status : 'missing';
    const badgeLabel = {
      active: '活跃',
      deleted: '已删除',
      history: '历史预览',
      missing: '缺失',
    }[status] || '缺失';

    const head = document.createElement('div');
    head.className = 'merge-source-head';
    let headHtml =
      '<span class="merge-source-badge ' + badgeClass + '">' + badgeLabel + '</span>' +
      '<span class="merge-source-id">' + (source.id || '').slice(0, 8) + '...</span>';
    if (status === 'active' && memoriesMap[source.id]) {
      headHtml += '<button type="button" class="merge-source-locate" data-id="' + source.id + '">在图谱定位</button>';
    }
    head.innerHTML = headHtml;

    const metaParts = [];
    if (source.project) metaParts.push('[' + source.project + ']');
    if (source.category) metaParts.push(source.category);
    if (source.deleted_at) metaParts.push('删除: ' + source.deleted_at);
    if (source.reason) metaParts.push('原因: ' + source.reason);
    if (source.note) metaParts.push(source.note);
    if (metaParts.length) {
      const meta = document.createElement('div');
      meta.className = 'merge-source-meta';
      meta.textContent = metaParts.join(' · ');
      head.appendChild(meta);
    }

    const locateBtn = head.querySelector('.merge-source-locate');
    if (locateBtn) {
      locateBtn.addEventListener('click', function() {
        jumpToMemory(locateBtn.getAttribute('data-id'));
      });
    }

    const body = document.createElement('div');
    body.className = 'merge-source-body';
    body.textContent = source.content || '(无正文记录)';

    wrap.appendChild(head);
    wrap.appendChild(body);

    const children = source.sources || [];
    if (children.length) {
      const childWrap = document.createElement('div');
      childWrap.className = 'merge-source-children';
      children.forEach(child => childWrap.appendChild(renderMergeSourceNode(child, true)));
      wrap.appendChild(childWrap);
    }
    return wrap;
  }

  function renderMergeSources(container, sources) {
    container.innerHTML = '';
    if (!sources || !sources.length) {
      container.innerHTML = '<div class="merge-sources-empty">合并来源: 无</div>';
      return;
    }
    const title = document.createElement('div');
    title.className = 'merge-sources-title';
    title.textContent = '合并来源 (' + sources.length + ' 条直接来源)';
    container.appendChild(title);
    sources.forEach(source => container.appendChild(renderMergeSourceNode(source, false)));
  }

  function loadTimeline(id) {
    const timelineEl = document.getElementById('detail-timeline');
    const ancestorsEl = document.getElementById('detail-ancestors');
    timelineEl.innerHTML = '<li class="timeline-item"><div class="tl-body">加载中...</div></li>';
    ancestorsEl.innerHTML = '<div class="merge-sources-empty">加载中...</div>';
    fetch('/api/timeline/' + encodeURIComponent(id))
      .then(r => r.json())
      .then(data => {
        renderTimelineEvents(timelineEl, data.events || []);
        renderMergeSources(ancestorsEl, data.merge_sources || []);
      })
      .catch(err => {
        console.error('timeline failed', err);
        timelineEl.innerHTML = '<li class="timeline-item"><div class="tl-body">时间线加载失败</div></li>';
        ancestorsEl.innerHTML = '<div class="merge-sources-empty">合并来源加载失败</div>';
      });
  }

  function jumpToMemory(id) {
    if (!memoriesMap[id]) {
      return;
    }
    selectedId = id;
    showDetail(id);
    network.selectNodes([id]);
    network.focus(id, { scale: 1.2, animation: true });
  }

  function hideDetail() {
    selectedId = null;
    cancelEditContent();
    document.getElementById('detail-panel').classList.remove('visible');
  }

  let similarCheckTimer = null;
  let contentEditing = false;

  function scheduleSimilarCheck() {
    clearTimeout(similarCheckTimer);
    similarCheckTimer = setTimeout(refreshSimilarWarn, 400);
  }

  function refreshSimilarWarn() {
    const content = document.getElementById('add-content').value.trim();
    const panel = document.getElementById('add-similar');
    if (!content) {
      panel.classList.remove('visible');
      panel.innerHTML = '';
      return;
    }
    const project = document.getElementById('add-project').value.trim();
    let url = '/api/similar?q=' + encodeURIComponent(content);
    if (project) {
      url += '&project=' + encodeURIComponent(project);
    }
    fetch(url)
      .then(r => r.json())
      .then(payload => {
        const results = payload.results || [];
        if (!results.length) {
          panel.classList.remove('visible');
          panel.innerHTML = '';
          return;
        }
        panel.classList.add('visible');
        panel.innerHTML = '<div>发现 ' + results.length + ' 条相似记忆（保存前请确认是否重复）：</div>' +
          results.map(item =>
            '<div class="similar-item" onclick="jumpToMemory(\\'' + item.id + '\\')">#' +
            item.id.slice(0, 8) + '... score=' + Number(item.score).toFixed(2) + ' · ' + item.text + '</div>'
          ).join('');
      })
      .catch(() => {
        panel.classList.remove('visible');
      });
  }

  function openAddModal() {
    document.getElementById('add-content').value = '';
    document.getElementById('add-project').value = '';
    document.getElementById('add-category').value = 'episodic';
    document.getElementById('add-similar').classList.remove('visible');
    document.getElementById('add-similar').innerHTML = '';
    document.getElementById('add-modal').classList.add('visible');
    document.getElementById('add-content').focus();
  }

  function closeAddModal() {
    document.getElementById('add-modal').classList.remove('visible');
  }

  function onAddModalBackdrop(event) {
    if (event.target.id === 'add-modal') {
      closeAddModal();
    }
  }

  function saveNewMemory() {
    const content = document.getElementById('add-content').value.trim();
    if (!content) {
      alert('请填写正文');
      return;
    }
    const project = document.getElementById('add-project').value.trim();
    const category = document.getElementById('add-category').value;
    const btn = document.getElementById('btn-add-save');
    btn.disabled = true;
    btn.textContent = '保存中...';
    fetch('/api/add', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content, project, category }),
    })
      .then(r => r.json())
      .then(result => {
        btn.disabled = false;
        btn.textContent = '保存';
        if (!result.ok) {
          alert('保存失败: ' + (result.error || '未知错误'));
          return;
        }
        closeAddModal();
        location.reload();
      })
      .catch(err => {
        btn.disabled = false;
        btn.textContent = '保存';
        console.error('add failed', err);
        alert('保存失败，请重试');
      });
  }

  function startEditContent() {
    if (!selectedId) return;
    const mem = memoriesMap[selectedId];
    if (!mem) return;
    contentEditing = true;
    document.getElementById('detail-text').style.display = 'none';
    const textarea = document.getElementById('edit-content');
    textarea.value = mem.text;
    textarea.classList.add('visible');
    document.getElementById('btn-edit-content').style.display = 'none';
    document.getElementById('btn-save-content').classList.add('visible');
    document.getElementById('btn-cancel-content').classList.add('visible');
  }

  function cancelEditContent() {
    contentEditing = false;
    document.getElementById('detail-text').style.display = '';
    const textarea = document.getElementById('edit-content');
    textarea.classList.remove('visible');
    textarea.value = '';
    document.getElementById('btn-edit-content').style.display = '';
    document.getElementById('btn-save-content').classList.remove('visible');
    document.getElementById('btn-cancel-content').classList.remove('visible');
  }

  function saveContent() {
    if (!selectedId) return;
    const mem = memoriesMap[selectedId];
    if (!mem) return;
    const content = document.getElementById('edit-content').value.trim();
    if (!content) {
      alert('正文不能为空');
      return;
    }
    if (content === mem.text) {
      cancelEditContent();
      return;
    }
    const btn = document.getElementById('btn-save-content');
    btn.disabled = true;
    btn.textContent = '保存中...';
    fetch('/api/update/' + encodeURIComponent(selectedId), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content }),
    })
      .then(r => r.json())
      .then(result => {
        btn.disabled = false;
        btn.textContent = '保存正文';
        if (!result.ok) {
          alert('保存失败: ' + (result.error || '未知错误'));
          return;
        }
        mem.text = result.content || content;
        document.getElementById('detail-text').textContent = mem.text;
        nodes.update({
          id: selectedId,
          label: mem.text.slice(0, 30) + (mem.text.length > 30 ? '...' : ''),
          title: (mem.project ? '[' + mem.project + '] ' : '[全局] ') +
            mem.text.slice(0, 60) + (mem.text.length > 60 ? '...' : ''),
        });
        cancelEditContent();
        loadTimeline(selectedId);
      })
      .catch(err => {
        btn.disabled = false;
        btn.textContent = '保存正文';
        console.error('save content failed', err);
        alert('保存失败，请重试');
      });
  }

  function getFilters() {
    return {
      project: document.getElementById('project-filter').value,
      category: document.getElementById('category-filter').value,
      grooming: document.getElementById('grooming-filter').value,
    };
  }

  function passesFilters(mem, filters) {
    if (!mem) return false;
    if (filters.project && mem.project !== filters.project) return false;
    if (filters.category && mem.category !== filters.category) return false;
    if (filters.grooming) {
      const g = mem.grooming || {};
      const hint = mem.merge_hint;
      const action = g.action || '';
      if (filters.grooming === 'review') {
        if (!(g.pending || action === 'delete' || action === 'promote' || hint)) return false;
      } else if (filters.grooming === 'pending') {
        if (!g.pending) return false;
      } else if (filters.grooming === 'merge') {
        if (!hint) return false;
      } else if (action !== filters.grooming) {
        return false;
      }
    }
    return true;
  }

  function renderSearchResults(payload) {
    const panel = document.getElementById('search-results');
    const head = document.getElementById('search-results-head');
    const list = document.getElementById('search-results-list');
    lastSearchResults = payload.results || [];
    lastSearchPayload = payload;
    if (!lastSearchResults.length) {
      panel.classList.remove('visible');
      list.innerHTML = '';
      return;
    }

    const filters = getFilters();
    const scope = payload.effective_project
      ? ('项目作用域: ' + payload.effective_project)
      : '项目作用域: 全局（未 detect 到项目，与 MCP project=\"\" 且 cwd 无项目时一致）';
    head.textContent =
      '混合检索 Top ' + lastSearchResults.length +
      '（MCP 同算法 hybrid_search，max=' + payload.max_results + '）· ' + scope +
      ' · 比对 MCP 请用相同 query + project';

    list.innerHTML = '';
    lastSearchResults.forEach((item, index) => {
      const mem = memoriesMap[item.id];
      const filteredOut = mem && !passesFilters(mem, filters);
      const row = document.createElement('div');
      row.className = 'search-result-item' + (filteredOut ? ' dimmed' : '');
      const scopeTag = item.project ? ('[' + item.project + ']') : '[全局]';
      row.innerHTML =
        '<div class="search-result-rank">#' + (index + 1) + '</div>' +
        '<div class="search-result-score">score=' + Number(item.score).toFixed(2) + '<br>(' + (item.source || 'unknown') + ')</div>' +
        '<div class="search-result-text">' +
          item.text +
          '<div class="search-result-meta">' + scopeTag + ' · ' + item.id +
          (filteredOut ? ' · 已被项目/分类筛选隐藏' : '') +
          '</div>' +
        '</div>';
      row.onclick = () => focusSearchResult(item.id);
      list.appendChild(row);
    });
    panel.classList.add('visible');
  }

  function focusSearchResult(id) {
    if (!memoriesMap[id] || deletedNodeIds.has(id)) {
      alert('该结果不在当前图谱中（可能已删除）');
      return;
    }
    selectedId = id;
    showDetail(id);
    network.selectNodes([id]);
    network.focus(id, { scale: 1.2, animation: true });
  }

  function applyGraphVisibility(matchSet, searchResults) {
    const filters = getFilters();
    const maxScore = (searchResults && searchResults.length)
      ? Math.max(...searchResults.map(item => Number(item.score) || 0), 1)
      : 1;

    nodes.update(nodesData.filter(n => !deletedNodeIds.has(n.id)).map(n => {
      const mem = memoriesMap[n.id];
      const passes = passesFilters(mem, filters);
      const inSearch = matchSet ? matchSet.has(n.id) : true;
      const result = (searchResults || []).find(item => item.id === n.id);
      const scoreRatio = result ? (Number(result.score) || 0) / maxScore : 0.15;
      const dimmed = matchSet ? !matchSet.has(n.id) : false;
      return {
        ...n,
        hidden: matchSet ? (!passes || !inSearch) : !passes,
        opacity: !passes ? 0 : (matchSet ? (dimmed ? 0.12 : Math.max(0.35, scoreRatio)) : 1.0),
        borderWidth: result ? 2 : 1,
      };
    }));

    if (matchSet) {
      const edgeIds = edges.getIds();
      const edgeUpdates = edgeIds.map(eid => {
        const edge = edges.get(eid);
        const bothMatch = matchSet.has(edge.from) && matchSet.has(edge.to);
        return {
          id: eid,
          color: bothMatch ? { color: '#3498db', highlight: '#3498db' } : { color: '#1a1a2e', highlight: '#1a1a2e' },
          opacity: bothMatch ? 1.0 : 0.08,
        };
      });
      edges.update(edgeUpdates);
    }
  }

  function doSearch() {
    const query = document.getElementById('search-input').value.trim();
    syncUrlWithFilters();
    if (!query) {
      document.getElementById('search-results').classList.remove('visible');
      applyGraphVisibility(null, []);
      updateStats();
      renderMemoryList();
      return;
    }
    const projectFilter = document.getElementById('project-filter').value;
    let url = '/search?q=' + encodeURIComponent(query);
    if (projectFilter) {
      url += '&project=' + encodeURIComponent(projectFilter);
    } else {
      // 全部项目 = 全局检索（project 空），与 MCP 显式传 project="" 且 detect 不到项目时一致
      url += '&project=';
    }
    fetch(url)
      .then(r => r.json())
      .then(payload => {
        lastSearchPayload = payload;
        const results = payload.results || [];
        const matchSet = new Set(results.map(item => item.id));
        renderSearchResults(payload);
        applyGraphVisibility(matchSet, results);
        renderMemoryList();
        const visibleCount = nodes.get().filter(n => !n.hidden).length;
        document.getElementById('stats').innerHTML =
          '检索 ' + results.length + ' 条 | 图谱可见 ' + visibleCount + ' 节点' +
          ' <span class="legend">边框加粗=命中项 | 亮度≈score</span>';
      })
      .catch(err => {
        console.error('Search failed:', err);
        document.getElementById('stats').textContent = '搜索失败，请重试';
      });
  }

  function onFilterChange() {
    const query = document.getElementById('search-input').value.trim();
    if (query) {
      if (lastSearchPayload && lastSearchResults.length) {
        renderSearchResults(lastSearchPayload);
        applyGraphVisibility(new Set(lastSearchResults.map(item => item.id)), lastSearchResults);
      } else {
        doSearch();
      }
    } else {
      lastSearchPayload = null;
      lastSearchResults = [];
      document.getElementById('search-results').classList.remove('visible');
      applyGraphVisibility(null, []);
      updateStats();
    }
    renderMemoryList();
    syncUrlWithFilters();
  }

  document.getElementById('project-filter').addEventListener('change', onFilterChange);
  document.getElementById('category-filter').addEventListener('change', onFilterChange);
  document.getElementById('grooming-filter').addEventListener('change', onFilterChange);

  function getUrlParams() {
    const params = new URLSearchParams(window.location.search);
    const out = {};
    ['project', 'category', 'grooming', 'q', 'list'].forEach(key => {
      const value = params.get(key);
      if (value) out[key] = value;
    });
    return out;
  }

  function syncUrlWithFilters() {
    const params = new URLSearchParams();
    const project = document.getElementById('project-filter').value;
    const category = document.getElementById('category-filter').value;
    const grooming = document.getElementById('grooming-filter').value;
    const q = document.getElementById('search-input').value.trim();
    if (project) params.set('project', project);
    if (category) params.set('category', category);
    if (grooming) params.set('grooming', grooming);
    if (q) params.set('q', q);
    if (memoryListCollapsed) params.set('list', '0');
    const queryString = params.toString();
    history.replaceState(null, '', queryString ? ('?' + queryString) : window.location.pathname);
  }

  function memoryListItems() {
    const filters = getFilters();
    let items = Object.values(memoriesMap).filter(mem => passesFilters(mem, filters));
    if (lastSearchPayload) {
      const matchSet = new Set(lastSearchResults.map(item => item.id));
      items = items.filter(mem => matchSet.has(mem.id));
    }
    return items.sort((a, b) => String(a.created_at || '').localeCompare(String(b.created_at || '')));
  }

  function groomingActionBadgeHtml(mem) {
    const grooming = mem.grooming || {};
    const hint = mem.merge_hint || null;
    let html = '';
    if (grooming.action) {
      html += '<span class="grooming-action ' + grooming.action + '">' + (grooming.action_label || grooming.action) + '</span>';
    }
    if (hint && hint.target_id) {
      html += '<span class="grooming-action merge" style="cursor:pointer" title="点击查看合并对照" onclick="event.stopPropagation();openMergeCompare(\\'' + mem.id + '\\')">合并 → ' + hint.target_id.slice(0, 8) + '...</span>';
    }
    if (!grooming.action && !hint && grooming.pending) {
      html += '<span class="grooming-pending-badge">待确认</span>';
    }
    return html;
  }

  function renderMemoryList() {
    const panel = document.getElementById('memory-list');
    const body = document.getElementById('memory-list-body');
    panel.classList.toggle('collapsed', memoryListCollapsed);
    document.getElementById('memory-list-toggle').textContent = memoryListCollapsed ? '▸' : '▾';
    const items = memoryListItems();
    document.getElementById('memory-list-title').textContent = '记忆列表 (' + items.length + ')';
    updateBatchBar(items);
    if (memoryListCollapsed) {
      body.innerHTML = '';
      return;
    }
    if (!items.length) {
      body.innerHTML = '<div class="memory-list-empty">无匹配记忆</div>';
      return;
    }
    body.innerHTML = '';
    items.forEach(mem => {
      const row = document.createElement('div');
      row.className = 'memory-list-item' + (selectedId === mem.id ? ' active' : '');
      row.dataset.id = mem.id;
      const text = mem.text.length > 46 ? mem.text.slice(0, 46) + '...' : mem.text;
      let metaHtml = '<span>' + (mem.category || 'episodic') + '</span>';
      if (mem.project) metaHtml += '<span>' + mem.project + '</span>';
      if (mem.grooming && mem.grooming.pending) metaHtml += ' · ' + groomingActionBadgeHtml(mem);
      else if (mem.grooming && (mem.grooming.action === 'delete' || mem.grooming.action === 'promote')) metaHtml += ' · ' + groomingActionBadgeHtml(mem);
      else if (mem.merge_hint) metaHtml += ' · ' + groomingActionBadgeHtml(mem);
      const checkboxHtml = batchSelectable(mem)
        ? '<input type="checkbox" class="list-check batch-check" data-id="' + mem.id + '"' + (batchSelected.has(mem.id) ? ' checked' : '') + ' onclick="event.stopPropagation();onBatchCheck(this)" />'
        : '';
      row.innerHTML =
        '<div class="memory-list-text">' + checkboxHtml + text + '</div>' +
        '<div class="memory-list-meta">' + metaHtml + '</div>';
      row.onclick = () => {
        selectedId = mem.id;
        jumpToMemory(mem.id);
      };
      body.appendChild(row);
    });
  }

  const batchSelected = new Set();

  function batchSelectable(mem) {
    const g = mem.grooming || {};
    return g.pending || g.action === 'promote';
  }

  function onBatchCheck(checkbox) {
    if (checkbox.checked) batchSelected.add(checkbox.dataset.id);
    else batchSelected.delete(checkbox.dataset.id);
    updateBatchBar(memoryListItems());
  }

  function toggleSelectAll(checked) {
    memoryListItems().forEach(mem => {
      if (batchSelectable(mem)) {
        if (checked) batchSelected.add(mem.id);
        else batchSelected.delete(mem.id);
      }
    });
    renderMemoryList();
  }

  function clearSelection() {
    batchSelected.clear();
    renderMemoryList();
  }

  function updateBatchBar(items) {
    const bar = document.getElementById('batch-bar');
    const selectableCount = items.filter(batchSelectable).length;
    bar.classList.toggle('visible', selectableCount > 0);
    if (!selectableCount) batchSelected.clear();
    const selectAll = document.getElementById('batch-select-all');
    if (selectAll) {
      selectAll.checked = selectableCount > 0 && items.filter(batchSelectable).every(mem => batchSelected.has(mem.id));
      selectAll.disabled = !selectableCount;
    }
  }

  function batchAct(action) {
    const ids = [...batchSelected];
    if (!ids.length) {
      showToast('请先勾选记忆', 'info');
      return;
    }
    const label = action === 'confirm' ? '确认保留' : '升类';
    if (!confirm('批量' + label + ' ' + ids.length + ' 条？')) return;
    fetch('/api/grooming/batch', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action: action, ids: ids }),
    })
      .then(r => r.json())
      .then(result => {
        if (!result.ok) {
          showToast('批量失败: ' + (result.error || '未知错误'), 'error');
          return;
        }
        result.succeeded.forEach(item => {
          const id = item.id;
          const mem = memoriesMap[id];
          if (!mem) return;
          if (action === 'promote') {
            mem.category = item.category || mem.category;
            mem.category_label = item.category_label || mem.category_label;
            mem.category_raw = item.category_raw || '';
            mem.grooming = { pending: false, action: '', action_label: '', reason: '', target_category: '', at: '' };
          } else {
            mem.grooming = item.grooming || mem.grooming || {};
            mem.grooming.pending = false;
          }
          const bg = action === 'promote'
            ? ({{ category_colors_json | safe }}[mem.category] || '{{ global_color }}')
            : (originalNodeColors[id] || '#95a5a6');
          nodes.update({ id: id, borderWidth: 1, color: { background: bg, border: bg, highlight: { background: bg, border: '#f39c12' } } });
          originalNodeColors[id] = bg;
        });
        batchSelected.clear();
        syncGroomingUi();
        const failCount = (result.failed || []).length;
        showToast('批量' + label + '完成：成功 ' + result.succeeded.length + ' 条' + (failCount ? '，失败 ' + failCount + ' 条' : ''), failCount ? 'error' : 'success');
      })
      .catch(() => showToast('批量' + label + '失败，请重试', 'error'));
  }

  function batchConfirm() {
    batchAct('confirm');
  }

  function batchPromote() {
    batchAct('promote');
  }

  function updateMemoryListHighlight() {
    const rows = document.querySelectorAll('#memory-list-body .memory-list-item');
    rows.forEach(row => {
      row.classList.toggle('active', row.dataset.id === selectedId);
    });
  }

  function toggleMemoryList() {
    memoryListCollapsed = !memoryListCollapsed;
    renderMemoryList();
    syncUrlWithFilters();
    network.redraw();
  }

  document.getElementById('search-input').addEventListener('keydown', function(e) {
    if (e.key === 'Enter') doSearch();
  });

  function resetGraph() {
    document.getElementById('search-input').value = '';
    document.getElementById('project-filter').value = '';
    document.getElementById('category-filter').value = '';
    document.getElementById('grooming-filter').value = '';
    document.getElementById('search-results').classList.remove('visible');
    lastSearchResults = [];
    lastSearchPayload = null;
    nodes.update(nodesData.filter(n => !deletedNodeIds.has(n.id)).map(n => ({
      ...n,
      opacity: 1.0,
      hidden: false,
      borderWidth: 1,
    })));
    edges.update(edgesData.map(e => ({...e})));
    hideDetail();
    updateStats();
    renderMemoryList();
    syncUrlWithFilters();
  }

  function saveMetadata() {
    if (!selectedId) return;
    const mem = memoriesMap[selectedId];
    if (!mem) return;

    const project = document.getElementById('edit-project').value.trim();
    const category = document.getElementById('edit-category').value;
    const payload = {};
    if (project !== (mem.project || '')) payload.project = project;
    if (category !== mem.category) payload.category = category;
    if (Object.keys(payload).length === 0) {
      alert('无变更');
      return;
    }

    const btn = document.getElementById('btn-save');
    btn.disabled = true;
    btn.textContent = '保存中...';

    fetch('/api/update/' + encodeURIComponent(selectedId), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    })
      .then(r => r.json())
      .then(result => {
        btn.disabled = false;
        btn.textContent = '保存';
        if (!result.ok) {
          alert('保存失败: ' + (result.error || '未知错误'));
          return;
        }
        if (!result.changed) {
          alert('无变更');
          return;
        }

        mem.project = result.project || '';
        mem.category = result.category || mem.category;
        mem.category_label = result.category_label || mem.category_label;
        mem.category_raw = result.category_raw || '';
        if (mem.metadata) {
          mem.metadata.project = mem.project;
          mem.metadata.category = mem.category;
          if (mem.category_raw) {
            mem.metadata.category_raw = mem.category_raw;
          }
        }

        const nodeColor = {{ category_colors_json | safe }}[mem.category] || '{{ global_color }}';
        nodes.update({
          id: selectedId,
          color: nodeColor,
          title: (mem.project ? '[' + mem.project + '] ' : '[全局] ') +
            mem.text.slice(0, 60) + (mem.text.length > 60 ? '...' : ''),
        });
        originalNodeColors[selectedId] = nodeColor;

        showDetail(selectedId);
        loadTimeline(selectedId);
      })
      .catch(err => {
        btn.disabled = false;
        btn.textContent = '保存';
        console.error('save failed', err);
        alert('保存失败，请重试');
      });
  }

  function deleteMemory() {
    if (!selectedId) return;
    const reason = prompt('请填写删除原因（必填，便于追溯）：');
    if (reason === null) return;
    if (!reason.trim()) {
      alert('必须填写删除原因');
      return;
    }
    if (!confirm('确认删除记忆 ' + selectedId + '？\\n原因：' + reason.trim())) return;
    fetch('/delete/' + selectedId, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ reason: reason.trim() }),
    })
      .then(r => r.json())
      .then(result => {
        if (result.ok) {
          deletedNodeIds.add(selectedId);
          nodes.remove(selectedId);
          const relatedEdges = edges.getIds().filter(eid => {
            const edge = edges.get(eid);
            return edge.from === selectedId || edge.to === selectedId;
          });
          edges.remove(relatedEdges);
          delete memoriesMap[selectedId];
          hideDetail();
          updateStats();
          renderMemoryList();
        } else {
          alert('删除失败: ' + result.error);
        }
      });
  }

  function updateStats() {
    const count = nodes.getIds().length;
    document.getElementById('total-count').textContent = count;
  }

  // 从 URL 恢复筛选条件与搜索词（刷新后保持视图）
  const urlParams = getUrlParams();
  ['project', 'category', 'grooming'].forEach(key => {
    if (urlParams[key]) {
      document.getElementById(key + '-filter').value = urlParams[key];
    }
  });
  if (urlParams.list === '0') memoryListCollapsed = true;
  if (urlParams.q) {
    document.getElementById('search-input').value = urlParams.q;
    updateStats();
    doSearch();
  } else {
    applyGraphVisibility(null, []);
    updateStats();
  }
  renderMemoryList();
</script>
</body>
</html>'''


@app.route('/')
def index():
    """主页面：渲染图谱。"""
    memories = load_all_memories()
    edges = compute_edges(memories)
    thickness = compute_thickness(memories, edges)
    merge_hints_payload = read_merge_hints()

    grooming_counts = {'review': 0, 'pending': 0, 'delete': 0, 'promote': 0, 'keep': 0, 'merge': 0}
    for m in memories:
        g = m.get('grooming') or {}
        hint = m.get('merge_hint')
        action = g.get('action', '')
        if g.get('pending'):
            grooming_counts['pending'] += 1
        if g.get('pending') or action in ('delete', 'promote') or hint:
            grooming_counts['review'] += 1
        if action in ('delete', 'promote', 'keep'):
            grooming_counts[action] += 1
        if hint:
            grooming_counts['merge'] += 1

    nodes_json = json.dumps([
        {
            'id': m['id'],
            'label': m['text'][:30] + ('...' if len(m['text']) > 30 else ''),
            'title': (f"[{m['project']}] " if m['project'] else '[全局] ') + m['text'][:60] + ('...' if len(m['text']) > 60 else ''),
            'color': {
                'background': CATEGORY_COLORS.get(m['category'], _GLOBAL_COLOR),
                'border': '#f39c12' if (m.get('grooming') or {}).get('pending') else CATEGORY_COLORS.get(m['category'], _GLOBAL_COLOR),
                'highlight': {
                    'background': CATEGORY_COLORS.get(m['category'], _GLOBAL_COLOR),
                    'border': '#f39c12',
                },
            },
            'borderWidth': 3 if (m.get('grooming') or {}).get('pending') else 1,
            'shape': thickness[m['id']]['shape'],
            'size': thickness[m['id']]['size'],
            'shadow': thickness[m['id']]['shadow'] if thickness[m['id']]['shadow'] else None,
        }
        for m in memories
    ], ensure_ascii=False)

    edges_json = json.dumps(edges, ensure_ascii=False)

    memories_map = {m['id']: m for m in memories}
    memories_map_json = json.dumps(memories_map, ensure_ascii=False)

    thickness_json = json.dumps(thickness, ensure_ascii=False)

    projects = sorted(set(m['project'] for m in memories if m['project']))
    category_items = [(cat, CATEGORY_LABELS[cat]) for cat in sorted(VALID_CATEGORIES)]
    category_colors_json = json.dumps(CATEGORY_COLORS, ensure_ascii=False)
    merge_hints_json = json.dumps(merge_hints_payload, ensure_ascii=False)

    return render_template_string(
        HTML_TEMPLATE,
        nodes_json=nodes_json,
        edges_json=edges_json,
        memories_map_json=memories_map_json,
        thickness_json=thickness_json,
        merge_hints_json=merge_hints_json,
        grooming_counts=grooming_counts,
        projects=projects,
        category_items=category_items,
        category_colors=CATEGORY_COLORS,
        category_colors_json=category_colors_json,
        vis_js_src=url_for('static', filename=VIS_JS_STATIC),
        global_color=_GLOBAL_COLOR,
        mcp_search_max_results=MCP_SEARCH_MAX_RESULTS,
    )


@app.route('/api/timeline/<memory_id>')
def timeline(memory_id: str):
    """返回记忆的演变时间线（history.db + lineage.jsonl）及上游 ID。"""
    return jsonify(build_timeline(memory_id))


@app.route('/search')
def search():
    """搜索接口：hybrid_search（viewer 不应用 workspace read 过滤）。"""
    query = request.args.get('q', '').strip()
    if not query:
        return jsonify({'results': [], 'effective_project': '', 'max_results': MCP_SEARCH_MAX_RESULTS})

    ws = _viewer_workspace()
    if 'project' in request.args:
        effective_project = normalize_project(request.args.get('project', ''))
    elif VIEWER_WORKSPACE_ROOT and ws.aliases:
        effective_project = detect_project(
            VIEWER_WORKSPACE_ROOT,
            workspace_aliases=ws.aliases,
        )
    else:
        effective_project = detect_project()
    results = hybrid_search(
        query,
        project=effective_project,
        max_results=MCP_SEARCH_MAX_RESULTS,
    )

    payload = []
    for item in results:
        payload.append({
            'id': item.get('id', ''),
            'score': round(float(item.get('score', 0) or 0), 2),
            'source': item.get('source', ''),
            'project': item.get('project', '') or '',
            'category': item.get('category', '') or '',
            'text': (item.get('text', '') or '')[:160],
        })

    return jsonify({
        'query': query,
        'effective_project': effective_project,
        'max_results': MCP_SEARCH_MAX_RESULTS,
        'results': payload,
    })


def update_memory_metadata(
    memory_id: str,
    *,
    project: str | None = None,
    category: str | None = None,
) -> dict:
    """更新 Chroma metadata 中的 project/category，不重算向量。"""
    col = _get_chroma_collection()
    result = col.get(ids=[memory_id], include=['metadatas'])
    ids = result.get('ids') or []
    if not ids:
        raise ValueError(f'记忆不存在: {memory_id}')

    old_meta = dict((result.get('metadatas') or [{}])[0] or {})
    new_meta = dict(old_meta)
    changes: list[tuple[str, str, str]] = []

    if project is not None:
        old_project = normalize_project(str(old_meta.get('project', '') or ''))
        new_project = normalize_project(project)
        if old_project != new_project:
            new_meta['project'] = new_project
            changes.append(('project', old_project, new_project))

    if category is not None:
        old_category = normalize_category(str(old_meta.get('category', '') or ''))
        new_meta['category'] = category
        apply_category_metadata(new_meta)
        new_category = normalize_category(new_meta.get('category', ''))
        if old_category != new_category:
            changes.append(('category', old_category, new_category))

    if not changes:
        normalized_project = normalize_project(str(new_meta.get('project', '') or ''))
        normalized_category = normalize_category(str(new_meta.get('category', '') or ''))
        return {
            'changed': False,
            'project': normalized_project,
            'category': normalized_category,
            'category_label': CATEGORY_LABELS.get(normalized_category, normalized_category),
            'category_raw': str(new_meta.get('category_raw', '') or ''),
        }

    clear_grooming_suggestion(new_meta)
    col.update(ids=[memory_id], metadatas=[_sanitize_chroma_metadata(new_meta)])

    from memory_sync import sync_active_update_meta

    sync_active_update_meta(
        memory_id,
        project=normalize_project(str(new_meta.get('project', '') or '')) if project is not None else None,
        category=normalize_category(str(new_meta.get('category', '') or '')) if category is not None else None,
    )

    for field, old_val, new_val in changes:
        if field == 'category':
            record_event(
                'CATEGORY_CHANGE',
                memory_id,
                category=new_val,
                note=f'{old_val} → {new_val}',
                actor='mem_viewer',
            )
        elif field == 'project':
            record_event(
                'GROOMING',
                memory_id,
                note=f'project: {old_val or "全局"} → {new_val or "全局"}',
                actor='mem_viewer',
            )

    normalized_project = normalize_project(str(new_meta.get('project', '') or ''))
    normalized_category = normalize_category(str(new_meta.get('category', '') or ''))
    return {
        'changed': True,
        'project': normalized_project,
        'category': normalized_category,
        'category_label': CATEGORY_LABELS.get(normalized_category, normalized_category),
        'category_raw': str(new_meta.get('category_raw', '') or ''),
    }


@app.route('/api/similar')
def similar_memories():
    """保存前查相似记忆（与 hybrid_search 同算法）。"""
    query = request.args.get('q', '').strip()
    if not query:
        return jsonify({'results': [], 'query': ''})

    project = normalize_project(request.args.get('project', ''))
    results = hybrid_search(
        query,
        project=project,
        max_results=5,
    )
    payload = []
    for item in results:
        payload.append({
            'id': item.get('id', ''),
            'score': round(float(item.get('score', 0) or 0), 2),
            'source': item.get('source', ''),
            'text': (item.get('text', '') or '')[:160],
        })
    return jsonify({'query': query, 'effective_project': project, 'results': payload})


@app.route('/api/add', methods=['POST'])
def add_memory_route():
    """viewer 手动新增记忆。"""
    body = request.get_json(silent=True) or {}
    content = str(body.get('content', '') or '').strip()
    if not content:
        return jsonify({'ok': False, 'error': '正文不能为空'}), 400

    project = normalize_project(str(body.get('project', '') or ''))
    category = normalize_category(str(body.get('category', '') or '') or DEFAULT_CATEGORY)
    if category not in VALID_CATEGORIES:
        return jsonify({'ok': False, 'error': f'无效 category: {category}'}), 400

    try:
        result = add_memory_from_viewer(content, project=project, category=category)
        status = 200 if result.get('ok') else 409
        return jsonify(result), status
    except Exception as exc:
        return jsonify({'ok': False, 'error': str(exc)}), 500


@app.route('/api/update/<memory_id>', methods=['POST'])
def update_memory(memory_id: str):
    """更新记忆：正文扩写（重嵌向量）或 project/category（仅 metadata）。"""
    body = request.get_json(silent=True) or {}
    has_content = 'content' in body
    has_project = 'project' in body
    has_category = 'category' in body

    if has_content:
        content = str(body.get('content', '') or '').strip()
        if not content:
            return jsonify({'ok': False, 'error': '正文不能为空'}), 400
        project = body.get('project') if has_project else None
        category = body.get('category') if has_category else None
        if has_category:
            normalized = normalize_category(category)
            if normalized not in VALID_CATEGORIES:
                return jsonify({'ok': False, 'error': f'无效 category: {category}'}), 400
        try:
            write_warning = _write_access_warning_for_memory(
                memory_id,
                project,
                has_project=has_project,
            )
            result = update_memory_content(
                memory_id,
                content,
                project=body.get('project') if has_project else None,
                category=category,
            )
            if write_warning:
                result['warning'] = write_warning
            return jsonify({'ok': True, **result})
        except ValueError as exc:
            return jsonify({'ok': False, 'error': str(exc)}), 404
        except Exception as exc:
            return jsonify({'ok': False, 'error': str(exc)}), 500

    if not has_project and not has_category:
        return jsonify({'ok': False, 'error': '至少提供 content、project 或 category'}), 400

    project = body.get('project') if has_project else None
    category = body.get('category') if has_category else None
    if has_category:
        normalized = normalize_category(category)
        if normalized not in VALID_CATEGORIES:
            return jsonify({'ok': False, 'error': f'无效 category: {category}'}), 400

    try:
        write_warning = _write_access_warning_for_memory(
            memory_id,
            project,
            has_project=has_project,
        )
        result = update_memory_metadata(memory_id, project=project, category=category)
        if write_warning:
            result['warning'] = write_warning
        return jsonify({'ok': True, **result})
    except ValueError as exc:
        return jsonify({'ok': False, 'error': str(exc)}), 404
    except Exception as exc:
        return jsonify({'ok': False, 'error': str(exc)}), 500


@app.route('/api/grooming/confirm/<memory_id>', methods=['POST'])
def grooming_confirm(memory_id: str):
    """确认保留：仅清 grooming_pending。"""
    try:
        result = confirm_grooming_memory(memory_id)
        return jsonify({'ok': True, **result})
    except ValueError as exc:
        return jsonify({'ok': False, 'error': str(exc)}), 404
    except Exception as exc:
        return jsonify({'ok': False, 'error': str(exc)}), 500


@app.route('/api/grooming/promote/<memory_id>', methods=['POST'])
def grooming_promote(memory_id: str):
    """采纳升类建议。"""
    try:
        result = execute_grooming_promote(memory_id)
        confirm_grooming_memory(memory_id)
        return jsonify({'ok': True, **result})
    except ValueError as exc:
        return jsonify({'ok': False, 'error': str(exc)}), 400
    except Exception as exc:
        return jsonify({'ok': False, 'error': str(exc)}), 500


@app.route('/api/grooming/merge/<source_id>', methods=['POST'])
def grooming_merge(source_id: str):
    """合并（重校验）。"""
    body = request.get_json(silent=True) or {}
    hinted_target = str(body.get('target_id', '') or '')
    try:
        result = execute_grooming_merge(source_id, hinted_target)
        return jsonify({'ok': True, **result})
    except ValueError as exc:
        return jsonify({'ok': False, 'error': str(exc)}), 400
    except Exception as exc:
        return jsonify({'ok': False, 'error': str(exc)}), 500


@app.route('/api/grooming/batch', methods=['POST'])
def grooming_batch():
    """批量操作：action ∈ confirm | promote。delete 不提供批量。"""
    body = request.get_json(silent=True) or {}
    action = str(body.get('action', '') or '').strip()
    ids = body.get('ids') or []
    if action not in ('confirm', 'promote'):
        return jsonify({'ok': False, 'error': 'action 须为 confirm 或 promote（删除不支持批量）'}), 400
    if not isinstance(ids, list) or not ids:
        return jsonify({'ok': False, 'error': 'ids 不能为空'}), 400

    succeeded: list[dict] = []
    failed: list[dict] = []
    for memory_id in ids:
        try:
            if action == 'confirm':
                result = confirm_grooming_memory(memory_id)
                succeeded.append({'id': memory_id, 'grooming': result.get('grooming')})
            else:
                result = execute_grooming_promote(memory_id)
                confirm_grooming_memory(memory_id)
                succeeded.append({
                    'id': memory_id,
                    'category': result.get('category', ''),
                    'category_label': result.get('category_label', ''),
                    'category_raw': result.get('category_raw', ''),
                })
        except Exception as exc:
            failed.append({'id': memory_id, 'error': str(exc)})
    return jsonify({'ok': True, 'action': action, 'succeeded': succeeded, 'failed': failed})


@app.route('/delete/<memory_id>', methods=['POST'])
def delete(memory_id):
    """删除记忆：memory_sync 多表事务 + Chroma。"""
    from memory_delete import archive_delete
    from memory_sync import SyncError

    payload = request.get_json(silent=True) or {}
    reason = str(payload.get('reason', '') or '').strip()
    if not reason:
        return jsonify({'ok': False, 'error': '必须填写删除原因（reason）'}), 400

    try:
        result = archive_delete(
            memory_id,
            reason,
            actor='mem_viewer',
            source='mem_viewer',
        )
        return jsonify({'ok': True, 'counts': result.get('counts', {})})
    except SyncError as exc:
        return jsonify({'ok': False, 'error': str(exc), 'sync_pending': True}), 500
    except Exception as exc:
        return jsonify({'ok': False, 'error': str(exc)})


if __name__ == '__main__':
    ws = _bootstrap_viewer_workspace()
    if ws.is_configured:
        print(f'workspace write check: {ws.source_path}')
    print(f'local-memory 记忆图谱启动: http://{HOST}:{PORT}（默认展示 active pool 全部 project）')
    app.run(host=HOST, port=PORT, debug=False)