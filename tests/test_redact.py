"""redact 脱敏单测：凭证模式命中 + 误伤边界。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from redact import redact  # noqa: E402


class RedactTests(unittest.TestCase):
    def test_github_token(self) -> None:
        text = 'token 是 ghp_ABCDEFGHIJKLMNOPQRSTUVWXab12CD34EF56，别提交'
        cleaned, findings = redact(text)
        self.assertEqual(findings, ['github_token'])
        self.assertIn('[REDACTED:github_token]', cleaned)
        self.assertNotIn('ghp_', cleaned)

    def test_github_pat(self) -> None:
        cleaned, findings = redact('key: github_pat_ABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890abcd')
        self.assertEqual(findings, ['github_pat'])
        self.assertIn('[REDACTED:github_pat]', cleaned)

    def test_openai_key(self) -> None:
        cleaned, findings = redact('OPENAI_API_KEY=sk-abcdefghijklmnop1234567890abcd')
        self.assertEqual(findings, ['openai_key'])
        self.assertNotIn('sk-', cleaned)

    def test_aws_key(self) -> None:
        cleaned, findings = redact('AKIAIOSFODNN7EXAMPLE')
        self.assertEqual(findings, ['aws_key'])

    def test_jwt(self) -> None:
        token = 'eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJVadQssw5c'
        cleaned, findings = redact(f'Bearer {token}')
        self.assertEqual(findings, ['jwt'])
        self.assertNotIn(token, cleaned)

    def test_private_key_block(self) -> None:
        text = '-----BEGIN RSA PRIVATE KEY-----\nMIIEowABC\n-----END RSA PRIVATE KEY-----\n后续正文保留'
        cleaned, findings = redact(text)
        self.assertEqual(findings, ['private_key'])
        self.assertNotIn('MIIEow', cleaned)
        self.assertIn('后续正文保留', cleaned)

    def test_npm_slack_google(self) -> None:
        cases = [
            ('npm_' + 'A' * 36, 'npm_token'),
            ('xoxb-1234567890-abcdef', 'slack_token'),
            ('AIzaSyA1234567890abcdefghijklmnopqrstuv', 'google_key'),
        ]
        for token, label in cases:
            cleaned, findings = redact(f'密钥 {token}')
            self.assertEqual(findings, [label], token)
            self.assertIn(f'[REDACTED:{label}]', cleaned)

    def test_no_false_positive_on_normal_text(self) -> None:
        text = 'sk-开头的是密钥前缀，ghp_ 也是；tokenizer.split() 拆分；task-123456 完成'
        cleaned, findings = redact(text)
        self.assertEqual(findings, [])
        self.assertEqual(cleaned, text)

    def test_short_sk_not_redacted(self) -> None:
        text = 'sk-123'  # 长度不足 20
        cleaned, findings = redact(text)
        self.assertEqual(findings, [])
        self.assertEqual(cleaned, text)

    def test_empty_content(self) -> None:
        self.assertEqual(redact(''), ('', []))

    def test_multiple_findings(self) -> None:
        cleaned, findings = redact('a=AKIAIOSFODNN7EXAMPLE b=sk-abcdefghijklmnop1234567890abcd')
        self.assertEqual(sorted(findings), ['aws_key', 'openai_key'])
        self.assertNotIn('AKIA', cleaned)


if __name__ == '__main__':
    unittest.main()
