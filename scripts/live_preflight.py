# -*- coding: utf-8 -*-
"""Live infrastructure preflight. NEVER sends a Binance order.

Validates local configuration, Binance USD-M connectivity, executable universe,
public market data and the configured brain transport:
- external_chatgpt -> private Neon bridge
- openai_api       -> optional OpenAI Responses API
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config.settings import BINANCE_API_KEY, BINANCE_API_SECRET, BOT_MODE
from core.binance_connection import BinanceConnection
from market.context_builder import MarketContextBuilder
from market.derivatives_sampler import DerivativesSampler
from market.live_stream import FuturesMarketStream
from market.structure_sampler import StructureSampler
from market.symbol_scanner import ExecutableSymbolScanner


def _result(check: str, ok: bool, **details: Any) -> Dict[str, Any]:
    return {"check": check, "ok": bool(ok), **details}


async def _market_checks(symbols: List[str]) -> List[Dict[str, Any]]:
    checks: List[Dict[str, Any]] = []
    stream = FuturesMarketStream(symbols)
    task = asyncio.create_task(stream.run())
    try:
        deadline = time.monotonic() + 12
        snapshot = None
        while time.monotonic() < deadline:
            await asyncio.sleep(0.5)
            snapshot = await stream.snapshot()
            rows = snapshot.get("symbols") or {}
            if snapshot.get("connected") and rows and all(
                row.get("best_bid") is not None
                and row.get("best_ask") is not None
                and row.get("mark_price") is not None
                for row in rows.values()
            ):
                break
        snapshot = snapshot or await stream.snapshot()
        market_ok = bool(snapshot.get("connected")) and any(
            row.get("best_bid") is not None and row.get("mark_price") is not None
            for row in (snapshot.get("symbols") or {}).values()
        )
        checks.append(
            _result(
                "binance_public_websocket",
                market_ok,
                connected=snapshot.get("connected"),
                last_error=snapshot.get("last_error"),
                reconnect_count=snapshot.get("reconnect_count"),
            )
        )

        derivatives = DerivativesSampler(symbols, interval_seconds=60)
        structure = StructureSampler(symbols, interval_seconds=30)
        d_result, s_result = await asyncio.gather(
            derivatives.sample_once(),
            structure.sample_once(),
            return_exceptions=True,
        )
        derivatives_ok = not isinstance(d_result, Exception) and bool(d_result)
        structure_ok = not isinstance(s_result, Exception) and bool(s_result)
        checks.append(
            _result(
                "binance_derivatives_sampler",
                derivatives_ok,
                error=str(d_result) if isinstance(d_result, Exception) else derivatives.last_error,
            )
        )
        checks.append(
            _result(
                "binance_structure_sampler",
                structure_ok,
                error=str(s_result) if isinstance(s_result, Exception) else structure.last_error,
            )
        )

        if market_ok and derivatives_ok and structure_ok:
            d_snapshot = await derivatives.snapshot()
            s_snapshot = await structure.snapshot()
            context = MarketContextBuilder.build(
                snapshot,
                symbols=symbols,
                max_symbols=len(symbols),
                derivatives_snapshot=d_snapshot,
                structure_snapshot=s_snapshot,
            )
            context_ok = bool(context.get("symbols")) and all(
                row.get("data_quality") == "OK" for row in context.get("symbols") or []
            )
            checks.append(
                _result(
                    "market_context_builder",
                    context_ok,
                    symbols=[
                        {
                            "symbol": row.get("symbol"),
                            "quality": row.get("data_quality"),
                            "flags": row.get("quality_flags"),
                        }
                        for row in context.get("symbols") or []
                    ],
                )
            )
    finally:
        await stream.stop()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    return checks


def _check_openai() -> Dict[str, Any]:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return _result("openai_responses_api", False, error="OPENAI_API_KEY missing")
    try:
        from openai import OpenAI

        model = os.getenv("OPENAI_MODEL", "gpt-5.6-sol")
        client = OpenAI(api_key=api_key)
        response = client.responses.create(
            model=model,
            input="Reply with exactly OK.",
            max_output_tokens=16,
            store=False,
        )
        text = (getattr(response, "output_text", "") or "").strip()
        return _result("openai_responses_api", text.upper().startswith("OK"), model=model, output=text[:80])
    except Exception as exc:
        return _result("openai_responses_api", False, error=str(exc))


def _check_neon() -> Dict[str, Any]:
    database_url = (os.getenv("NEON_DATABASE_URL") or "").strip()
    if not database_url:
        return _result("neon_private_bridge", False, error="NEON_DATABASE_URL missing")
    try:
        import psycopg
        from psycopg.rows import dict_row

        with psycopg.connect(
            database_url,
            autocommit=True,
            connect_timeout=10,
            row_factory=dict_row,
            application_name="adaptive-binance-preflight",
        ) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT current_database() AS db, now() AS server_time")
                row = cur.fetchone() or {}
                cur.execute("SELECT to_regclass('public.bot_runtime_state') AS runtime_table, to_regclass('public.brain_proposals') AS proposals_table")
                tables = cur.fetchone() or {}
        ok = bool(tables.get("runtime_table") and tables.get("proposals_table"))
        return _result(
            "neon_private_bridge",
            ok,
            database=row.get("db"),
            runtime_table=bool(tables.get("runtime_table")),
            proposals_table=bool(tables.get("proposals_table")),
        )
    except Exception as exc:
        return _result("neon_private_bridge", False, error=str(exc))


async def main_async(test_brain: bool = False) -> int:
    checks: List[Dict[str, Any]] = []
    brain_mode = (os.getenv("BRAIN_MODE") or "external_chatgpt").strip().lower()
    checks.append(_result("bot_mode_live", BOT_MODE == "live", bot_mode=BOT_MODE))
    checks.append(
        _result(
            "brain_mode",
            brain_mode in {"external_chatgpt", "openai_api"},
            brain_mode=brain_mode,
        )
    )

    base_secrets_ok = bool(BINANCE_API_KEY and BINANCE_API_SECRET)
    mode_secret_ok = bool(os.getenv("NEON_DATABASE_URL")) if brain_mode == "external_chatgpt" else bool(os.getenv("OPENAI_API_KEY"))
    checks.append(
        _result(
            "local_secrets_present",
            base_secrets_ok and mode_secret_ok,
            binance_key=bool(BINANCE_API_KEY),
            binance_secret=bool(BINANCE_API_SECRET),
            neon_database_url=bool(os.getenv("NEON_DATABASE_URL")),
            openai_key_required=(brain_mode == "openai_api"),
            openai_key=bool(os.getenv("OPENAI_API_KEY")) if brain_mode == "openai_api" else None,
        )
    )

    if not BINANCE_API_KEY or not BINANCE_API_SECRET:
        print(json.dumps({"ok": False, "orders_sent": 0, "checks": checks}, ensure_ascii=False, indent=2))
        return 2

    conn = BinanceConnection(BINANCE_API_KEY, BINANCE_API_SECRET)
    connected = await asyncio.to_thread(conn.connect)
    checks.append(_result("binance_futures_connection", connected))
    if not connected:
        print(json.dumps({"ok": False, "orders_sent": 0, "checks": checks}, ensure_ascii=False, indent=2))
        return 3

    balance = await asyncio.to_thread(conn.get_usdt_balance)
    checks.append(_result("futures_usdt_balance", balance > 0, available_usdt=balance))

    try:
        account = await asyncio.to_thread(conn.client.futures_account)
        checks.append(
            _result(
                "binance_account_read",
                isinstance(account, dict),
                can_trade=account.get("canTrade") if isinstance(account, dict) else None,
            )
        )
    except Exception as exc:
        checks.append(_result("binance_account_read", False, error=str(exc)))

    scanner = ExecutableSymbolScanner(
        conn,
        max_leverage=int(os.getenv("MAX_LEVERAGE_HARD", "20")),
        max_margin_usage_pct=float(os.getenv("MAX_MARGIN_USAGE_PCT", "0.95")),
    )
    universe = await asyncio.to_thread(scanner.scan, balance, 10)
    checks.append(
        _result(
            "executable_symbol_universe",
            bool(universe),
            count=len(universe),
            candidates=[
                {
                    "symbol": row["symbol"],
                    "price": row["price"],
                    "min_notional": row["min_notional"],
                    "min_leverage_for_balance": row["min_leverage_for_balance"],
                    "quote_volume_24h": row["quote_volume_24h"],
                }
                for row in universe
            ],
        )
    )

    if universe:
        sample_symbols = [row["symbol"] for row in universe[: min(3, len(universe))]]
        checks.extend(await _market_checks(sample_symbols))

    if brain_mode == "external_chatgpt":
        checks.append(await asyncio.to_thread(_check_neon))
    elif test_brain:
        checks.append(await asyncio.to_thread(_check_openai))
    else:
        checks.append(_result("openai_api_configuration", bool(os.getenv("OPENAI_API_KEY"))))

    overall = all(item.get("ok") for item in checks)
    payload = {
        "ok": overall,
        "orders_sent": 0,
        "note": "This diagnostic never calls a Binance order endpoint.",
        "checks": checks,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    return 0 if overall else 1


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--brain", action="store_true", help="for openai_api mode, also make one harmless Responses API call")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main_async(test_brain=args.brain)))


if __name__ == "__main__":
    main()
