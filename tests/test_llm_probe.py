"""llm_client.probe_llm_reachable 单元测试（P6）。"""

from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from llm_client import (  # noqa: E402
    _apply_auto_follow,
    _llm_endpoint_url,
    _record_degradation,
    _resolve_llm_configs,
    LlmClient,
    probe_llm_reachable,
)


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
    def test_memory_fallback_config_expanduser(self, mock_read) -> None:
        mock_read.side_effect = [
            {'llm': {'provider': 'anthropic', 'base_url': 'http://primary'}},
            {'llm': {'provider': 'ollama', 'base_url': 'http://fallback-ollama'}},
        ]
        fake_expanded = mock.Mock()
        fake_expanded.is_file.return_value = True
        with mock.patch.dict(os.environ, {'MEMORY_FALLBACK_CONFIG': '~/fallback-config.json'}, clear=False):
            with mock.patch('llm_client.Path') as mock_path_cls:
                mock_path_cls.return_value.expanduser.return_value = fake_expanded
                primary, fallback = _resolve_llm_configs()
        self.assertEqual(primary.get('base_url'), 'http://primary')
        self.assertEqual(fallback.get('base_url'), 'http://fallback-ollama')
        mock_path_cls.return_value.expanduser.assert_called_once()


class AutoFollowTests(unittest.TestCase):
    @mock.patch('llm_client._read_session_llm_env')
    def test_auto_follow_overrides_session_model(self, mock_env) -> None:
        mock_env.return_value = {
            'ANTHROPIC_BASE_URL': 'http://192.168.2.252:7080',
            'ANTHROPIC_MODEL': 'deepseek-v4-flash',
            'ANTHROPIC_AUTH_TOKEN': 'sk-test',
        }
        block = {
            'provider': 'anthropic',
            'auto_follow': True,
            'model': 'stale',
            'base_url': 'http://192.168.2.252:6200',
            'api_key_env': 'NEWAPI_KEY',
            'temperature': 0.1,
        }

        resolved = _apply_auto_follow(block)

        self.assertEqual(resolved['provider'], 'anthropic')
        self.assertEqual(resolved['base_url'], 'http://192.168.2.252:7080')
        self.assertEqual(resolved['model'], 'deepseek-v4-flash')
        self.assertEqual(resolved['api_key'], 'sk-test')
        self.assertNotIn('api_key_env', resolved)

    @mock.patch('llm_client._read_session_llm_env')
    def test_auto_follow_env_missing_keeps_static(self, mock_env) -> None:
        mock_env.return_value = {}
        block = {'provider': 'anthropic', 'auto_follow': True, 'base_url': 'http://static'}

        resolved = _apply_auto_follow(block)

        self.assertEqual(resolved['base_url'], 'http://static')

    @mock.patch('llm_client._read_session_llm_env')
    def test_auto_follow_partial_env_falls_back(self, mock_env) -> None:
        mock_env.return_value = {
            'ANTHROPIC_BASE_URL': 'http://192.168.2.252:7080',
            'ANTHROPIC_MODEL': 'deepseek-v4-flash',
        }
        block = {'provider': 'anthropic', 'auto_follow': True, 'base_url': 'http://static', 'api_key': 'static-key'}

        resolved = _apply_auto_follow(block)

        self.assertEqual(resolved['base_url'], 'http://static')
        self.assertEqual(resolved['api_key'], 'static-key')

    @mock.patch('llm_client._read_session_llm_env')
    @mock.patch('llm_client._read_config')
    def test_resolve_auto_follow_from_config(self, mock_read, mock_env) -> None:
        mock_env.return_value = {
            'ANTHROPIC_BASE_URL': 'http://192.168.2.252:7080',
            'ANTHROPIC_MODEL': 'deepseek-v4-flash',
            'ANTHROPIC_AUTH_TOKEN': 'sk-test',
        }
        mock_read.return_value = {
            'llm': {'provider': 'anthropic', 'auto_follow': True, 'base_url': 'http://stale'},
            'fallback_llm': {'provider': 'ollama', 'base_url': 'http://localhost:11434'},
        }

        primary, fallback = _resolve_llm_configs()

        self.assertEqual(primary.get('base_url'), 'http://192.168.2.252:7080')
        self.assertEqual(primary.get('model'), 'deepseek-v4-flash')
        self.assertEqual(fallback.get('base_url'), 'http://localhost:11434')


