"""local-memory MCP server — 混合检索，B/D/E 写入策略，无 mem0 依赖"""

from __future__ import annotations

import json
import logging
import os
import time

from mcp.server.fastmcp import FastMCP

from add_policy import prepare_add_plan
from hybrid_search import (
    detect_project,
    format_mcp_search_output,
    hybrid_search,
    normalize_project,
)
from llm_client import probe_llm_reachable
from memory_paths import pending_dir
from memory_store import add, delete, get_all_memories as get_all_memories_from_store, retry_pending_entry
from pool_manager import format_pools_text
from pool_manager import list_pools as _list_pools
from pool_manager import switch_pool as _switch_pool
from workspace_config import (
    check_write_access,
    filter_records_by_read_access,
    log_workspace_warning,
    workspace_runtime,
)

logger = logging.getLogger('local-memory')

DEFAULT_MAX_RESULTS = 8
MAX_RETRY_COUNT = 3

mcp = FastMCP('local-memory')

# 启动探活（短超时，不阻塞 MCP 握手；失败仅 warning）
try:
    _base = probe_llm_reachable(timeout=2.0)
    if _base:
        logger.info('local-memory LLM 探活成功: %s', _base)
except Exception as exc:
    logger.warning('local-memory LLM 探活失败（检索仍可用）: %s', exc)


def _write_to_pending(content: str, metadata: dict, project: str) -> None:
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
    logger.info('已写入 pending: %s', filepath)


def _optional_cwd(cwd: str) -> str | None:
    value = (cwd or '').strip()
    return value or None


@mcp.tool()
def add_memory(content: str, metadata: str = '', project: str = '', cwd: str = '') -> str:
    """添加一条记忆。

    content: 要记忆的内容（中文完整句，含模块名/字段名更易检索）
    metadata: 可选 JSON，例如 {"category":"reference","project":"your-project"}
    project: 项目标识；省略则 detect_project(cwd)
    cwd: 可选工作区路径，用于加载 .cursor/memory.json 读写权限
    """
    work_cwd = _optional_cwd(cwd)
    write_warning: str | None = None

    def _run_add(effective_project: str) -> str:
        nonlocal write_warning
        meta = {}
        if metadata and metadata.strip():
            try:
                parsed = json.loads(metadata)
                if isinstance(parsed, dict):
                    meta = parsed
            except json.JSONDecodeError:
                meta = {}

        try:
            result = add(
                content,
                metadata=meta,
                project=effective_project,
                actor='mcp',
            )
        except Exception as exc:
            logger.error('add 失败，写入 pending: %s', exc)
            plan = prepare_add_plan(content, metadata, effective_project)
            _write_to_pending(plan.content, plan.metadata, effective_project)
            return f'写入失败: {exc}。已存入待办队列({pending_dir()})，可用 retry_pending 重试。'

        if result.dropped:
            scope = result.project or '全局'
            mode = '结构化原样入库' if result.storage_mode == 'structured' else '原样入库'
            return f'记忆[{scope}]（{mode}）与已有记忆重复，未新增。{result.merge_note or ""}'

        scope = result.project or '全局'
        mode = '结构化原样入库' if result.storage_mode == 'structured' else '原样入库'
        extra = f'；{result.merge_note}' if result.merge_note else ''
        suffix = f' (warning: {write_warning})' if write_warning else ''
        return f'已处理记忆[{scope}]（{mode}），ID: {result.memory_id}{extra}{suffix}'

    if work_cwd:
        with workspace_runtime(work_cwd) as config:
            effective_project = project or detect_project(work_cwd, workspace_aliases=config.aliases or None)
            write_warning = check_write_access(effective_project, config.write_projects)
            if write_warning:
                log_workspace_warning(write_warning)
            return _run_add(effective_project)

    effective_project = project or detect_project()
    return _run_add(effective_project)


@mcp.tool()
def search_memory(query: str, project: str = '', cwd: str = '') -> str:
    """搜索记忆（关键词+向量混合检索）。cwd 可选，用于 workspace 读权限过滤。"""
    work_cwd = _optional_cwd(cwd)

    if work_cwd:
        with workspace_runtime(work_cwd) as config:
            effective_project = normalize_project(project) or detect_project(
                work_cwd,
                workspace_aliases=config.aliases or None,
            )
            results = hybrid_search(
                query,
                project=effective_project,
                max_results=DEFAULT_MAX_RESULTS,
                read_projects=config.read_projects,
            )
            return format_mcp_search_output(results)

    effective_project = normalize_project(project) or detect_project()
    results = hybrid_search(
        query,
        project=effective_project,
        max_results=DEFAULT_MAX_RESULTS,
    )
    return format_mcp_search_output(results)


@mcp.tool()
def get_all_memories(project: str = '', cwd: str = '') -> str:
    """获取全部活跃记忆。project 可选；cwd 可选用于 workspace 读权限过滤。"""
    work_cwd = _optional_cwd(cwd)

    if work_cwd:
        with workspace_runtime(work_cwd) as config:
            records = get_all_memories_from_store(project=project or None)
            records = filter_records_by_read_access(records, config.read_projects)
            return _format_memory_records(records)

    records = get_all_memories_from_store(project=project or None)
    return _format_memory_records(records)


