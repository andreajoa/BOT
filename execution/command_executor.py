# -*- coding: utf-8 -*-
"""Approval-gated executor for structured adaptive TradeCommand objects."""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from approval import TradeApproval
from command_protocol import Action, EntryType, Side, TradeCommand
from execution.exchange_adapter import ExchangeAdapter
from execution.journal import ExecutionJournal
from risk.governor import RiskGovernor


class CommandExecutor:
    def __init__(
        self,
        connection: Any,
        governor: Optional[RiskGovernor] = None,
        adapter: Optional[ExchangeAdapter] = None,
        journal: Optional[ExecutionJournal] = None,
        position_manager: Any = None,
    ):
        self.connection = connection
        self.governor = governor or RiskGovernor(connection)
        self.adapter = adapter or ExchangeAdapter(connection)
        self.journal = journal or ExecutionJournal()
        self.position_manager = position_manager
        self.pending_entries: Dict[str, Dict[str, Any]] = {}

    def _approval_ok(self, command: TradeCommand, approval: Optional[TradeApproval]) -> Tuple[bool, str]:
        if command.action == Action.WAIT:
            return True, "WAIT"
        if approval is None:
            return False, "APPROVAL_REQUIRED"
        if approval.command_id != command.command_id:
            return False, "APPROVAL_COMMAND_MISMATCH"
        if not approval.is_valid():
            return False, "APPROVAL_INVALID_OR_EXPIRED"
        return True, "OK"

    def execute(
        self,
        command: TradeCommand,
        approval: Optional[TradeApproval],
        market_state: Dict[str, Any],
    ) -> Dict[str, Any]:
        self.journal.command_received(command)

        approval_ok, approval_reason = self._approval_ok(command, approval)
        if not approval_ok:
            self.journal.validation(command.command_id, False, approval_reason)
            return {"status": "REJECTED", "reason": approval_reason, "command_id": command.command_id}

        preflight = self.governor.preflight(command, market_state)
        self.journal.validation(
            command.command_id,
            preflight.accepted,
            preflight.reason,
            details=preflight.details,
        )
        if not preflight.accepted:
            return {
                "status": "REJECTED",
                "reason": preflight.reason,
                "details": preflight.details,
                "command_id": command.command_id,
            }

        if command.action == Action.WAIT:
            return {"status": "WAIT", "command_id": command.command_id, "reason": command.reason}
        if command.action == Action.OPEN_POSITION:
            return self._open(command, preflight.details)
        if command.action == Action.CLOSE_POSITION:
            return self._close(command, market_state)
        if command.action == Action.MODIFY_POSITION:
            if self.position_manager is None:
                return {"status": "REJECTED", "reason": "POSITION_MANAGER_UNAVAILABLE", "command_id": command.command_id}
            return self.position_manager.modify_from_command(command, market_state)
        return {"status": "REJECTED", "reason": "UNSUPPORTED_ACTION", "command_id": command.command_id}

    def _open(self, command: TradeCommand, plan: Dict[str, Any]) -> Dict[str, Any]:
        symbol = str(command.symbol).upper()
        position_side = command.side.value
        qty = float(plan["quantity"])
        leverage = int(plan["leverage"])

        self.journal.exchange_request(command.command_id, "SET_LEVERAGE", symbol=symbol, leverage=leverage)
        leverage_result = self.adapter.set_leverage(symbol, leverage)
        self.journal.exchange_result(command.command_id, "SET_LEVERAGE", leverage_result.get("success", False), result=leverage_result)
        if not leverage_result.get("success"):
            return {"status": "REJECTED", "reason": "SET_LEVERAGE_FAILED", "details": leverage_result, "command_id": command.command_id}

        self.journal.exchange_request(
            command.command_id,
            "OPEN_ENTRY",
            symbol=symbol,
            position_side=position_side,
            quantity=qty,
            entry_type=command.entry_type.value,
        )
        if command.entry_type == EntryType.MARKET:
            entry = self.adapter.open_market(command.command_id, symbol, position_side, qty)
        else:
            entry = self.adapter.open_limit(
                command.command_id,
                symbol,
                position_side,
                qty,
                float(plan.get("normalized_entry_price") or command.entry_price),
            )
        self.journal.exchange_result(command.command_id, "OPEN_ENTRY", entry.get("success", False), result=entry)
        if not entry.get("success"):
            return {"status": "REJECTED", "reason": "ENTRY_ORDER_FAILED", "details": entry, "command_id": command.command_id}

        order = entry.get("order") or {}
        client_order_id = order.get("clientOrderId") or self.adapter.client_order_id(command.command_id, "entry")
        status = str(order.get("status") or "").upper()

        if command.entry_type == EntryType.LIMIT and status != "FILLED":
            self.pending_entries[client_order_id] = {
                "command": command,
                "plan": plan,
                "quantity": float(entry.get("quantity") or qty),
            }
            return {
                "status": "PENDING_FILL",
                "command_id": command.command_id,
                "symbol": symbol,
                "client_order_id": client_order_id,
                "order": order,
            }

        filled_qty = self._filled_quantity(order, float(entry.get("quantity") or qty))
        protection = self._install_protection(command, plan, filled_qty)
        if not protection.get("success"):
            return protection

        return {
            "status": "EXECUTED",
            "command_id": command.command_id,
            "symbol": symbol,
            "side": position_side,
            "quantity": filled_qty,
            "entry_order": order,
            "protection": protection,
        }

    @staticmethod
    def _filled_quantity(order: Dict[str, Any], fallback: float) -> float:
        for key in ("executedQty", "origQty"):
            try:
                value = float(order.get(key) or 0)
            except (TypeError, ValueError):
                value = 0
            if value > 0:
                return value
        return fallback

    def _install_protection(self, command: TradeCommand, plan: Dict[str, Any], quantity: float) -> Dict[str, Any]:
        symbol = str(command.symbol).upper()
        position_side = command.side.value

        sl_price = float(plan.get("normalized_stop_loss") or command.stop_loss)
        self.journal.exchange_request(command.command_id, "SET_STOP_LOSS", symbol=symbol, stop_price=sl_price)
        sl = self.adapter.stop_close_all(command.command_id, symbol, position_side, sl_price)
        self.journal.exchange_result(command.command_id, "SET_STOP_LOSS", sl.get("success", False), result=sl)
        if not sl.get("success"):
            return self._emergency_flatten(command, quantity, "STOP_LOSS_INSTALL_FAILED", sl)

        tp_results = []
        targets = list(command.take_profits)
        cumulative_pct = 0.0
        for index, target in enumerate(targets):
            cumulative_pct += float(target.close_pct)
            is_last = index == len(targets) - 1
            trigger = float(target.price)
            self.journal.exchange_request(
                command.command_id,
                "SET_TAKE_PROFIT",
                symbol=symbol,
                target_index=index,
                trigger_price=trigger,
                close_pct=target.close_pct,
            )
            if is_last and cumulative_pct >= 99.999:
                result = self.adapter.take_profit_close_all(
                    command.command_id, symbol, position_side, trigger, suffix=f"tp{index + 1}"
                )
            else:
                partial_qty = quantity * (float(target.close_pct) / 100.0)
                result = self.adapter.take_profit_partial(
                    command.command_id,
                    symbol,
                    position_side,
                    trigger,
                    partial_qty,
                    suffix=f"tp{index + 1}",
                )
            self.journal.exchange_result(command.command_id, "SET_TAKE_PROFIT", result.get("success", False), result=result)
            tp_results.append(result)
            if not result.get("success"):
                return self._emergency_flatten(command, quantity, "TAKE_PROFIT_INSTALL_FAILED", result)

        registered = None
        if self.position_manager is not None:
            registered = self.position_manager.register_open_position(
                command=command,
                quantity=quantity,
                stop_order=sl.get("order") or {},
                take_profit_orders=[r.get("order") or {} for r in tp_results],
            )

        return {
            "success": True,
            "stop_loss": sl,
            "take_profits": tp_results,
            "trailing_registered": bool(registered) if command.trailing.enabled else False,
        }

    def _emergency_flatten(
        self,
        command: TradeCommand,
        quantity: float,
        reason: str,
        failure: Dict[str, Any],
    ) -> Dict[str, Any]:
        symbol = str(command.symbol).upper()
        self.connection.cancel_all_orders(symbol)
        close = self.adapter.close_market(symbol, command.side.value, quantity)
        self.journal.exchange_result(
            command.command_id,
            "EMERGENCY_FLATTEN",
            close.get("success", False),
            reason=reason,
            result=close,
        )
        return {
            "status": "FAILED_SAFE",
            "reason": reason,
            "failure": failure,
            "emergency_close": close,
            "command_id": command.command_id,
        }

    def _close(self, command: TradeCommand, market_state: Dict[str, Any]) -> Dict[str, Any]:
        symbol = str(command.symbol).upper()
        positions = [
            p
            for p in ((market_state.get("account") or {}).get("positions") or [])
            if str(p.get("symbol", "")).upper() == symbol and abs(float(p.get("position_amount") or 0)) > 0
        ]
        if command.side is not None:
            positions = [p for p in positions if self._position_side(p) == command.side.value]
        if len(positions) != 1:
            return {"status": "REJECTED", "reason": "POSITION_NOT_UNIQUE", "command_id": command.command_id}

        pos = positions[0]
        position_side = self._position_side(pos)
        quantity = abs(float(pos.get("position_amount") or 0))
        self.connection.cancel_all_orders(symbol)
        result = self.adapter.close_market(symbol, position_side, quantity)
        self.journal.exchange_result(command.command_id, "CLOSE_POSITION", result.get("success", False), result=result)
        return {
            "status": "CLOSED" if result.get("success") else "REJECTED",
            "reason": "OK" if result.get("success") else "CLOSE_FAILED",
            "command_id": command.command_id,
            "result": result,
        }

    @staticmethod
    def _position_side(position: Dict[str, Any]) -> str:
        explicit = str(position.get("position_side") or "").upper()
        if explicit in {"LONG", "SHORT"}:
            return explicit
        return "LONG" if float(position.get("position_amount") or 0) > 0 else "SHORT"

    def handle_order_event(self, order_event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Called from User Data Stream. Finalizes protections only after LIMIT fill."""
        client_order_id = order_event.get("client_order_id")
        if not client_order_id:
            return None
        pending = self.pending_entries.get(str(client_order_id))
        if pending is None:
            return None

        status = str(order_event.get("status") or "").upper()
        command: TradeCommand = pending["command"]
        self.journal.account_event({"order": order_event}, command.command_id)

        if status in {"CANCELED", "EXPIRED", "EXPIRED_IN_MATCH"}:
            self.pending_entries.pop(str(client_order_id), None)
            return {"status": "ENTRY_TERMINATED", "command_id": command.command_id, "order_status": status}
        if status != "FILLED":
            return {"status": "ENTRY_PENDING", "command_id": command.command_id, "order_status": status}

        self.pending_entries.pop(str(client_order_id), None)
        qty = float(order_event.get("filled_qty") or pending["quantity"])
        protection = self._install_protection(command, pending["plan"], qty)
        return {
            "status": "EXECUTED" if protection.get("success") else protection.get("status", "FAILED_SAFE"),
            "command_id": command.command_id,
            "quantity": qty,
            "protection": protection,
        }
