"""pool_manager registry 操作单测（P6）。"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from pool_manager import get_active_pool_id, list_pools, switch_pool  # noqa: E402


class PoolManagerTests(unittest.TestCase):
    def _write_registry(self, root: Path, *, active: str = 'default') -> None:
        default_dir = root / 'pools' / 'default'
        other_dir = root / 'pools' / 'other'
        default_dir.mkdir(parents=True)
        other_dir.mkdir(parents=True)
        (default_dir / 'pool.meta.json').write_text(
            json.dumps({'pool_id': 'default', 'chroma_collection': 'mem0'}),
            encoding='utf-8',
        )
        (other_dir / 'pool.meta.json').write_text(
            json.dumps({'pool_id': 'other', 'chroma_collection': 'memories'}),
            encoding='utf-8',
        )
        registry = {
            'active_pool': active,
            'pools': {
                'default': {'path': str(default_dir), 'created_at': '2026-07-07T00:00:00+00:00'},
                'other': {'path': str(other_dir), 'created_at': '2026-07-07T00:00:00+00:00'},
            },
        }
        (root / 'registry.json').write_text(json.dumps(registry, indent=2), encoding='utf-8')

    def test_list_pools_marks_active(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_registry(root, active='other')
            entries = list_pools(root)
            self.assertEqual([row.pool_id for row in entries], ['other', 'default'])
            self.assertTrue(entries[0].active)
            self.assertFalse(entries[1].active)

    def test_switch_pool_updates_registry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_registry(root, active='default')
            switched = switch_pool('other', root)
            self.assertEqual(switched.pool_id, 'other')
            self.assertEqual(get_active_pool_id(root), 'other')


if __name__ == '__main__':
    unittest.main()
