# -*- coding: utf-8 -*-
"""Persistent lifecycle manager for approved open positions.

Manages already-authorized protection: current stop, TP orders and trailing
movement. It does not choose entries or strategies.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict
from typing import Any, Dict, List, Optional

from command_protocol import Action, Side, TradeCommand
from execution.exchange_adapter import ExchangeAdapter
from execution.journal import ExecutionJournal


class PositionManager:
    def __init__(
        self,
        adapter: ExchangeAdapter,
        journal: Optional[ExecutionJournal] = None,
        state_path: str = "logs/managed_positions.json",
    ):
        self.adapter = adapter
        self.journal = journal or ExecutionJournal()
        self.state_path = state_path
        self.positions: Dict[str, Dict[str, Any]] = {}
        self._load()

    @staticmethod
    def _key(symbol: str, side: str) -> str:
        return f"{symbol.upper()}:{side.upper()}"

    @staticmethod
    def _order_id(order: Dict[str, Any]) -> Optional[int]:
        value = order.get("orderId")
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    def _load(self) -> None:
        if not os.path.exists(self.state_path):
            return
        try:
            with open(self.state_path, "r", encoding="utf-8") as fh:
                payload = json.load(fh)
            if isinstance(payload, dict):
                self.positions = payload
        except Exception:
            self.positions = {}

    def _save(self) -> None:
        directory = os.path.dirname(self.state_path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        fd, temp_path = tempfile.mkstemp(prefix="managed_positions_", suffix=".json", dir=directory or None)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(self.positions, fh, ensure_ascii=False, indent=2)
            os.replace(temp_path, self.state_path)
        finally:
            if os.path.exists(temp_path):
                os.unlink(temp_path)

    def register_open_position(
        self,
        command: TradeCommand,
        quantity: float,
        stop_order: Dict[str, Any],
        take_profit_orders: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        symbol = str(command.symbol).upper()
        side = command.side.value
        key = self._key(symbol, side)
        state = {
            "command_id": command.command_id,
            "symbol": symbol,
            "side": side,
            "quantity": float(quantity),
            "entry_price": float(command.entry_price) if command.entry_price else None,
            "current_stop": float(command.stop_loss),
            "stop_order_id": self._order_id(stop_order),
            "stop_client_order_id": stop_order.get("clientOrderId"),
            "tp_order_ids": [oid for oid in (self._order_id(o) for o in take_profit_orders) if oid is not None],
            "tp_client_order_ids": [o.get("clientOrderId") for o in take_profit_orders if o.get("clientOrderId")],
            "trailing": {
                "enabled": bool(command.trailing.enabled),
                "activation_price": command.trailing.activation_price,
                "callback_rate": command.trailing.callback_rate,
                "activated": False,
                "best_price": None,
                "move_count": 0,
            },
            "strategy": command.strategy,
            "regime": command.regime,
        }
        self.positions[key] = state
        self._save()
        self.journal.append("POSITION_REGISTERED", command.command_id, {"position": state})
        return state

    def get(self, symbol: str, side: str) -> Optional[Dict[str, Any]]:
        return self.positions.get(self._key(symbol, side))

    def on_price(self, symbol: str, price: float) -> List[Dict[str, Any]]:
        symbol = symbol.upper()
        price = float(price)
        updates: List[Dict[str, Any]] = []
        for key, state in list(self.positions.items()):
            if state.get("symbol") != symbol:
                continue
            trailing = state.get("trailing") or {}
            if not trailing.get("enabled") or not trailing.get("callback_rate"):
                continue
            side = state["side"]
            activation = trailing.get("activation_price")
            activated = bool(trailing.get("activated"))
            if not activated:
                if activation is None:
                    activated = True
                elif side == "LONG" and price >= float(activation):
                    activated = True
                elif side == "SHORT" and price <= float(activation):
                    activated = True
                if activated:
                    trailing["activated"] = True
                    trailing["best_price"] = price
                    self.journal.append("TRAILING_ACTIVATED", state.get("command_id"), {"symbol": symbol, "side": side, "price": price})

            if not activated:
                continue

            best = trailing.get("best_price")
            if best is None:
                best = price
            best = max(float(best), price) if side == "LONG" else min(float(best), price)
            trailing["best_price"] = best
            callback_fraction = float(trailing["callback_rate"]) / 100.0
            candidate = best * (1 - callback_fraction) if side == "LONG" else best * (1 + callback_fraction)
            current_stop = float(state.get("current_stop") or 0)
            improves = candidate > current_stop if side == "LONG" else candidate < current_stop
            if not improves:
                continue

            result = self._replace_stop(state, candidate)
            updates.append(result)
        if updates:
            self._save()
        return updates

    def _replace_stop(self, state: Dict[str, Any], new_stop: float) -> Dict[str, Any]:
        command_id = state["command_id"]
        symbol = state["symbol"]
        side = state["side"]
        trailing = state.get("trailing") or {}
        sequence = int(trailing.get("move_count") or 0) + 1

        # Create new protection first. Only then cancel the old stop.
        created = self.adapter.stop_close_all(
            command_id,
            symbol,
            side,
            new_stop,
            suffix=f"trail{sequence}",
        )
        if not created.get("success"):
            self.journal.exchange_result(command_id, "MOVE_STOP", False, result=created)
            return {"success": False, "reason": "NEW_STOP_REJECTED", "result": created}

        old_id = state.get("stop_order_id")
        cancel_result = None
        if old_id is not None:
            cancel_result = self.adapter.cancel_order(symbol, order_id=int(old_id))

        actual_stop = float(created.get("stop_price") or new_stop)
        state["current_stop"] = actual_stop
        state["stop_order_id"] = self._order_id(created.get("order") or {})
        state["stop_client_order_id"] = (created.get("order") or {}).get("clientOrderId")
        trailing["move_count"] = sequence
        state["trailing"] = trailing
        self.journal.exchange_result(
            command_id,
            "MOVE_STOP",
            True,
            new_stop=actual_stop,
            old_stop_cancel=cancel_result,
        )
        return {"success": True, "new_stop": actual_stop, "cancel_old": cancel_result}

    def modify_from_command(self, command: TradeCommand, market_state: Dict[str, Any]) -> Dict[str, Any]:
        if command.action != Action.MODIFY_POSITION or not command.symbol:
            return {"status": "REJECTED", "reason": "INVALID_MODIFY_COMMAND", "command_id": command.command_id}

        candidates = [
            state for state in self.positions.values()
            if state.get("symbol") == str(command.symbol).upper()
            and (command.side is None or state.get("side") == command.side.value)
        ]
        if len(candidates) != 1:
            return {"status": "REJECTED", "reason": "MANAGED_POSITION_NOT_UNIQUE", "command_id": command.command_id}
        state = candidates[0]

        changes: Dict[str, Any] = {}
        if command.stop_loss is not None:
            changes["stop"] = self._replace_stop(state, float(command.stop_loss))
            if not changes["stop"].get("success"):
                return {"status": "REJECTED", "reason": "STOP_MODIFY_FAILED", "command_id": command.command_id, "changes": changes}

        if command.trailing.enabled:
            trailing = state.setdefault("trailing", {})
            trailing.update(
                {
                    "enabled": True,
                    "activation_price": command.trailing.activation_price,
                    "callback_rate": command.trailing.callback_rate,
                    "activated": False,
                    "best_price": None,
                }
            )
            changes["trailing"] = dict(trailing)

        self._save()
        self.journal.append("POSITION_MODIFIED", command.command_id, {"symbol": state["symbol"], "changes": changes})
        return {"status": "MODIFIED", "command_id": command.command_id, "changes": changes}

    def handle_order_event(self, order: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        order_id = order.get("order_id")
        client_id = order.get("client_order_id")
        status = str(order.get("status") or "").upper()
        if status != "FILLED":
            return None
        for key, state in list(self.positions.items()):
            known_ids = {state.get("stop_order_id"), *(state.get("tp_order_ids") or [])}
            known_clients = {state.get("stop_client_order_id"), *(state.get("tp_client_order_ids") or [])}
            if order_id in known_ids or client_id in known_clients:
                self.journal.append("PROTECTION_FILLED", state.get("command_id"), {"order": order})
                return {"status": "PROTECTION_FILLED", "position_key": key, "order": order}
        return None

    def reconcile(self, account_snapshot: Dict[str, Any]) -> List[str]:
        live_keys = set()
        for p in (account_snapshot.get("positions") or {}).values():
            amount = float(p.get("position_amount") or 0)
            if amount == 0:
                continue
            side = str(p.get("position_side") or "").upper()
            if side not in {"LONG", "SHORT"}:
                side = "LONG" if amount > 0 else "SHORT"
            live_keys.add(self._key(str(p.get("symbol")), side))

        removed = []
        for key in list(self.positions):
            if key not in live_keys:
                removed.append(key)
                self.positions.pop(key, None)
        if removed:
            self._save()
        return removed