class DegradationRecordTests(unittest.TestCase):
    @mock.patch('llm_client._record_degradation')
    @mock.patch('llm_client._resolve_llm_configs')
    @mock.patch('llm_client._generate_with_block')
    def test_primary_fail_fallback_ok_records(self, mock_gen, mock_resolve, mock_record) -> None:
        mock_resolve.return_value = (
            {'provider': 'anthropic', 'base_url': 'http://primary'},
            {'provider': 'ollama', 'base_url': 'http://localhost:11434'},
        )
        mock_gen.side_effect = [Exception('primary down'), 'fallback result']

        result = LlmClient().generate_response([{'role': 'user', 'content': 'hi'}])

        self.assertEqual(result, 'fallback result')
        mock_record.assert_called_once()

    @mock.patch('llm_client._record_degradation')
    @mock.patch('llm_client._resolve_llm_configs')
    @mock.patch('llm_client._generate_with_block')
    def test_primary_ok_no_record(self, mock_gen, mock_resolve, mock_record) -> None:
        mock_resolve.return_value = (
            {'provider': 'anthropic', 'base_url': 'http://primary'},
            {'provider': 'ollama', 'base_url': 'http://localhost:11434'},
        )
        mock_gen.side_effect = ['primary result']

        result = LlmClient().generate_response([{'role': 'user', 'content': 'hi'}])

        self.assertEqual(result, 'primary result')
        mock_record.assert_not_called()

    @mock.patch('llm_client._record_degradation')
    @mock.patch('llm_client._resolve_llm_configs')
    @mock.patch('llm_client._generate_with_block')
    def test_primary_fail_no_fallback_records_and_raises(self, mock_gen, mock_resolve, mock_record) -> None:
        mock_resolve.return_value = (
            {'provider': 'anthropic', 'base_url': 'http://primary'},
            None,
        )
        mock_gen.side_effect = [Exception('primary down')]

        with self.assertRaisesRegex(Exception, 'primary down'):
            LlmClient().generate_response([{'role': 'user', 'content': 'hi'}])

        mock_record.assert_called_once()

    @mock.patch('llm_client.resolve_pool_path')
    @mock.patch('llm_client._degradation_recorded', new_callable=lambda: set())
    def test_record_writes_pending_json(self, _mock_set, mock_resolve) -> None:
        from pathlib import Path
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            pool = Path(tmp)
            (pool / 'pending').mkdir()
            mock_resolve.return_value = pool

            _record_degradation('http://primary', 'http://localhost:11434', RuntimeError('boom'))

            files = list((pool / 'pending').glob('llm-degraded-*.json'))
            self.assertEqual(len(files), 1)
            payload = json.loads(files[0].read_text(encoding='utf-8'))
            self.assertEqual(payload['source'], 'llm-degradation-alert')
            self.assertIn('http://primary', payload['content'])

    @mock.patch('llm_client.resolve_pool_path')
    @mock.patch('llm_client._degradation_recorded', new_callable=lambda: set())
    def test_record_throttled_per_process(self, mock_set, mock_resolve) -> None:
        from pathlib import Path
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            pool = Path(tmp)
            (pool / 'pending').mkdir()
            mock_resolve.return_value = pool

            _record_degradation('http://primary', 'http://f1', RuntimeError('boom'))
            _record_degradation('http://primary', 'http://f2', RuntimeError('boom again'))

            files = list((pool / 'pending').glob('llm-degraded-*.json'))
            self.assertEqual(len(files), 1)


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
