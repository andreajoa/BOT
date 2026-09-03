# -*- coding: utf-8 -*-
"""Consistent decision snapshot composed from public market + private account state."""

from __future__ import annotations

import time
from typing import Any, Dict, Optional


class MarketStateAssembler:
    def __init__(self, max_market_stale_ms: int = 5_000, max_account_stale_ms: int = 30_000):
        self.max_market_stale_ms = int(max_market_stale_ms)
        self.max_account_stale_ms = int(max_account_stale_ms)

    def build(self, market_context: Dict[str, Any], account_snapshot: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        now_ms = int(time.time() * 1000)
        account = account_snapshot or {}
        quality_flags = []

        if not market_context.get("market_stream_connected"):
            quality_flags.append("MARKET_STREAM_DISCONNECTED")
        market_last = market_context.get("market_stream_last_message_ms")
        if not market_last or now_ms - int(market_last) > self.max_market_stale_ms:
            quality_flags.append("MARKET_STREAM_STALE")

        if account_snapshot is not None:
            if not account.get("connected"):
                quality_flags.append("ACCOUNT_STREAM_DISCONNECTED")
            account_last = account.get("last_message_ms") or account.get("last_event_ms")
            # A private stream can be quiet when nothing changes. Staleness is informational,
            # not equivalent to stale market prices.
            if account_last and now_ms - int(account_last) > self.max_account_stale_ms:
                quality_flags.append("ACCOUNT_STREAM_QUIET")

        symbol_rows = []
        positions = account.get("positions") or {}
        for row in market_context.get("symbols") or []:
            symbol = str(row.get("symbol", "")).upper()
            relevant_positions = [
                p for p in positions.values() if str(p.get("symbol", "")).upper() == symbol and abs(float(p.get("position_amount") or 0)) > 0
            ]
            enriched = dict(row)
            enriched["open_positions"] = relevant_positions
            symbol_rows.append(enriched)
            if row.get("data_quality") != "OK":
                quality_flags.append(f"{symbol}:DATA_DEGRADED")

        usdt = (account.get("balances") or {}).get("USDT") or {}
        return {
            "captured_at_ms": now_ms,
            "decision_ready": not any(
                flag in {"MARKET_STREAM_DISCONNECTED", "MARKET_STREAM_STALE"} or flag.endswith(":DATA_DEGRADED")
                for flag in quality_flags
            ),
            "quality_flags": sorted(set(quality_flags)),
            "account": {
                "stream_connected": bool(account.get("connected")) if account_snapshot is not None else None,
                "wallet_balance_usdt": usdt.get("wallet_balance"),
                "positions": list(positions.values()),
                "margin_call": account.get("margin_call"),
                "last_event_type": account.get("last_event_type"),
                "last_event_ms": account.get("last_event_ms"),
            },
            "market": {
                "stream_connected": bool(market_context.get("market_stream_connected")),
                "symbols": symbol_rows,
            },
        }
