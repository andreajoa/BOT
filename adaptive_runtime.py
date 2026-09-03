# -*- coding: utf-8 -*-
"""Adaptive live Binance Futures runtime.

Pipeline:
  executable universe -> live market/account data -> decision state -> BrainClient
  -> preflight -> persistent proposal -> explicit per-command approval -> executor
  -> User Data Stream feedback -> position/trailing management.

No fixed trading strategy is embedded in this runtime.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import time
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

from account.user_stream import FuturesUserDataStream
from command_protocol import Action
from config.settings import BINANCE_API_KEY, BINANCE_API_SECRET, BOT_MODE
from control.approval_gateway import ApprovalGateway
from core.binance_connection import BinanceConnection
from execution.command_executor import CommandExecutor
from execution.exchange_adapter import ExchangeAdapter
from execution.journal import ExecutionJournal
from execution.position_manager import PositionManager
from intelligence.brain_client import BrainClient
from market.context_builder import MarketContextBuilder
from market.derivatives_sampler import DerivativesSampler
from market.live_stream import FuturesMarketStream
from market.market_state import MarketStateAssembler
from market.structure_sampler import StructureSampler
from market.symbol_scanner import ExecutableSymbolScanner
from risk.governor import RiskGovernor


class AdaptiveRuntime:
    def __init__(self):
        if BOT_MODE != "live":
            raise RuntimeError(
                "AdaptiveRuntime nao envia ordens a menos que BOT_MODE=live esteja explicitamente definido no .env"
            )
        if not BINANCE_API_KEY or not BINANCE_API_SECRET:
            raise RuntimeError("BINANCEAPIKEY/BINANCEAPISECRET ausentes no .env local")
        if not os.getenv("OPENAI_API_KEY"):
            raise RuntimeError("OPENAI_API_KEY ausente no .env local")

        self.stop_event = asyncio.Event()
        self.journal = ExecutionJournal()
        self.gateway = ApprovalGateway()
        self.connection = BinanceConnection(BINANCE_API_KEY, BINANCE_API_SECRET)

        self.max_leverage = int(os.getenv("MAX_LEVERAGE_HARD", "20"))
        self.max_margin_usage_pct = float(os.getenv("MAX_MARGIN_USAGE_PCT", "0.95"))
        self.max_symbols = max(1, int(os.getenv("SCANNER_MAX_SYMBOLS", "15")))
        self.decision_interval = max(5, int(os.getenv("BRAIN_DECISION_INTERVAL_SECONDS", "20")))
        self.command_ttl = max(15, int(os.getenv("BRAIN_COMMAND_TTL_SECONDS", "90")))
        self.status_interval = max(1, int(os.getenv("STATUS_INTERVAL_SECONDS", "2")))
        self.universe_refresh_interval = max(30, int(os.getenv("UNIVERSE_REFRESH_SECONDS", "300")))

        self.symbols: List[str] = []
        self.candidate_symbols: List[str] = []
        self.scanner: Optional[ExecutableSymbolScanner] = None
        self.market_stream: Optional[FuturesMarketStream] = None
        self.derivatives: Optional[DerivativesSampler] = None
        self.structure: Optional[StructureSampler] = None
        self.user_stream: Optional[FuturesUserDataStream] = None
        self.brain: Optional[BrainClient] = None
        self.governor: Optional[RiskGovernor] = None
        self.adapter: Optional[ExchangeAdapter] = None
        self.position_manager: Optional[PositionManager] = None
        self.executor: Optional[CommandExecutor] = None
        self.state_assembler = MarketStateAssembler()
        self.latest_state: Dict[str, Any] = {}
        self.latest_execution: Optional[Dict[str, Any]] = None
        self.started_at = datetime.now(timezone.utc).isoformat()

    async def initialize(self) -> None:
        connected = await asyncio.to_thread(self.connection.connect)
        if not connected:
            raise RuntimeError("Falha ao conectar na Binance Futures")

        self.scanner = ExecutableSymbolScanner(
            self.connection,
            max_leverage=self.max_leverage,
            max_margin_usage_pct=self.max_margin_usage_pct,
        )
        universe = await asyncio.to_thread(self.scanner.scan, None, self.max_symbols)
        self.candidate_symbols = [row["symbol"] for row in universe]

        raw_positions = await asyncio.to_thread(self.connection.client.futures_position_information)
        open_symbols = {
            str(p.get("symbol", "")).upper()
            for p in raw_positions
            if abs(float(p.get("positionAmt") or 0)) > 0
        }
        self.symbols = sorted(set(self.candidate_symbols) | open_symbols)
        if not self.symbols:
            raise RuntimeError("Nenhum contrato USD-M executavel e nenhuma posicao aberta para monitorar")

        self.journal.append(
            "RUNTIME_UNIVERSE",
            payload={
                "symbols": self.symbols,
                "candidate_symbols": self.candidate_symbols,
                "required_open_symbols": sorted(open_symbols),
                "scanner": universe,
            },
        )

        self.market_stream = FuturesMarketStream(self.symbols)
        self.derivatives = DerivativesSampler(
            self.symbols,
            interval_seconds=int(os.getenv("DERIVATIVES_INTERVAL_SECONDS", "60")),
        )
        self.structure = StructureSampler(
            self.symbols,
            interval_seconds=int(os.getenv("STRUCTURE_INTERVAL_SECONDS", "30")),
        )
        self.user_stream = FuturesUserDataStream(BINANCE_API_KEY)
        self.brain = BrainClient(command_ttl_seconds=self.command_ttl)
        self.governor = RiskGovernor(
            self.connection,
            max_leverage=self.max_leverage,
            max_margin_usage_pct=self.max_margin_usage_pct,
        )
        self.adapter = ExchangeAdapter(self.connection)
        self.position_manager = PositionManager(self.adapter, self.journal)
        self.executor = CommandExecutor(
            self.connection,
            governor=self.governor,
            adapter=self.adapter,
            journal=self.journal,
            position_manager=self.position_manager,
        )

        await self._bootstrap_account_state(raw_positions=raw_positions)
        await asyncio.gather(
            self.derivatives.sample_once(),
            self.structure.sample_once(),
            return_exceptions=True,
        )

    async def _bootstrap_account_state(self, raw_positions: Optional[List[Dict[str, Any]]] = None) -> None:
        assert self.user_stream is not None
        balance_task = asyncio.to_thread(self.connection.client.futures_account_balance)
        if raw_positions is None:
            positions_task = asyncio.to_thread(self.connection.client.futures_position_information)
            balances, positions = await asyncio.gather(balance_task, positions_task)
        else:
            balances = await balance_task
            positions = raw_positions

        now_ms = int(time.time() * 1000)
        async with self.user_stream._lock:
            for b in balances:
                asset = str(b.get("asset", "")).upper()
                if not asset:
                    continue
                self.user_stream.state["balances"][asset] = {
                    "asset": asset,
                    "wallet_balance": float(b.get("balance") or 0),
                    "cross_wallet_balance": float(b.get("crossWalletBalance") or 0),
                    "available_balance": float(b.get("availableBalance") or 0),
                    "balance_change": None,
                    "reason": "REST_BOOTSTRAP",
                    "event_ms": now_ms,
                }
            for p in positions:
                amount = float(p.get("positionAmt") or 0)
                if amount == 0:
                    continue
                symbol = str(p.get("symbol", "")).upper()
                explicit = str(p.get("positionSide") or "BOTH").upper()
                side = explicit if explicit in {"LONG", "SHORT"} else ("LONG" if amount > 0 else "SHORT")
                key = f"{symbol}:{side}"
                self.user_stream.state["positions"][key] = {
                    "symbol": symbol,
                    "position_side": side,
                    "position_amount": amount,
                    "entry_price": float(p.get("entryPrice") or 0),
                    "break_even_price": float(p.get("breakEvenPrice") or 0),
                    "unrealized_pnl": float(p.get("unRealizedProfit") or 0),
                    "margin_type": p.get("marginType"),
                    "isolated_wallet": float(p.get("isolatedWallet") or 0),
                    "reason": "REST_BOOTSTRAP",
                    "event_ms": now_ms,
                }
            self.user_stream.state["last_event_type"] = "REST_BOOTSTRAP"
            self.user_stream.state["last_event_ms"] = now_ms

    def _required_monitor_symbols(self) -> Set[str]:
        required: Set[str] = set()
        if self.position_manager:
            required.update(
                str(state.get("symbol", "")).upper()
                for state in self.position_manager.positions.values()
                if state.get("symbol")
            )
        if self.user_stream:
            for position in (self.user_stream.state.get("positions") or {}).values():
                if abs(float(position.get("position_amount") or 0)) > 0:
                    required.add(str(position.get("symbol", "")).upper())
        if self.executor:
            for pending in self.executor.pending_entries.values():
                command = pending.get("command")
                if command and command.symbol:
                    required.add(str(command.symbol).upper())
        pending = self.gateway.pending()
        if pending and pending[0].symbol:
            required.add(str(pending[0].symbol).upper())
        return {symbol for symbol in required if symbol}

    async def refresh_universe_once(self) -> Dict[str, Any]:
        assert self.scanner and self.market_stream and self.derivatives and self.structure
        universe = await asyncio.to_thread(self.scanner.scan, None, self.max_symbols)
        candidates = [row["symbol"] for row in universe]
        required = self._required_monitor_symbols()
        new_symbols = sorted(set(candidates) | required)
        if not new_symbols:
            new_symbols = list(self.symbols)

        changed = new_symbols != sorted(self.symbols) or candidates != self.candidate_symbols
        self.candidate_symbols = candidates
        if changed:
            await asyncio.gather(
                self.market_stream.replace_symbols(new_symbols),
                self.derivatives.replace_symbols(new_symbols),
                self.structure.replace_symbols(new_symbols),
            )
            self.symbols = new_symbols
            await asyncio.gather(
                self.derivatives.sample_once(),
                self.structure.sample_once(),
                return_exceptions=True,
            )
            self.journal.append(
                "RUNTIME_UNIVERSE_UPDATED",
                payload={
                    "symbols": self.symbols,
                    "candidate_symbols": self.candidate_symbols,
                    "required_symbols": sorted(required),
                    "scanner": universe,
                },
            )
        return {"changed": changed, "symbols": self.symbols, "candidate_symbols": self.candidate_symbols}

    async def universe_loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                await self.refresh_universe_once()
            except Exception as exc:
                self.journal.append("UNIVERSE_REFRESH_ERROR", payload={"error": str(exc)}, level="ERROR")
            try:
                await asyncio.wait_for(self.stop_event.wait(), timeout=self.universe_refresh_interval)
            except asyncio.TimeoutError:
                continue

    async def build_state(self) -> Dict[str, Any]:
        assert self.market_stream and self.derivatives and self.structure and self.user_stream
        market_snapshot, derivatives_snapshot, structure_snapshot, account_snapshot = await asyncio.gather(
            self.market_stream.snapshot(),
            self.derivatives.snapshot(),
            self.structure.snapshot(),
            self.user_stream.snapshot(),
        )
        market_context = MarketContextBuilder.build(
            market_snapshot,
            symbols=self.symbols,
            max_symbols=len(self.symbols),
            derivatives_snapshot=derivatives_snapshot,
            structure_snapshot=structure_snapshot,
        )
        state = self.state_assembler.build(market_context, account_snapshot)
        usdt = (account_snapshot.get("balances") or {}).get("USDT") or {}
        state["account"]["available_balance_usdt"] = usdt.get("available_balance")
        state["candidate_symbols"] = list(self.candidate_symbols)
        state["monitor_only_symbols"] = sorted(set(self.symbols) - set(self.candidate_symbols))
        self.latest_state = state
        return state

    async def market_price_loop(self) -> None:
        assert self.market_stream and self.position_manager
        while not self.stop_event.is_set():
            try:
                snapshot = await self.market_stream.snapshot()
                for symbol, row in (snapshot.get("symbols") or {}).items():
                    price = row.get("mark_price") or row.get("last_trade_price")
                    if price:
                        self.position_manager.on_price(symbol, float(price))
            except Exception as exc:
                self.journal.append("PRICE_MANAGER_ERROR", payload={"error": str(exc)}, level="ERROR")
            await asyncio.sleep(0.5)

    async def account_event_loop(self) -> None:
        assert self.user_stream and self.executor and self.position_manager
        while not self.stop_event.is_set():
            try:
                event = await self.user_stream.next_event(timeout=1.0)
            except asyncio.TimeoutError:
                continue
            except Exception as exc:
                self.journal.append("ACCOUNT_EVENT_LOOP_ERROR", payload={"error": str(exc)}, level="ERROR")
                await asyncio.sleep(1)
                continue

            self.journal.account_event(event)
            order = event.get("order")
            if order:
                try:
                    result = self.executor.handle_order_event(order)
                    if result:
                        self.latest_execution = result
                    protection_event = self.position_manager.handle_order_event(order)
                    if protection_event:
                        self.latest_execution = protection_event
                except Exception as exc:
                    self.journal.append("ORDER_EVENT_HANDLER_ERROR", payload={"error": str(exc), "order": order}, level="ERROR")

            if event.get("account"):
                try:
                    account_snapshot = await self.user_stream.snapshot()
                    removed = self.position_manager.reconcile(account_snapshot)
                    if removed:
                        self.journal.append("POSITIONS_RECONCILED", payload={"removed": removed})
                except Exception as exc:
                    self.journal.append("POSITION_RECONCILE_ERROR", payload={"error": str(exc)}, level="ERROR")

            if event.get("margin_call"):
                self.journal.append("MARGIN_CALL", payload=event["margin_call"], level="ERROR")

    async def decision_loop(self) -> None:
        assert self.brain and self.governor and self.executor
        while not self.stop_event.is_set():
            try:
                pending = self.gateway.pending()
                if pending is None and not self.executor.pending_entries:
                    state = await self.build_state()
                    command = await asyncio.to_thread(self.brain.decide, state)
                    self.journal.command_received(command)
                    if command.action != Action.WAIT:
                        if command.action == Action.OPEN_POSITION and command.symbol not in self.candidate_symbols:
                            self.journal.append(
                                "BRAIN_COMMAND_REJECTED_LOCALLY",
                                command.command_id,
                                {"reason": "SYMBOL_NOT_EXECUTABLE_CANDIDATE", "symbol": command.symbol},
                                "WARN",
                            )
                        else:
                            preflight = await asyncio.to_thread(self.governor.preflight, command, state)
                            self.journal.validation(command.command_id, preflight.accepted, preflight.reason, details=preflight.details)
                            if preflight.accepted:
                                self.gateway.publish(
                                    command,
                                    {
                                        "accepted": True,
                                        "reason": preflight.reason,
                                        "details": preflight.details,
                                    },
                                )
                            else:
                                self.journal.append(
                                    "BRAIN_COMMAND_REJECTED_LOCALLY",
                                    command.command_id,
                                    {"reason": preflight.reason, "details": preflight.details},
                                    "WARN",
                                )
                    else:
                        self.journal.append("BRAIN_WAIT", command.command_id, {"reason": command.reason})
            except Exception as exc:
                self.journal.append("DECISION_LOOP_ERROR", payload={"error": str(exc)}, level="ERROR")
            try:
                await asyncio.wait_for(self.stop_event.wait(), timeout=self.decision_interval)
            except asyncio.TimeoutError:
                continue

    async def approval_loop(self) -> None:
        assert self.executor
        while not self.stop_event.is_set():
            try:
                pending = self.gateway.pending()
                if pending is not None:
                    command, _wrapper = pending
                    approval = self.gateway.consume_approval(command.command_id)
                    if approval is not None:
                        state = await self.build_state()
                        result = await asyncio.to_thread(self.executor.execute, command, approval, state)
                        self.latest_execution = result
                        self.journal.append("COMMAND_EXECUTION_FINISHED", command.command_id, {"result": result})
                        self.gateway.clear(command.command_id)
            except Exception as exc:
                self.journal.append("APPROVAL_LOOP_ERROR", payload={"error": str(exc)}, level="ERROR")
            await asyncio.sleep(0.25)

    def _write_status_sync(self, payload: Dict[str, Any]) -> None:
        os.makedirs("logs", exist_ok=True)
        temp = "logs/runtime_status.json.tmp"
        with open(temp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        os.replace(temp, "logs/runtime_status.json")

    async def status_loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                pending = self.gateway.pending()
                payload = {
                    "started_at": self.started_at,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                    "bot_mode": BOT_MODE,
                    "symbols": self.symbols,
                    "candidate_symbols": self.candidate_symbols,
                    "monitor_only_symbols": sorted(set(self.symbols) - set(self.candidate_symbols)),
                    "market_stream_connected": bool(self.market_stream and self.market_stream.connected),
                    "user_stream_connected": bool(self.user_stream and self.user_stream.connected),
                    "decision_ready": self.latest_state.get("decision_ready"),
                    "quality_flags": self.latest_state.get("quality_flags") or [],
                    "pending_command_id": pending[0].command_id if pending else None,
                    "pending_limit_entries": list((self.executor.pending_entries if self.executor else {}).keys()),
                    "managed_positions": list((self.position_manager.positions if self.position_manager else {}).keys()),
                    "latest_execution": deepcopy(self.latest_execution),
                }
                await asyncio.to_thread(self._write_status_sync, payload)
            except Exception as exc:
                self.journal.append("STATUS_LOOP_ERROR", payload={"error": str(exc)}, level="ERROR")
            await asyncio.sleep(self.status_interval)

    async def run(self) -> None:
        await self.initialize()
        assert self.market_stream and self.derivatives and self.structure and self.user_stream
        tasks = [
            asyncio.create_task(self.market_stream.run(), name="market-stream"),
            asyncio.create_task(self.derivatives.run(), name="derivatives-sampler"),
            asyncio.create_task(self.structure.run(), name="structure-sampler"),
            asyncio.create_task(self.user_stream.run(), name="user-stream"),
            asyncio.create_task(self.account_event_loop(), name="account-events"),
            asyncio.create_task(self.market_price_loop(), name="position-prices"),
            asyncio.create_task(self.decision_loop(), name="brain-decisions"),
            asyncio.create_task(self.approval_loop(), name="approvals"),
            asyncio.create_task(self.universe_loop(), name="universe-refresh"),
            asyncio.create_task(self.status_loop(), name="status"),
        ]
        self.journal.append("RUNTIME_STARTED", payload={"symbols": self.symbols, "candidate_symbols": self.candidate_symbols})
        try:
            await self.stop_event.wait()
        finally:
            await self.shutdown()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def shutdown(self) -> None:
        if self.market_stream:
            await self.market_stream.stop()
        if self.derivatives:
            await self.derivatives.stop()
        if self.structure:
            await self.structure.stop()
        if self.user_stream:
            await self.user_stream.stop()
        self.journal.append("RUNTIME_STOPPED")

    def request_stop(self) -> None:
        self.stop_event.set()


async def _main() -> None:
    runtime = AdaptiveRuntime()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, runtime.request_stop)
        except NotImplementedError:
            pass
    await runtime.run()


def main() -> None:
    asyncio.run(_main())


if __name__ == "__main__":
    main()
