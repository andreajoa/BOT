import unittest
from unittest.mock import MagicMock

from core.binance_connection import BinanceConnection


SAMPLE_EXCHANGE_INFO = {
    "symbols": [
        {
            "symbol": "TESTUSDT",
            "status": "TRADING",
            "contractType": "PERPETUAL",
            "baseAsset": "TEST",
            "quoteAsset": "USDT",
            "marginAsset": "USDT",
            "pricePrecision": 4,
            "quantityPrecision": 0,
            "filters": [
                {
                    "filterType": "PRICE_FILTER",
                    "minPrice": "0.0001",
                    "maxPrice": "1000",
                    "tickSize": "0.0001",
                },
                {
                    "filterType": "LOT_SIZE",
                    "minQty": "1",
                    "maxQty": "1000000",
                    "stepSize": "1",
                },
                {
                    "filterType": "MARKET_LOT_SIZE",
                    "minQty": "1",
                    "maxQty": "1000000",
                    "stepSize": "1",
                },
                {"filterType": "MIN_NOTIONAL", "notional": "5"},
            ],
        }
    ]
}


class BinanceConnectionTests(unittest.TestCase):
    def setUp(self):
        self.conn = BinanceConnection("dummy_key_123456", "dummy_secret_123456")
        self.conn.client = MagicMock()
        self.conn._exchange_info = SAMPLE_EXCHANGE_INFO
        self.conn.hedge_mode = False

    def test_set_leverage_uses_change_leverage_endpoint(self):
        self.conn.client.futures_change_leverage.return_value = {
            "symbol": "TESTUSDT",
            "leverage": 10,
            "maxNotionalValue": "1000000",
        }

        result = self.conn.set_leverage("TESTUSDT", 10)

        self.assertTrue(result["success"])
        self.conn.client.futures_change_leverage.assert_called_once_with(
            symbol="TESTUSDT", leverage=10
        )

    def test_close_position_long_sends_sell_reduce_only_in_one_way(self):
        self.conn.client.futures_create_order.return_value = {"status": "FILLED"}

        result = self.conn.close_position("TESTUSDT", 10, "LONG")

        self.assertTrue(result["success"])
        params = self.conn.client.futures_create_order.call_args.kwargs
        self.assertEqual(params["side"], "SELL")
        self.assertTrue(params["reduceOnly"])
        self.assertNotIn("positionSide", params)

    def test_close_position_short_sends_buy_reduce_only_in_one_way(self):
        self.conn.client.futures_create_order.return_value = {"status": "FILLED"}

        result = self.conn.close_position("TESTUSDT", 10, "SHORT")

        self.assertTrue(result["success"])
        params = self.conn.client.futures_create_order.call_args.kwargs
        self.assertEqual(params["side"], "BUY")
        self.assertTrue(params["reduceOnly"])

    def test_stop_close_all_never_sends_quantity(self):
        self.conn.client.futures_create_order.return_value = {"status": "NEW"}

        result = self.conn.create_stop_order(
            "TESTUSDT", "SELL", 0.49, quantity=10, position_side="LONG"
        )

        self.assertTrue(result["success"])
        params = self.conn.client.futures_create_order.call_args.kwargs
        self.assertEqual(params["closePosition"], "true")
        self.assertNotIn("quantity", params)
        self.assertNotIn("reduceOnly", params)

    def test_hedge_mode_sends_position_side_and_not_reduce_only(self):
        self.conn.hedge_mode = True
        self.conn.client.futures_create_order.return_value = {"status": "FILLED"}

        result = self.conn.close_position("TESTUSDT", 10, "SHORT")

        self.assertTrue(result["success"])
        params = self.conn.client.futures_create_order.call_args.kwargs
        self.assertEqual(params["side"], "BUY")
        self.assertEqual(params["positionSide"], "SHORT")
        self.assertNotIn("reduceOnly", params)

    def test_quantity_from_margin_respects_min_notional(self):
        result = self.conn.quantity_from_margin(
            "TESTUSDT", margin_usdt=0.50, leverage=10, price=0.50
        )

        self.assertTrue(result["success"])
        self.assertEqual(result["quantity"], 10.0)
        self.assertEqual(result["notional"], 5.0)

    def test_quantity_from_margin_rejects_below_min_notional(self):
        result = self.conn.quantity_from_margin(
            "TESTUSDT", margin_usdt=0.50, leverage=5, price=0.50
        )

        self.assertFalse(result["success"])
        self.assertEqual(result["error"], "MIN_NOTIONAL_NOT_MET")


if __name__ == "__main__":
    unittest.main()
