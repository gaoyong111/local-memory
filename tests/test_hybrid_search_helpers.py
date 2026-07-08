"""hybrid_search 分词、RRF 融合、输出格式单测。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from hybrid_search import (  # noqa: E402
    HOOK_RESULTS_HEADER,
    RESULTS_HEADER,
    extract_keywords,
    format_results_lines,
    merge_and_rank,
    primary_cjk_keyword,
    query_has_cjk,
)


class HybridSearchHelperTests(unittest.TestCase):
    def test_extract_keywords_cjk_sliding_window(self) -> None:
        keywords = extract_keywords('中药购物车计数')
        self.assertIn('中药', keywords)
        self.assertIn('购物车', keywords)
        self.assertIn('中药购', keywords)

    def test_primary_cjk_keyword_caps_length(self) -> None:
        self.assertEqual(primary_cjk_keyword('混合检索优化方案'), '混合检索优化')

    def test_query_has_cjk(self) -> None:
        self.assertTrue(query_has_cjk('测试 query'))
        self.assertFalse(query_has_cjk('hello world'))

    def test_merge_and_rank_prefers_dual_path_hits(self) -> None:
        shared = {'id': 'dual', 'text': 'dual hit', 'project': ''}
        keyword_results = [{**shared, 'score': 10.0, 'keyword_score': 10.0}]
        vector_results = [{**shared, 'score': 0.85, 'vector_score': 0.85}]
        single_kw = {'id': 'kw-only', 'text': 'kw only', 'project': '', 'score': 20.0, 'keyword_score': 20.0}

        merged = merge_and_rank([single_kw, *keyword_results], vector_results, max_results=2)
        self.assertGreaterEqual(len(merged), 1)
        self.assertEqual(merged[0]['id'], 'dual')
        self.assertGreater(float(merged[0]['rrf_score']), 0)

    def test_format_results_lines_uses_unified_header(self) -> None:
        text = format_results_lines([{'id': 'x', 'text': 'hello', 'project': '', 'source': 'keyword', 'rank': 1}])
        self.assertTrue(text.startswith(RESULTS_HEADER))
        self.assertIn('#1 [全局]', text)

    def test_hook_header_distinct_from_default(self) -> None:
        self.assertNotEqual(HOOK_RESULTS_HEADER, RESULTS_HEADER)
        self.assertIn('自动注入', HOOK_RESULTS_HEADER)


if __name__ == '__main__':
    unittest.main()
