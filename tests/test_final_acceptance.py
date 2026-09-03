# -*- coding: utf-8 -*-
from datetime import datetime, timezone
import unittest

from scripts.final_acceptance import _infer_command_id, _row_matches_side, _runtime_fresh


class FinalAcceptanceTests(unittest.TestCase):
    def test_infers_latest_successful_executed_entry(self):
        records = [
            {
                "event": "EXCHANGE_RESULT",
                "command_id": "old",
                "payload": {"operation": "OPEN_ENTRY", "success": True},
            },
            {
                "event": "COMMAND_EXECUTION_FINISHED",
                "command_id": "new",
                "payload": {"result": {"status": "EXECUTED"}},
            },
        ]
        self.assertEqual(_infer_command_id(records), "new")

    def test_zero_one_way_position_can_still_match_side_for_margin_audit(self):
        row = {"positionSide": "BOTH", "positionAmt": "0"}
        self.assertTrue(_row_matches_side(row, "LONG"))
        self.assertTrue(_row_matches_side(row, "SHORT"))

    def test_explicit_hedge_side_must_match(self):
        row = {"positionSide": "LONG", "positionAmt": "0.5"}
        self.assertTrue(_row_matches_side(row, "LONG"))
        self.assertFalse(_row_matches_side(row, "SHORT"))

    def test_current_timestamp_is_fresh(self):
        now = datetime.now(timezone.utc).isoformat()
        self.assertTrue(_runtime_fresh(now, max_age_seconds=15))


if __name__ == "__main__":
    unittest.main()
