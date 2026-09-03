# -*- coding: utf-8 -*-
from datetime import datetime, timedelta, timezone
import os
import tempfile
import unittest

from approval import TradeApproval
from command_protocol import Action, EntryType, Side, TakeProfitTarget, TradeCommand
from execution.exchange_adapter import ExchangeAdapter
from execution.journal import ExecutionJournal
from execution.resilient_command_executor import ResilientCommandExecutor
from risk.governor import PreflightResult


class _Conn:
    def __init__(self):
        self.cancelled = []

    def cancel_all_orders(self, symbol):
        self.cancelled.append(symbol)
        return {"success": True}


class _Governor:
    def preflight(self, command, market_state):
        return PreflightResult(
            True,
            "OK",
            {
                "quantity": 5.0,
                "leverage": 10,
                "normalized_entry_price": 0.80,
                "normalized_stop_loss": 0.78,
                "normalized_take_profits": [0.84],
            },
        )


class _Adapter:
    def __init__(self):
        self.calls = []
        self.query_results = []
        self.market_result = None
        self.limit_result = None
        self.cancel_result = None
        self.executor = None
        self.pending_seen_before_submit = False

    @staticmethod
    def client_order_id(command_id, suffix):
        return ExchangeAdapter.client_order_id(command_id, suffix)

    def query_order(self, symbol, order_id=None, client_order_id=None):
        self.calls.append(("query", symbol, client_order_id))
        if self.query_results:
            return self.query_results.pop(0)
        return {"success": False, "error": "-2013: Order does not exist"}

    def set_leverage(self, symbol, leverage):
        self.calls.append(("leverage", symbol, leverage))
        return {"success": True, "leverage": leverage}

    def _probe_pending(self, command_id):
        if self.executor is None:
            return
        client_id = self.client_order_id(command_id, "entry")
        self.pending_seen_before_submit = client_id in self.executor.pending_entries

    def open_market(self, command_id, symbol, position_side, quantity):
        self._probe_pending(command_id)
        self.calls.append(("market", symbol, position_side, quantity))
        if self.market_result is not None:
            return self.market_result
        client_id = self.client_order_id(command_id, "entry")
        return {
            "success": True,
            "quantity": quantity,
            "order": {
                "status": "FILLED",
                "executedQty": str(quantity),
                "origQty": str(quantity),
                "clientOrderId": client_id,
            },
        }

    def open_limit(self, command_id, symbol, position_side, quantity, price):
        self._probe_pending(command_id)
        self.calls.append(("limit", symbol, position_side, quantity, price))
        if self.limit_result is not None:
            return self.limit_result
        client_id = self.client_order_id(command_id, "entry")
        return {
            "success": True,
            "quantity": quantity,
            "order": {
                "status": "NEW",
                "executedQty": "0",
                "origQty": str(quantity),
                "clientOrderId": client_id,
            },
        }

    def cancel_order(self, symbol, order_id=None, client_order_id=None):
        self.calls.append(("cancel", symbol, client_order_id))
        if self.cancel_result is not None:
            return self.cancel_result
        return {
            "success": True,
            "order": {
                "status": "CANCELED",
                "executedQty": "0",
                "origQty": "5",
                "clientOrderId": client_order_id,
            },
        }

    def stop_close_all(self, command_id, symbol, position_side, stop_price, suffix="sl"):
        self.calls.append(("sl", symbol, stop_price))
        return {
            "success": True,
            "order": {"orderId": 10, "clientOrderId": self.client_order_id(command_id, suffix)},
            "stop_price": stop_price,
        }

    def take_profit_close_all(self, command_id, symbol, position_side, trigger_price, suffix="tp"):
        self.calls.append(("tp", symbol, trigger_price))
        return {
            "success": True,
            "order": {"orderId": 11, "clientOrderId": self.client_order_id(command_id, suffix)},
            "tp_price": trigger_price,
        }

    def take_profit_partial(self, *args, **kwargs):
        self.calls.append(("tp_partial",))
        return {"success": True, "order": {"orderId": 12}}

    def close_market(self, symbol, position_side, quantity):
        self.calls.append(("close", symbol, position_side, quantity))
        return {"success": True, "order": {"status": "FILLED"}}


