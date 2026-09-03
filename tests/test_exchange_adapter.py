# -*- coding: utf-8 -*-
import unittest

from execution.exchange_adapter import ExchangeAdapter


class _Client:
    def __init__(self):
        self.calls = []

    def futures_change_margin_type(self, **params):
        self.calls.append(("margin", params))
        return {"code": 200, "msg": "success"}


class _Connection:
    def __init__(self):
        self.client = _Client()
        self.hedge_mode = False
        self.calls = []

    def set_leverage(self, symbol, leverage):
        self.calls.append(("leverage", symbol, leverage))
        return {"success": True, "leverage": leverage}


class ExchangeAdapterTests(unittest.TestCase):
    def test_set_leverage_forces_isolated_margin_first(self):
        connection = _Connection()
        adapter = ExchangeAdapter(connection)
        result = adapter.set_leverage("SUIUSDT", 10)
        self.assertTrue(result["success"])
        self.assertEqual(result["margin_type"], "ISOLATED")
        self.assertEqual(
            connection.client.calls,
            [("margin", {"symbol": "SUIUSDT", "marginType": "ISOLATED"})],
        )
        self.assertEqual(connection.calls, [("leverage", "SUIUSDT", 10)])

    def test_rejects_unknown_margin_type_without_api_call(self):
        connection = _Connection()
        adapter = ExchangeAdapter(connection)
        result = adapter.set_margin_type("SUIUSDT", "banana")
        self.assertFalse(result["success"])
        self.assertEqual(connection.client.calls, [])


if __name__ == "__main__":
    unittest.main()
