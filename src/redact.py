"""写入路径敏感凭证脱敏 — 所有写入统一过 memory_store.add，此处收口。

保守清单：只匹配知名凭证格式，不做高熵启发式（误伤不可控）。
"""

from __future__ import annotations

import re

# (label, pattern)。顺序即优先级，前面的先替换（JWT 需在通用 token 前匹配完整三段式）。
REDACT_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    # 私钥整块（含头尾）
    ('private_key', re.compile(
        r'-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----'
    )),
    # JWT 三段式
    ('jwt', re.compile(r'\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b')),
    # GitHub PAT 细分格式（github_pat_ 前缀较长，须在 ghp_ 系列前）
    ('github_pat', re.compile(r'\bgithub_pat_[A-Za-z0-9_]{22,255}\b')),
    ('github_token', re.compile(r'\bgh[posuvr]_[A-Za-z0-9]{36,255}\b')),
    # OpenAI / Anthropic
    ('openai_key', re.compile(r'\bsk-[A-Za-z0-9_-]{20,255}\b')),
    # AWS Access Key ID
    ('aws_key', re.compile(r'\bAKIA[0-9A-Z]{16}\b')),
    # npm
    ('npm_token', re.compile(r'\bnpm_[A-Za-z0-9]{36}\b')),
    # Slack
    ('slack_token', re.compile(r'\bxox[abprs]-[A-Za-z0-9-]{10,255}\b')),
    # Google API key
    ('google_key', re.compile(r'\bAIza[0-9A-Za-z_-]{35}\b')),
]


def redact(content: str) -> tuple[str, list[str]]:
    """替换敏感凭证为 [REDACTED:<label>]，返回 (脱敏后文本, 命中 label 列表)。"""
    if not content:
        return content, []
    findings: list[str] = []
    cleaned = content
    for label, pattern in REDACT_PATTERNS:
        cleaned, count = pattern.subn(f'[REDACTED:{label}]', cleaned)
        if count:
            findings.append(label)
    return cleaned, findings
