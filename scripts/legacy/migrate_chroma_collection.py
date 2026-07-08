#!/usr/bin/env python3
"""Chroma collection mem0 → memories（copy 验证后删旧）。"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent.parent / 'src'
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


def migrate(
    pool: Path,
    *,
    source: str = 'mem0',
    target: str = 'memories',
    dry_run: bool = False,
) -> dict:
    os.environ['MEMORY_DIR'] = str(Path.home() / '.memory')
    os.environ['MEMORY_CHROMA_PATH'] = str(pool / 'chroma_db')
    os.environ.pop('MEMORY_CHROMA_COLLECTION', None)

    chroma_path = pool / 'chroma_db'
    if not chroma_path.is_dir():
        raise FileNotFoundError(f'chroma_db 不存在: {chroma_path}')

    from hybrid_search import get_chroma_client

    client = get_chroma_client()
    names = {col.name for col in client.list_collections()}

    if source not in names and target in names:
        target_col = client.get_collection(target)
        return {
            'skipped': True,
            'reason': f'source {source!r} 不存在，target 已有',
            'target_count': target_col.count(),
        }

    if source not in names:
        raise RuntimeError(f'collection {source!r} 不存在，现有: {sorted(names)}')

    src_col = client.get_collection(source)
    src_count = src_col.count()
    if src_count == 0:
        return {'skipped': True, 'reason': 'source 为空', 'source_count': 0}

    if dry_run:
        return {'dry_run': True, 'source': source, 'target': target, 'source_count': src_count}

    raw = src_col.get(include=['embeddings', 'metadatas', 'documents'])
    ids = raw.get('ids') or []
    embeddings = raw.get('embeddings')
    if embeddings is None:
        embeddings = []
    metadatas = raw.get('metadatas')
    if metadatas is None:
        metadatas = []
    documents = raw.get('documents')
    if documents is None:
        documents = []

    if target in names:
        client.delete_collection(target)
    dst_col = client.create_collection(target)

    batch_size = 100
    for i in range(0, len(ids), batch_size):
        batch_ids = ids[i:i + batch_size]
        kwargs: dict = {'ids': batch_ids}
        if len(embeddings) > 0:
            kwargs['embeddings'] = embeddings[i:i + batch_size]
        if len(metadatas) > 0:
            kwargs['metadatas'] = metadatas[i:i + batch_size]
        if len(documents) > 0:
            kwargs['documents'] = documents[i:i + batch_size]
        dst_col.upsert(**kwargs)

    dst_count = dst_col.count()
    if dst_count != src_count:
        raise RuntimeError(f'id 数量不一致: source={src_count} target={dst_count}')

    client.delete_collection(source)

    meta_file = pool / 'pool.meta.json'
    if meta_file.is_file():
        meta = json.loads(meta_file.read_text(encoding='utf-8'))
        meta['chroma_collection'] = target
        meta_file.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

    return {
        'source': source,
        'target': target,
        'copied_ids': dst_count,
        'deleted_source': True,
        'pool_meta_updated': meta_file.is_file(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description='Chroma collection mem0 → memories')
    parser.add_argument('--pool', required=True)
    parser.add_argument('--source', default='mem0')
    parser.add_argument('--target', default='memories')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()

    pool = Path(args.pool).expanduser().resolve()
    result = migrate(pool, source=args.source, target=args.target, dry_run=args.dry_run)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
