"""grooming_episodic 决策解析与规则兜底单测。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from grooming_episodic import advise_episodic_grooming  # noqa: E402


class _FakeLlm:
    def __init__(self, payload: str) -> None:
        self._payload = payload

    def generate_response(self, messages, response_format=None) -> str:
        return self._payload


class GroomingEpisodicTests(unittest.TestCase):
    def test_advise_parses_llm_delete_json(self) -> None:
        llm = _FakeLlm('{"action":"delete","target_category":"","reason":"已被 reference 覆盖"}')
        decision = advise_episodic_grooming(llm, 'id-1', '重复内容', category='episodic')
        self.assertEqual(decision.action, 'delete')
        self.assertIn('覆盖', decision.reason)

    def test_rule_based_delete_generic_memo(self) -> None:
        decision = advise_episodic_grooming(
            None,
            'id-2',
            'Redis 缓存击穿要用分布式锁',
            category='episodic',
        )
        self.assertEqual(decision.action, 'delete')

    def test_rule_based_promote_workflow_with_why(self) -> None:
        decision = advise_episodic_grooming(
            None,
            'id-3',
            'Why：复盘要增量。How to apply：按 last_review_date 扫描流程步骤。',
            category='episodic',
        )
        self.assertEqual(decision.action, 'promote')
        self.assertEqual(decision.target_category, 'workflow')


if __name__ == '__main__':
    unittest.main()
