# -*- coding: utf-8 -*-
from datetime import datetime, timedelta, timezone
import os
import tempfile
import unittest

from command_protocol import Action, Side, TakeProfitTarget, TradeCommand, TrailingSpec
from execution.journal import ExecutionJournal
from execution.position_manager import PositionManager


class _Adapter:
    def __init__(self):
        self.calls = []
        self.next_order = 100

    def stop_close_all(self, command_id, symbol, position_side, stop_price, suffix="sl"):
        self.next_order += 1
        self.calls.append(("create_stop", stop_price, suffix))
        return {
            "success": True,
            "stop_price": stop_price,
            "order": {"orderId": self.next_order, "clientOrderId": f"{command_id}-{suffix}"},
        }

    def cancel_order(self, symbol, order_id=None, client_order_id=None):
        self.calls.append(("cancel", order_id or client_order_id))
        return {"success": True}


def _command():
    now = datetime.now(timezone.utc)
    return TradeCommand(
        command_id="cmd-pos",
        action=Action.OPEN_POSITION,
        issued_at=now.isoformat(),
        expires_at=(now + timedelta(seconds=90)).isoformat(),
        symbol="SUIUSDT",
        side=Side.LONG,
        strategy="trend",
        regime="TREND",
        confidence=0.8,
        margin_usdt=0.5,
        leverage=10,
        stop_loss=0.78,
        take_profits=[TakeProfitTarget(0.90, 100)],
        trailing=TrailingSpec(enabled=True, activation_price=0.84, callback_rate=1.0),
    )


class PositionManagerTests(unittest.TestCase):
    def _manager(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        adapter = _Adapter()
        journal = ExecutionJournal(os.path.join(tmp.name, "journal.jsonl"))
        manager = PositionManager(adapter, journal, os.path.join(tmp.name, "positions.json"))
        manager.register_open_position(
            _command(),
            quantity=5,
            stop_order={"orderId": 10, "clientOrderId": "old-stop"},
            take_profit_orders=[{"orderId": 11, "clientOrderId": "tp-1"}],
        )
        return manager, adapter

    def test_trailing_does_not_move_before_activation(self):
        manager, adapter = self._manager()
        updates = manager.on_price("SUIUSDT", 0.83)
        self.assertEqual(updates, [])
        self.assertEqual(adapter.calls, [])

    def test_trailing_creates_new_stop_then_cancels_old(self):
        manager, adapter = self._manager()
        updates = manager.on_price("SUIUSDT", 0.85)
        self.assertTrue(updates[0]["success"])
        self.assertEqual(adapter.calls[0][0], "create_stop")
        self.assertEqual(adapter.calls[1], ("cancel", 10))
        state = manager.get("SUIUSDT", "LONG")
        self.assertGreater(state["current_stop"], 0.78)

    def test_reconcile_removes_position_not_present_on_exchange(self):
        manager, _ = self._manager()
        removed = manager.reconcile({"positions": {}})
        self.assertEqual(removed, ["SUIUSDT:LONG"])
        self.assertIsNone(manager.get("SUIUSDT", "LONG"))


if __name__ == "__main__":
    unittest.main()
