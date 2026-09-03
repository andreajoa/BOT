# -*- coding: utf-8 -*-
import unittest

from adaptive_runtime import AdaptiveRuntime


class AdaptiveRuntimeConstraintTests(unittest.TestCase):
    def test_constraint_row_contains_execution_limits_without_secrets(self):
        row = {
            "symbol": "SUIUSDT",
            "price": 0.80,
            "min_qty": 1.0,
            "min_notional": 5.0,
            "required_notional": 5.0,
            "min_leverage_for_balance": 10,
            "usable_margin_usdt": 0.5415,
            "balance_usdt": 0.57,
            "quote_volume_24h": 123456789.0,
            "api_key": "must-not-leak",
        }
        safe = AdaptiveRuntime._constraint_row(row)
        self.assertEqual(safe["symbol"], "SUIUSDT")
        self.assertEqual(safe["min_leverage_for_balance"], 10)
        self.assertNotIn("balance_usdt", safe)
        self.assertNotIn("api_key", safe)

    def test_set_candidate_universe_preserves_scanner_order(self):
        runtime = AdaptiveRuntime.__new__(AdaptiveRuntime)
        runtime.candidate_symbols = []
        runtime.candidate_constraints = {}
        universe = [
            {"symbol": "AAAUSDT", "price": 1, "min_qty": 1, "min_notional": 5, "required_notional": 5, "min_leverage_for_balance": 10, "usable_margin_usdt": 0.54, "quote_volume_24h": 100},
            {"symbol": "BBBUSDT", "price": 2, "min_qty": 1, "min_notional": 5, "required_notional": 5, "min_leverage_for_balance": 10, "usable_margin_usdt": 0.54, "quote_volume_24h": 90},
        ]
        runtime._set_candidate_universe(universe)
        self.assertEqual(runtime.candidate_symbols, ["AAAUSDT", "BBBUSDT"])
        self.assertEqual(runtime.candidate_constraints["AAAUSDT"]["min_notional"], 5)


if __name__ == "__main__":
    unittest.main()
