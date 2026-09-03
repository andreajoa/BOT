# -*- coding: utf-8 -*-
import os
import unittest
from unittest.mock import patch

import external_production_runtime


class ExternalChatGPTRuntimeTests(unittest.TestCase):
    def test_constructor_requires_neon_but_not_openai_key(self):
        env = {
            "NEON_DATABASE_URL": "postgresql://user:password@example.neon.tech/db?sslmode=require",
            "BRAIN_MODE": "external_chatgpt",
        }
        with patch.object(external_production_runtime, "BOT_MODE", "live"), \
             patch.object(external_production_runtime, "BINANCE_API_KEY", "binance-key"), \
             patch.object(external_production_runtime, "BINANCE_API_SECRET", "binance-secret"), \
             patch.dict(os.environ, env, clear=False):
            os.environ.pop("OPENAI_API_KEY", None)
            runtime = external_production_runtime.ExternalChatGPTRuntime()
        self.assertIsNone(runtime.brain)
        self.assertEqual(runtime.runtime_instance_id.split("-")[0], "external")
        self.assertEqual(runtime.decision_interval, 5)

    def test_constructor_fails_closed_without_neon_bridge(self):
        with patch.object(external_production_runtime, "BOT_MODE", "live"), \
             patch.object(external_production_runtime, "BINANCE_API_KEY", "binance-key"), \
             patch.object(external_production_runtime, "BINANCE_API_SECRET", "binance-secret"), \
             patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "NEON_DATABASE_URL"):
                external_production_runtime.ExternalChatGPTRuntime()


if __name__ == "__main__":
    unittest.main()
