# -*- coding: utf-8 -*-
import time
import unittest

from market.market_state import MarketStateAssembler


class MarketStateTests(unittest.TestCase):
    def test_ready_state_contains_open_position(self):
        now = int(time.time() * 1000)
        market = {
            "market_stream_connected": True,
            "market_stream_last_message_ms": now,
            "symbols": [{"symbol": "SUIUSDT", "data_quality": "OK", "mark_price": 0.8}],
        }
        account = {
            "connected": True,
            "last_message_ms": now,
            "last_event_ms": now,
            "balances": {"USDT": {"wallet_balance": 0.57}},
            "positions": {
                "SUIUSDT:LONG": {
                    "symbol": "SUIUSDT",
                    "position_side": "LONG",
                    "position_amount": 5.0,
                    "entry_price": 0.79,
                }
            },
        }
        state = MarketStateAssembler().build(market, account)
        self.assertTrue(state["decision_ready"])
        self.assertEqual(state["account"]["wallet_balance_usdt"], 0.57)
        self.assertEqual(len(state["market"]["symbols"][0]["open_positions"]), 1)

    def test_stale_market_is_not_ready(self):
        market = {
            "market_stream_connected": True,
            "market_stream_last_message_ms": int(time.time() * 1000) - 60_000,
            "symbols": [{"symbol": "SUIUSDT", "data_quality": "OK"}],
        }
        state = MarketStateAssembler(max_market_stale_ms=5_000).build(market)
        self.assertFalse(state["decision_ready"])
        self.assertIn("MARKET_STREAM_STALE", state["quality_flags"])


if __name__ == "__main__":
    unittest.main()
