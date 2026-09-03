# -*- coding: utf-8 -*-
"""Persistent approval gateway keyed by TradeCommand.command_id."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional, Tuple

from approval import TradeApproval
from command_protocol import TradeCommand


class ApprovalGateway:
    def __init__(
        self,
        pending_path: str = "logs/pending_command.json",
        approval_path: str = "logs/trade_approval.json",
        history_path: str = "logs/approval_history.jsonl",
    ):
        self.pending_path = pending_path
        self.approval_path = approval_path
        self.history_path = history_path
        for path in (pending_path, approval_path, history_path):
            directory = os.path.dirname(path)
            if directory:
                os.makedirs(directory, exist_ok=True)

    @staticmethod
    def _jsonable(value: Any) -> Any:
        if isinstance(value, Enum):
            return value.value
        if isinstance(value, dict):
            return {str(k): ApprovalGateway._jsonable(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [ApprovalGateway._jsonable(v) for v in value]
        return value

    @staticmethod
    def _atomic_write(path: str, payload: Dict[str, Any]) -> None:
        directory = os.path.dirname(path)
        fd, temp_path = tempfile.mkstemp(prefix="approval_", suffix=".json", dir=directory or None)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=2)
            os.replace(temp_path, path)
        finally:
            if os.path.exists(temp_path):
                os.unlink(temp_path)

    def _history(self, event: str, payload: Dict[str, Any]) -> None:
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": event,
            **self._jsonable(payload),
        }
        with open(self.history_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")

    def publish(self, command: TradeCommand, preflight: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        payload = self._jsonable(asdict(command))
        wrapper = {
            "published_at": datetime.now(timezone.utc).isoformat(),
            "command": payload,
            "preflight": preflight or None,
        }
        self._atomic_write(self.pending_path, wrapper)
        # Approval from any earlier command must never carry forward.
        if os.path.exists(self.approval_path):
            os.unlink(self.approval_path)
        self._history("COMMAND_PUBLISHED", {"command_id": command.command_id, "command": payload})
        return wrapper

    def pending(self) -> Optional[Tuple[TradeCommand, Dict[str, Any]]]:
        if not os.path.exists(self.pending_path):
            return None
        try:
            with open(self.pending_path, "r", encoding="utf-8") as fh:
                wrapper = json.load(fh)
            command = TradeCommand.from_dict(wrapper["command"])
            command.validate()
            return command, wrapper
        except Exception:
            return None

    def approve(self, command_id: str) -> TradeApproval:
        pending = self.pending()
        if pending is None:
            raise ValueError("Nenhum comando pendente valido")
        command, _ = pending
        if command.command_id != command_id:
            raise ValueError("command_id nao corresponde ao comando pendente")
        now = datetime.now(timezone.utc)
        approval = TradeApproval(
            command_id=command.command_id,
            approved=True,
            approved_at=now.isoformat(),
            expires_at=command.expires_at,
        )
        self._atomic_write(self.approval_path, asdict(approval))
        self._history("COMMAND_APPROVED", {"command_id": command.command_id})
        return approval

    def reject(self, command_id: str, reason: str = "USER_REJECTED") -> None:
        pending = self.pending()
        if pending is None or pending[0].command_id != command_id:
            raise ValueError("command_id nao corresponde ao comando pendente")
        self._history("COMMAND_REJECTED", {"command_id": command_id, "reason": reason})
        self.clear(command_id)

    def consume_approval(self, command_id: str) -> Optional[TradeApproval]:
        if not os.path.exists(self.approval_path):
            return None
        try:
            with open(self.approval_path, "r", encoding="utf-8") as fh:
                payload = json.load(fh)
            approval = TradeApproval(**payload)
        except Exception:
            return None
        if approval.command_id != command_id or not approval.is_valid():
            return None
        os.unlink(self.approval_path)
        self._history("APPROVAL_CONSUMED", {"command_id": command_id})
        return approval

    def clear(self, command_id: Optional[str] = None) -> None:
        pending_id = None
        if os.path.exists(self.pending_path):
            try:
                with open(self.pending_path, "r", encoding="utf-8") as fh:
                    pending_id = (json.load(fh).get("command") or {}).get("command_id")
            except Exception:
                pass
        if command_id is None or pending_id == command_id:
            for path in (self.pending_path, self.approval_path):
                if os.path.exists(path):
                    os.unlink(path)
            if pending_id:
                self._history("COMMAND_CLEARED", {"command_id": pending_id})
