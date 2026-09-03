# -*- coding: utf-8 -*-
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import bootstrap_local
from utils.tls import create_ssl_context


class BootstrapLocalTests(unittest.TestCase):
    def test_env_update_preserves_other_values_and_sets_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text("BINANCEAPIKEY=secret-binance\nBOT_MODE=paper\n", encoding="utf-8")
            old = bootstrap_local.ENV_FILE
            bootstrap_local.ENV_FILE = env_path
            try:
                bootstrap_local._set_env_file_value("BOT_MODE", "live")
                bootstrap_local._set_env_file_value("OPENAI_API_KEY", "sk-test-value-123456789012345")
                text = env_path.read_text(encoding="utf-8")
                self.assertIn("BINANCEAPIKEY=secret-binance", text)
                self.assertIn("BOT_MODE=live", text)
                self.assertIn("OPENAI_API_KEY=sk-test-value-123456789012345", text)
                self.assertEqual(bootstrap_local._env_file_value("BOT_MODE"), "live")
            finally:
                bootstrap_local.ENV_FILE = old

    def test_child_environment_adds_tls_bootstrap_without_dropping_existing_pythonpath(self):
        with patch.dict(os.environ, {"PYTHONPATH": "/existing"}, clear=False):
            env = bootstrap_local._child_environment()
        self.assertTrue(env["PYTHONPATH"].startswith(str(bootstrap_local.TLS_BOOTSTRAP)))
        self.assertIn("/existing", env["PYTHONPATH"])
        self.assertEqual(env["PYTHONUNBUFFERED"], "1")

    def test_tls_context_keeps_verification_enabled(self):
        context = create_ssl_context()
        self.assertTrue(context.check_hostname)
        import ssl
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)


if __name__ == "__main__":
    unittest.main()
