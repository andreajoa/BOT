# -*- coding: utf-8 -*-
import asyncio
import json
import unittest

from account.user_stream import FuturesUserDataStream


class UserDataStreamTests(unittest.TestCase):
    def test_order_trade_update_records_fill(self):
        stream = FuturesUserDataStream("test-key")
        payload = {
            "e": "ORDER_TRADE_UPDATE",
            "E": 1700000000100,
            "T": 1700000000000,
            "o": {
                "s": "SUIUSDT",
                "i": 12345,
                "c": "brain-cmd-1",
                "S": "BUY",
                "ps": "LONG",
                "o": "MARKET",
                "x": "TRADE",
                "X": "FILLED",
                "q": "5",
                "l": "5",
                "z": "5",
                "L": "0.8000",
                "ap": "0.8001",
                "sp": "0",
                "rp": "0.0000",
                "n": "0.0012",
                "N": "USDT",
                "R": False,
                "cp": False,
            },
        }
        asyncio.run(stream._handle_message(json.dumps(payload)))
        order = stream.state["orders"]["SUIUSDT:12345"]
        self.assertEqual(order["status"], "FILLED")
        self.assertEqual(order["execution_type"], "TRADE")
        self.assertEqual(order["average_price"], 0.8001)
        self.assertEqual(order["filled_qty"], 5.0)
        self.assertEqual(order["commission_asset"], "USDT")

    def test_account_update_records_balance_and_position(self):
        stream = FuturesUserDataStream("test-key")
        payload = {
            "e": "ACCOUNT_UPDATE",
            "E": 1700000000200,
            "T": 1700000000100,
            "a": {
                "m": "ORDER",
                "B": [{"a": "USDT", "wb": "0.5700", "cw": "0.5700", "bc": "-0.001"}],
                "P": [
                    {
                        "s": "SUIUSDT",
                        "pa": "5",
                        "ep": "0.8001",
                        "bep": "0.8005",
                        "up": "0.012",
                        "mt": "isolated",
                        "iw": "0.50",
                        "ps": "LONG",
                    }
                ],
            },
        }
        asyncio.run(stream._handle_message(json.dumps(payload)))
        balance = stream.state["balances"]["USDT"]
        position = stream.state["positions"]["SUIUSDT:LONG"]
        self.assertEqual(balance["wallet_balance"], 0.57)
        self.assertEqual(position["position_amount"], 5.0)
        self.assertEqual(position["entry_price"], 0.8001)
        self.assertEqual(position["margin_type"], "isolated")

    def test_listen_key_expired_requests_reconnect(self):
        stream = FuturesUserDataStream("test-key")
        should_reconnect = asyncio.run(
            stream._handle_message(json.dumps({"e": "listenKeyExpired", "E": 1700000000300}))
        )
        self.assertTrue(should_reconnect)


if __name__ == "__main__":
    unittest.main()
