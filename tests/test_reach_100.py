# -*- coding: utf-8 -*-
from datetime import datetime, timedelta, timezone
import unittest

from scripts.reach_100 import _approval_matches, _runtime_healthy, _timestamp_fresh


class Reach100SupervisorTests(unittest.TestCase):
    def test_approval_requires_exact_command_id_phrase(self):
        command_id = "0123456789abcdef0123456789abcdef"
        self.assertTrue(_approval_matches(f"APROVAR {command_id}", command_id))
        self.assertFalse(_approval_matches("APROVAR", command_id))
        self.assertFalse(_approval_matches("SIM", command_id))
        self.assertFalse(_approval_matches(f"aprovar {command_id}", command_id))
        self.assertFalse(_approval_matches("APROVAR outro-command-id", command_id))
        self.assertFalse(_approval_matches(f"APROVAR  {command_id}", command_id))

    def test_runtime_healthy_requires_all_three_live_signals(self):
        now = datetime.now(timezone.utc).isoformat()
        base = {
            "updated_at": now,
            "market_stream_connected": True,
            "user_stream_connected": True,
            "decision_ready": True,
        }
        self.assertTrue(_runtime_healthy(base))

        for key in ("market_stream_connected", "user_stream_connected", "decision_ready"):
            degraded = dict(base)
            degraded[key] = False
            self.assertFalse(_runtime_healthy(degraded), key)

    def test_runtime_health_rejects_stale_status(self):
        stale = (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat()
        status = {
            "updated_at": stale,
            "market_stream_connected": True,
            "user_stream_connected": True,
            "decision_ready": True,
        }
        self.assertFalse(_runtime_healthy(status))
        self.assertFalse(_timestamp_fresh(stale, max_age_seconds=12))

    def test_timestamp_fresh_accepts_current_utc_timestamp(self):
        self.assertTrue(_timestamp_fresh(datetime.now(timezone.utc).isoformat()))


if __name__ == "__main__":
    unittest.main()
