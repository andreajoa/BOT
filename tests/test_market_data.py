import json
import unittest

from market.context_builder import MarketContextBuilder
from market.live_stream import FuturesMarketStream


class MarketDataTests(unittest.IsolatedAsyncioTestCase):
    async def test_parses_book_trade_and_mark_price(self):
        stream = FuturesMarketStream(["BTCUSDT"])

        await stream._handle_message(json.dumps({
            "e": "bookTicker", "E": 1000, "T": 999, "s": "BTCUSDT",
            "b": "100.0", "B": "3", "a": "100.2", "A": "1", "st": 1,
        }))
        await stream._handle_message(json.dumps({
            "e": "aggTrade", "E": 1001, "T": 1001, "s": "BTCUSDT",
            "p": "100.1", "q": "2", "m": False, "st": 1,
        }))
        await stream._handle_message(json.dumps({
            "e": "markPriceUpdate", "E": 1002, "T": 2000, "s": "BTCUSDT",
            "p": "100.15", "i": "100.05", "r": "0.0001", "st": 1,
        }))

        raw = stream.state["BTCUSDT"]
        self.assertEqual(raw["best_bid"], 100.0)
        self.assertEqual(raw["best_ask"], 100.2)
        self.assertEqual(raw["mark_price"], 100.15)
        self.assertEqual(raw["funding_rate"], 0.0001)
        self.assertEqual(len(stream.trade_flow["BTCUSDT"]), 1)
        _, buy_quote, sell_quote = stream.trade_flow["BTCUSDT"][0]
        self.assertGreater(buy_quote, 0)
        self.assertEqual(sell_quote, 0)

    async def test_ignores_coin_m_payload_on_merged_stream(self):
        stream = FuturesMarketStream(["BTCUSDT"])
        await stream._handle_message(json.dumps({
            "e": "bookTicker", "E": 1000, "T": 999, "s": "BTCUSDT",
            "b": "99", "B": "1", "a": "101", "A": "1", "st": 2,
        }))
        self.assertIsNone(stream.state["BTCUSDT"]["best_bid"])

    def test_context_builder_is_neutral_and_compact(self):
        raw = {
            "symbol": "BTCUSDT",
            "best_bid": 100.0,
            "best_bid_qty": 3.0,
            "best_ask": 100.2,
            "best_ask_qty": 1.0,
            "last_trade_price": 100.1,
            "mark_price": 100.15,
            "index_price": 100.05,
            "funding_rate": 0.0001,
            "next_funding_time_ms": 123,
            "stale_ms": 200,
            "flow_60s": {"taker_delta_ratio": 0.25, "taker_total_quote": 10000},
            "flow_300s": {"taker_delta_ratio": -0.10, "taker_total_quote": 50000},
        }
        structure = {
            "captured_at_ms": 1002,
            "timeframes": {
                "5m": {"ema_alignment": "BULLISH", "atr14_pct": 0.4},
                "1h": {"ema_alignment": "MIXED", "atr14_pct": 1.2},
            },
        }
        enriched = MarketContextBuilder.enrich_symbol(raw, structure=structure)
        self.assertAlmostEqual(enriched["top_book_imbalance"], 0.5)
        self.assertGreater(enriched["spread_bps"], 0)
        self.assertEqual(enriched["taker_delta_60s"], 0.25)
        self.assertEqual(enriched["data_quality"], "OK")
        self.assertEqual(enriched["timeframes"]["5m"]["ema_alignment"], "BULLISH")
        self.assertNotIn("signal", enriched)
        self.assertNotIn("direction", enriched)

    def test_missing_structure_is_degraded(self):
        raw = {
            "symbol": "BTCUSDT",
            "best_bid": 100.0,
            "best_bid_qty": 1.0,
            "best_ask": 100.1,
            "best_ask_qty": 1.0,
            "last_trade_price": 100.05,
            "mark_price": 100.05,
            "index_price": 100.05,
            "stale_ms": 100,
        }
        enriched = MarketContextBuilder.enrich_symbol(raw)
        self.assertEqual(enriched["data_quality"], "DEGRADED")
        self.assertIn("NO_STRUCTURE", enriched["quality_flags"])


if __name__ == "__main__":
    unittest.main()