def _format_memory_records(records) -> str:
    if not records:
        return '暂无记忆'
    lines = []
    for item in records:
        scope_tag = f'[{item.project}]' if item.project else '[全局]'
        lines.append(f'[{item.memory_id}] {scope_tag} {item.content}')
    return '\n'.join(lines)


@mcp.tool()
def delete_memory(memory_id: str, reason: str = '') -> str:
    """删除记忆。reason 必填。"""
    reason = (reason or '').strip()
    if not reason:
        return '删除失败：必须提供 reason（删除原因）'
    result = delete(memory_id, reason, actor='mcp', source='delete_memory')
    if result.ok:
        return f'已删除记忆 {memory_id}，原因：{reason}'
    return f'删除未完全同步（已写入 sync_pending）: {result.detail}'


@mcp.tool()
def retry_pending() -> str:
    """扫描 pending 目录并重试写入；末尾顺带 retry_sync_pending。"""
    pdir = str(pending_dir())
    if not os.path.isdir(pdir):
        return 'pending 目录不存在，无需重试'

    files = sorted(f for f in os.listdir(pdir) if f.endswith('.json'))
    if not files:
        return 'pending 队列空，无需重试'

    success_count = 0
    fail_count = 0
    manual_review: list[str] = []

    for filename in files:
        filepath = os.path.join(pdir, filename)
        with open(filepath, encoding='utf-8') as handle:
            payload = json.load(handle)

        payload['retry_count'] = int(payload.get('retry_count', 0) or 0) + 1
        payload.pop('use_infer', None)

        try:
            retry_pending_entry(payload)
            os.remove(filepath)
            success_count += 1
        except Exception as exc:
            if payload['retry_count'] >= MAX_RETRY_COUNT:
                payload['status'] = 'manual_review'
                payload['last_error'] = str(exc)
                with open(filepath, 'w', encoding='utf-8') as handle:
                    json.dump(payload, handle, ensure_ascii=False, indent=2)
                manual_review.append(filename)
            else:
                payload['last_error'] = str(exc)
                with open(filepath, 'w', encoding='utf-8') as handle:
                    json.dump(payload, handle, ensure_ascii=False, indent=2)
                fail_count += 1

    lines = [f'重试完成: 成功{success_count}条, 失败{fail_count}条']
    if manual_review:
        lines.append(f'需人工介入: {manual_review}')

    from memory_sync import retry_sync_pending

    sync_lines = retry_sync_pending()
    if sync_lines:
        lines.append('--- sync_pending ---')
        lines.extend(sync_lines)

    return '\n'.join(lines)


@mcp.tool()
def run_episodic_grooming(all_episodic: bool = False, dry_run: bool = False) -> str:
    """批处理 episodic grooming（与 MCP 共用 Chroma，MCP 运行时优先于独立脚本）。"""
    try:
        from episodic_grooming_batch import format_grooming_summary, run_episodic_grooming as _run

        summary = _run(all_episodic=all_episodic, dry_run=dry_run)
        return format_grooming_summary(summary)
    except Exception as exc:
        logger.error('run_episodic_grooming 失败: %s', exc)
        return f'grooming 失败: {exc}'


@mcp.tool()
def confirm_grooming(memory_id: str) -> str:
    """确认 episodic 梳理建议（清除 grooming_pending，不改正文）。"""
    try:
        from grooming_metadata import clear_grooming_pending, is_grooming_pending, parse_grooming_fields
        from hybrid_search import get_chroma_client, get_chroma_collection
        from memory_lineage import record_event

        col = get_chroma_collection(get_chroma_client())
        raw = col.get(ids=[memory_id], include=['metadatas'])
        if not raw.get('ids'):
            return f'记忆不存在: {memory_id}'

        meta = dict((raw.get('metadatas') or [{}])[0] or {})
        if not is_grooming_pending(meta):
            grooming = parse_grooming_fields(meta)
            return f'记忆 {memory_id} 无待确认标记。当前建议: {grooming.get("action") or "无"}'

        clear_grooming_pending(meta)
        clean = {
            key: value for key, value in meta.items()
            if isinstance(value, (str, int, float, bool))
        }
        col.update(ids=[memory_id], metadatas=[clean])
        record_event(
            'GROOMING',
            memory_id,
            note='MCP 确认保留（清除待确认标记）',
            actor='mcp',
        )
        return f'已确认记忆 {memory_id}，待确认标记已清除'
    except Exception as exc:
        return f'确认失败: {exc}'


@mcp.tool()
def list_pools() -> str:
    """列出 registry 中所有记忆池及当前 active pool。"""
    return format_pools_text(_list_pools())


@mcp.tool()
def switch_pool(pool_id: str) -> str:
    """切换活跃记忆池（更新 registry.active_pool）。pool_id: 目标池 ID。"""
    try:
        _switch_pool(pool_id)
    except ValueError as exc:
        return f'切换失败: {exc}'
    return f'已切换 active pool → {pool_id}\n\n{format_pools_text()}'


if __name__ == '__main__':
    mcp.run()
