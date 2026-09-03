"""Modelos de aprovacao explicita para comandos de operacao real.

Este modulo nao envia ordens e nao escolhe estrategia. Ele apenas representa
a autorizacao especifica dada pelo usuario para uma operacao concreta.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional


@dataclass(frozen=True)
class TradeApproval:
    command_id: str
    approved: bool
    approved_at: str
    expires_at: Optional[str] = None

    def is_valid(self, now: Optional[datetime] = None) -> bool:
        if not self.approved or not self.command_id:
            return False
        now = now or datetime.now(timezone.utc)
        try:
            approved_at = datetime.fromisoformat(self.approved_at.replace("Z", "+00:00"))
        except Exception:
            return False
        if approved_at > now:
            return False
        if self.expires_at:
            try:
                expires_at = datetime.fromisoformat(self.expires_at.replace("Z", "+00:00"))
            except Exception:
                return False
            if now >= expires_at:
                return False
        return True
