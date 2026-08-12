"""独立 LLM 客户端 — 读池 config.json + .env，支持 ollama / openai_compatible。"""

from __future__ import annotations

import json
import logging
import os
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

from memory_paths import resolve_config_path, resolve_pool_path

logger = logging.getLogger(__name__)

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None  # type: ignore


def _load_dotenv_for_pool() -> None:
    if load_dotenv is None:
        return
    pool = resolve_pool_path()
    env_file = pool / '.env'
    if env_file.is_file():
        load_dotenv(env_file, override=False)


def _read_config(path: str | None = None) -> dict[str, Any]:
    _load_dotenv_for_pool()
    config_path = path or str(resolve_config_path())
    try:
        with open(config_path, encoding='utf-8') as handle:
            return json.load(handle)
    except OSError:
        return {}


def _flatten_llm_block(block: dict[str, Any]) -> dict[str, Any]:
    """v1 嵌套 config → v2 扁平。"""
    if not block:
        return {}
    inner = block.get('config')
    if isinstance(inner, dict):
        flat = dict(inner)
        flat.setdefault('provider', block.get('provider', 'ollama'))
        return flat
    return dict(block)


_SESSION_SETTINGS_PATH = '~/.claude/settings.json'


def _read_session_llm_env() -> dict[str, str]:
    """读 cc-switch 写入 ~/.claude/settings.json 的 env，取当前会话在线模型三元组。"""
    path = Path(_SESSION_SETTINGS_PATH).expanduser()
    try:
        with open(path, encoding='utf-8') as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {}
    env = data.get('env') or {}
    keys = ('ANTHROPIC_BASE_URL', 'ANTHROPIC_MODEL', 'ANTHROPIC_AUTH_TOKEN')
    values = [env.get(k) for k in keys]
    if not all(values):
        return {}
    return dict(zip(keys, values))


def _apply_auto_follow(block: dict[str, Any]) -> dict[str, Any]:
    """auto_follow=true 时用当前 Claude 会话的在线模型覆盖 endpoint/model/token。"""
    resolved = dict(block)
    env = _read_session_llm_env()
    required = ('ANTHROPIC_BASE_URL', 'ANTHROPIC_MODEL', 'ANTHROPIC_AUTH_TOKEN')
    if not env or not all(env.get(k) for k in required):
        return resolved
    resolved['provider'] = 'anthropic'
    resolved['base_url'] = env['ANTHROPIC_BASE_URL']
    resolved['model'] = env['ANTHROPIC_MODEL']
    resolved['api_key'] = env['ANTHROPIC_AUTH_TOKEN']
    resolved.pop('api_key_env', None)
    resolved.pop('api_key_env_name', None)
    return resolved


_degradation_recorded: set[str] = set()


def _record_degradation(primary_url: str, fallback_url: str, error: Exception) -> None:
    """主 LLM 不可用/降级时写一条 pending 记忆，供每日复盘可见；每进程每主端点仅记一次。"""
    key = primary_url or 'primary'
    if key in _degradation_recorded:
        return
    _degradation_recorded.add(key)
    try:
        pending_dir = resolve_pool_path() / 'pending'
        pending_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            'content': (
                f'local-memory 主 LLM 不可用：primary={primary_url or "(空)"}'
                f'（{type(error).__name__}: {str(error)[:120]}）。'
                '本地 qwen 降级已移除，涉及 LLM 的调用（grooming/add_policy）将失败或走规则兜底，'
                '请检查主端点可达性或 auto_follow 配置。'
            ),
            'metadata': {'category': 'episodic', 'project': 'local-memory'},
            'project': 'local-memory',
            'use_infer': False,
            'retry_count': 0,
            'created_at': datetime.now().isoformat(),
            'source': 'llm-degradation-alert',
        }
        filename = f"llm-degraded-{datetime.now().strftime('%Y%m%d-%H%M%S%f')}.json"
        (pending_dir / filename).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    except Exception:
        logger.exception('记录 LLM 降级失败')


def _resolve_llm_configs() -> tuple[dict[str, Any], dict[str, Any] | None]:
    config = _read_config()
    primary = _flatten_llm_block(config.get('llm') or {})
    if primary.get('auto_follow'):
        primary = _apply_auto_follow(primary)
    fallback_raw = config.get('fallback_llm')
    fallback = _flatten_llm_block(fallback_raw) if fallback_raw else None

    # v1 双文件兜底
    if not fallback:
        fallback_path = os.environ.get('MEMORY_FALLBACK_CONFIG', '')
        expanded = Path(fallback_path).expanduser() if fallback_path else None
        if expanded and expanded.is_file():
            fb_config = _read_config(str(expanded))
            fallback = _flatten_llm_block(fb_config.get('llm') or {})

    return primary, fallback


