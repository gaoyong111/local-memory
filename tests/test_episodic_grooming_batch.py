"""episodic_grooming_batch 单元测试（P6）。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from episodic_grooming_batch import run_episodic_grooming  # noqa: E402


class EpisodicGroomingBatchTests(unittest.TestCase):
    @mock.patch('episodic_grooming_batch.write_merge_hints')
    @mock.patch('episodic_grooming_batch._update_chroma_metadata')
    @mock.patch('episodic_grooming_batch.analyze_memory_grooming')
    @mock.patch('episodic_grooming_batch.build_merge_hints', return_value=[])
    @mock.patch('episodic_grooming_batch._load_llm', return_value=None)
    @mock.patch('episodic_grooming_batch._load_memories')
    def test_dry_run_skips_writes(
        self,
        mock_load,
        _mock_llm,
        _mock_hints,
        mock_analyze,
        mock_update,
        mock_write,
    ) -> None:
        mock_load.return_value = [{
            'id': 'abc',
            'text': 'test',
            'project': '',
            'category': 'episodic',
            'metadata': {},
            'grooming': {},
        }]
        from grooming_episodic import GroomingDecision

        mock_analyze.return_value = (GroomingDecision(action='keep', reason='ok'), [])

        summary = run_episodic_grooming(dry_run=True)

        self.assertEqual(summary['analyzed'], 1)
        self.assertTrue(summary['dry_run'])
        mock_update.assert_not_called()
        mock_write.assert_not_called()


if __name__ == '__main__':
    unittest.main()
