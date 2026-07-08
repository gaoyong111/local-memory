"""memory_store 读路径单测（P6，mock 存储层）。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from memory_store import MemoryRecord, get_all_memories  # noqa: E402


class MemoryStoreGetAllTests(unittest.TestCase):
    @mock.patch('memory_sync.get_active_record')
    @mock.patch('memory_sync.load_active_memories')
    @mock.patch('memory_sync.migrate_active_if_needed')
    def test_get_all_filters_by_project(
        self,
        _migrate: mock.Mock,
        load_active: mock.Mock,
        get_record: mock.Mock,
    ) -> None:
        load_active.return_value = {
            'a': 'alpha',
            'b': 'beta',
            'g': 'global',
        }

        def _record(memory_id: str) -> dict:
            mapping = {
                'a': {'project': 'proj-a', 'category': 'reference', 'lang': 'zh'},
                'b': {'project': 'proj-b', 'category': 'reference', 'lang': 'zh'},
                'g': {'project': '', 'category': 'reference', 'lang': 'zh'},
            }
            return {**mapping[memory_id], 'content': load_active.return_value[memory_id]}

        get_record.side_effect = _record

        all_rows = get_all_memories()
        self.assertEqual(len(all_rows), 3)

        filtered = get_all_memories(project='proj-a')
        self.assertEqual(len(filtered), 1)
        self.assertIsInstance(filtered[0], MemoryRecord)
        self.assertEqual(filtered[0].memory_id, 'a')
        self.assertEqual(filtered[0].project, 'proj-a')


if __name__ == '__main__':
    unittest.main()
