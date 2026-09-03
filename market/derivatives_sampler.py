# -*- coding: utf-8 -*-
"""Periodic public USD-M Futures positioning/derivatives sampler.

Collects public context such as open interest and long/short ratios. It does not
turn those values into trading signals; consumers decide how to interpret them.
"""

from __future__ import annotations

import asyncio
import json
import time
import urllib.parse
import urllib.request
from copy import deepcopy
from typing import Any, Dict, Iterable, List, Optional


class DerivativesSampler:
    FAPI = "https://fapi.binance.com"
    DATA = "https://fapi.binance.com/futures/data"

    def __init__(self, symbols: Iterable[str], period: str = "5m", interval_seconds: int = 60):
        self.symbols = self._normalize_symbols(symbols)
        self.period = period
        self.interval_seconds = max(10, int(interval_seconds))
        self.state: Dict[str, Dict[str, Any]] = {}
        self.last_error: Optional[str] = None
        self.last_update_ms = 0
        self._stop = asyncio.Event()
        self._lock = asyncio.Lock()

    @staticmethod
    def _normalize_symbols(symbols: Iterable[str]) -> List[str]:
        result = sorted({str(s).upper().strip() for s in symbols if str(s).strip()})
        if not result:
            raise ValueError("Informe ao menos um simbolo")
        return result

    async def replace_symbols(self, symbols: Iterable[str]) -> None:
        normalized = self._normalize_symbols(symbols)
        async with self._lock:
            self.symbols = normalized
            self.state = {s: self.state[s] for s in normalized if s in self.state}

    @staticmethod
    def _get_json_sync(url: str) -> Any:
        req = urllib.request.Request(url, headers={"User-Agent": "adaptive-futures-executor/1.0"})
        with urllib.request.urlopen(req, timeout=15) as response:
            return json.loads(response.read().decode("utf-8"))

    async def _get_json(self, base: str, path: str, params: Dict[str, Any]) -> Any:
        query = urllib.parse.urlencode(params)
        return await asyncio.to_thread(self._get_json_sync, f"{base}{path}?{query}")

    @staticmethod
    def _float(value: Any) -> Optional[float]:
        if value in (None, ""):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _last_row(payload: Any) -> Dict[str, Any]:
        if isinstance(payload, list) and payload:
            item = payload[-1]
            return item if isinstance(item, dict) else {}
        return payload if isinstance(payload, dict) else {}

    async def sample_symbol(self, symbol: str) -> Dict[str, Any]:
        symbol = symbol.upper()
        open_interest, global_ratio, top_accounts, top_positions = await asyncio.gather(
            self._get_json(self.FAPI, "/fapi/v1/openInterest", {"symbol": symbol}),
            self._get_json(self.DATA, "/globalLongShortAccountRatio", {"symbol": symbol, "period": self.period, "limit": 2}),
            self._get_json(self.DATA, "/topLongShortAccountRatio", {"symbol": symbol, "period": self.period, "limit": 2}),
            self._get_json(self.DATA, "/topLongShortPositionRatio", {"symbol": symbol, "period": self.period, "limit": 2}),
        )
        g = self._last_row(global_ratio)
        ta = self._last_row(top_accounts)
        tp = self._last_row(top_positions)
        oi = self._last_row(open_interest)
        now_ms = int(time.time() * 1000)
        return {
            "symbol": symbol,
            "captured_at_ms": now_ms,
            "open_interest": self._float(oi.get("openInterest")),
            "open_interest_time_ms": oi.get("time"),
            "global_long_short_ratio": self._float(g.get("longShortRatio")),
            "global_long_account": self._float(g.get("longAccount")),
            "global_short_account": self._float(g.get("shortAccount")),
            "top_account_long_short_ratio": self._float(ta.get("longShortRatio")),
            "top_account_long": self._float(ta.get("longAccount")),
            "top_account_short": self._float(ta.get("shortAccount")),
            "top_position_long_short_ratio": self._float(tp.get("longShortRatio")),
            "top_position_long": self._float(tp.get("longPosition")),
            "top_position_short": self._float(tp.get("shortPosition")),
            "ratio_period": self.period,
        }

    @staticmethod
    def _add_oi_change(current: Dict[str, Any], previous: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        item = dict(current)
        current_oi = item.get("open_interest")
        previous_oi = (previous or {}).get("open_interest")
        change = None
        absolute_change = None
        if current_oi is not None and previous_oi is not None and float(previous_oi) != 0:
            current_value = float(current_oi)
            previous_value = float(previous_oi)
            absolute_change = current_value - previous_value
            change = (absolute_change / previous_value) * 100.0
        item["previous_open_interest"] = previous_oi
        item["open_interest_change"] = absolute_change
        item["open_interest_change_pct"] = change
        return item

    async def sample_once(self) -> Dict[str, Dict[str, Any]]:
        async with self._lock:
            symbols = list(self.symbols)
            previous_state = deepcopy(self.state)
        results = await asyncio.gather(*(self.sample_symbol(s) for s in symbols), return_exceptions=True)
        update: Dict[str, Dict[str, Any]] = {}
        errors: List[str] = []

        for symbol, result in zip(symbols, results):
            if isinstance(result, Exception):
                errors.append(f"{symbol}: {result}")
            else:
                update[symbol] = self._add_oi_change(result, previous_state.get(symbol))

        async with self._lock:
            active = set(self.symbols)
            self.state.update({k: v for k, v in update.items() if k in active})
            self.state = {s: self.state[s] for s in self.symbols if s in self.state}
            self.last_update_ms = int(time.time() * 1000)
            self.last_error = " | ".join(errors) if errors else None
            return deepcopy(self.state)

    async def run(self) -> None:
        while not self._stop.is_set():
            try:
                await self.sample_once()
            except Exception as exc:
                self.last_error = str(exc)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.interval_seconds)
            except asyncio.TimeoutError:
                continue

    async def stop(self) -> None:
        self._stop.set()

    async def snapshot(self) -> Dict[str, Any]:
        async with self._lock:
            return {
                "captured_at_ms": int(time.time() * 1000),
                "last_update_ms": self.last_update_ms,
                "last_error": self.last_error,
                "symbols": deepcopy(self.state),
            }
