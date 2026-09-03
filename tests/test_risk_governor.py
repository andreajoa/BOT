# -*- coding: utf-8 -*-
from datetime import datetime, timedelta, timezone
import unittest

from command_protocol import Action, Side, TakeProfitTarget, TradeCommand
from risk.governor import RiskGovernor


class _FakeConn:
    def __init__(self, balance=0.57, min_qty=0.0):
        self.balance = balance
        self.min_qty = float(min_qty)

    def get_usdt_balance(self):
        return self.balance

    def normalize_price(self, symbol, price, mode="nearest"):
        return float(price)

    def normalize_quantity(self, symbol, quantity, market=True):
        quantity = float(quantity)
        if quantity < self.min_qty:
            raise ValueError(f"Quantidade {quantity} abaixo do minimo {self.min_qty}")
        return quantity

    def quantity_from_margin(self, symbol, margin_usdt, leverage, price):
        return {
            "success": True,
            "quantity": (margin_usdt * leverage) / price,
            "notional": margin_usdt * leverage,
            "actual_margin": margin_usdt,
        }


def _command(**overrides):
    now = datetime.now(timezone.utc)
    data = dict(
        command_id="cmd-risk",
        action=Action.OPEN_POSITION,
        issued_at=now.isoformat(),
        expires_at=(now + timedelta(seconds=90)).isoformat(),
        symbol="SUIUSDT",
        side=Side.LONG,
        strategy="adaptive",
        regime="TREND",
        confidence=0.7,
        margin_usdt=0.50,
        leverage=10,
        stop_loss=0.78,
        take_profits=[TakeProfitTarget(price=0.84, close_pct=100)],
    )
    data.update(overrides)
    return TradeCommand(**data)


def _state(positions=None):
    return {
        "decision_ready": True,
        "quality_flags": [],
        "account": {"positions": positions or []},
        "market": {
            "symbols": [
                {
                    "symbol": "SUIUSDT",
                    "data_quality": "OK",
                    "quality_flags": [],
                    "mark_price": 0.80,
                    "mid_price": 0.80,
                }
            ]
        },
    }


class RiskGovernorTests(unittest.TestCase):
    def test_accepts_coherent_small_balance_trade(self):
        gov = RiskGovernor(_FakeConn(), max_leverage=20, max_margin_usage_pct=0.95)
        result = gov.preflight(_command(), _state())
        self.assertTrue(result.accepted)
        self.assertAlmostEqual(result.details["notional"], 5.0)
        self.assertGreater(result.details["max_loss_usdt"], result.details["estimated_loss_to_stop_usdt"])
        self.assertEqual(result.details["take_profit_execution"][0]["mode"], "CLOSE_ALL")

    def test_small_balance_allows_only_one_active_position(self):
        gov = RiskGovernor(_FakeConn(balance=0.57), single_position_below_usdt=5.0)
        positions = [{"symbol": "ETHUSDT", "position_amount": 0.01}]
        result = gov.preflight(_command(), _state(positions))
        self.assertFalse(result.accepted)
        self.assertEqual(result.reason, "MAX_OPEN_POSITIONS_REACHED")

    def test_rejects_long_stop_above_entry(self):
        gov = RiskGovernor(_FakeConn())
        result = gov.preflight(_command(stop_loss=0.82), _state())
        self.assertFalse(result.accepted)
        self.assertEqual(result.reason, "LONG_STOP_MUST_BE_BELOW_ENTRY")

    def test_rejects_excess_leverage(self):
        gov = RiskGovernor(_FakeConn(), max_leverage=20)
        result = gov.preflight(_command(leverage=50), _state())
        self.assertFalse(result.accepted)
        self.assertEqual(result.reason, "LEVERAGE_OUT_OF_BOUNDS")

    def test_rejects_trade_whose_stop_risk_exceeds_balance_cap(self):
        gov = RiskGovernor(
            _FakeConn(balance=0.57),
            max_loss_pct_balance=0.35,
            estimated_taker_fee_rate=0.0005,
        )
        result = gov.preflight(_command(stop_loss=0.70), _state())
        self.assertFalse(result.accepted)
        self.assertEqual(result.reason, "ESTIMATED_STOP_LOSS_EXCEEDS_HARD_LIMIT")
        self.assertGreater(
            result.details["estimated_loss_to_stop_usdt"],
            result.details["max_loss_usdt"],
        )

    def test_rejects_partial_tp_below_exchange_minimum_quantity(self):
        gov = RiskGovernor(_FakeConn(balance=0.57, min_qty=1.0))
        command = _command(
            take_profits=[
                TakeProfitTarget(price=0.82, close_pct=10),
                TakeProfitTarget(price=0.84, close_pct=90),
            ]
        )
        result = gov.preflight(command, _state())
        self.assertFalse(result.accepted)
        self.assertEqual(result.reason, "PARTIAL_TAKE_PROFIT_NOT_EXECUTABLE")
        self.assertEqual(result.details["target_index"], 0)


if __name__ == "__main__":
    unittest.main()
