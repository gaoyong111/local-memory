"""mem_viewer workspace 默认行为单测（P6）。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

import mem_viewer  # noqa: E402


class MemViewerWorkspaceTests(unittest.TestCase):
    def setUp(self) -> None:
        mem_viewer._viewer_workspace_config = None

    def test_bootstrap_without_workspace_root_is_unconfigured(self) -> None:
        with mock.patch.object(mem_viewer, 'VIEWER_WORKSPACE_ROOT', None):
            config = mem_viewer._bootstrap_viewer_workspace()
            self.assertFalse(config.is_configured)
            self.assertIsNone(config.read_projects)
            self.assertIsNone(config.write_projects)

    def test_bootstrap_loads_repo_config_when_root_set(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        with mock.patch.object(mem_viewer, 'VIEWER_WORKSPACE_ROOT', str(repo_root)):
            config = mem_viewer._bootstrap_viewer_workspace()
            self.assertTrue(config.is_configured)
            self.assertIsNotNone(config.write_projects)
            # read_projects 会加载，但 viewer 检索/图谱不消费该字段
            self.assertIsNotNone(config.read_projects)


if __name__ == '__main__':
    unittest.main()
