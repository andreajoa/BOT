# -*- coding: utf-8 -*-
"""Build an executable USD-M Futures universe for the current balance."""

from __future__ import annotations

import math
from typing import Any, Dict, Iterable, List, Optional, Set


class ExecutableSymbolScanner:
    DEFAULT_BLACKLIST = {"USDCUSDT", "FDUSDUSDT", "TUSDUSDT", "BUSDUSDT"}

    def __init__(
        self,
        connection: Any,
        max_leverage: int = 20,
        max_margin_usage_pct: float = 0.95,
        blacklist: Optional[Iterable[str]] = None,
    ):
        self.connection = connection
        self.max_leverage = max(1, int(max_leverage))
        self.max_margin_usage_pct = min(max(float(max_margin_usage_pct), 0.01), 1.0)
        self.blacklist: Set[str] = self.DEFAULT_BLACKLIST | {
            str(s).upper() for s in (blacklist or [])
        }

    def scan(self, available_usdt: Optional[float] = None, limit: int = 30) -> List[Dict[str, Any]]:
        balance = float(available_usdt if available_usdt is not None else self.connection.get_usdt_balance())
        usable_margin = balance * self.max_margin_usage_pct
        if usable_margin <= 0:
            return []

        info = self.connection.client.futures_exchange_info()
        tickers = self.connection.client.futures_ticker()
        ticker_map = {str(t.get("symbol", "")).upper(): t for t in tickers}
        prices = self.connection.client.futures_symbol_ticker()
        price_map = {str(p.get("symbol", "")).upper(): float(p.get("price") or 0) for p in prices}

        eligible: List[Dict[str, Any]] = []
        for raw in info.get("symbols", []):
            symbol = str(raw.get("symbol", "")).upper()
            if (
                not symbol
                or symbol in self.blacklist
                or raw.get("status") != "TRADING"
                or raw.get("contractType") != "PERPETUAL"
                or raw.get("quoteAsset") != "USDT"
            ):
                continue
            price = price_map.get(symbol, 0.0)
            if price <= 0:
                continue

            filters = {f.get("filterType"): f for f in raw.get("filters", [])}
            lot = filters.get("MARKET_LOT_SIZE") or filters.get("LOT_SIZE") or {}
            notional_filter = filters.get("MIN_NOTIONAL") or filters.get("NOTIONAL") or {}
            min_qty = float(lot.get("minQty") or 0)
            min_notional = float(
                notional_filter.get("notional")
                or notional_filter.get("minNotional")
                or 0
            )
            required_notional = max(min_notional, min_qty * price)
            if required_notional <= 0:
                continue

            min_leverage = max(1, int(math.ceil(required_notional / usable_margin)))
            if min_leverage > self.max_leverage:
                continue

            ticker = ticker_map.get(symbol, {})
            quote_volume = float(ticker.get("quoteVolume") or 0)
            eligible.append(
                {
                    "symbol": symbol,
                    "price": price,
                    "min_qty": min_qty,
                    "min_notional": min_notional,
                    "required_notional": required_notional,
                    "min_leverage_for_balance": min_leverage,
                    "usable_margin_usdt": usable_margin,
                    "balance_usdt": balance,
                    "quote_volume_24h": quote_volume,
                }
            )

        eligible.sort(key=lambda x: x["quote_volume_24h"], reverse=True)
        return eligible[: max(0, int(limit))]
