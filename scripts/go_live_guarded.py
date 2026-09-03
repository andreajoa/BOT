# -*- coding: utf-8 -*-
"""Guarded end-to-end live launcher.

This script does NOT auto-approve trades. It:
1. Runs the non-trading live preflight (including one harmless OpenAI call).
2. Starts the adaptive runtime.
3. Waits for market/account readiness and a valid pending proposal.
4. Prints the exact proposal and requires the operator to type:
       APROVAR <command_id>
5. Watches execution until entry/protection are confirmed or a failure occurs.

It is intentionally interactive so a real-money entry cannot be authorized by
an unattended process or by the model itself.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from control.approval_gateway import ApprovalGateway

STATUS_PATH = ROOT / "logs" / "runtime_status.json"
PENDING_PATH = ROOT / "logs" / "pending_command.json"


def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        with path.open("r", encoding="utf-8") as fh:
            value = json.load(fh)
        return value if isinstance(value, dict) else None
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def approval_phrase(command_id: str) -> str:
    return f"APROVAR {command_id}"


def approval_matches(text: str, command_id: str) -> bool:
    return text.strip() == approval_phrase(command_id)


def _run_preflight() -> int:
    print("\n=== 1/5 PREFLIGHT LIVE (ZERO ORDENS) ===", flush=True)
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "live_preflight.py"), "--brain"],
        cwd=str(ROOT),
        check=False,
    )
    if result.returncode != 0:
        print("\nPREFLIGHT FALHOU. Nenhuma ordem foi enviada. Corrija os checks acima antes de continuar.")
        return result.returncode or 1
    print("\nPREFLIGHT OK. O diagnóstico confirmou orders_sent=0.")
    return 0


def _terminate_runtime(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    try:
        process.send_signal(signal.SIGINT)
        process.wait(timeout=8)
    except Exception:
        try:
            process.terminate()
            process.wait(timeout=5)
        except Exception:
            process.kill()


def _wait_runtime_ready(process: subprocess.Popen, timeout: int) -> bool:
    print("\n=== 2/5 INICIANDO RUNTIME ===", flush=True)
    deadline = time.monotonic() + timeout
    last_summary = None
    while time.monotonic() < deadline:
        if process.poll() is not None:
            print(f"Runtime encerrou antes de ficar pronto (exit={process.returncode}).")
            return False
        status = _read_json(STATUS_PATH) or {}
        summary = (
            status.get("market_stream_connected"),
            status.get("user_stream_connected"),
            status.get("decision_ready"),
            tuple(status.get("quality_flags") or []),
        )
        if summary != last_summary:
            print(
                "runtime: "
                f"market_ws={summary[0]} user_ws={summary[1]} "
                f"decision_ready={summary[2]} flags={list(summary[3])}",
                flush=True,
            )
            last_summary = summary
        if summary[0] and summary[1] and summary[2] is True:
            print("Runtime pronto: WebSocket público + User Data Stream + decision_ready=true.")
            return True
        time.sleep(1)
    print("Timeout aguardando runtime ficar pronto.")
    return False


def _wait_for_proposal(process: subprocess.Popen, timeout: int) -> Optional[Dict[str, Any]]:
    print("\n=== 3/5 AGUARDANDO PROPOSTA VÁLIDA ===")
    print("WAIT é permitido; o sistema não força uma entrada sem edge suficiente.")
    deadline = time.monotonic() + timeout if timeout > 0 else None
    last_heartbeat = 0.0
    while deadline is None or time.monotonic() < deadline:
        if process.poll() is not None:
            print(f"Runtime encerrou enquanto aguardava proposta (exit={process.returncode}).")
            return None
        wrapper = _read_json(PENDING_PATH)
        command = (wrapper or {}).get("command") if wrapper else None
        if isinstance(command, dict) and command.get("command_id"):
            return wrapper
        now = time.monotonic()
        if now - last_heartbeat >= 15:
            status = _read_json(STATUS_PATH) or {}
            print(
                "aguardando... "
                f"decision_ready={status.get('decision_ready')} "
                f"candidates={len(status.get('candidate_symbols') or [])}",
                flush=True,
            )
            last_heartbeat = now
        time.sleep(1)
    print("Nenhuma proposta válida apareceu dentro do período configurado. Nenhuma ordem foi enviada.")
    return None


def _print_proposal(wrapper: Dict[str, Any]) -> str:
    command = wrapper["command"]
    command_id = str(command["command_id"])
    print("\n=== 4/5 APROVAÇÃO HUMANA OBRIGATÓRIA ===")
    print(json.dumps({"command": command, "preflight": wrapper.get("preflight")}, ensure_ascii=False, indent=2))
    print("\nPara AUTORIZAR exatamente esta operação, digite:")
    print(approval_phrase(command_id))
    print("Qualquer outro texto cancela esta proposta.")
    return command_id


def _approve_interactively(wrapper: Dict[str, Any]) -> bool:
    command_id = _print_proposal(wrapper)
    try:
        typed = input("\n> ")
    except (EOFError, KeyboardInterrupt):
        print("\nAprovação cancelada.")
        return False
    if not approval_matches(typed, command_id):
        try:
            ApprovalGateway().reject(command_id, "LOCAL_OPERATOR_DID_NOT_CONFIRM_EXACT_PHRASE")
        except Exception:
            pass
        print("Operação NÃO aprovada. Nenhuma autorização foi emitida.")
        return False
    approval = ApprovalGateway().approve(command_id)
    print(f"APROVAÇÃO REGISTRADA para {approval.command_id} até {approval.expires_at}.")
    return True


def _wait_execution(process: subprocess.Popen, command_id: str, timeout: int) -> bool:
    print("\n=== 5/5 CONFIRMANDO FILL + PROTEÇÃO ===")
    deadline = time.monotonic() + timeout
    last_value = None
    while time.monotonic() < deadline:
        if process.poll() is not None:
            print(f"Runtime encerrou durante execução (exit={process.returncode}).")
            return False
        status = _read_json(STATUS_PATH) or {}
        latest = status.get("latest_execution") or {}
        if latest != last_value and latest:
            print(json.dumps(latest, ensure_ascii=False, indent=2), flush=True)
            last_value = latest

        latest_command = str(latest.get("command_id") or "")
        latest_status = str(latest.get("status") or "").upper()
        if latest_command == command_id:
            if latest_status in {"REJECTED", "FAILED_SAFE", "ENTRY_TERMINATED"}:
                print(f"Execução não concluiu com sucesso: {latest_status}.")
                return False
            if latest_status == "EXECUTED":
                protection = latest.get("protection") or {}
                if protection.get("success") is True:
                    managed = status.get("managed_positions") or []
                    print("\n100% END-TO-END CONFIRMADO")
                    print(f"command_id={command_id}")
                    print("entry_fill=CONFIRMADO")
                    print("stop_loss=CONFIRMADO")
                    print("take_profit=CONFIRMADO")
                    print(f"managed_positions={managed}")
                    return True
        time.sleep(0.5)
    print("Timeout aguardando confirmação final da execução/proteção.")
    return False


def main() -> int:
    if _run_preflight() != 0:
        return 2

    # Remove only stale status from a previous process. Never remove approvals,
    # managed positions or pending entries here.
    try:
        STATUS_PATH.unlink()
    except FileNotFoundError:
        pass

    runtime = subprocess.Popen([sys.executable, str(ROOT / "main.py")], cwd=str(ROOT))
    try:
        ready_timeout = max(30, int(os.getenv("GUARDED_READY_TIMEOUT_SECONDS", "90")))
        proposal_timeout = int(os.getenv("GUARDED_PROPOSAL_TIMEOUT_SECONDS", "0"))
        execution_timeout = max(30, int(os.getenv("GUARDED_EXECUTION_TIMEOUT_SECONDS", "180")))

        if not _wait_runtime_ready(runtime, ready_timeout):
            return 3

        wrapper = _wait_for_proposal(runtime, proposal_timeout)
        if wrapper is None:
            return 4

        command = wrapper.get("command") or {}
        command_id = str(command.get("command_id") or "")
        if not command_id:
            print("Proposta sem command_id; recusada.")
            return 5

        if not _approve_interactively(wrapper):
            return 6

        return 0 if _wait_execution(runtime, command_id, execution_timeout) else 7
    except KeyboardInterrupt:
        print("\nInterrompido pelo operador. Nenhuma nova aprovação será emitida.")
        return 130
    finally:
        _terminate_runtime(runtime)


if __name__ == "__main__":
    raise SystemExit(main())
