# -*- coding: utf-8 -*-
"""Strategy-agnostic preflight gate for adaptive TradeCommand objects."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Dict, Optional

from command_protocol import Action, EntryType, Side, TradeCommand


@dataclass(frozen=True)
class PreflightResult:
    accepted: bool
    reason: str
    details: Dict[str, Any]


class RiskGovernor:
    def __init__(
        self,
        connection: Any,
        max_leverage: Optional[int] = None,
        max_margin_usage_pct: Optional[float] = None,
        single_position_below_usdt: Optional[float] = None,
        max_open_positions: Optional[int] = None,
    ):
        self.connection = connection
        self.max_leverage = int(max_leverage or os.getenv("MAX_LEVERAGE_HARD", "20"))
        self.max_margin_usage_pct = float(max_margin_usage_pct or os.getenv("MAX_MARGIN_USAGE_PCT", "0.95"))
        self.single_position_below_usdt = float(
            single_position_below_usdt or os.getenv("SINGLE_POSITION_BELOW_USDT", "5.0")
        )
        self.max_open_positions = int(max_open_positions or os.getenv("MAX_OPEN_POSITIONS_HARD", "3"))

    @staticmethod
    def _active_positions(market_state: Dict[str, Any]) -> list:
        positions = ((market_state.get("account") or {}).get("positions") or [])
        return [p for p in positions if abs(float(p.get("position_amount") or 0)) > 0]

    @staticmethod
    def _symbol_row(market_state: Dict[str, Any], symbol: str) -> Optional[Dict[str, Any]]:
        for row in ((market_state.get("market") or {}).get("symbols") or []):
            if str(row.get("symbol", "")).upper() == symbol.upper():
                return row
        return None

    @staticmethod
    def _reference_price(command: TradeCommand, row: Dict[str, Any]) -> Optional[float]:
        if command.entry_type == EntryType.LIMIT and command.entry_price:
            return float(command.entry_price)
        for key in ("mark_price", "mid_price", "last_trade_price"):
            value = row.get(key)
            if value is not None and float(value) > 0:
                return float(value)
        return None

    @staticmethod
    def _geometry_ok(command: TradeCommand, reference_price: float) -> tuple[bool, str]:
        if command.action != Action.OPEN_POSITION or command.side is None:
            return True, "OK"
        if command.stop_loss is None:
            return False, "STOP_LOSS_REQUIRED"
        targets = [float(t.price) for t in command.take_profits]
        if command.side == Side.LONG:
            if not float(command.stop_loss) < reference_price:
                return False, "LONG_STOP_MUST_BE_BELOW_ENTRY"
            if any(tp <= reference_price for tp in targets):
                return False, "LONG_TP_MUST_BE_ABOVE_ENTRY"
        else:
            if not float(command.stop_loss) > reference_price:
                return False, "SHORT_STOP_MUST_BE_ABOVE_ENTRY"
            if any(tp >= reference_price for tp in targets):
                return False, "SHORT_TP_MUST_BE_BELOW_ENTRY"
        return True, "OK"

    def preflight(self, command: TradeCommand, market_state: Dict[str, Any]) -> PreflightResult:
        try:
            command.validate()
        except Exception as exc:
            return PreflightResult(False, "COMMAND_INVALID", {"error": str(exc)})

        if command.action == Action.WAIT:
            return PreflightResult(True, "WAIT", {})

        symbol = str(command.symbol).upper()

        # Saidas devem permanecer disponiveis mesmo se o stream publico estiver
        # degradado. O executor confirma a posicao real antes de enviar a ordem.
        if command.action == Action.CLOSE_POSITION:
            return PreflightResult(True, "OK", {"symbol": symbol, "exit_without_public_market_dependency": True})

        if not market_state.get("decision_ready"):
            return PreflightResult(False, "MARKET_STATE_NOT_READY", {"flags": market_state.get("quality_flags") or []})

        row = self._symbol_row(market_state, symbol)
        if row is None:
            return PreflightResult(False, "SYMBOL_NOT_IN_CURRENT_STATE", {"symbol": symbol})
        if row.get("data_quality") != "OK":
            return PreflightResult(False, "SYMBOL_DATA_DEGRADED", {"flags": row.get("quality_flags") or []})

        if command.action == Action.MODIFY_POSITION:
            return PreflightResult(True, "OK", {"symbol": symbol})

        available = self.connection.get_usdt_balance()
        if available <= 0:
            return PreflightResult(False, "NO_AVAILABLE_USDT", {"available_usdt": available})

        active = self._active_positions(market_state)
        allowed_positions = 1 if available < self.single_position_below_usdt else self.max_open_positions
        if len(active) >= allowed_positions:
            return PreflightResult(
                False,
                "MAX_OPEN_POSITIONS_REACHED",
                {"active_positions": len(active), "allowed_positions": allowed_positions, "available_usdt": available},
            )

        leverage = int(command.leverage or 0)
        if leverage < 1 or leverage > self.max_leverage:
            return PreflightResult(
                False,
                "LEVERAGE_OUT_OF_BOUNDS",
                {"requested": leverage, "max_leverage": self.max_leverage},
            )

        margin = float(command.margin_usdt or 0)
        max_margin = available * self.max_margin_usage_pct
        if margin > max_margin:
            return PreflightResult(
                False,
                "MARGIN_EXCEEDS_AVAILABLE_POLICY",
                {"requested_margin": margin, "max_margin": max_margin, "available_usdt": available},
            )

        reference = self._reference_price(command, row)
        if reference is None:
            return PreflightResult(False, "REFERENCE_PRICE_UNAVAILABLE", {})

        geometry_ok, geometry_reason = self._geometry_ok(command, reference)
        if not geometry_ok:
            return PreflightResult(False, geometry_reason, {"reference_price": reference})

        try:
            normalized_stop = self.connection.normalize_price(symbol, float(command.stop_loss), "nearest")
            normalized_tps = [self.connection.normalize_price(symbol, float(t.price), "nearest") for t in command.take_profits]
            normalized_entry = (
                self.connection.normalize_price(symbol, float(command.entry_price), "nearest")
                if command.entry_type == EntryType.LIMIT and command.entry_price is not None
                else None
            )
        except Exception as exc:
            return PreflightResult(False, "PRICE_FILTER_REJECTED", {"error": str(exc)})

        sizing = self.connection.quantity_from_margin(symbol, margin, leverage, reference)
        if not sizing.get("success"):
            return PreflightResult(False, str(sizing.get("error") or "SIZING_REJECTED"), dict(sizing))

        return PreflightResult(
            True,
            "OK",
            {
                "symbol": symbol,
                "side": command.side.value,
                "reference_price": reference,
                "quantity": sizing["quantity"],
                "notional": sizing["notional"],
                "actual_margin": sizing["actual_margin"],
                "leverage": leverage,
                "normalized_entry_price": normalized_entry,
                "normalized_stop_loss": normalized_stop,
                "normalized_take_profits": normalized_tps,
                "available_usdt": available,
            },
        )
