# -*- coding: utf-8 -*-
"""Binance USD-M Futures public market stream.

Coleta apenas dados publicos. Nao envia ordens e nao usa credenciais.
O objetivo e manter um estado compacto e recente para a camada de decisao.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections import defaultdict, deque
from copy import deepcopy
from typing import Deque, Dict, Iterable, List, Tuple
from uuid import uuid4

import websockets


class FuturesMarketStream:
    """Coletor resiliente de aggTrade, bookTicker e markPrice da Binance USD-M."""

    BASE_URL = "wss://fstream.binance.com/public/stream"
    STREAM_SUFFIXES = ("@aggTrade", "@bookTicker", "@markPrice@1s")

    def __init__(self, symbols: Iterable[str]):
        self.symbols = self._normalize_symbols(symbols)
        self.state: Dict[str, Dict] = {s: self._empty_symbol_state(s) for s in self.symbols}
        self.trade_flow: Dict[str, Deque[Tuple[int, float, float]]] = defaultdict(deque)
        self.connected = False
        self.last_error = None
        self.reconnect_count = 0
        self.last_message_ms = 0
        self._stop = asyncio.Event()
        self._lock = asyncio.Lock()

    @staticmethod
    def _normalize_symbols(symbols: Iterable[str]) -> List[str]:
        normalized = sorted({str(s).upper().strip() for s in symbols if str(s).strip()})
        if not normalized:
            raise ValueError("Informe ao menos um simbolo")
        return normalized

    @staticmethod
    def _empty_symbol_state(symbol: str) -> Dict:
        return {
            "symbol": symbol,
            "event_time_ms": 0,
            "transaction_time_ms": 0,
            "last_trade_price": None,
            "last_trade_qty": None,
            "best_bid": None,
            "best_bid_qty": None,
            "best_ask": None,
            "best_ask_qty": None,
            "mark_price": None,
            "index_price": None,
            "funding_rate": None,
            "next_funding_time_ms": None,
        }

    def _subscription_params(self) -> List[str]:
        return [f"{symbol.lower()}{suffix}" for symbol in self.symbols for suffix in self.STREAM_SUFFIXES]

    async def replace_symbols(self, symbols: Iterable[str]) -> None:
        """Atualiza universo. A nova lista entra na proxima reconexao."""
        normalized = self._normalize_symbols(symbols)
        async with self._lock:
            self.symbols = normalized
            for symbol in normalized:
                self.state.setdefault(symbol, self._empty_symbol_state(symbol))
            self.state = {s: self.state[s] for s in normalized}
            self.trade_flow = defaultdict(deque, {s: self.trade_flow[s] for s in normalized})

    async def stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        """Mantem conexao viva e reconecta com backoff quando necessario."""
        backoff = 1
        while not self._stop.is_set():
            try:
                async with websockets.connect(
                    self.BASE_URL,
                    ping_interval=20,
                    ping_timeout=20,
                    close_timeout=5,
                    max_queue=4096,
                ) as ws:
                    params = self._subscription_params()
                    await ws.send(json.dumps({"method": "SUBSCRIBE", "params": params, "id": uuid4().hex}))
                    self.connected = True
                    self.last_error = None
                    backoff = 1

                    while not self._stop.is_set():
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=30)
                        except asyncio.TimeoutError:
                            continue
                        self.last_message_ms = int(time.time() * 1000)
                        await self._handle_message(raw)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.connected = False
                self.last_error = str(exc)
                self.reconnect_count += 1
                if self._stop.is_set():
                    break
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30)
            finally:
                self.connected = False

    async def _handle_message(self, raw: str) -> None:
        payload = json.loads(raw)
        data = payload.get("data", payload)
        event = data.get("e")
        if not event:
            return

        # Depois da migracao UM/CM, st=1 identifica USD-M.
        symbol_type = data.get("st")
        if symbol_type is not None and int(symbol_type) != 1:
            return

        symbol = str(data.get("s", "")).upper()
        if symbol not in self.state:
            return

        async with self._lock:
            state = self.state[symbol]
            state["event_time_ms"] = int(data.get("E") or state["event_time_ms"] or 0)
            state["transaction_time_ms"] = int(data.get("T") or state["transaction_time_ms"] or 0)

            if event == "bookTicker":
                state["best_bid"] = self._float_or_none(data.get("b"))
                state["best_bid_qty"] = self._float_or_none(data.get("B"))
                state["best_ask"] = self._float_or_none(data.get("a"))
                state["best_ask_qty"] = self._float_or_none(data.get("A"))

            elif event == "aggTrade":
                price = self._float_or_none(data.get("p"))
                qty = self._float_or_none(data.get("q"))
                state["last_trade_price"] = price
                state["last_trade_qty"] = qty
                if price is not None and qty is not None:
                    quote = price * qty
                    buyer_is_maker = bool(data.get("m"))
                    buy_quote = 0.0 if buyer_is_maker else quote
                    sell_quote = quote if buyer_is_maker else 0.0
                    event_ms = int(data.get("T") or data.get("E") or time.time() * 1000)
                    flow = self.trade_flow[symbol]
                    flow.append((event_ms, buy_quote, sell_quote))
                    cutoff = event_ms - 300_000
                    while flow and flow[0][0] < cutoff:
                        flow.popleft()

            elif event == "markPriceUpdate":
                state["mark_price"] = self._float_or_none(data.get("p"))
                state["index_price"] = self._float_or_none(data.get("i"))
                state["funding_rate"] = self._float_or_none(data.get("r"))
                nft = data.get("T")
                state["next_funding_time_ms"] = int(nft) if nft is not None else None

    @staticmethod
    def _float_or_none(value):
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _flow_window(self, symbol: str, now_ms: int, window_ms: int) -> Dict[str, float]:
        buy = sell = 0.0
        cutoff = now_ms - window_ms
        for ts, buy_quote, sell_quote in self.trade_flow[symbol]:
            if ts >= cutoff:
                buy += buy_quote
                sell += sell_quote
        total = buy + sell
        delta = (buy - sell) / total if total > 0 else 0.0
        return {
            "taker_buy_quote": buy,
            "taker_sell_quote": sell,
            "taker_total_quote": total,
            "taker_delta_ratio": delta,
        }

    async def snapshot(self) -> Dict:
        """Retorna copia consistente do estado e fluxo de 60s/300s."""
        now_ms = int(time.time() * 1000)
        async with self._lock:
            result = {}
            for symbol, state in self.state.items():
                item = deepcopy(state)
                item["flow_60s"] = self._flow_window(symbol, now_ms, 60_000)
                item["flow_300s"] = self._flow_window(symbol, now_ms, 300_000)
                last_event = int(item.get("event_time_ms") or 0)
                item["stale_ms"] = max(0, now_ms - last_event) if last_event else None
                result[symbol] = item

        return {
            "captured_at_ms": now_ms,
            "connected": self.connected,
            "last_message_ms": self.last_message_ms,
            "reconnect_count": self.reconnect_count,
            "last_error": self.last_error,
            "symbols": result,
        }