def _get_api_key(block: dict[str, Any]) -> str:
    env_name = block.get('api_key_env') or block.get('api_key_env_name') or ''
    if env_name:
        return os.environ.get(env_name, '')
    return str(block.get('api_key') or os.environ.get('NEWAPI_KEY', '') or os.environ.get('LLM_API_KEY', ''))


def _ollama_generate(block: dict[str, Any], messages: list[dict[str, str]], *, json_mode: bool) -> str:
    base_url = (block.get('base_url') or block.get('ollama_base_url') or 'http://localhost:11434').rstrip('/')
    model = block.get('model', 'qwen2.5:7b')
    prompt_parts = []
    for msg in messages:
        role = msg.get('role', 'user')
        prompt_parts.append(f'{role}: {msg.get("content", "")}')
    prompt = '\n'.join(prompt_parts)
    payload: dict[str, Any] = {
        'model': model,
        'prompt': prompt,
        'stream': False,
        'options': {'temperature': float(block.get('temperature', 0.1))},
    }
    if json_mode:
        payload['format'] = 'json'
    data = json.dumps(payload).encode()
    request = urllib.request.Request(
        f'{base_url}/api/generate',
        data=data,
        headers={'Content-Type': 'application/json'},
    )
    response = urllib.request.urlopen(request, timeout=120)
    body = json.loads(response.read())
    return str(body.get('response', '') or '')


def _openai_compatible_generate(
    block: dict[str, Any],
    messages: list[dict[str, str]],
    *,
    json_mode: bool,
) -> str:
    import httpx

    base_url = (block.get('base_url') or block.get('api_base') or 'https://api.openai.com/v1').rstrip('/')
    model = block.get('model', 'gpt-4o-mini')
    api_key = _get_api_key(block)
    headers = {'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'}
    payload: dict[str, Any] = {
        'model': model,
        'messages': messages,
        'temperature': float(block.get('temperature', 0.1)),
    }
    if json_mode:
        payload['response_format'] = {'type': 'json_object'}
    with httpx.Client(timeout=120) as client:
        response = client.post(f'{base_url}/chat/completions', headers=headers, json=payload)
        response.raise_for_status()
        data = response.json()
    choices = data.get('choices') or []
    if not choices:
        return ''
    return str(choices[0].get('message', {}).get('content', '') or '')


def _anthropic_generate(
    block: dict[str, Any],
    messages: list[dict[str, str]],
    *,
    json_mode: bool,
) -> str:
    import httpx

    base_url = (block.get('anthropic_base_url') or block.get('base_url') or '').rstrip('/')
    model = block.get('model', 'claude-3-5-sonnet-latest')
    api_key = _get_api_key(block)
    headers = {
        'x-api-key': api_key,
        'anthropic-version': '2023-06-01',
        'Content-Type': 'application/json',
    }
    system_parts = [m['content'] for m in messages if m.get('role') == 'system']
    user_parts = [m for m in messages if m.get('role') != 'system']
    payload: dict[str, Any] = {
        'model': model,
        'max_tokens': int(block.get('max_tokens', 2000)),
        'temperature': float(block.get('temperature', 0.1)),
        'messages': [{'role': m.get('role', 'user'), 'content': m.get('content', '')} for m in user_parts],
    }
    if system_parts:
        payload['system'] = '\n'.join(system_parts)
    if json_mode:
        payload['system'] = (payload.get('system', '') + '\n只输出 JSON。').strip()
    url = f'{base_url}/v1/messages' if base_url else 'https://api.anthropic.com/v1/messages'
    with httpx.Client(timeout=120) as client:
        response = client.post(url, headers=headers, json=payload)
        response.raise_for_status()
        data = response.json()
    content_blocks = data.get('content') or []
    texts = [b.get('text', '') for b in content_blocks if b.get('type') == 'text']
    return '\n'.join(texts)


