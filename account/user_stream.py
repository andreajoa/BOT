# -*- coding: utf-8 -*-
"""Private Binance USD-M Futures user-data stream.

Observa ordens, fills, saldo, posicoes e margin calls em tempo real.
Nao cria ordens. A responsabilidade deste modulo e telemetria/estado.
"""

from __future__ import annotations

import asyncio
import json
import time
import urllib.request
from copy import deepcopy
from typing import Any, Dict, Optional

import websockets


class FuturesUserDataStream:
    REST_LISTEN_KEY_URL = "https://fapi.binance.com/fapi/v1/listenKey"
    WS_BASE_URL = "wss://fstream.binance.com/ws"

    def __init__(self, api_key: str, keepalive_seconds: int = 45 * 60):
        if not api_key:
            raise ValueError("api_key obrigatoria para User Data Stream")
        self.api_key = api_key
        self.keepalive_seconds = max(60, int(keepalive_seconds))
        self.listen_key: Optional[str] = None
        self.connected = False
        self.last_error: Optional[str] = None
        self.last_message_ms = 0
        self.reconnect_count = 0
        self._stop = asyncio.Event()
        self._lock = asyncio.Lock()
        self._keepalive_task: Optional[asyncio.Task] = None

        self.state: Dict[str, Any] = {
            "orders": {},
            "positions": {},
            "balances": {},
            "margin_call": None,
            "last_event_type": None,
            "last_event_ms": 0,
        }

    def _request_listen_key_sync(self, method: str) -> Dict[str, Any]:
        req = urllib.request.Request(
            self.REST_LISTEN_KEY_URL,
            method=method,
            headers={"X-MBX-APIKEY": self.api_key},
        )
        with urllib.request.urlopen(req, timeout=15) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else {}

    async def _request_listen_key(self, method: str) -> Dict[str, Any]:
        return await asyncio.to_thread(self._request_listen_key_sync, method)

    async def _start_listen_key(self) -> str:
        payload = await self._request_listen_key("POST")
        listen_key = payload.get("listenKey")
        if not listen_key:
            raise RuntimeError("Binance nao retornou listenKey")
        self.listen_key = str(listen_key)
        return self.listen_key

    async def _keepalive(self) -> None:
        while not self._stop.is_set() and self.listen_key:
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.keepalive_seconds)
                break
            except asyncio.TimeoutError:
                try:
                    payload = await self._request_listen_key("PUT")
                    refreshed = payload.get("listenKey")
                    if refreshed:
                        self.listen_key = str(refreshed)
                except Exception as exc:  # reconnect loop will recreate when needed
                    self.last_error = f"listenKey keepalive: {exc}"
                    return

    async def _close_listen_key(self) -> None:
        if not self.listen_key:
            return
        try:
            await self._request_listen_key("DELETE")
        except Exception:
            pass
        finally:
            self.listen_key = None

    async def stop(self) -> None:
        self._stop.set()
        if self._keepalive_task:
            self._keepalive_task.cancel()
        await self._close_listen_key()

    async def run(self) -> None:
        backoff = 1
        while not self._stop.is_set():
            try:
                listen_key = await self._start_listen_key()
                self._keepalive_task = asyncio.create_task(self._keepalive())
                url = f"{self.WS_BASE_URL}/{listen_key}"
                async with websockets.connect(
                    url,
                    ping_interval=20,
                    ping_timeout=20,
                    close_timeout=5,
                    max_queue=4096,
                ) as ws:
                    self.connected = True
                    self.last_error = None
                    backoff = 1
                    while not self._stop.is_set():
                        raw = await ws.recv()
                        self.last_message_ms = int(time.time() * 1000)
                        should_reconnect = await self._handle_message(raw)
                        if should_reconnect:
                            break
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.last_error = str(exc)
                self.reconnect_count += 1
                if self._stop.is_set():
                    break
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30)
            finally:
                self.connected = False
                if self._keepalive_task:
                    self._keepalive_task.cancel()
                    self._keepalive_task = None
                await self._close_listen_key()

    async def _handle_message(self, raw: str) -> bool:
        payload = json.loads(raw)
        event_type = str(payload.get("e", ""))
        event_ms = int(payload.get("E") or payload.get("T") or time.time() * 1000)

        async with self._lock:
            self.state["last_event_type"] = event_type or None
            self.state["last_event_ms"] = event_ms

            if event_type == "ORDER_TRADE_UPDATE":
                self._apply_order_update(payload, event_ms)
            elif event_type == "ACCOUNT_UPDATE":
                self._apply_account_update(payload, event_ms)
            elif event_type == "MARGIN_CALL":
                self.state["margin_call"] = deepcopy(payload)
            elif event_type == "listenKeyExpired":
                return True
        return False

    def _apply_order_update(self, payload: Dict[str, Any], event_ms: int) -> None:
        o = payload.get("o") or {}
        symbol = str(o.get("s", "")).upper()
        order_id = o.get("i")
        key = f"{symbol}:{order_id}"
        self.state["orders"][key] = {
            "symbol": symbol,
            "order_id": order_id,
            "client_order_id": o.get("c"),
            "side": o.get("S"),
            "position_side": o.get("ps"),
            "order_type": o.get("o"),
            "execution_type": o.get("x"),
            "status": o.get("X"),
            "original_qty": self._float_or_none(o.get("q")),
            "last_fill_qty": self._float_or_none(o.get("l")),
            "filled_qty": self._float_or_none(o.get("z")),
            "last_fill_price": self._float_or_none(o.get("L")),
            "average_price": self._float_or_none(o.get("ap")),
            "stop_price": self._float_or_none(o.get("sp")),
            "realized_pnl": self._float_or_none(o.get("rp")),
            "commission": self._float_or_none(o.get("n")),
            "commission_asset": o.get("N"),
            "reduce_only": bool(o.get("R", False)),
            "close_position": bool(o.get("cp", False)),
            "event_ms": event_ms,
            "transaction_ms": int(payload.get("T") or event_ms),
        }

    def _apply_account_update(self, payload: Dict[str, Any], event_ms: int) -> None:
        account = payload.get("a") or {}
        reason = account.get("m")
        for b in account.get("B") or []:
            asset = str(b.get("a", "")).upper()
            if not asset:
                continue
            self.state["balances"][asset] = {
                "asset": asset,
                "wallet_balance": self._float_or_none(b.get("wb")),
                "cross_wallet_balance": self._float_or_none(b.get("cw")),
                "balance_change": self._float_or_none(b.get("bc")),
                "reason": reason,
                "event_ms": event_ms,
            }

        for p in account.get("P") or []:
            symbol = str(p.get("s", "")).upper()
            position_side = str(p.get("ps", "BOTH")).upper()
            if not symbol:
                continue
            key = f"{symbol}:{position_side}"
            self.state["positions"][key] = {
                "symbol": symbol,
                "position_side": position_side,
                "position_amount": self._float_or_none(p.get("pa")),
                "entry_price": self._float_or_none(p.get("ep")),
                "break_even_price": self._float_or_none(p.get("bep")),
                "unrealized_pnl": self._float_or_none(p.get("up")),
                "margin_type": p.get("mt"),
                "isolated_wallet": self._float_or_none(p.get("iw")),
                "reason": reason,
                "event_ms": event_ms,
            }

    @staticmethod
    def _float_or_none(value: Any) -> Optional[float]:
        if value in (None, ""):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    async def snapshot(self) -> Dict[str, Any]:
        async with self._lock:
            state = deepcopy(self.state)
        state.update(
            {
                "captured_at_ms": int(time.time() * 1000),
                "connected": self.connected,
                "last_message_ms": self.last_message_ms,
                "reconnect_count": self.reconnect_count,
                "last_error": self.last_error,
            }
        )
        return state