def _command(entry_type=EntryType.MARKET, expired=False):
    now = datetime.now(timezone.utc)
    issued = now - timedelta(seconds=120) if expired else now
    expires = now - timedelta(seconds=1) if expired else now + timedelta(seconds=90)
    return TradeCommand(
        command_id="0123456789abcdef0123456789abcdef",
        action=Action.OPEN_POSITION,
        issued_at=issued.isoformat(),
        expires_at=expires.isoformat(),
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


def _approval(command_id="0123456789abcdef0123456789abcdef"):
    now = datetime.now(timezone.utc)
    return TradeApproval(
        command_id=command_id,
        approved=True,
        approved_at=now.isoformat(),
        expires_at=(now + timedelta(seconds=90)).isoformat(),
    )


class ResilientCommandExecutorTests(unittest.TestCase):
    def _executor(self, adapter=None):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        adapter = adapter or _Adapter()
        executor = ResilientCommandExecutor(
            _Conn(),
            governor=_Governor(),
            adapter=adapter,
            journal=ExecutionJournal(os.path.join(tmp.name, "journal.jsonl")),
            pending_path=os.path.join(tmp.name, "pending_entries.json"),
            recover_on_init=False,
            recovery_poll_seconds=0,
        )
        adapter.executor = executor
        return executor, adapter

    def test_pending_intent_exists_before_market_submission(self):
        executor, adapter = self._executor()
        result = executor.execute(_command(), _approval(), {})
        self.assertEqual(result["status"], "EXECUTED")
        self.assertTrue(adapter.pending_seen_before_submit)
        self.assertEqual(executor.pending_entries, {})

    def test_ambiguous_pre_submit_lookup_fails_closed(self):
        adapter = _Adapter()
        adapter.query_results = [{"success": False, "error": "network timeout"}]
        executor, adapter = self._executor(adapter)
        result = executor.execute(_command(), _approval(), {})
        self.assertEqual(result["status"], "REJECTED")
        self.assertEqual(result["reason"], "ENTRY_IDEMPOTENCY_CHECK_AMBIGUOUS")
        self.assertNotIn("market", [call[0] for call in adapter.calls])

    def test_partial_limit_fill_cancels_remainder_and_protects_executed_qty(self):
        adapter = _Adapter()
        client_id = adapter.client_order_id(_command().command_id, "entry")
        adapter.limit_result = {
            "success": True,
            "quantity": 5.0,
            "order": {
                "status": "PARTIALLY_FILLED",
                "executedQty": "2",
                "origQty": "5",
                "clientOrderId": client_id,
            },
        }
        adapter.cancel_result = {
            "success": True,
            "order": {
                "status": "CANCELED",
                "executedQty": "2",
                "origQty": "5",
                "clientOrderId": client_id,
            },
        }
        executor, adapter = self._executor(adapter)
        result = executor.execute(_command(EntryType.LIMIT), _approval(), {})
        self.assertEqual(result["status"], "PARTIAL_EXECUTED_PROTECTED")
        self.assertEqual(result["quantity"], 2.0)
        self.assertIn("cancel", [call[0] for call in adapter.calls])
        self.assertIn("sl", [call[0] for call in adapter.calls])
        self.assertEqual(executor.pending_entries, {})

    def test_submission_timeout_recovers_filled_order_by_client_id(self):
        adapter = _Adapter()
        command = _command()
        client_id = adapter.client_order_id(command.command_id, "entry")
        adapter.query_results = [
            {"success": False, "error": "-2013: Order does not exist"},
            {
                "success": True,
                "order": {
                    "status": "FILLED",
                    "executedQty": "5",
                    "origQty": "5",
                    "clientOrderId": client_id,
                },
            },
        ]
        adapter.market_result = {"success": False, "error": "HTTP timeout"}
        executor, adapter = self._executor(adapter)
        result = executor.execute(command, _approval(), {})
        self.assertEqual(result["status"], "RECOVERED_EXECUTED")
        self.assertIn("sl", [call[0] for call in adapter.calls])
        self.assertEqual(executor.pending_entries, {})

    def test_expired_new_limit_is_canceled_without_fake_fill(self):
        adapter = _Adapter()
        executor, adapter = self._executor(adapter)
        command = _command(EntryType.LIMIT, expired=True)
        client_id = adapter.client_order_id(command.command_id, "entry")
        executor._remember_pending(
            client_id,
            command,
            {
                "quantity": 5.0,
                "leverage": 10,
                "normalized_entry_price": 0.80,
                "normalized_stop_loss": 0.78,
                "normalized_take_profits": [0.84],
            },
            5.0,
            "SUIUSDT",
        )
        adapter.query_results = [
            {
                "success": True,
                "order": {
                    "status": "NEW",
                    "executedQty": "0",
                    "origQty": "5",
                    "clientOrderId": client_id,
                },
            }
        ]
        outcomes = executor.recover_pending_entries()
        self.assertEqual(outcomes[client_id]["status"], "ENTRY_TERMINATED")
        self.assertIn("cancel", [call[0] for call in adapter.calls])
        self.assertNotIn("sl", [call[0] for call in adapter.calls])
        self.assertEqual(executor.pending_entries, {})


if __name__ == "__main__":
    unittest.main()
