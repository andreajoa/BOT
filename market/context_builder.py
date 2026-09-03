# -*- coding: utf-8 -*-
"""Transforma dados publicos da Binance em contexto compacto para decisao."""

from __future__ import annotations

from typing import Dict, Iterable, List, Optional


class MarketContextBuilder:
    """Enriquece microestrutura/derivativos/estrutura sem impor estrategia ou direcao."""

    @staticmethod
    def _safe_div(num: float, den: float, default: float = 0.0) -> float:
        return num / den if den else default

    @classmethod
    def enrich_symbol(
        cls,
        raw: Dict,
        derivatives: Optional[Dict] = None,
        structure: Optional[Dict] = None,
    ) -> Dict:
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
        d = derivatives or {}
        s = structure or {}
        timeframes = dict(s.get("timeframes") or {})

        quality_flags: List[str] = []
        if bid is None or ask is None:
            quality_flags.append("NO_BOOK")
        if raw.get("last_trade_price") is None:
            quality_flags.append("NO_TRADES")
        if mark is None:
            quality_flags.append("NO_MARK")
        if stale_ms is None or stale_ms > 5_000:
            quality_flags.append("STALE")
        if not timeframes:
            quality_flags.append("NO_STRUCTURE")

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
            "depth_bid_quote_5": raw.get("depth_bid_quote_5"),
            "depth_ask_quote_5": raw.get("depth_ask_quote_5"),
            "depth_imbalance_5": raw.get("depth_imbalance_5"),
            "depth_bid_quote_20": raw.get("depth_bid_quote_20"),
            "depth_ask_quote_20": raw.get("depth_ask_quote_20"),
            "depth_imbalance_20": raw.get("depth_imbalance_20"),
            "taker_delta_60s": float(flow_60.get("taker_delta_ratio") or 0.0),
            "taker_quote_60s": float(flow_60.get("taker_total_quote") or 0.0),
            "taker_delta_300s": float(flow_300.get("taker_delta_ratio") or 0.0),
            "taker_quote_300s": float(flow_300.get("taker_total_quote") or 0.0),
            "funding_rate": raw.get("funding_rate"),
            "basis_bps": basis_bps,
            "next_funding_time_ms": raw.get("next_funding_time_ms"),
            "open_interest": d.get("open_interest"),
            "open_interest_change_pct": d.get("open_interest_change_pct"),
            "global_long_short_ratio": d.get("global_long_short_ratio"),
            "top_account_long_short_ratio": d.get("top_account_long_short_ratio"),
            "top_position_long_short_ratio": d.get("top_position_long_short_ratio"),
            "derivatives_captured_at_ms": d.get("captured_at_ms"),
            "structure_captured_at_ms": s.get("captured_at_ms"),
            "timeframes": timeframes,
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
        derivatives_snapshot: Optional[Dict] = None,
        structure_snapshot: Optional[Dict] = None,
    ) -> Dict:
        source = snapshot.get("symbols") or {}
        derivatives_source = (derivatives_snapshot or {}).get("symbols") or {}
        structure_source = (structure_snapshot or {}).get("symbols") or {}
        allowed = {str(s).upper() for s in symbols} if symbols else None

        rows = []
        for symbol, raw in source.items():
            symbol_key = symbol.upper()
            if allowed is not None and symbol_key not in allowed:
                continue
            rows.append(
                cls.enrich_symbol(
                    raw,
                    derivatives_source.get(symbol_key),
                    structure_source.get(symbol_key),
                )
            )

        rows.sort(key=lambda x: x.get("taker_quote_60s") or 0.0, reverse=True)
        if max_symbols is not None:
            rows = rows[: max(0, int(max_symbols))]

        return {
            "captured_at_ms": snapshot.get("captured_at_ms"),
            "market_stream_connected": bool(snapshot.get("connected")),
            "market_stream_last_message_ms": snapshot.get("last_message_ms"),
            "market_stream_reconnect_count": int(snapshot.get("reconnect_count") or 0),
            "market_stream_last_error": snapshot.get("last_error"),
            "derivatives_last_update_ms": (derivatives_snapshot or {}).get("last_update_ms"),
            "derivatives_last_error": (derivatives_snapshot or {}).get("last_error"),
            "structure_last_update_ms": (structure_snapshot or {}).get("last_update_ms"),
            "structure_last_error": (structure_snapshot or {}).get("last_error"),
            "symbols": rows,
        }
