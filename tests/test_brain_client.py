# -*- coding: utf-8 -*-
import json
import unittest

from command_protocol import Action, Side
from intelligence.brain_client import BrainClient


class _FakeResponse:
    def __init__(self, payload):
        self.output_text = json.dumps(payload)


class _FakeResponses:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return _FakeResponse(self.payload)


class _FakeClient:
    def __init__(self, payload):
        self.responses = _FakeResponses(payload)


class BrainClientTests(unittest.TestCase):
    def _ready_state(self):
        return {
            "decision_ready": True,
            "quality_flags": [],
            "account": {"wallet_balance_usdt": 0.57, "positions": []},
            "market": {"symbols": [{"symbol": "SUIUSDT", "mark_price": 0.8}]},
        }

    def test_not_ready_short_circuits_without_model_call(self):
        fake = _FakeClient({})
        brain = BrainClient(client=fake, model="test-model")
        command = brain.decide({"decision_ready": False, "quality_flags": ["MARKET_STREAM_STALE"]})
        self.assertEqual(command.action, Action.WAIT)
        self.assertEqual(len(fake.responses.calls), 0)

    def test_open_position_is_wrapped_and_validated(self):
        fake = _FakeClient(
            {
                "action": "OPEN_POSITION",
                "symbol": "SUIUSDT",
                "side": "LONG",
                "strategy": "trend_pullback",
                "regime": "TREND",
                "confidence": 0.76,
                "entry_type": "MARKET",
                "entry_price": None,
                "margin_usdt": 0.50,
                "leverage": 10,
                "stop_loss": 0.78,
                "take_profits": [{"price": 0.84, "close_pct": 100}],
                "trailing": {"enabled": True, "activation_price": 0.83, "callback_rate": 0.5},
                "reason": "flow and structure aligned",
            }
        )
        brain = BrainClient(client=fake, model="test-model")
        command = brain.decide(self._ready_state())
        self.assertEqual(command.action, Action.OPEN_POSITION)
        self.assertEqual(command.side, Side.LONG)
        self.assertEqual(command.margin_usdt, 0.50)
        self.assertEqual(command.leverage, 10)
        self.assertEqual(command.metadata["brain_model"], "test-model")
        self.assertEqual(len(fake.responses.calls), 1)
        call = fake.responses.calls[0]
        self.assertFalse(call["store"])
        self.assertEqual(call["text"]["format"]["type"], "json_schema")


if __name__ == "__main__":
    unittest.main()
