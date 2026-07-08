"""hybrid_search.merge_and_rank read 过滤顺序测试（P6）。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from hybrid_search import merge_and_rank  # noqa: E402


def _item(memory_id: str, project: str, *, kw_rank: int, vec_rank: int) -> tuple[dict, dict]:
    base = {'id': memory_id, 'text': memory_id, 'project': project}
    kw = {**base, 'score': 1.0 / kw_rank, 'keyword_score': 1.0 / kw_rank} if kw_rank else None
    vec = {**base, 'score': 1.0 / vec_rank, 'vector_score': 1.0 / vec_rank} if vec_rank else None
    kw_list = [kw] if kw else []
    vec_list = [vec] if vec else []
    return kw_list, vec_list


class MergeAndRankReadFilterTests(unittest.TestCase):
    def test_read_filter_before_quota_when_project_set(self) -> None:
        """allowed 项目不应被 project quota 挤掉（workspace-binding §4.3）。"""
        kw_a, vec_a = _item('a', 'allowed', kw_rank=1, vec_rank=0)
        kw_b, vec_b = _item('b', 'blocked', kw_rank=2, vec_rank=1)
        kw_c, vec_c = _item('c', 'allowed', kw_rank=3, vec_rank=2)
        keyword_results = kw_a + kw_b + kw_c
        vector_results = vec_a + vec_b + vec_c

        results = merge_and_rank(
            keyword_results,
            vector_results,
            project='allowed',
            max_results=2,
            read_projects=['allowed'],
        )
        projects = {item['project'] for item in results}
        self.assertEqual(projects, {'allowed'})
        self.assertLessEqual(len(results), 2)

    def test_read_filter_limits_global_scope(self) -> None:
        kw_a, vec_a = _item('x', 'only-me', kw_rank=1, vec_rank=1)
        kw_b, vec_b = _item('y', 'other', kw_rank=2, vec_rank=2)
        results = merge_and_rank(
            kw_a + kw_b,
            vec_a + vec_b,
            project='',
            max_results=5,
            read_projects=['only-me'],
        )
        self.assertEqual([r['project'] for r in results], ['only-me'])


if __name__ == '__main__':
    unittest.main()
