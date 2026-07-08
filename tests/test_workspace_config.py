"""workspace_config 单元测试（P6）。"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from workspace_config import (  # noqa: E402
    WorkspaceConfig,
    check_write_access,
    filter_by_read_access,
    load_workspace_config,
    merge_aliases,
    resolve_pool_id,
)


class WorkspaceConfigTests(unittest.TestCase):
    def test_merge_aliases_workspace_overrides_pool(self) -> None:
        merged = merge_aliases({'foo': 'pool-a'}, {'foo': 'ws-b', 'bar': 'ws-c'})
        self.assertEqual(merged, {'foo': 'ws-b', 'bar': 'ws-c'})

    def test_filter_read_none_passthrough(self) -> None:
        rows = [{'project': 'a'}, {'project': 'b'}]
        self.assertEqual(filter_by_read_access(rows, None), rows)

    def test_filter_read_empty_list(self) -> None:
        rows = [{'project': 'a'}]
        self.assertEqual(filter_by_read_access(rows, []), [])

    def test_filter_read_wildcard(self) -> None:
        rows = [{'project': 'a'}, {'project': ''}]
        self.assertEqual(len(filter_by_read_access(rows, ['*'])), 2)

    def test_filter_read_project_list(self) -> None:
        rows = [
            {'project': 'local-memory'},
            {'project': 'favorites'},
            {'project': ''},
        ]
        filtered = filter_by_read_access(rows, ['local-memory', ''])
        self.assertEqual([r['project'] for r in filtered], ['local-memory', ''])

    def test_check_write_access_none_allows(self) -> None:
        self.assertIsNone(check_write_access('anything', None))

    def test_check_write_access_restricted(self) -> None:
        msg = check_write_access('favorites', ['local-memory'])
        self.assertIsNotNone(msg)
        self.assertIn('favorites', msg or '')

    def test_resolve_pool_id_env_over_config(self) -> None:
        config = WorkspaceConfig(pool='from-config')
        with mock.patch.dict(os.environ, {'MEMORY_POOL': 'from-env'}):
            self.assertEqual(resolve_pool_id(config), 'from-env')

    def test_load_workspace_config_walks_up_to_git_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / 'repo'
            nested = repo / 'src' / 'pkg'
            nested.mkdir(parents=True)
            (repo / '.git').mkdir()
            cfg = repo / '.cursor' / 'memory.json'
            cfg.parent.mkdir()
            cfg.write_text(
                json.dumps({'pool': 'default', 'access': {'read': ['demo']}}),
                encoding='utf-8',
            )
            loaded = load_workspace_config(str(nested))
            self.assertTrue(loaded.is_configured)
            self.assertEqual(loaded.read_projects, ['demo'])
            self.assertEqual(loaded.pool, 'default')


if __name__ == '__main__':
    unittest.main()
