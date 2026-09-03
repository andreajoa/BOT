# -*- coding: utf-8 -*-
"""Private Neon bridge between the local executor and external ChatGPT analysis.

This module never creates Binance orders. It only:
- publishes sanitized runtime/account/market state to private Neon Postgres;
- reads proposed TradeCommand payloads created externally;
- updates proposal lifecycle status.

The Neon connection string is a LOCAL secret (NEON_DATABASE_URL) and must never
be committed to GitHub or printed to logs.
"""

from __future__ import annotations

import json
import threading
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import psycopg
from psycopg.rows import dict_row


class NeonBridge:
    def __init__(self, database_url: str):
        if not str(database_url or "").strip():
            raise ValueError("NEON_DATABASE_URL obrigatoria para BRAIN_MODE=external_chatgpt")
        self.database_url = str(database_url).strip()
        self._lock = threading.RLock()
        self._conn: Optional[psycopg.Connection] = None

    def _connect(self) -> psycopg.Connection:
        with self._lock:
            if self._conn is not None and not self._conn.closed:
                return self._conn
            self._conn = psycopg.connect(
                self.database_url,
                autocommit=True,
                row_factory=dict_row,
                connect_timeout=10,
                application_name="adaptive-binance-executor",
            )
            return self._conn

    def _execute(self, sql: str, params: tuple = (), *, fetchone: bool = False):
        with self._lock:
            try:
                conn = self._connect()
                with conn.cursor() as cur:
                    cur.execute(sql, params)
                    return cur.fetchone() if fetchone else None
            except Exception:
                # Drop the connection so the next operation can reconnect.
                try:
                    if self._conn is not None:
                        self._conn.close()
                except Exception:
                    pass
                self._conn = None
                raise

    @staticmethod
    def _json(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)

    @staticmethod
    def _open_positions_from_state(state: Dict[str, Any]) -> list[Dict[str, Any]]:
        positions = []
        for item in ((state.get("account") or {}).get("positions") or []):
            try:
                amount = float(item.get("position_amount") or 0)
            except (TypeError, ValueError):
                amount = 0.0
            if abs(amount) > 0:
                positions.append(deepcopy(item))
        return positions

    def publish_state(
        self,
        *,
        runtime_instance_id: str,
        bot_mode: str,
        state: Dict[str, Any],
        status: Dict[str, Any],
        latest_execution: Optional[Dict[str, Any]],
    ) -> None:
        positions = self._open_positions_from_state(state)
        account = state.get("account") or {}
        available = account.get("available_balance_usdt")
        health = {
            "decision_ready": state.get("decision_ready"),
            "quality_flags": state.get("quality_flags") or [],
            "market_stream_connected": status.get("market_stream_connected"),
            "user_stream_connected": status.get("user_stream_connected"),
            "pending_command_id": status.get("pending_command_id"),
            "pending_limit_entries": status.get("pending_limit_entries") or [],
            "managed_positions": status.get("managed_positions") or [],
        }
        market_payload = {
            "candidate_symbols": state.get("candidate_symbols") or [],
            "candidate_execution_constraints": state.get("candidate_execution_constraints") or [],
            "monitor_only_symbols": state.get("monitor_only_symbols") or [],
            "market": state.get("market") or state.get("symbols") or [],
            "quality_flags": state.get("quality_flags") or [],
        }
        self._execute(
            """
            INSERT INTO bot_runtime_state (
                singleton_id, updated_at, runtime_instance_id, bot_mode,
                decision_ready, market_stream_connected, user_stream_connected,
                available_balance_usdt, position_open, open_positions,
                candidate_symbols, market_context, latest_execution, health
            ) VALUES (
                1, now(), %s, %s, %s, %s, %s, %s, %s,
                %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb
            )
            ON CONFLICT (singleton_id) DO UPDATE SET
                updated_at = excluded.updated_at,
                runtime_instance_id = excluded.runtime_instance_id,
                bot_mode = excluded.bot_mode,
                decision_ready = excluded.decision_ready,
                market_stream_connected = excluded.market_stream_connected,
                user_stream_connected = excluded.user_stream_connected,
                available_balance_usdt = excluded.available_balance_usdt,
                position_open = excluded.position_open,
                open_positions = excluded.open_positions,
                candidate_symbols = excluded.candidate_symbols,
                market_context = excluded.market_context,
                latest_execution = excluded.latest_execution,
                health = excluded.health
            """,
            (
                runtime_instance_id,
                bot_mode,
                bool(state.get("decision_ready")),
                bool(status.get("market_stream_connected")),
                bool(status.get("user_stream_connected")),
                available,
                bool(positions),
                self._json(positions),
                self._json(state.get("candidate_symbols") or []),
                self._json(market_payload),
                self._json(latest_execution) if latest_execution is not None else "null",
                self._json(health),
            ),
        )

    def append_event(self, event_type: str, payload: Dict[str, Any], command_id: Optional[str] = None) -> None:
        self._execute(
            "INSERT INTO bot_events (event_type, command_id, payload) VALUES (%s, %s, %s::jsonb)",
            (str(event_type), command_id, self._json(payload)),
        )

    def fetch_next_proposal(self) -> Optional[Dict[str, Any]]:
        row = self._execute(
            """
            SELECT proposal_id, created_at, expires_at, status, command, analysis, preflight
            FROM brain_proposals
            WHERE status = 'PROPOSED' AND expires_at > now()
            ORDER BY created_at ASC
            LIMIT 1
            """,
            fetchone=True,
        )
        return dict(row) if row else None

    def mark_proposal(
        self,
        proposal_id: str,
        status: str,
        *,
        preflight: Optional[Dict[str, Any]] = None,
    ) -> None:
        normalized = str(status).upper()
        allowed = {"PROPOSED", "PENDING_APPROVAL", "APPROVED", "REJECTED", "CONSUMED", "EXPIRED"}
        if normalized not in allowed:
            raise ValueError(f"status de proposta invalido: {status}")
        self._execute(
            """
            UPDATE brain_proposals
            SET status = %s,
                preflight = COALESCE(%s::jsonb, preflight),
                approved_at = CASE WHEN %s = 'APPROVED' THEN now() ELSE approved_at END,
                consumed_at = CASE WHEN %s = 'CONSUMED' THEN now() ELSE consumed_at END
            WHERE proposal_id = %s
            """,
            (
                normalized,
                self._json(preflight) if preflight is not None else None,
                normalized,
                normalized,
                proposal_id,
            ),
        )

    def expire_old_proposals(self) -> int:
        with self._lock:
            conn = self._connect()
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE brain_proposals
                    SET status = 'EXPIRED'
                    WHERE status IN ('PROPOSED','PENDING_APPROVAL') AND expires_at <= now()
                    """
                )
                return int(cur.rowcount or 0)

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                try:
                    self._conn.close()
                finally:
                    self._conn = None
