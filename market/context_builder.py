# -*- coding: utf-8 -*-
"""Transforma o fluxo bruto da Binance em contexto compacto para decisao."""

from __future__ import annotations

from typing import Dict, Iterable, List, Optional


class MarketContextBuilder:
    """Calcula microestrutura simples sem impor estrategia ou direcao."""

    @staticmethod
    def _safe_div(num: float, den: float, default: float = 0.0) -> float:
        return num / den if den else default

    @classmethod
    def enrich_symbol(cls, raw: Dict) -> Dict:
        bid = raw.get("best_bid")
        ask = raw.get("best_ask")
        bid_qty = raw.get("best_bid_qty") or 0.0
        ask_qty = raw.get("best_ask_qty") or 0.0
        mark = raw.get("mark_price")
        index = raw.get("index_price")

        mid = None
        spread_bps = None
        if bid is not None and ask is not None and bid > 0 and ask >= bid:
            mid = (bid + ask) / 2.0
            spread_bps = cls._safe_div(ask - bid, mid) * 10_000 if mid else None

        top_total = bid_qty + ask_qty
        top_book_imbalance = cls._safe_div(bid_qty - ask_qty, top_total) if top_total else 0.0

        basis_bps = None
        if mark is not None and index is not None and index > 0:
            basis_bps = ((mark - index) / index) * 10_000

        flow_60 = raw.get("flow_60s") or {}
        flow_300 = raw.get("flow_300s") or {}
        stale_ms = raw.get("stale_ms")

        quality_flags: List[str] = []
        if bid is None or ask is None:
            quality_flags.append("NO_BOOK")
        if raw.get("last_trade_price") is None:
            quality_flags.append("NO_TRADES")
        if mark is None:
            quality_flags.append("NO_MARK")
        if stale_ms is None or stale_ms > 5_000:
            quality_flags.append("STALE")

        return {
            "symbol": raw.get("symbol"),
            "last_trade_price": raw.get("last_trade_price"),
            "mark_price": mark,
            "index_price": index,
            "best_bid": bid,
            "best_ask": ask,
            "mid_price": mid,
            "spread_bps": spread_bps,
            "top_book_imbalance": top_book_imbalance,
            "taker_delta_60s": float(flow_60.get("taker_delta_ratio") or 0.0),
            "taker_quote_60s": float(flow_60.get("taker_total_quote") or 0.0),
            "taker_delta_300s": float(flow_300.get("taker_delta_ratio") or 0.0),
            "taker_quote_300s": float(flow_300.get("taker_total_quote") or 0.0),
            "funding_rate": raw.get("funding_rate"),
            "basis_bps": basis_bps,
            "next_funding_time_ms": raw.get("next_funding_time_ms"),
            "stale_ms": stale_ms,
            "data_quality": "OK" if not quality_flags else "DEGRADED",
            "quality_flags": quality_flags,
        }

    @classmethod
    def build(
        cls,
        snapshot: Dict,
        symbols: Optional[Iterable[str]] = None,
        max_symbols: Optional[int] = None,
    ) -> Dict:
        source = snapshot.get("symbols") or {}
        allowed = {str(s).upper() for s in symbols} if symbols else None

        rows = []
        for symbol, raw in source.items():
            if allowed is not None and symbol.upper() not in allowed:
                continue
            rows.append(cls.enrich_symbol(raw))

        # Activity is only a sorting heuristic; it is not a trading signal.
        rows.sort(key=lambda x: x.get("taker_quote_60s") or 0.0, reverse=True)
        if max_symbols is not None:
            rows = rows[: max(0, int(max_symbols))]

        return {
            "captured_at_ms": snapshot.get("captured_at_ms"),
            "market_stream_connected": bool(snapshot.get("connected")),
            "market_stream_last_message_ms": snapshot.get("last_message_ms"),
            "market_stream_reconnect_count": int(snapshot.get("reconnect_count") or 0),
            "market_stream_last_error": snapshot.get("last_error"),
            "symbols": rows,
        }
