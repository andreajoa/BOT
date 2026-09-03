# -*- coding: utf-8 -*-
"""Crash-safe entry state machine for the production executor.

This layer extends the approval-gated CommandExecutor without changing strategy
logic. Its purpose is execution integrity around the dangerous window between
submitting an entry and confirming protective orders.

Key guarantees:
- a pending intent is persisted before the entry request is sent;
- ambiguous API outcomes are never blindly resent;
- deterministic clientOrderId lookup resolves whether Binance accepted it;
- PARTIALLY_FILLED entries cancel the remainder and protect the executed qty;
- stale LIMIT orders are canceled when the command TTL expires;
- pending entry state is removed only after protection is installed or a
  fail-safe emergency close is confirmed;
- a lightweight recovery thread rechecks unresolved pending entries.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from command_protocol import EntryType, TradeCommand
from execution.command_executor import CommandExecutor


class ResilientCommandExecutor(CommandExecutor):
    def __init__(self, *args: Any, recovery_poll_seconds: float = 2.0, **kwargs: Any):
        self._pending_lock = threading.RLock()
        self._state_machine_lock = threading.RLock()
        self._recovery_stop = threading.Event()
        self._recovery_poll_seconds = max(0.0, float(recovery_poll_seconds))
        super().__init__(*args, **kwargs)
        self._recovery_thread: Optional[threading.Thread] = None
        if self._recovery_poll_seconds > 0:
            self._recovery_thread = threading.Thread(
                target=self._recovery_loop,
                name="pending-entry-recovery",
                daemon=True,
            )
            self._recovery_thread.start()

    def _load_pending_entries(self) -> None:
        with self._pending_lock:
            super()._load_pending_entries()

    def _save_pending_entries(self) -> None:
        with self._pending_lock:
            super()._save_pending_entries()

    @staticmethod
    def _is_not_found(result: Dict[str, Any]) -> bool:
        if result.get("not_found") is True:
            return True
        error = str(result.get("error") or "")
        return error.startswith("-2013:") or "Order does not exist" in error

    @staticmethod
    def _expired(command: TradeCommand) -> bool:
        try:
            expires = datetime.fromisoformat(command.expires_at.replace("Z", "+00:00"))
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)
            return expires <= datetime.now(timezone.utc)
        except Exception:
            return True

    @staticmethod
    def _executed_quantity(order: Dict[str, Any], filled_fallback: float = 0.0) -> float:
        for key in ("executedQty", "cumQty", "filled_qty"):
            try:
                value = float(order.get(key) or 0)
            except (TypeError, ValueError):
                value = 0.0
            if value > 0:
                return value
        if str(order.get("status") or "").upper() == "FILLED":
            for key in ("origQty", "original_qty"):
                try:
                    value = float(order.get(key) or 0)
                except (TypeError, ValueError):
                    value = 0.0
                if value > 0:
                    return value
            return max(0.0, float(filled_fallback or 0.0))
        return 0.0

    def _remember_pending(
        self,
        client_id: str,
        command: TradeCommand,
        plan: Dict[str, Any],
        quantity: float,
        symbol: str,
    ) -> Dict[str, Any]:
        with self._pending_lock:
            record = {
                "command": command,
                "plan": dict(plan),
                "quantity": float(quantity),
                "symbol": symbol.upper(),
            }
            self.pending_entries[client_id] = record
            self._save_pending_entries()
            return record

    def _clear_pending(self, client_id: str) -> None:
        with self._pending_lock:
            self.pending_entries.pop(client_id, None)
            self._save_pending_entries()

    @staticmethod
    def _protection_terminal(result: Dict[str, Any]) -> bool:
        if result.get("success") is True:
            return True
        if result.get("status") == "FAILED_SAFE":
            return bool((result.get("emergency_close") or {}).get("success"))
        return False

    def _protect_pending(
        self,
        client_id: str,
        pending: Dict[str, Any],
        quantity: float,
        recovered_status: str = "EXECUTED",
    ) -> Dict[str, Any]:
        command: TradeCommand = pending["command"]
        protection = self._install_protection(command, pending["plan"], quantity)
        terminal = self._protection_terminal(protection)
        if terminal:
            self._clear_pending(client_id)
        return {
            "status": recovered_status if protection.get("success") else protection.get("status", "PROTECTION_FAILED"),
            "command_id": command.command_id,
            "quantity": quantity,
            "protection": protection,
            "pending_cleared": terminal,
        }

    def _cancel_remaining(
        self,
        client_id: str,
        pending: Dict[str, Any],
        current_order: Dict[str, Any],
        reason: str,
    ) -> Dict[str, Any]:
        command: TradeCommand = pending["command"]
        symbol = str(pending.get("symbol") or command.symbol or "").upper()
        self.journal.exchange_request(
            command.command_id,
            "CANCEL_ENTRY_REMAINDER",
            symbol=symbol,
            client_order_id=client_id,
            reason=reason,
        )
        cancel = self.adapter.cancel_order(symbol, client_order_id=client_id)
        self.journal.exchange_result(
            command.command_id,
            "CANCEL_ENTRY_REMAINDER",
            cancel.get("success", False),
            reason=reason,
            result=cancel,
        )

        order = (cancel.get("order") or {}) if cancel.get("success") else current_order
        quantity = self._executed_quantity(order)
        if quantity <= 0:
            quantity = self._executed_quantity(current_order)

        if not cancel.get("success"):
            refreshed = self.adapter.query_order(symbol, client_order_id=client_id)
            if refreshed.get("success"):
                refreshed_order = refreshed.get("order") or {}
                refreshed_status = str(refreshed_order.get("status") or "").upper()
                if refreshed_status == "FILLED":
                    quantity = self._executed_quantity(refreshed_order, float(pending.get("quantity") or 0))
                    return self._protect_pending(client_id, pending, quantity, "RECOVERED_EXECUTED")
                if refreshed_status in {"CANCELED", "EXPIRED", "EXPIRED_IN_MATCH", "REJECTED"}:
                    quantity = self._executed_quantity(refreshed_order)
                    if quantity > 0:
                        return self._protect_pending(client_id, pending, quantity, "PARTIAL_EXECUTED_PROTECTED")
                    self._clear_pending(client_id)
                    return {
                        "status": "ENTRY_TERMINATED",
                        "command_id": command.command_id,
                        "order_status": refreshed_status,
                        "reason": reason,
                    }
            return {
                "status": "CANCEL_REMAINDER_UNCONFIRMED",
                "command_id": command.command_id,
                "reason": reason,
                "cancel": cancel,
                "pending_retained": True,
            }

        if quantity > 0:
            return self._protect_pending(client_id, pending, quantity, "PARTIAL_EXECUTED_PROTECTED")

        self._clear_pending(client_id)
        return {
            "status": "ENTRY_TERMINATED",
            "command_id": command.command_id,
            "order_status": str(order.get("status") or "CANCELED").upper(),
            "reason": reason,
        }

    def _process_order_state(
        self,
        client_id: str,
        pending: Dict[str, Any],
        order: Dict[str, Any],
        source: str,
    ) -> Dict[str, Any]:
        with self._state_machine_lock:
            command: TradeCommand = pending["command"]
            status = str(order.get("status") or "").upper()

            if status == "FILLED":
                quantity = self._executed_quantity(order, float(pending.get("quantity") or 0))
                return self._protect_pending(
                    client_id,
                    pending,
                    quantity,
                    "RECOVERED_EXECUTED" if source != "submit" else "EXECUTED",
                )

            if status == "PARTIALLY_FILLED":
                return self._cancel_remaining(client_id, pending, order, "PARTIAL_FILL_PROTECT_NOW")

            if status == "NEW":
                if self._expired(command):
                    return self._cancel_remaining(client_id, pending, order, "COMMAND_TTL_EXPIRED")
                return {
                    "status": "PENDING_FILL",
                    "command_id": command.command_id,
                    "order_status": status,
                    "source": source,
                    "pending_retained": True,
                }

            if status in {"CANCELED", "EXPIRED", "EXPIRED_IN_MATCH", "REJECTED"}:
                quantity = self._executed_quantity(order)
                if quantity > 0:
                    return self._protect_pending(client_id, pending, quantity, "PARTIAL_EXECUTED_PROTECTED")
                self._clear_pending(client_id)
                return {
                    "status": "ENTRY_TERMINATED",
                    "command_id": command.command_id,
                    "order_status": status,
                    "source": source,
                }

            return {
                "status": "ENTRY_STATE_UNKNOWN",
                "command_id": command.command_id,
                "order_status": status,
                "source": source,
                "pending_retained": True,
            }

    def _query_and_process(self, client_id: str, pending: Dict[str, Any], source: str) -> Dict[str, Any]:
        command: TradeCommand = pending["command"]
        symbol = str(pending.get("symbol") or command.symbol or "").upper()
        result = self.adapter.query_order(symbol, client_order_id=client_id)
        if result.get("success"):
            return self._process_order_state(client_id, pending, result.get("order") or {}, source)
        if self._is_not_found(result):
            if self._expired(command):
                self._clear_pending(client_id)
                return {
                    "status": "ENTRY_NOT_FOUND_AFTER_TTL",
                    "command_id": command.command_id,
                    "pending_cleared": True,
                }
            return {
                "status": "ENTRY_NOT_FOUND_YET",
                "command_id": command.command_id,
                "pending_retained": True,
                "details": result,
            }
        return {
            "status": "ENTRY_QUERY_AMBIGUOUS",
            "command_id": command.command_id,
            "pending_retained": True,
            "details": result,
        }

    def _open(self, command: TradeCommand, plan: Dict[str, Any]) -> Dict[str, Any]:
        symbol = str(command.symbol).upper()
        position_side = command.side.value
        qty = float(plan["quantity"])
        leverage = int(plan["leverage"])
        entry_client_id = self.adapter.client_order_id(command.command_id, "entry")

        managed = self._managed_for_command(command)
        if managed is not None:
            return {
                "status": "ALREADY_EXECUTED",
                "reason": "POSITION_ALREADY_MANAGED_FOR_COMMAND",
                "command_id": command.command_id,
                "position": managed,
            }

        with self._pending_lock:
            existing_pending = self.pending_entries.get(entry_client_id)
        if existing_pending is not None:
            return self._query_and_process(entry_client_id, existing_pending, "existing_pending")

        existing = self.adapter.query_order(symbol, client_order_id=entry_client_id)
        if existing.get("success"):
            pending = self._remember_pending(entry_client_id, command, plan, qty, symbol)
            return self._process_order_state(entry_client_id, pending, existing.get("order") or {}, "existing_order")
        if not self._is_not_found(existing):
            return {
                "status": "REJECTED",
                "reason": "ENTRY_IDEMPOTENCY_CHECK_AMBIGUOUS",
                "details": existing,
                "command_id": command.command_id,
            }

        self.journal.exchange_request(command.command_id, "SET_LEVERAGE", symbol=symbol, leverage=leverage)
        leverage_result = self.adapter.set_leverage(symbol, leverage)
        self.journal.exchange_result(
            command.command_id,
            "SET_LEVERAGE",
            leverage_result.get("success", False),
            result=leverage_result,
        )
        if not leverage_result.get("success"):
            return {
                "status": "REJECTED",
                "reason": "SET_LEVERAGE_FAILED",
                "details": leverage_result,
                "command_id": command.command_id,
            }

        # Persist BEFORE the network submission. If the process dies after Binance
        # accepts the order but before the HTTP response arrives, restart recovery
        # can still find it by deterministic clientOrderId and install protection.
        pending = self._remember_pending(entry_client_id, command, plan, qty, symbol)

        self.journal.exchange_request(
            command.command_id,
            "OPEN_ENTRY",
            symbol=symbol,
            position_side=position_side,
            quantity=qty,
            entry_type=command.entry_type.value,
            client_order_id=entry_client_id,
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

        if entry.get("success"):
            order = entry.get("order") or {}
            actual_client_id = str(order.get("clientOrderId") or entry_client_id)
            if actual_client_id != entry_client_id:
                with self._pending_lock:
                    self.pending_entries[actual_client_id] = self.pending_entries.pop(entry_client_id)
                    self._save_pending_entries()
                entry_client_id = actual_client_id
                pending = self.pending_entries[entry_client_id]
            return self._process_order_state(entry_client_id, pending, order, "submit")

        # Never blindly resend after an error: Binance may have accepted the order
        # even if the HTTP response was lost. Keep the persisted intent and resolve
        # it by clientOrderId now and in the recovery loop.
        resolved = self._query_and_process(entry_client_id, pending, "submit_error")
        if resolved.get("status") == "ENTRY_NOT_FOUND_YET":
            resolved["submission_error"] = entry
        return resolved

    def handle_order_event(self, order_event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        client_id = order_event.get("client_order_id")
        if not client_id:
            return None
        with self._pending_lock:
            pending = self.pending_entries.get(str(client_id))
        if pending is None:
            return None

        command: TradeCommand = pending["command"]
        self.journal.account_event({"order": order_event}, command.command_id)
        normalized = {
            "status": order_event.get("status"),
            "executedQty": order_event.get("filled_qty"),
            "origQty": order_event.get("original_qty"),
            "clientOrderId": client_id,
        }
        return self._process_order_state(str(client_id), pending, normalized, "user_stream")

    def recover_pending_entries(self) -> Dict[str, Dict[str, Any]]:
        outcomes: Dict[str, Dict[str, Any]] = {}
        with self._pending_lock:
            snapshot = list(self.pending_entries.items())
        for client_id, pending in snapshot:
            try:
                outcomes[client_id] = self._query_and_process(client_id, pending, "recovery")
            except Exception as exc:
                command: TradeCommand = pending["command"]
                outcomes[client_id] = {
                    "status": "RECOVERY_EXCEPTION",
                    "command_id": command.command_id,
                    "error": str(exc),
                    "pending_retained": True,
                }
                self.journal.append(
                    "PENDING_ENTRY_RECOVERY_ERROR",
                    command.command_id,
                    {"client_order_id": client_id, "error": str(exc)},
                    "ERROR",
                )
        if outcomes:
            self.journal.append("PENDING_ENTRIES_RECOVERED", payload={"outcomes": outcomes})
        return outcomes

    def _recovery_loop(self) -> None:
        while not self._recovery_stop.wait(self._recovery_poll_seconds):
            with self._pending_lock:
                has_pending = bool(self.pending_entries)
            if not has_pending:
                continue
            try:
                self.recovery_outcomes = self.recover_pending_entries()
            except Exception as exc:
                self.journal.append(
                    "PENDING_ENTRY_RECOVERY_LOOP_ERROR",
                    payload={"error": str(exc)},
                    level="ERROR",
                )

    def stop_recovery(self) -> None:
        self._recovery_stop.set()
        thread = self._recovery_thread
        if thread and thread.is_alive():
            thread.join(timeout=max(1.0, self._recovery_poll_seconds + 0.5))
