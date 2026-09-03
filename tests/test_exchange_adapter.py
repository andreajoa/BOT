# -*- coding: utf-8 -*-
import unittest

from execution.exchange_adapter import ExchangeAdapter


class _Client:
    def __init__(self):
        self.calls = []
        self.orders = {}

    def futures_change_margin_type(self, **params):
        self.calls.append(("margin", params))
        return {"code": 200, "msg": "success"}

    def futures_get_order(self, **params):
        self.calls.append(("get_order", params))
        key = params.get("origClientOrderId")
        if key not in self.orders:
            raise RuntimeError("unexpected missing order in this unit test")
        return dict(self.orders[key])

    def futures_create_order(self, **params):
        self.calls.append(("create_order", params))
        order = {
            "orderId": 999,
            "clientOrderId": params.get("newClientOrderId"),
            "status": "NEW",
        }
        self.orders[params.get("newClientOrderId")] = dict(order)
        return order


class _Connection:
    def __init__(self):
        self.client = _Client()
        self.hedge_mode = False
        self.calls = []

    def set_leverage(self, symbol, leverage):
        self.calls.append(("leverage", symbol, leverage))
        return {"success": True, "leverage": leverage}

    def normalize_price(self, symbol, price, mode="nearest"):
        return float(price)

    def normalize_quantity(self, symbol, quantity, market=True):
        return float(quantity)


class ExchangeAdapterTests(unittest.TestCase):
    def test_real_length_command_id_keeps_order_roles_unique(self):
        command_id = "0123456789abcdef0123456789abcdef"
        entry = ExchangeAdapter.client_order_id(command_id, "entry")
        stop = ExchangeAdapter.client_order_id(command_id, "sl")
        tp1 = ExchangeAdapter.client_order_id(command_id, "tp1")
        trail1 = ExchangeAdapter.client_order_id(command_id, "trail1")

        self.assertLessEqual(len(entry), 36)
        self.assertEqual(len({entry, stop, tp1, trail1}), 4)
        self.assertTrue(entry.endswith("_entry"))
        self.assertTrue(stop.endswith("_sl"))
        self.assertTrue(tp1.endswith("_tp1"))
        self.assertTrue(trail1.endswith("_trail1"))
        self.assertEqual(entry, ExchangeAdapter.client_order_id(command_id, "entry"))

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

    def test_existing_stop_is_reused_instead_of_duplicated(self):
        connection = _Connection()
        adapter = ExchangeAdapter(connection)
        client_id = adapter.client_order_id("cmd-1", "sl")
        connection.client.orders[client_id] = {
            "orderId": 123,
            "clientOrderId": client_id,
            "status": "NEW",
        }
        result = adapter.stop_close_all("cmd-1", "SUIUSDT", "LONG", 0.78)
        self.assertTrue(result["success"])
        self.assertTrue(result["already_exists"])
        creates = [call for call in connection.client.calls if call[0] == "create_order"]
        self.assertEqual(creates, [])

    def test_existing_take_profit_is_reused_instead_of_duplicated(self):
        connection = _Connection()
        adapter = ExchangeAdapter(connection)
        client_id = adapter.client_order_id("cmd-1", "tp1")
        connection.client.orders[client_id] = {
            "orderId": 456,
            "clientOrderId": client_id,
            "status": "NEW",
        }
        result = adapter.take_profit_close_all("cmd-1", "SUIUSDT", "LONG", 0.84, suffix="tp1")
        self.assertTrue(result["success"])
        self.assertTrue(result["already_exists"])
        creates = [call for call in connection.client.calls if call[0] == "create_order"]
        self.assertEqual(creates, [])


if __name__ == "__main__":
    unittest.main()
