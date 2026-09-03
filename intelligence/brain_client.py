# -*- coding: utf-8 -*-
"""OpenAI-backed adaptive decision client.

This module ONLY proposes TradeCommand objects. It cannot talk to Binance and
cannot execute an order. Real execution remains behind command validation and
per-command approval in the executor layer.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional
from uuid import uuid4

from command_protocol import TradeCommand


DECISION_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "action": {"type": "string", "enum": ["WAIT", "OPEN_POSITION", "MODIFY_POSITION", "CLOSE_POSITION"]},
        "symbol": {"type": ["string", "null"]},
        "side": {"type": ["string", "null"], "enum": ["LONG", "SHORT", None]},
        "strategy": {"type": ["string", "null"]},
        "regime": {"type": ["string", "null"]},
        "confidence": {"type": ["number", "null"], "minimum": 0, "maximum": 1},
        "entry_type": {"type": "string", "enum": ["MARKET", "LIMIT"]},
        "entry_price": {"type": ["number", "null"]},
        "margin_usdt": {"type": ["number", "null"]},
        "leverage": {"type": ["integer", "null"]},
        "stop_loss": {"type": ["number", "null"]},
        "take_profits": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "price": {"type": "number"},
                    "close_pct": {"type": "number", "minimum": 0.0001, "maximum": 100},
                },
                "required": ["price", "close_pct"],
            },
        },
        "trailing": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "enabled": {"type": "boolean"},
                "activation_price": {"type": ["number", "null"]},
                "callback_rate": {"type": ["number", "null"]},
            },
            "required": ["enabled", "activation_price", "callback_rate"],
        },
        "reason": {"type": ["string", "null"]},
    },
    "required": [
        "action", "symbol", "side", "strategy", "regime", "confidence",
        "entry_type", "entry_price", "margin_usdt", "leverage", "stop_loss",
        "take_profits", "trailing", "reason"
    ],
}


SYSTEM_INSTRUCTIONS = """You are the decision layer of an adaptive USD-M Futures execution system.
You receive a current market/account snapshot. Do not force a trade. WAIT is a valid and preferred
answer when the evidence is conflicting, stale, illiquid, or insufficient. Treat regime and strategy
as dynamic: do not assume mean reversion, trend, breakout, or any fixed strategy. Consider the
provided market microstructure, order-book depth, taker flow, spread, funding/basis, open interest
and its change, positioning ratios, multi-timeframe structure, volatility, and any existing position.
Return exactly one structured decision. Never claim certainty.

For OPEN_POSITION, choose ONLY a symbol present in candidate_symbols. Read
candidate_execution_constraints before sizing: the leverage must be at least the symbol's
min_leverage_for_balance if an entry is proposed, but use no more leverage than is justified by the
trade geometry. Do not use leverage merely because it is available. The Risk Governor will still
independently validate hard leverage, margin and loss-to-stop limits.

For OPEN_POSITION, provide side, margin_usdt, leverage, stop_loss and at least one take profit.
For LIMIT, provide entry_price. Keep LONG/SHORT geometry internally coherent. When the balance or
quantity is very small, prefer a single take-profit target with close_pct=100 unless the snapshot
provides enough information to justify executable partial quantities; tiny partial exits are often
invalid under exchange minQty/stepSize rules and will be rejected locally.

For an existing position, MODIFY_POSITION or CLOSE_POSITION may be more appropriate than opening a
new trade. monitor_only_symbols are eligible for management/exit, not for a new OPEN_POSITION.
The execution system independently validates exchange rules and requires explicit approval before
any real new order is sent.
"""


class BrainClient:
    def __init__(
        self,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        command_ttl_seconds: int = 90,
        client: Any = None,
    ):
        self.model = model or os.getenv("OPENAI_MODEL", "gpt-5.6-sol")
        self.command_ttl_seconds = max(15, int(command_ttl_seconds))
        if client is not None:
            self.client = client
        else:
            try:
                from openai import OpenAI
            except ImportError as exc:
                raise RuntimeError("Instale a dependencia 'openai' para usar BrainClient") from exc
            self.client = OpenAI(api_key=api_key or os.getenv("OPENAI_API_KEY"))

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    def _wrap_decision(self, decision: Dict[str, Any], now: Optional[datetime] = None) -> TradeCommand:
        now = now or self._now()
        payload = dict(decision)
        payload.update(
            {
                "command_id": uuid4().hex,
                "issued_at": now.isoformat(),
                "expires_at": (now + timedelta(seconds=self.command_ttl_seconds)).isoformat(),
                "metadata": {"brain_model": self.model},
            }
        )
        command = TradeCommand.from_dict(payload)
        command.validate(now=now)
        return command

    def _wait(self, reason: str, now: Optional[datetime] = None) -> TradeCommand:
        return self._wrap_decision(
            {
                "action": "WAIT",
                "symbol": None,
                "side": None,
                "strategy": None,
                "regime": None,
                "confidence": 0.0,
                "entry_type": "MARKET",
                "entry_price": None,
                "margin_usdt": None,
                "leverage": None,
                "stop_loss": None,
                "take_profits": [],
                "trailing": {"enabled": False, "activation_price": None, "callback_rate": None},
                "reason": reason,
            },
            now=now,
        )

    def decide(self, market_state: Dict[str, Any]) -> TradeCommand:
        if not market_state.get("decision_ready"):
            flags = ", ".join(market_state.get("quality_flags") or []) or "market state not ready"
            return self._wait(f"WAIT local: {flags}")

        response = self.client.responses.create(
            model=self.model,
            instructions=SYSTEM_INSTRUCTIONS,
            input=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": "CURRENT_STATE_JSON\n" + json.dumps(market_state, separators=(",", ":"), ensure_ascii=False),
                        }
                    ],
                }
            ],
            text={
                "format": {
                    "type": "json_schema",
                    "name": "adaptive_trade_decision",
                    "schema": DECISION_SCHEMA,
                    "strict": True,
                }
            },
            store=False,
        )
        raw = getattr(response, "output_text", None)
        if not raw:
            return self._wait("WAIT local: brain returned no structured output")
        try:
            decision = json.loads(raw)
            return self._wrap_decision(decision)
        except Exception as exc:
            return self._wait(f"WAIT local: invalid brain decision: {exc}")
