"""memory_store.add 单测（P6，mock 存储层）。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from memory_store import add  # noqa: E402
from memory_sync import SyncResult  # noqa: E402


class MemoryStoreAddTests(unittest.TestCase):
    @mock.patch('memory_store.record_memory_event')
    @mock.patch('memory_store.run_merge_check', return_value=None)
    @mock.patch('memory_store.get_llm_client')
    @mock.patch('memory_store.sync_active_insert')
    @mock.patch('memory_store._chroma_upsert')
    @mock.patch('memory_store.ensure_pool_schema')
    def test_add_success(
        self,
        _schema: mock.Mock,
        chroma_upsert: mock.Mock,
        sync_insert: mock.Mock,
        _llm: mock.Mock,
        _merge: mock.Mock,
        record_event: mock.Mock,
    ) -> None:
        sync_insert.return_value = SyncResult(op='insert', memory_id='x', ok=True, counts={'active': 1})

        result = add(
            '测试记忆内容',
            metadata={'category': 'reference'},
            project='local-memory',
            actor='test',
        )

        self.assertFalse(result.dropped)
        self.assertEqual(result.event, 'ADD')
        self.assertEqual(result.project, 'local-memory')
        chroma_upsert.assert_called_once()
        sync_insert.assert_called_once()
        record_event.assert_called_once()

    @mock.patch('hybrid_search.get_chroma_collection')
    @mock.patch('hybrid_search.get_chroma_client')
    @mock.patch('memory_store.sync_active_insert')
    @mock.patch('memory_store._chroma_upsert')
    @mock.patch('memory_store.ensure_pool_schema')
    def test_add_active_sync_failure_rolls_back_chroma(
        self,
        _schema: mock.Mock,
        chroma_upsert: mock.Mock,
        sync_insert: mock.Mock,
        chroma_client: mock.Mock,
        chroma_collection: mock.Mock,
    ) -> None:
        sync_insert.return_value = SyncResult(
            op='insert',
            memory_id='x',
            ok=False,
            detail='active insert failed',
        )
        col = mock.Mock()
        chroma_collection.return_value = col
        chroma_client.return_value = mock.Mock()

        with self.assertRaises(RuntimeError):
            add('fail case', metadata={'category': 'reference'}, project='demo')

        chroma_upsert.assert_called_once()
        col.delete.assert_called_once()
        delete_ids = col.delete.call_args.kwargs.get('ids') or col.delete.call_args.args[0]
        self.assertEqual(len(delete_ids), 1)


if __name__ == '__main__':
    unittest.main()
