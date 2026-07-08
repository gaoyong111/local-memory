"""llm_client.probe_llm_reachable 单元测试（P6）。"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from llm_client import _llm_endpoint_url, _resolve_llm_configs, probe_llm_reachable  # noqa: E402


class LlmEndpointUrlTests(unittest.TestCase):
    def test_ollama_uses_ollama_base_url(self) -> None:
        url = _llm_endpoint_url({'provider': 'ollama', 'ollama_base_url': 'http://127.0.0.1:11434/'})
        self.assertEqual(url, 'http://127.0.0.1:11434')

    def test_anthropic_prefers_anthropic_base_url(self) -> None:
        url = _llm_endpoint_url({
            'provider': 'anthropic',
            'anthropic_base_url': 'http://proxy/v1',
            'base_url': 'http://other',
        })
        self.assertEqual(url, 'http://proxy/v1')


class ResolveLlmConfigsTests(unittest.TestCase):
    @mock.patch('llm_client._read_config')
    def test_mem0_fallback_config_expanduser(self, mock_read) -> None:
        mock_read.side_effect = [
            {'llm': {'provider': 'anthropic', 'base_url': 'http://primary'}},
            {'llm': {'provider': 'ollama', 'base_url': 'http://fallback-ollama'}},
        ]
        fake_expanded = mock.Mock()
        fake_expanded.is_file.return_value = True
        with mock.patch.dict(os.environ, {'MEM0_FALLBACK_CONFIG': '~/fallback-config.json'}, clear=False):
            with mock.patch('llm_client.Path') as mock_path_cls:
                mock_path_cls.return_value.expanduser.return_value = fake_expanded
                primary, fallback = _resolve_llm_configs()
        self.assertEqual(primary.get('base_url'), 'http://primary')
        self.assertEqual(fallback.get('base_url'), 'http://fallback-ollama')
        mock_path_cls.return_value.expanduser.assert_called_once()


class ProbeLlmReachableTests(unittest.TestCase):
    @mock.patch('httpx.Client')
    @mock.patch('llm_client._resolve_llm_configs')
    def test_primary_ok_returns_primary_url(self, mock_resolve, mock_client_cls) -> None:
        mock_resolve.return_value = (
            {'provider': 'anthropic', 'base_url': 'http://primary'},
            {'provider': 'ollama', 'base_url': 'http://fallback'},
        )
        mock_client_cls.return_value.__enter__.return_value.get.return_value = mock.Mock()

        result = probe_llm_reachable(timeout=1.0)

        self.assertEqual(result, 'http://primary')
        mock_client_cls.return_value.__enter__.return_value.get.assert_called_once_with('http://primary')

    @mock.patch('httpx.Client')
    @mock.patch('llm_client._resolve_llm_configs')
    def test_primary_fail_fallback_ok(self, mock_resolve, mock_client_cls) -> None:
        mock_resolve.return_value = (
            {'provider': 'anthropic', 'base_url': 'http://primary'},
            {'provider': 'ollama', 'base_url': 'http://localhost:11434'},
        )
        client = mock_client_cls.return_value.__enter__.return_value
        client.get.side_effect = [TimeoutError('primary down'), mock.Mock()]

        result = probe_llm_reachable(timeout=1.0)

        self.assertEqual(result, 'http://localhost:11434')
        self.assertEqual(client.get.call_count, 2)

    @mock.patch('llm_client._resolve_llm_configs')
    def test_no_urls_returns_none(self, mock_resolve) -> None:
        mock_resolve.return_value = ({}, None)
        self.assertIsNone(probe_llm_reachable(timeout=1.0))

    @mock.patch('httpx.Client')
    @mock.patch('llm_client._resolve_llm_configs')
    def test_all_fail_raises_last_error(self, mock_resolve, mock_client_cls) -> None:
        mock_resolve.return_value = (
            {'provider': 'anthropic', 'base_url': 'http://primary'},
            {'provider': 'ollama', 'base_url': 'http://localhost:11434'},
        )
        client = mock_client_cls.return_value.__enter__.return_value
        client.get.side_effect = [OSError('primary'), OSError('fallback')]

        with self.assertRaises(OSError) as ctx:
            probe_llm_reachable(timeout=1.0)
        self.assertIn('fallback', str(ctx.exception))


if __name__ == '__main__':
    unittest.main()
