# -*- coding: utf-8 -*-
"""Append-only execution journal keyed by command_id.

Every decision, validation result, exchange response and account-stream event can
be persisted as JSONL. This makes the path decision -> order -> fill auditable.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional


class ExecutionJournal:
    def __init__(self, path: str = "logs/execution_journal.jsonl"):
        self.path = path
        self._lock = threading.Lock()
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)

    @staticmethod
    def _serialize(value: Any) -> Any:
        if isinstance(value, Enum):
            return value.value
        if is_dataclass(value):
            return ExecutionJournal._serialize(asdict(value))
        if isinstance(value, dict):
            return {str(k): ExecutionJournal._serialize(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [ExecutionJournal._serialize(v) for v in value]
        return value

    def append(
        self,
        event: str,
        command_id: Optional[str] = None,
        payload: Optional[Dict[str, Any]] = None,
        level: str = "INFO",
    ) -> Dict[str, Any]:
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": str(event),
            "level": str(level).upper(),
            "command_id": command_id,
            "payload": self._serialize(payload or {}),
        }
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
        with self._lock:
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        return record

    def command_received(self, command: Any) -> Dict[str, Any]:
        command_id = getattr(command, "command_id", None)
        return self.append("COMMAND_RECEIVED", command_id, {"command": self._serialize(command)})

    def validation(self, command_id: str, accepted: bool, reason: str, **details: Any) -> Dict[str, Any]:
        return self.append(
            "COMMAND_VALIDATION",
            command_id,
            {"accepted": bool(accepted), "reason": reason, **details},
            "INFO" if accepted else "WARN",
        )

    def exchange_request(self, command_id: str, operation: str, **details: Any) -> Dict[str, Any]:
        return self.append("EXCHANGE_REQUEST", command_id, {"operation": operation, **details})

    def exchange_result(self, command_id: str, operation: str, success: bool, **details: Any) -> Dict[str, Any]:
        return self.append(
            "EXCHANGE_RESULT",
            command_id,
            {"operation": operation, "success": bool(success), **details},
            "INFO" if success else "ERROR",
        )

    def account_event(self, payload: Dict[str, Any], command_id: Optional[str] = None) -> Dict[str, Any]:
        return self.append("ACCOUNT_EVENT", command_id, payload)

    def read(self, command_id: Optional[str] = None, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        if not os.path.exists(self.path):
            return []
        records: List[Dict[str, Any]] = []
        with open(self.path, "r", encoding="utf-8") as fh:
            for line in fh:
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if command_id is None or record.get("command_id") == command_id:
                    records.append(record)
        if limit is not None and limit >= 0:
            return records[-limit:]
        return records
