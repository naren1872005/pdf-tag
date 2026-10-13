import os
import json
import tempfile
import unittest
from unittest.mock import patch, MagicMock

from parser.remote_config import RemoteConfigProvider
from parser.gemini_scheduler import GeminiRequestScheduler, CredentialState

class TestRemoteConfig(unittest.TestCase):
    def setUp(self):
        # Create a temporary directory for tests
        self.test_dir = tempfile.TemporaryDirectory()
        self.patcher = patch('os.environ.get')
        self.mock_env_get = self.patcher.start()
        self.mock_env_get.return_value = self.test_dir.name
        
    def tearDown(self):
        self.patcher.stop()
        self.test_dir.cleanup()

    @patch('parser.remote_config.urllib.request.urlopen')
    def test_fetch_remote_config_success(self, mock_urlopen):
        # Setup mock response
        mock_response = MagicMock()
        mock_response.status = 200
        mock_data = {
            "success": True,
            "credentials": [{"label": "test", "api_key": "test_key"}],
            "models": [{"model": "gemini-3.6-flash"}]
        }
        mock_response.read.return_value = json.dumps(mock_data).encode('utf-8')
        mock_urlopen.return_value.__enter__.return_value = mock_response

        # Call the method
        config = RemoteConfigProvider.fetch_remote_config()

        self.assertIsNotNone(config)
        self.assertEqual(len(config['credentials']), 1)
        self.assertEqual(config['credentials'][0]['api_key'], "test_key")

    @patch('parser.remote_config.win32crypt.CryptProtectData')
    @patch('parser.remote_config.win32crypt.CryptUnprotectData')
    def test_cache_save_load(self, mock_unprotect, mock_protect):
        # Mock the encryption/decryption
        mock_protect.return_value = b'encrypted_data'
        mock_unprotect.return_value = ("description", json.dumps({"test": "data"}).encode('utf-8'))

        # Save config
        test_config = {"test": "data"}
        RemoteConfigProvider.save_cache(test_config)

        # Check if file was created
        cache_path = RemoteConfigProvider.get_cache_path()
        self.assertTrue(os.path.exists(cache_path))

        # Load config
        loaded_config = RemoteConfigProvider.load_cache()
        self.assertEqual(loaded_config, test_config)

    @patch('parser.remote_config.RemoteConfigProvider.fetch_remote_config')
    @patch('parser.remote_config.RemoteConfigProvider.save_cache')
    def test_get_config_remote_success(self, mock_save, mock_fetch):
        mock_fetch.return_value = {"credentials": [{"label": "1", "api_key": "abc"}]}
        
        config = RemoteConfigProvider.get_config()
        self.assertIsNotNone(config)
        mock_save.assert_called_once()
        
    @patch('parser.remote_config.RemoteConfigProvider.fetch_remote_config')
    @patch('parser.remote_config.RemoteConfigProvider.load_cache')
    def test_get_config_fallback_to_cache(self, mock_load, mock_fetch):
        mock_fetch.return_value = None
        mock_load.return_value = {"credentials": [{"label": "cached", "api_key": "def"}]}
        
        config = RemoteConfigProvider.get_config()
        self.assertIsNotNone(config)
        self.assertEqual(config["credentials"][0]["api_key"], "def")
        
    @patch('parser.gemini_scheduler.genai.Client')
    @patch('parser.remote_config.RemoteConfigProvider.get_config')
    def test_scheduler_integration_remote_success(self, mock_get_config, mock_client):
        # Setup mock config
        mock_get_config.return_value = {
            "success": True,
            "credentials": [{"label": "remote_key", "api_key": "xyz"}],
            "models": [{"model": "remote-model-1"}]
        }
        
        # Override the paths so we don't pollute local app data
        app_data_dir = self.test_dir.name
        
        with patch('parser.gemini_scheduler.os.environ.get') as mock_env:
            mock_env.return_value = app_data_dir
            
            # Reset the scheduler singleton
            GeminiRequestScheduler._instance = None
            
            scheduler = GeminiRequestScheduler()
            
            self.assertEqual(len(scheduler.credentials), 1)
            self.assertEqual(scheduler.credentials[0].api_key, "xyz")
            self.assertEqual(scheduler.credentials[0].label, "remote_key")
            
            # Model fallback chain should be updated
            self.assertEqual(scheduler.MODEL_FALLBACK_CHAIN, ["remote-model-1"])
            self.assertIn("remote-model-1", scheduler.model_status)

if __name__ == '__main__':
    unittest.main()
