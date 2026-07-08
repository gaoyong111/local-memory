#!/usr/bin/env python3
"""config_local.json / config_ollama.json → v2 config.json（去 mem0 专有字段）。"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from typing import Any


def _flatten_embedder(block: dict[str, Any]) -> dict[str, Any]:
    inner = block.get('config') if isinstance(block.get('config'), dict) else block
    return {
        'provider': block.get('provider') or inner.get('provider') or 'ollama',
        'model': inner.get('model', 'bge-m3'),
        'base_url': inner.get('base_url') or inner.get('ollama_base_url') or 'http://localhost:11434',
    }


def _flatten_llm(block: dict[str, Any]) -> dict[str, Any]:
    inner = block.get('config') if isinstance(block.get('config'), dict) else block
    flat: dict[str, Any] = {
        'provider': block.get('provider') or inner.get('provider') or 'ollama',
        'model': inner.get('model', 'qwen2.5:7b'),
        'base_url': inner.get('base_url') or inner.get('ollama_base_url') or inner.get('anthropic_base_url') or '',
        'temperature': inner.get('temperature', 0.1),
        'max_tokens': inner.get('max_tokens', 2000),
    }
    if inner.get('api_key'):
        flat['api_key_env'] = 'NEWAPI_KEY'
    elif inner.get('api_key_env'):
        flat['api_key_env'] = inner['api_key_env']
    return {k: v for k, v in flat.items() if v not in ('', None)}


def convert_v1_config(v1: dict[str, Any], *, fallback_v1: dict[str, Any] | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {
        'embedder': _flatten_embedder(v1.get('embedder') or {}),
        'llm': _flatten_llm(v1.get('llm') or {}),
    }
    if fallback_v1 and fallback_v1.get('llm'):
        out['fallback_llm'] = _flatten_llm(fallback_v1['llm'])
    return out


def migrate(pool: Path, *, dry_run: bool = False) -> dict:
    candidates = [
        pool / 'config_local.json',
        pool / 'config.json',
    ]
    source = next((p for p in candidates if p.is_file()), None)
    if not source:
        raise FileNotFoundError(f'未找到 config.json / config_local.json in {pool}')

    v1 = json.loads(source.read_text(encoding='utf-8'))
    fallback_path = pool / 'config_ollama.json'
    fallback_v1 = json.loads(fallback_path.read_text(encoding='utf-8')) if fallback_path.is_file() else None
    v2 = convert_v1_config(v1, fallback_v1=fallback_v1)

    out_path = pool / 'config.json'
    if dry_run:
        return {'dry_run': True, 'source': str(source), 'v2': v2}

    if out_path.is_file():
        shutil.copy2(out_path, out_path.with_name(out_path.name + '.bak'))
    elif source.is_file() and source != out_path:
        shutil.copy2(source, source.with_suffix(source.suffix + '.bak'))

    out_path.write_text(json.dumps(v2, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

    env_path = pool / '.env'
    inner = (v1.get('llm') or {}).get('config') or v1.get('llm') or {}
    api_key = inner.get('api_key') if isinstance(inner, dict) else None
    env_note = ''
    if api_key and env_path.is_file():
        text = env_path.read_text(encoding='utf-8')
        if 'NEWAPI_KEY=' not in text:
            with env_path.open('a', encoding='utf-8') as handle:
                handle.write(f'\nNEWAPI_KEY={api_key}\n')
            env_note = 'appended NEWAPI_KEY to .env'
    elif api_key:
        env_path.write_text(f'NEWAPI_KEY={api_key}\n', encoding='utf-8')
        env_note = 'created .env with NEWAPI_KEY'

    return {
        'source': str(source),
        'output': str(out_path),
        'has_fallback_llm': 'fallback_llm' in v2,
        'env': env_note,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description='v1 mem0 配置 → v2 config.json')
    parser.add_argument('--pool', required=True)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    pool = Path(args.pool).expanduser().resolve()
    print(migrate(pool, dry_run=args.dry_run))


if __name__ == '__main__':
    main()
