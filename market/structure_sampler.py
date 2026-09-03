# -*- coding: utf-8 -*-
"""Public multi-timeframe USD-M structure sampler.

Produces descriptive features (returns, EMA alignment, ATR, volume and range
location) without converting them into LONG/SHORT signals.
"""

from __future__ import annotations

import asyncio
import json
import math
import time
import urllib.parse
import urllib.request
from copy import deepcopy
from typing import Any, Dict, Iterable, List, Optional, Sequence


class StructureSampler:
    BASE = "https://fapi.binance.com/fapi/v1/klines"

    def __init__(
        self,
        symbols: Iterable[str],
        intervals: Sequence[str] = ("5m", "15m", "1h", "4h"),
        kline_limit: int = 60,
        interval_seconds: int = 30,
        concurrency: int = 8,
    ):
        self.symbols = self._normalize_symbols(symbols)
        self.intervals = tuple(intervals)
        self.kline_limit = max(50, int(kline_limit))
        self.interval_seconds = max(10, int(interval_seconds))
        self._sem = asyncio.Semaphore(max(1, int(concurrency)))
        self._stop = asyncio.Event()
        self._lock = asyncio.Lock()
        self.state: Dict[str, Dict[str, Any]] = {}
        self.last_update_ms = 0
        self.last_error: Optional[str] = None

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

    async def _fetch_klines(self, symbol: str, interval: str) -> List[list]:
        query = urllib.parse.urlencode({"symbol": symbol, "interval": interval, "limit": self.kline_limit})
        async with self._sem:
            payload = await asyncio.to_thread(self._get_json_sync, f"{self.BASE}?{query}")
        return payload if isinstance(payload, list) else []

    @staticmethod
    def _ema(values: Sequence[float], period: int) -> Optional[float]:
        if len(values) < period:
            return None
        alpha = 2.0 / (period + 1.0)
        ema = sum(values[:period]) / period
        for value in values[period:]:
            ema = alpha * value + (1 - alpha) * ema
        return ema

    @staticmethod
    def _avg(values: Sequence[float]) -> Optional[float]:
        return sum(values) / len(values) if values else None

    @classmethod
    def compute_features(cls, klines: Sequence[Sequence[Any]]) -> Dict[str, Any]:
        if len(klines) < 20:
            raise ValueError("klines insuficientes")
        opens = [float(k[1]) for k in klines]
        highs = [float(k[2]) for k in klines]
        lows = [float(k[3]) for k in klines]
        closes = [float(k[4]) for k in klines]
        volumes = [float(k[5]) for k in klines]

        last = closes[-1]
        ema9 = cls._ema(closes, 9)
        ema21 = cls._ema(closes, 21)
        ema50 = cls._ema(closes, 50)

        true_ranges = []
        for i in range(1, len(klines)):
            true_ranges.append(max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1])))
        atr14 = cls._avg(true_ranges[-14:]) or 0.0

        high20 = max(highs[-20:])
        low20 = min(lows[-20:])
        range20 = high20 - low20
        range_position = (last - low20) / range20 if range20 > 0 else 0.5
        avg_volume20 = cls._avg(volumes[-20:]) or 0.0

        def ret(bars: int) -> Optional[float]:
            if len(closes) <= bars or closes[-1 - bars] == 0:
                return None
            return (last / closes[-1 - bars] - 1.0) * 100.0

        alignment = "MIXED"
        if None not in (ema9, ema21, ema50):
            if ema9 > ema21 > ema50:
                alignment = "BULLISH"
            elif ema9 < ema21 < ema50:
                alignment = "BEARISH"

        last_open, last_high, last_low = opens[-1], highs[-1], lows[-1]
        candle_range = max(last_high - last_low, 1e-18)
        body = abs(last - last_open)
        upper_wick = last_high - max(last_open, last)
        lower_wick = min(last_open, last) - last_low

        log_returns = []
        for prev, cur in zip(closes[-21:-1], closes[-20:]):
            if prev > 0 and cur > 0:
                log_returns.append(math.log(cur / prev))
        rv = None
        if len(log_returns) >= 2:
            mean = sum(log_returns) / len(log_returns)
            variance = sum((x - mean) ** 2 for x in log_returns) / (len(log_returns) - 1)
            rv = math.sqrt(variance) * 100.0

        return {
            "close": last,
            "ema9": ema9,
            "ema21": ema21,
            "ema50": ema50,
            "ema_alignment": alignment,
            "atr14": atr14,
            "atr14_pct": (atr14 / last * 100.0) if last else None,
            "return_1bar_pct": ret(1),
            "return_3bar_pct": ret(3),
            "return_12bar_pct": ret(12),
            "high_20": high20,
            "low_20": low20,
            "range_position_20": range_position,
            "volume_ratio_20": (volumes[-1] / avg_volume20) if avg_volume20 > 0 else None,
            "body_fraction": body / candle_range,
            "upper_wick_fraction": upper_wick / candle_range,
            "lower_wick_fraction": lower_wick / candle_range,
            "realized_vol_20": rv,
            "close_time_ms": int(klines[-1][6]) if len(klines[-1]) > 6 else None,
        }

    async def sample_symbol(self, symbol: str) -> Dict[str, Any]:
        payloads = await asyncio.gather(*(self._fetch_klines(symbol, interval) for interval in self.intervals))
        return {
            "symbol": symbol,
            "captured_at_ms": int(time.time() * 1000),
            "timeframes": {
                interval: self.compute_features(klines)
                for interval, klines in zip(self.intervals, payloads)
            },
        }

    async def sample_once(self) -> Dict[str, Any]:
        async with self._lock:
            symbols = list(self.symbols)
        results = await asyncio.gather(*(self.sample_symbol(s) for s in symbols), return_exceptions=True)
        update: Dict[str, Any] = {}
        errors = []
        for symbol, result in zip(symbols, results):
            if isinstance(result, Exception):
                errors.append(f"{symbol}: {result}")
            else:
                update[symbol] = result
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
