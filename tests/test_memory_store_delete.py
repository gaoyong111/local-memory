"""memory_store.delete 单测（P6，mock archive_delete）。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from memory_store import DeleteResult, delete  # noqa: E402


class MemoryStoreDeleteTests(unittest.TestCase):
    @mock.patch('memory_store.ensure_pool_schema')
    @mock.patch('memory_delete.archive_delete')
    def test_delete_ok(self, archive_delete: mock.Mock, _schema: mock.Mock) -> None:
        archive_delete.return_value = {'counts': {'active': 1}}
        result = delete('mid-1', 'duplicate', actor='test')
        self.assertTrue(result.ok)
        self.assertEqual(result.memory_id, 'mid-1')
        archive_delete.assert_called_once_with('mid-1', 'duplicate', actor='test', source='test')

    @mock.patch('memory_store.ensure_pool_schema')
    @mock.patch('memory_delete.archive_delete')
    def test_delete_sync_error_returns_false(self, archive_delete: mock.Mock, _schema: mock.Mock) -> None:
        from memory_sync import SyncError

        archive_delete.side_effect = SyncError('delete', {'active': 1}, {'active': 0}, 'active')
        result = delete('mid-2', 'dup')
        self.assertFalse(result.ok)
        self.assertIn('sync delete failed', result.detail)

    @mock.patch('memory_store.ensure_pool_schema')
    @mock.patch('memory_delete.archive_delete')
    def test_delete_not_found_chinese_message(self, archive_delete: mock.Mock, _schema: mock.Mock) -> None:
        archive_delete.side_effect = ValueError('记忆不存在')
        result = delete('gone-id', 'cleanup')
        self.assertTrue(result.ok)
        self.assertEqual(result.detail, 'already_gone')


if __name__ == '__main__':
    unittest.main()
