# -*- coding: utf-8 -*-
"""Read-only final acceptance audit for the first real adaptive trade.

This script NEVER creates, changes, cancels, or closes an order. It reads local
runtime state/journals and performs read-only Binance Futures queries to prove
that the end-to-end path really happened.

Usage:
    python3 scripts/final_acceptance.py
    python3 scripts/final_acceptance.py --command-id <COMMAND_ID>

It returns ``hundred_percent=true`` only when all required checks pass.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config.settings import BINANCE_API_KEY, BINANCE_API_SECRET, BOT_MODE
from core.binance_connection import BinanceConnection
from execution.exchange_adapter import ExchangeAdapter


LOGS = ROOT / "logs"


def _check(name: str, ok: bool, **details: Any) -> Dict[str, Any]:
    return {"check": name, "ok": bool(ok), **details}


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as fh:
            payload = json.load(fh)
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    rows: List[Dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(row, dict):
                    rows.append(row)
    except Exception:
        return []
    return rows


def _infer_command_id(records: Iterable[Dict[str, Any]]) -> Optional[str]:
    """Infer the latest command that reached a successful real entry result."""
    candidate: Optional[str] = None
    for row in records:
        command_id = row.get("command_id")
        if not command_id:
            continue
        if row.get("event") == "EXCHANGE_RESULT":
            payload = row.get("payload") or {}
            if payload.get("operation") == "OPEN_ENTRY" and payload.get("success") is True:
                candidate = str(command_id)
        if row.get("event") == "COMMAND_EXECUTION_FINISHED":
            result = (row.get("payload") or {}).get("result") or {}
            if result.get("status") == "EXECUTED":
                candidate = str(command_id)
    return candidate


def _command_payload(records: Iterable[Dict[str, Any]], command_id: str) -> Dict[str, Any]:
    payload: Dict[str, Any] = {}
    for row in records:
        if row.get("command_id") != command_id or row.get("event") != "COMMAND_RECEIVED":
            continue
        command = (row.get("payload") or {}).get("command")
        if isinstance(command, dict):
            payload = command
    return payload


def _event_ok(
    records: Iterable[Dict[str, Any]],
    command_id: str,
    event: str,
    operation: Optional[str] = None,
) -> bool:
    for row in records:
        if row.get("command_id") != command_id or row.get("event") != event:
            continue
        payload = row.get("payload") or {}
        if operation is not None and payload.get("operation") != operation:
            continue
        if event == "COMMAND_VALIDATION":
            if payload.get("accepted") is True:
                return True
        elif event == "EXCHANGE_RESULT":
            if payload.get("success") is True:
                return True
        else:
            return True
    return False


def _runtime_fresh(value: Any, max_age_seconds: int = 15) -> bool:
    if not value:
        return False
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds()
        return -2 <= age <= max_age_seconds
    except Exception:
        return False


def _git_value(*args: str) -> Optional[str]:
    try:
        return subprocess.check_output(
            ["git", *args],
            cwd=ROOT,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return None


def _position_side(raw: Dict[str, Any]) -> str:
    explicit = str(raw.get("positionSide") or "").upper()
    if explicit in {"LONG", "SHORT"}:
        return explicit
    return "LONG" if float(raw.get("positionAmt") or 0) > 0 else "SHORT"


def _query_order(client: Any, symbol: str, client_id: str) -> Dict[str, Any]:
    try:
        order = client.futures_get_order(symbol=symbol, origClientOrderId=client_id)
        return {"ok": True, "order": order}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def run(command_id: Optional[str] = None) -> Dict[str, Any]:
    checks: List[Dict[str, Any]] = []

    journal = _read_jsonl(LOGS / "execution_journal.jsonl")
    approvals = _read_jsonl(LOGS / "approval_history.jsonl")
    runtime = _read_json(LOGS / "runtime_status.json")
    managed = _read_json(LOGS / "managed_positions.json")

    branch = _git_value("rev-parse", "--abbrev-ref", "HEAD")
    head = _git_value("rev-parse", "HEAD")
    origin_main = _git_value("rev-parse", "origin/main")
    dirty = _git_value("status", "--porcelain")
    checks.append(_check("git_main", branch == "main", branch=branch))
    checks.append(_check("git_matches_origin_main", bool(head and origin_main and head == origin_main), head=head, origin_main=origin_main))
    checks.append(_check("git_tracked_tree_clean", dirty == "", dirty=bool(dirty)))

    checks.append(_check("bot_mode_live", BOT_MODE == "live", bot_mode=BOT_MODE))
    checks.append(
        _check(
            "local_secrets_present",
            bool(BINANCE_API_KEY and BINANCE_API_SECRET and os.getenv("OPENAI_API_KEY")),
            binance_key=bool(BINANCE_API_KEY),
            binance_secret=bool(BINANCE_API_SECRET),
            openai_key=bool(os.getenv("OPENAI_API_KEY")),
        )
    )

    checks.append(_check("runtime_status_present", bool(runtime)))
    checks.append(_check("runtime_status_fresh", _runtime_fresh(runtime.get("updated_at")), updated_at=runtime.get("updated_at")))
    checks.append(_check("public_market_stream_connected", runtime.get("market_stream_connected") is True))
    checks.append(_check("private_user_stream_connected", runtime.get("user_stream_connected") is True))
    checks.append(_check("decision_ready", runtime.get("decision_ready") is True, quality_flags=runtime.get("quality_flags") or []))

    selected = command_id or _infer_command_id(journal)
    checks.append(_check("live_command_identified", bool(selected), command_id=selected))
    if not selected:
        return _finalize(checks, None, None)

    command = _command_payload(journal, selected)
    symbol = str(command.get("symbol") or "").upper()
    side = str(command.get("side") or "").upper()
    action = str(command.get("action") or "")
    checks.append(_check("command_is_open_position", action == "OPEN_POSITION", action=action, symbol=symbol, side=side))
    checks.append(_check("command_validation_accepted", _event_ok(journal, selected, "COMMAND_VALIDATION")))
    checks.append(_check("entry_exchange_success", _event_ok(journal, selected, "EXCHANGE_RESULT", "OPEN_ENTRY")))
    checks.append(_check("stop_exchange_success", _event_ok(journal, selected, "EXCHANGE_RESULT", "SET_STOP_LOSS")))
    checks.append(_check("take_profit_exchange_success", _event_ok(journal, selected, "EXCHANGE_RESULT", "SET_TAKE_PROFIT")))

    approved = any(
        row.get("event") in {"COMMAND_APPROVED", "APPROVAL_CONSUMED"}
        and row.get("command_id") == selected
        for row in approvals
    )
    checks.append(_check("specific_command_approval_recorded", approved))

    managed_matches = [
        value
        for value in managed.values()
        if isinstance(value, dict) and value.get("command_id") == selected
    ]
    checks.append(_check("position_manager_recorded_command", bool(managed_matches), count=len(managed_matches)))

    if not BINANCE_API_KEY or not BINANCE_API_SECRET or not symbol:
        return _finalize(checks, selected, symbol)

    conn = BinanceConnection(BINANCE_API_KEY, BINANCE_API_SECRET)
    connected = conn.connect()
    checks.append(_check("binance_read_connection", connected))
    if not connected:
        return _finalize(checks, selected, symbol)

    entry_id = ExchangeAdapter.client_order_id(selected, "entry")
    entry_query = _query_order(conn.client, symbol, entry_id)
    entry_order = entry_query.get("order") or {}
    checks.append(
        _check(
            "binance_entry_order_confirmed",
            entry_query.get("ok") is True and str(entry_order.get("status") or "").upper() == "FILLED",
            client_order_id=entry_id,
            status=entry_order.get("status"),
            order_id=entry_order.get("orderId"),
            error=entry_query.get("error"),
        )
    )

    try:
        raw_positions = conn.client.futures_position_information(symbol=symbol)
    except Exception as exc:
        raw_positions = []
        checks.append(_check("binance_position_read", False, error=str(exc)))
    else:
        checks.append(_check("binance_position_read", isinstance(raw_positions, list)))

    matching_position_rows = [
        p for p in raw_positions
        if str(p.get("symbol") or "").upper() == symbol
        and (side not in {"LONG", "SHORT"} or _position_side(p) == side)
    ]
    isolated = any(str(p.get("marginType") or "").lower() == "isolated" for p in matching_position_rows)
    checks.append(_check("binance_margin_isolated", isolated, matching_rows=len(matching_position_rows)))

    stop_id = ExchangeAdapter.client_order_id(selected, "sl")
    stop_query = _query_order(conn.client, symbol, stop_id)
    stop_order = stop_query.get("order") or {}
    stop_status = str(stop_order.get("status") or "").upper()
    checks.append(
        _check(
            "binance_stop_order_confirmed",
            stop_query.get("ok") is True and stop_status in {"NEW", "FILLED", "CANCELED", "EXPIRED"},
            client_order_id=stop_id,
            status=stop_order.get("status"),
            order_id=stop_order.get("orderId"),
            error=stop_query.get("error"),
        )
    )

    tp_targets = list(command.get("take_profits") or [])
    tp_results = []
    for index, _target in enumerate(tp_targets, start=1):
        tp_id = ExchangeAdapter.client_order_id(selected, f"tp{index}")
        queried = _query_order(conn.client, symbol, tp_id)
        order = queried.get("order") or {}
        status = str(order.get("status") or "").upper()
        tp_results.append(
            {
                "client_order_id": tp_id,
                "ok": queried.get("ok") is True and status in {"NEW", "FILLED", "CANCELED", "EXPIRED"},
                "status": order.get("status"),
                "order_id": order.get("orderId"),
                "error": queried.get("error"),
            }
        )
    checks.append(_check("binance_take_profit_orders_confirmed", bool(tp_results) and all(x["ok"] for x in tp_results), targets=tp_results))

    fatal_for_command = [
        row for row in journal
        if row.get("command_id") == selected
        and str(row.get("level") or "").upper() == "ERROR"
    ]
    checks.append(_check("no_command_fatal_errors", not fatal_for_command, errors=fatal_for_command[-5:]))

    return _finalize(checks, selected, symbol)


def _finalize(checks: List[Dict[str, Any]], command_id: Optional[str], symbol: Optional[str]) -> Dict[str, Any]:
    passed = sum(1 for item in checks if item.get("ok"))
    total = len(checks)
    all_ok = total > 0 and passed == total
    return {
        "ok": all_ok,
        "hundred_percent": all_ok,
        "completion_percent": 100.0 if all_ok else round((passed / total) * 100.0, 2) if total else 0.0,
        "command_id": command_id,
        "symbol": symbol,
        "orders_sent": 0,
        "note": "Read-only audit: no create/change/cancel/close order endpoint is called.",
        "checks_passed": passed,
        "checks_total": total,
        "checks": checks,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--command-id", help="audit one exact approved command id; otherwise infer latest executed entry")
    args = parser.parse_args()
    payload = run(args.command_id)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    raise SystemExit(0 if payload.get("hundred_percent") else 1)


if __name__ == "__main__":
    main()
