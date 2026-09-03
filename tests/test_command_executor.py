# -*- coding: utf-8 -*-
from datetime import datetime, timedelta, timezone
import os
import tempfile
import unittest

from approval import TradeApproval
from command_protocol import Action, EntryType, Side, TakeProfitTarget, TradeCommand
from execution.command_executor import CommandExecutor
from execution.journal import ExecutionJournal
from risk.governor import PreflightResult


class _Conn:
    def __init__(self):
        self.cancelled = []

    def cancel_all_orders(self, symbol):
        self.cancelled.append(symbol)
        return {"success": True}


class _Governor:
    def __init__(self, accepted=True, reason="OK"):
        self.accepted = accepted
        self.reason = reason

    def preflight(self, command, market_state):
        return PreflightResult(
            self.accepted,
            self.reason,
            {
                "quantity": 5.0,
                "leverage": 10,
                "normalized_entry_price": 0.80,
                "normalized_stop_loss": 0.78,
                "normalized_take_profits": [0.84],
            },
        )


class _Adapter:
    def __init__(self, sl_success=True):
        self.calls = []
        self.sl_success = sl_success

    @staticmethod
    def client_order_id(command_id, suffix):
        return f"brain_{command_id}_{suffix}"

    def set_leverage(self, symbol, leverage):
        self.calls.append(("leverage", symbol, leverage))
        return {"success": True}

    def open_market(self, command_id, symbol, position_side, quantity):
        self.calls.append(("market", symbol, position_side, quantity))
        return {
            "success": True,
            "quantity": quantity,
            "order": {"status": "FILLED", "executedQty": str(quantity), "clientOrderId": f"brain_{command_id}_entry"},
        }

    def open_limit(self, command_id, symbol, position_side, quantity, price):
        self.calls.append(("limit", symbol, position_side, quantity, price))
        return {
            "success": True,
            "quantity": quantity,
            "order": {"status": "NEW", "origQty": str(quantity), "clientOrderId": f"brain_{command_id}_entry"},
        }

    def stop_close_all(self, command_id, symbol, position_side, stop_price, suffix="sl"):
        self.calls.append(("sl", symbol, stop_price))
        if not self.sl_success:
            return {"success": False, "error": "SL rejected"}
        return {"success": True, "order": {"orderId": 10}, "stop_price": stop_price}

    def take_profit_close_all(self, command_id, symbol, position_side, trigger_price, suffix="tp"):
        self.calls.append(("tp", symbol, trigger_price))
        return {"success": True, "order": {"orderId": 11}, "tp_price": trigger_price}

    def take_profit_partial(self, *args, **kwargs):
        return {"success": True, "order": {"orderId": 12}}

    def close_market(self, symbol, position_side, quantity):
        self.calls.append(("close", symbol, position_side, quantity))
        return {"success": True, "order": {"status": "FILLED"}}


def _command(entry_type=EntryType.MARKET):
    now = datetime.now(timezone.utc)
    return TradeCommand(
        command_id="cmd-exec",
        action=Action.OPEN_POSITION,
        issued_at=now.isoformat(),
        expires_at=(now + timedelta(seconds=90)).isoformat(),
        symbol="SUIUSDT",
        side=Side.LONG,
        strategy="adaptive",
        regime="TREND",
        confidence=0.7,
        entry_type=entry_type,
        entry_price=0.80 if entry_type == EntryType.LIMIT else None,
        margin_usdt=0.50,
        leverage=10,
        stop_loss=0.78,
        take_profits=[TakeProfitTarget(price=0.84, close_pct=100)],
    )


def _approval(command_id="cmd-exec"):
    now = datetime.now(timezone.utc)
    return TradeApproval(
        command_id=command_id,
        approved=True,
        approved_at=now.isoformat(),
        expires_at=(now + timedelta(seconds=90)).isoformat(),
    )


class CommandExecutorTests(unittest.TestCase):
    def _executor(self, adapter=None):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        journal = ExecutionJournal(os.path.join(tmp.name, "journal.jsonl"))
        return CommandExecutor(_Conn(), governor=_Governor(), adapter=adapter or _Adapter(), journal=journal)

    def test_missing_approval_rejects_before_exchange(self):
        adapter = _Adapter()
        executor = self._executor(adapter)
        result = executor.execute(_command(), None, {})
        self.assertEqual(result["status"], "REJECTED")
        self.assertEqual(result["reason"], "APPROVAL_REQUIRED")
        self.assertEqual(adapter.calls, [])

    def test_market_entry_installs_protection(self):
        adapter = _Adapter()
        executor = self._executor(adapter)
        result = executor.execute(_command(), _approval(), {})
        self.assertEqual(result["status"], "EXECUTED")
        kinds = [c[0] for c in adapter.calls]
        self.assertEqual(kinds[:4], ["leverage", "market", "sl", "tp"])

    def test_limit_waits_for_real_fill_before_protection(self):
        adapter = _Adapter()
        executor = self._executor(adapter)
        result = executor.execute(_command(EntryType.LIMIT), _approval(), {})
        self.assertEqual(result["status"], "PENDING_FILL")
        self.assertNotIn("sl", [c[0] for c in adapter.calls])

        fill = executor.handle_order_event(
            {
                "client_order_id": "brain_cmd-exec_entry",
                "status": "FILLED",
                "filled_qty": 5.0,
            }
        )
        self.assertEqual(fill["status"], "EXECUTED")
        self.assertIn("sl", [c[0] for c in adapter.calls])

    def test_failed_stop_triggers_emergency_flatten(self):
        adapter = _Adapter(sl_success=False)
        executor = self._executor(adapter)
        result = executor.execute(_command(), _approval(), {})
        self.assertEqual(result["status"], "FAILED_SAFE")
        self.assertEqual(result["reason"], "STOP_LOSS_INSTALL_FAILED")
        self.assertIn("close", [c[0] for c in adapter.calls])


if __name__ == "__main__":
    unittest.main()
