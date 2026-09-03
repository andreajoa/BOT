# -*- coding: utf-8 -*-
"""Production runtime where ChatGPT is an external decision source.

No OpenAI API key is used in this mode. The local bot publishes private state to
Neon and consumes only PROPOSED commands from Neon. Every non-WAIT proposal is
validated locally and then becomes PENDING_APPROVAL. Real execution still
requires the existing exact per-command human approval.
"""

from __future__ import annotations

import asyncio
import os
import signal
import time
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import adaptive_runtime
from command_protocol import Action, TradeCommand
from config.settings import BINANCE_API_KEY, BINANCE_API_SECRET, BOT_MODE
from control.approval_gateway import ApprovalGateway
from control.neon_bridge import NeonBridge
from core.binance_connection import BinanceConnection
from execution.exchange_adapter import ExchangeAdapter
from execution.journal import ExecutionJournal
from execution.position_manager import PositionManager
from execution.resilient_command_executor import ResilientCommandExecutor
from market.market_state import MarketStateAssembler
from risk.governor import RiskGovernor


class ExternalChatGPTRuntime(adaptive_runtime.AdaptiveRuntime):
    def __init__(self):
        # Reproduce AdaptiveRuntime initialization without requiring OPENAI_API_KEY.
        if BOT_MODE != "live":
            raise RuntimeError("BRAIN_MODE=external_chatgpt exige BOT_MODE=live explicitamente no .env")
        if not BINANCE_API_KEY or not BINANCE_API_SECRET:
            raise RuntimeError("BINANCEAPIKEY/BINANCEAPISECRET ausentes no .env local")

        database_url = (os.getenv("NEON_DATABASE_URL") or "").strip()
        if not database_url:
            raise RuntimeError("NEON_DATABASE_URL ausente no .env local para BRAIN_MODE=external_chatgpt")

        self.stop_event = asyncio.Event()
        self.journal = ExecutionJournal()
        self.gateway = ApprovalGateway()
        self.connection = BinanceConnection(BINANCE_API_KEY, BINANCE_API_SECRET)
        self.bridge = NeonBridge(database_url)

        self.max_leverage = int(os.getenv("MAX_LEVERAGE_HARD", "20"))
        self.max_margin_usage_pct = float(os.getenv("MAX_MARGIN_USAGE_PCT", "0.95"))
        self.max_symbols = max(1, int(os.getenv("SCANNER_MAX_SYMBOLS", "15")))
        self.decision_interval = max(2, int(os.getenv("EXTERNAL_PROPOSAL_POLL_SECONDS", "5")))
        self.command_ttl = max(15, int(os.getenv("BRAIN_COMMAND_TTL_SECONDS", "90")))
        self.status_interval = max(1, int(os.getenv("STATUS_INTERVAL_SECONDS", "2")))
        self.universe_refresh_interval = max(30, int(os.getenv("UNIVERSE_REFRESH_SECONDS", "300")))
        self.telemetry_interval = max(5, int(os.getenv("NEON_TELEMETRY_INTERVAL_SECONDS", "15")))

        self.symbols: List[str] = []
        self.candidate_symbols: List[str] = []
        self.candidate_constraints: Dict[str, Dict[str, Any]] = {}
        self.scanner = None
        self.market_stream = None
        self.derivatives = None
        self.structure = None
        self.user_stream = None
        self.brain = None
        self.governor: Optional[RiskGovernor] = None
        self.adapter: Optional[ExchangeAdapter] = None
        self.position_manager: Optional[PositionManager] = None
        self.executor = None
        self.state_assembler = MarketStateAssembler()
        self.latest_state: Dict[str, Any] = {}
        self.latest_execution: Optional[Dict[str, Any]] = None
        self.started_at = datetime.now(timezone.utc).isoformat()
        self.runtime_instance_id = f"external-{int(time.time())}"
        self.proposal_by_command: Dict[str, str] = {}
        self._last_telemetry_at = 0.0

    async def initialize(self) -> None:
        # Reuse the tested AdaptiveRuntime initialization but replace only the
        # components that would otherwise instantiate an API brain/executor.
        original_brain = adaptive_runtime.BrainClient
        original_executor = adaptive_runtime.CommandExecutor
        adaptive_runtime.BrainClient = lambda *args, **kwargs: None
        adaptive_runtime.CommandExecutor = ResilientCommandExecutor
        try:
            await adaptive_runtime.AdaptiveRuntime.initialize(self)
        finally:
            adaptive_runtime.BrainClient = original_brain
            adaptive_runtime.CommandExecutor = original_executor

        self.journal.append(
            "EXTERNAL_CHATGPT_MODE_READY",
            payload={"runtime_instance_id": self.runtime_instance_id, "telemetry": "neon_private"},
        )
        await asyncio.to_thread(
            self.bridge.append_event,
            "RUNTIME_CONNECTED",
            {"runtime_instance_id": self.runtime_instance_id, "started_at": self.started_at},
        )

    def _telemetry_status(self) -> Dict[str, Any]:
        pending = self.gateway.pending()
        return {
            "started_at": self.started_at,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "bot_mode": BOT_MODE,
            "brain_mode": "external_chatgpt",
            "market_stream_connected": bool(self.market_stream and self.market_stream.connected),
            "user_stream_connected": bool(self.user_stream and self.user_stream.connected),
            "decision_ready": self.latest_state.get("decision_ready"),
            "quality_flags": self.latest_state.get("quality_flags") or [],
            "pending_command_id": pending[0].command_id if pending else None,
            "pending_limit_entries": list((self.executor.pending_entries if self.executor else {}).keys()),
            "managed_positions": list((self.position_manager.positions if self.position_manager else {}).keys()),
            "latest_execution": deepcopy(self.latest_execution),
        }

    async def _publish_telemetry_if_due(self, state: Dict[str, Any], *, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_telemetry_at < self.telemetry_interval:
            return
        status = self._telemetry_status()
        await asyncio.to_thread(
            self.bridge.publish_state,
            runtime_instance_id=self.runtime_instance_id,
            bot_mode=BOT_MODE,
            state=state,
            status=status,
            latest_execution=self.latest_execution,
        )
        self._last_telemetry_at = now

    async def decision_loop(self) -> None:
        assert self.governor and self.executor
        while not self.stop_event.is_set():
            try:
                state = await self.build_state()
                await self._publish_telemetry_if_due(state)
                await asyncio.to_thread(self.bridge.expire_old_proposals)

                pending = self.gateway.pending()
                if pending is None and not self.executor.pending_entries:
                    proposal = await asyncio.to_thread(self.bridge.fetch_next_proposal)
                    if proposal:
                        proposal_id = str(proposal["proposal_id"])
                        try:
                            command_payload = proposal.get("command") or {}
                            command = TradeCommand.from_dict(command_payload)
                            command.validate()
                            self.journal.command_received(command)

                            if command.action == Action.OPEN_POSITION and command.symbol not in self.candidate_symbols:
                                preflight_payload = {
                                    "accepted": False,
                                    "reason": "SYMBOL_NOT_EXECUTABLE_CANDIDATE",
                                    "symbol": command.symbol,
                                }
                                await asyncio.to_thread(
                                    self.bridge.mark_proposal,
                                    proposal_id,
                                    "REJECTED",
                                    preflight=preflight_payload,
                                )
                                self.journal.append(
                                    "EXTERNAL_PROPOSAL_REJECTED",
                                    command.command_id,
                                    preflight_payload,
                                    "WARN",
                                )
                            else:
                                preflight = await asyncio.to_thread(self.governor.preflight, command, state)
                                preflight_payload = {
                                    "accepted": preflight.accepted,
                                    "reason": preflight.reason,
                                    "details": preflight.details,
                                }
                                self.journal.validation(
                                    command.command_id,
                                    preflight.accepted,
                                    preflight.reason,
                                    details=preflight.details,
                                )
                                if preflight.accepted:
                                    self.gateway.publish(command, preflight_payload)
                                    self.proposal_by_command[command.command_id] = proposal_id
                                    await asyncio.to_thread(
                                        self.bridge.mark_proposal,
                                        proposal_id,
                                        "PENDING_APPROVAL",
                                        preflight=preflight_payload,
                                    )
                                else:
                                    await asyncio.to_thread(
                                        self.bridge.mark_proposal,
                                        proposal_id,
                                        "REJECTED",
                                        preflight=preflight_payload,
                                    )
                        except Exception as exc:
                            await asyncio.to_thread(
                                self.bridge.mark_proposal,
                                proposal_id,
                                "REJECTED",
                                preflight={"accepted": False, "reason": "INVALID_EXTERNAL_PROPOSAL", "error": str(exc)},
                            )
                            self.journal.append(
                                "EXTERNAL_PROPOSAL_INVALID",
                                payload={"proposal_id": proposal_id, "error": str(exc)},
                                level="ERROR",
                            )
            except Exception as exc:
                self.journal.append("EXTERNAL_DECISION_LOOP_ERROR", payload={"error": str(exc)}, level="ERROR")
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
                        proposal_id = self.proposal_by_command.get(command.command_id)
                        if proposal_id:
                            await asyncio.to_thread(self.bridge.mark_proposal, proposal_id, "APPROVED")
                        state = await self.build_state()
                        result = await asyncio.to_thread(self.executor.execute, command, approval, state)
                        self.latest_execution = result
                        self.journal.append("COMMAND_EXECUTION_FINISHED", command.command_id, {"result": result})
                        if proposal_id:
                            await asyncio.to_thread(self.bridge.mark_proposal, proposal_id, "CONSUMED")
                            await asyncio.to_thread(
                                self.bridge.append_event,
                                "COMMAND_EXECUTION_FINISHED",
                                {"result": result},
                                command.command_id,
                            )
                        self.gateway.clear(command.command_id)
                        self.proposal_by_command.pop(command.command_id, None)
                        await self._publish_telemetry_if_due(state, force=True)
            except Exception as exc:
                self.journal.append("EXTERNAL_APPROVAL_LOOP_ERROR", payload={"error": str(exc)}, level="ERROR")
            await asyncio.sleep(0.25)

    async def shutdown(self) -> None:
        try:
            if self.latest_state:
                await self._publish_telemetry_if_due(self.latest_state, force=True)
            await asyncio.to_thread(
                self.bridge.append_event,
                "RUNTIME_STOPPING",
                {"runtime_instance_id": self.runtime_instance_id},
            )
        except Exception:
            pass
        if self.executor and hasattr(self.executor, "stop_recovery"):
            self.executor.stop_recovery()
        await adaptive_runtime.AdaptiveRuntime.shutdown(self)
        await asyncio.to_thread(self.bridge.close)


async def _main() -> None:
    runtime = ExternalChatGPTRuntime()
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