def _generate_with_block(
    block: dict[str, Any],
    messages: list[dict[str, str]],
    *,
    json_mode: bool,
) -> str:
    provider = str(block.get('provider', 'ollama')).lower()
    if provider in ('ollama',):
        return _ollama_generate(block, messages, json_mode=json_mode)
    if provider in ('openai_compatible', 'openai', 'openrouter'):
        return _openai_compatible_generate(block, messages, json_mode=json_mode)
    if provider in ('anthropic',):
        return _anthropic_generate(block, messages, json_mode=json_mode)
    raise ValueError(f'unsupported llm provider: {provider}')


class LlmClient:
    """add_policy / grooming 使用的 LLM 接口。"""

    def __init__(self) -> None:
        self._primary, self._fallback = _resolve_llm_configs()

    def generate_response(
        self,
        messages: list[dict[str, str]],
        response_format: dict[str, str] | None = None,
    ) -> str:
        """主端点失败即写降级告警并抛错，不再静默降级本地 qwen（兜底已移除）。"""
        json_mode = (response_format or {}).get('type') == 'json_object'
        last_error: Exception | None = None
        primary, fallback = _resolve_llm_configs()
        for idx, (label, block) in enumerate([('primary', primary), ('fallback', fallback)]):
            if not block:
                continue
            try:
                result = _generate_with_block(block, messages, json_mode=json_mode)
                if result.strip():
                    if last_error is not None and idx > 0:
                        _record_degradation(
                            primary.get('base_url', '') if primary else '',
                            block.get('base_url', ''),
                            last_error,
                        )
                    return result
            except Exception as exc:
                last_error = exc
                logger.warning('llm %s failed: %s', label, exc)
        if last_error is not None:
            _record_degradation(
                primary.get('base_url', '') if primary else '',
                fallback.get('base_url', '') if fallback else '',
                last_error,
            )
            raise last_error
        raise RuntimeError('LLM 主配置和兜底配置均不可用')

    def ping(self) -> bool:
        try:
            self.generate_response([{'role': 'user', 'content': 'ping'}])
            return True
        except Exception:
            return False


def _llm_endpoint_url(block: dict[str, Any]) -> str:
    """与 generate 路径对齐的 HTTP base URL（供探活用）。"""
    if not block:
        return ''
    provider = str(block.get('provider', 'ollama')).lower()
    if provider == 'ollama':
        return (block.get('base_url') or block.get('ollama_base_url') or 'http://localhost:11434').rstrip('/')
    if provider == 'anthropic':
        return (block.get('anthropic_base_url') or block.get('base_url') or '').rstrip('/')
    return (
        block.get('base_url')
        or block.get('api_base')
        or block.get('anthropic_base_url')
        or ''
    ).rstrip('/')


def probe_llm_reachable(*, timeout: float = 2.0) -> str | None:
    """轻量探活：按 primary→fallback 顺序测 base URL 可达，不跑完整 generate。"""
    import httpx

    primary, fallback = _resolve_llm_configs()
    last_error: Exception | None = None

    with httpx.Client(timeout=timeout) as client:
        for label, block in [('primary', primary), ('fallback', fallback)]:
            base_url = _llm_endpoint_url(block)
            if not base_url:
                continue
            try:
                client.get(base_url)
                logger.debug('llm probe %s ok: %s', label, base_url)
                return base_url
            except Exception as exc:
                last_error = exc
                logger.debug('llm probe %s failed (%s): %s', label, base_url, exc)

    if last_error:
        raise last_error
    return None


def get_embedder_config() -> tuple[str, str]:
    """返回 (model, base_url)。"""
    config = _read_config()
    embedder = config.get('embedder') or {}
    inner = embedder.get('config') if isinstance(embedder.get('config'), dict) else embedder
    model = inner.get('model', 'bge-m3')
    base_url = inner.get('base_url') or inner.get('ollama_base_url') or 'http://localhost:11434'
    return model, base_url.rstrip('/')


def embed_text(text: str) -> list[float]:
    """Ollama embedding。"""
    model, base_url = get_embedder_config()
    payload = json.dumps({'model': model, 'prompt': text}).encode()
    request = urllib.request.Request(
        f'{base_url}/api/embeddings',
        data=payload,
        headers={'Content-Type': 'application/json'},
    )
    response = urllib.request.urlopen(request, timeout=30)
    vector = json.loads(response.read()).get('embedding', [])
    if not vector:
        raise RuntimeError('embedding 返回为空，请确认 Ollama 已启动')
    return vector


_llm_singleton: LlmClient | None = None


def get_llm_client() -> LlmClient:
    global _llm_singleton
    if _llm_singleton is None:
        _llm_singleton = LlmClient()
    return _llm_singleton
