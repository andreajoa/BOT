"""Protocolo estruturado entre a camada de decisao e o executor.

O protocolo nao contem estrategia fixa. Cada comando descreve uma decisao
pontual e expira rapidamente. A execucao real exige uma TradeApproval valida
para o mesmo command_id.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional


class Action(str, Enum):
    WAIT = "WAIT"
    OPEN_POSITION = "OPEN_POSITION"
    MODIFY_POSITION = "MODIFY_POSITION"
    CLOSE_POSITION = "CLOSE_POSITION"


class Side(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"


class EntryType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"


@dataclass(frozen=True)
class TakeProfitTarget:
    price: float
    close_pct: float = 100.0

    def validate(self) -> None:
        if self.price <= 0:
            raise ValueError("take profit price deve ser > 0")
        if not 0 < self.close_pct <= 100:
            raise ValueError("take profit close_pct deve estar entre 0 e 100")


@dataclass(frozen=True)
class TrailingSpec:
    enabled: bool = False
    activation_price: Optional[float] = None
    callback_rate: Optional[float] = None

    def validate(self) -> None:
        if not self.enabled:
            return
        if self.activation_price is not None and self.activation_price <= 0:
            raise ValueError("trailing activation_price deve ser > 0")
        if self.callback_rate is None or not 0.1 <= self.callback_rate <= 10:
            raise ValueError("trailing callback_rate deve estar entre 0.1 e 10")


@dataclass(frozen=True)
class TradeCommand:
    command_id: str
    action: Action
    issued_at: str
    expires_at: str
    symbol: Optional[str] = None
    side: Optional[Side] = None
    strategy: Optional[str] = None
    regime: Optional[str] = None
    confidence: Optional[float] = None
    entry_type: EntryType = EntryType.MARKET
    entry_price: Optional[float] = None
    margin_usdt: Optional[float] = None
    leverage: Optional[int] = None
    stop_loss: Optional[float] = None
    take_profits: List[TakeProfitTarget] = field(default_factory=list)
    trailing: TrailingSpec = field(default_factory=TrailingSpec)
    reason: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    @staticmethod
    def _parse_time(value: str) -> datetime:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))

    def validate(self, now: Optional[datetime] = None) -> None:
        if not self.command_id.strip():
            raise ValueError("command_id obrigatorio")

        now = now or datetime.now(timezone.utc)
        issued = self._parse_time(self.issued_at)
        expires = self._parse_time(self.expires_at)
        if issued > now:
            raise ValueError("issued_at esta no futuro")
        if expires <= now:
            raise ValueError("comando expirado")
        if expires <= issued:
            raise ValueError("expires_at deve ser posterior a issued_at")

        if self.confidence is not None and not 0 <= self.confidence <= 1:
            raise ValueError("confidence deve estar entre 0 e 1")

        self.trailing.validate()
        for target in self.take_profits:
            target.validate()

        if self.action == Action.WAIT:
            return

        if not self.symbol:
            raise ValueError("symbol obrigatorio para comando de posicao")

        if self.action == Action.OPEN_POSITION:
            if self.side is None:
                raise ValueError("side obrigatorio para OPEN_POSITION")
            if self.margin_usdt is None or self.margin_usdt <= 0:
                raise ValueError("margin_usdt deve ser > 0")
            if self.leverage is None or self.leverage < 1:
                raise ValueError("leverage deve ser >= 1")
            if self.stop_loss is None or self.stop_loss <= 0:
                raise ValueError("stop_loss obrigatorio e deve ser > 0")
            if not self.take_profits:
                raise ValueError("ao menos um take profit e obrigatorio")
            if self.entry_type == EntryType.LIMIT and (
                self.entry_price is None or self.entry_price <= 0
            ):
                raise ValueError("entry_price obrigatorio para entrada LIMIT")

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "TradeCommand":
        targets = [
            TakeProfitTarget(
                price=float(item["price"]),
                close_pct=float(item.get("close_pct", 100.0)),
            )
            for item in payload.get("take_profits", [])
        ]
        trailing_payload = payload.get("trailing") or {}
        trailing = TrailingSpec(
            enabled=bool(trailing_payload.get("enabled", False)),
            activation_price=(
                float(trailing_payload["activation_price"])
                if trailing_payload.get("activation_price") is not None
                else None
            ),
            callback_rate=(
                float(trailing_payload["callback_rate"])
                if trailing_payload.get("callback_rate") is not None
                else None
            ),
        )
        return cls(
            command_id=str(payload["command_id"]),
            action=Action(payload["action"]),
            issued_at=str(payload["issued_at"]),
            expires_at=str(payload["expires_at"]),
            symbol=(str(payload["symbol"]).upper() if payload.get("symbol") else None),
            side=(Side(payload["side"]) if payload.get("side") else None),
            strategy=payload.get("strategy"),
            regime=payload.get("regime"),
            confidence=(float(payload["confidence"]) if payload.get("confidence") is not None else None),
            entry_type=EntryType(payload.get("entry_type", "MARKET")),
            entry_price=(float(payload["entry_price"]) if payload.get("entry_price") is not None else None),
            margin_usdt=(float(payload["margin_usdt"]) if payload.get("margin_usdt") is not None else None),
            leverage=(int(payload["leverage"]) if payload.get("leverage") is not None else None),
            stop_loss=(float(payload["stop_loss"]) if payload.get("stop_loss") is not None else None),
            take_profits=targets,
            trailing=trailing,
            reason=payload.get("reason"),
            metadata=dict(payload.get("metadata") or {}),
        )
