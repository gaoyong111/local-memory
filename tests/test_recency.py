"""recency 回血一半单测：折半数学、bonus 上限、merge 接线、丢弃收集。"""

from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from hybrid_search import (  # noqa: E402
    DAY_SECONDS,
    RECENCY_BONUS_MAX,
    merge_and_rank,
    next_accessed_ts,
    recency_bonus,
)


class RecencyBonusTests(unittest.TestCase):
    def test_never_accessed_gets_max(self) -> None:
        self.assertAlmostEqual(recency_bonus(None), RECENCY_BONUS_MAX)

    def test_recent_access_near_max(self) -> None:
        self.assertGreater(recency_bonus(time.time() - 60), RECENCY_BONUS_MAX * 0.99)

    def test_half_life_at_30_days(self) -> None:
        bonus = recency_bonus(time.time() - 30 * DAY_SECONDS)
        self.assertAlmostEqual(bonus, RECENCY_BONUS_MAX / 2, places=6)

    def test_very_old_near_zero(self) -> None:
        self.assertLess(recency_bonus(time.time() - 365 * DAY_SECONDS), RECENCY_BONUS_MAX * 0.001)

    def test_future_ts_clamped(self) -> None:
        self.assertAlmostEqual(recency_bonus(time.time() + 9999), RECENCY_BONUS_MAX)


class NextAccessedTsTests(unittest.TestCase):
    def test_first_hit_records_now(self) -> None:
        now = 1000.0
        self.assertEqual(next_accessed_ts(None, now), now)

    def test_rehit_halves_elapsed(self) -> None:
        now = 1000.0 * DAY_SECONDS
        old = now - 10 * DAY_SECONDS
        self.assertAlmostEqual(next_accessed_ts(old, now), now - 5 * DAY_SECONDS)

    def test_stale_or_future_old_records_now(self) -> None:
        now = 1000.0
        self.assertEqual(next_accessed_ts(0.0, now), now)
        self.assertEqual(next_accessed_ts(now + 50, now), now)

    def test_repeated_rehit_converges_not_accumulates(self) -> None:
        """连续高频命中：等效间隔按几何折半收敛，不会推成「刚刚」（防刷分核心）。"""
        now = 1000.0 * DAY_SECONDS
        ts = now - 10 * DAY_SECONDS
        for _ in range(10):
            ts = next_accessed_ts(ts, now + 1)
        elapsed = (now + 10) - ts
        self.assertLess(elapsed, 1 * DAY_SECONDS)
        self.assertGreater(elapsed, 0)


class MergeRecencyWiringTests(unittest.TestCase):
    def _kw_item(self, mid: str, score: float) -> dict:
        return {
            'id': mid, 'text': f'记忆{mid}', 'keyword_score': score, 'vector_score': 0.0,
            'score': score, 'source': 'keyword', 'project': '', 'category': 'reference', 'lang': 'zh',
        }

    @mock.patch('memory_sync.touch_last_accessed')
    @mock.patch('memory_sync.load_last_accessed')
    def test_apply_recency_adds_bonus_and_touches(
        self, load_last: mock.Mock, touch: mock.Mock,
    ) -> None:
        now = time.time()
        load_last.return_value = {'m1': now - 30 * DAY_SECONDS, 'm2': None}
        results = merge_and_rank(
            [self._kw_item('m1', 0.1), self._kw_item('m2', 0.08)],
            [],
            apply_recency=True,
        )
        by_id = {item['id']: item for item in results}
        self.assertAlmostEqual(by_id['m1']['recency_bonus'], RECENCY_BONUS_MAX / 2, places=6)
        self.assertAlmostEqual(by_id['m2']['recency_bonus'], RECENCY_BONUS_MAX, places=6)
        # score = rrf + bonus
        self.assertAlmostEqual(by_id['m2']['score'], by_id['m2']['rrf_score'] + RECENCY_BONUS_MAX, places=6)
        touched = {mid: ts for mid, ts in touch.call_args[0][0]}
        self.assertAlmostEqual(touched['m1'], now - 15 * DAY_SECONDS, delta=5)
        self.assertAlmostEqual(touched['m2'], now, delta=5)

    @mock.patch('memory_sync.load_last_accessed', side_effect=RuntimeError('db gone'))
    @mock.patch('memory_sync.touch_last_accessed')
    def test_recency_failure_does_not_break_search(self, touch: mock.Mock, _load: mock.Mock) -> None:
        results = merge_and_rank([self._kw_item('m1', 0.1)], [], apply_recency=True)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['recency_bonus'], 0.0)
        touch.assert_not_called()

    @mock.patch('memory_sync.load_last_accessed')
    @mock.patch('memory_sync.touch_last_accessed')
    def test_no_recency_by_default(self, touch: mock.Mock, load_last: mock.Mock) -> None:
        results = merge_and_rank([self._kw_item('m1', 0.1)], [])
        self.assertNotIn('recency_bonus', results[0])
        load_last.assert_not_called()
        touch.assert_not_called()


class DropCollectionTests(unittest.TestCase):
    def _kw_item(self, mid: str, score: float) -> dict:
        return {
            'id': mid, 'text': f'记忆{mid}', 'keyword_score': score, 'vector_score': 0.0,
            'score': score, 'source': 'keyword', 'project': '', 'category': 'reference', 'lang': 'zh',
        }

    def test_rank_out_collected(self) -> None:
        dropped: list[dict] = []
        merge_and_rank(
            [self._kw_item('m1', 0.1), self._kw_item('m2', 0.05), self._kw_item('m3', 0.01)],
            [],
            max_results=1,
            dropped=dropped,
        )
        stages = {(item['id'], item['stage']) for item in dropped}
        self.assertIn(('m2', 'rank_out'), stages)
        self.assertIn(('m3', 'rank_out'), stages)

    def test_quota_drop_collected(self) -> None:
        dropped: list[dict] = []
        merge_and_rank(
            [self._kw_item('m1', 0.1)],
            [self._kw_item('v1', 0.2)],
            project='demo',
            max_results=1,
            dropped=dropped,
        )
        self.assertTrue(any(item['stage'] == 'project_quota' for item in dropped))


if __name__ == '__main__':
    unittest.main()
