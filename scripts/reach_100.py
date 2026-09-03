# -*- coding: utf-8 -*-
"""Human-confirmed supervisor to reach the first live 100% acceptance gate.

This script does NOT choose trades and never auto-approves an order. The adaptive
runtime/Brain Client proposes a command; the operator must type the exact phrase
``APROVAR <command_id>`` before ApprovalGateway writes the approval.

Flow:
1. verify local git is exactly origin/main;
2. run the non-trading live preflight with the real OpenAI/Binance config;
3. reuse a healthy runtime or start ``python3 main.py``;
4. wait for a concrete pending command;
5. show its full command + deterministic Risk Governor preflight;
6. require exact human confirmation for that command id;
7. monitor the real execution and run the read-only final acceptance audit;
8. report 100% only when ``hundred_percent=true``.

Usage:
    python3 scripts/reach_100.py
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from dataclasses import asdict
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from control.approval_gateway import ApprovalGateway
from scripts.final_acceptance import run as final_acceptance_run


LOGS = ROOT / "logs"
RUNTIME_LOG = LOGS / "production_runtime.log"
STATUS_PATH = LOGS / "runtime_status.json"


def _jsonable(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as fh:
            payload = json.load(fh)
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _timestamp_fresh(value: Any, max_age_seconds: int = 12) -> bool:
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


def _runtime_healthy(status: Dict[str, Any]) -> bool:
    return bool(
        status
        and _timestamp_fresh(status.get("updated_at"))
        and status.get("market_stream_connected") is True
        and status.get("user_stream_connected") is True
        and status.get("decision_ready") is True
    )


def _git(*args: str) -> Tuple[int, str]:
    proc = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    return proc.returncode, (proc.stdout or "").strip()


def _verify_git() -> bool:
    code, output = _git("fetch", "origin")
    if code != 0:
        print("ERRO: git fetch origin falhou:\n" + output)
        return False

    _, branch = _git("rev-parse", "--abbrev-ref", "HEAD")
    _, head = _git("rev-parse", "HEAD")
    _, origin_main = _git("rev-parse", "origin/main")
    _, dirty = _git("status", "--porcelain", "--untracked-files=no")

    checks = {
        "branch_main": branch == "main",
        "head_equals_origin_main": bool(head and origin_main and head == origin_main),
        "tracked_tree_clean": dirty == "",
    }
    print(json.dumps({"git": checks, "head": head, "origin_main": origin_main}, indent=2))
    return all(checks.values())


def _run_preflight() -> bool:
    print("\n=== PREFLIGHT REAL (0 ordens) ===")
    proc = subprocess.run(
        [sys.executable, "scripts/live_preflight.py", "--brain"],
        cwd=ROOT,
    )
    return proc.returncode == 0


def _start_runtime() -> Tuple[Optional[subprocess.Popen], Optional[Any]]:
    LOGS.mkdir(parents=True, exist_ok=True)
    log_handle = RUNTIME_LOG.open("a", encoding="utf-8", buffering=1)
    log_handle.write("\n\n=== production runtime start ===\n")
    process = subprocess.Popen(
        [sys.executable, "main.py"],
        cwd=ROOT,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        text=True,
    )
    return process, log_handle


def _tail_runtime_log(lines: int = 60) -> str:
    if not RUNTIME_LOG.exists():
        return ""
    try:
        content = RUNTIME_LOG.read_text(encoding="utf-8", errors="replace").splitlines()
        return "\n".join(content[-lines:])
    except Exception:
        return ""


def _wait_for_healthy_runtime(process: Optional[subprocess.Popen], timeout_seconds: int = 90) -> bool:
    deadline = time.monotonic() + timeout_seconds
    last_status: Dict[str, Any] = {}
    while time.monotonic() < deadline:
        if process is not None and process.poll() is not None:
            print(f"ERRO: runtime encerrou com código {process.returncode}.")
            print(_tail_runtime_log())
            return False
        last_status = _read_json(STATUS_PATH)
        if _runtime_healthy(last_status):
            print("\nRUNTIME OK: market stream + user stream + decision_ready=true")
            return True
        time.sleep(1)
    print("ERRO: runtime não atingiu estado saudável.")
    print(json.dumps(last_status, ensure_ascii=False, indent=2))
    print(_tail_runtime_log())
    return False


def _approval_matches(text: str, command_id: str) -> bool:
    return text.strip() == f"APROVAR {command_id}"


def _show_command(command: Any, wrapper: Dict[str, Any]) -> None:
    payload = _jsonable(asdict(command))
    preflight = wrapper.get("preflight") or {}
    print("\n" + "=" * 80)
    print("PROPOSTA REAL PENDENTE — NÃO EXECUTADA AINDA")
    print("=" * 80)
    print(json.dumps({"command": payload, "preflight": preflight}, ensure_ascii=False, indent=2))
    details = preflight.get("details") or {}
    if details:
        print("\nResumo de risco validado pelo Risk Governor:")
        print(f"  símbolo: {details.get('symbol')}")
        print(f"  lado: {details.get('side')}")
        print(f"  notional: {details.get('notional')}")
        print(f"  leverage: {details.get('leverage')}")
        print(f"  perda estimada até SL: {details.get('estimated_loss_to_stop_usdt')} USDT")
        print(f"  limite máximo de perda: {details.get('max_loss_usdt')} USDT")
    print("\nNenhuma ordem será liberada por este supervisor sem a confirmação exata abaixo.")


def _acceptance_live_position(payload: Dict[str, Any]) -> Optional[bool]:
    for check in payload.get("checks") or []:
        if check.get("check") == "position_lifecycle_proven":
            return bool(check.get("live_position"))
    return None


def _monitor_after_approval(
    command_id: str,
    expires_at: str,
    runtime_process: Optional[subprocess.Popen],
) -> Optional[Dict[str, Any]]:
    try:
        expiry = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        deadline = max(time.time() + 30, expiry.timestamp() + 45)
    except Exception:
        deadline = time.time() + 150

    last_payload: Optional[Dict[str, Any]] = None
    while time.time() < deadline:
        if runtime_process is not None and runtime_process.poll() is not None:
            print(f"ERRO: runtime encerrou durante a execução (código {runtime_process.returncode}).")
            print(_tail_runtime_log())
            return None

        try:
            payload = final_acceptance_run(command_id)
        except Exception as exc:
            payload = {"hundred_percent": False, "error": str(exc), "checks": []}
        last_payload = payload
        if payload.get("hundred_percent") is True:
            return payload
        time.sleep(5)

    if last_payload:
        print("\nA operação aprovada não atingiu o gate de 100% dentro da janela desta proposta.")
        failed = [c for c in last_payload.get("checks") or [] if not c.get("ok")]
        print(json.dumps({"failed_checks": failed}, ensure_ascii=False, indent=2))
    return None


def _stop_owned_runtime(process: Optional[subprocess.Popen]) -> None:
    if process is None or process.poll() is not None:
        return
    try:
        process.send_signal(signal.SIGINT)
        process.wait(timeout=10)
    except Exception:
        try:
            process.terminate()
            process.wait(timeout=5)
        except Exception:
            pass


def main() -> int:
    print("=== ADAPTIVE FUTURES — CAMINHO GUIADO ATÉ 100% ===")
    print("Este supervisor exige confirmação humana explícita por command_id para qualquer operação real.\n")

    if not _verify_git():
        print("\nBLOQUEADO: atualize/limpe a cópia local antes de usar conta real.")
        return 2

    if not _run_preflight():
        print("\nBLOQUEADO: preflight não ficou 100% verde. Nenhuma ordem foi enviada.")
        return 3

    existing_status = _read_json(STATUS_PATH)
    owned_process: Optional[subprocess.Popen] = None
    log_handle: Optional[Any] = None
    if _runtime_healthy(existing_status):
        print("\nRuntime saudável já está ativo; vou reutilizá-lo.")
    else:
        print("\nIniciando production runtime...")
        owned_process, log_handle = _start_runtime()
        if not _wait_for_healthy_runtime(owned_process):
            _stop_owned_runtime(owned_process)
            if log_handle:
                log_handle.close()
            return 4

    gateway = ApprovalGateway()

    try:
        while True:
            if owned_process is not None and owned_process.poll() is not None:
                print(f"ERRO: runtime encerrou com código {owned_process.returncode}.")
                print(_tail_runtime_log())
                return 5

            pending = gateway.pending()
            if pending is None:
                time.sleep(1)
                continue

            command, wrapper = pending
            _show_command(command, wrapper)
            expected = f"APROVAR {command.command_id}"
            print(f"\nPara autorizar ESTA operação real, digite exatamente:\n{expected}")
            print("Para rejeitar, digite: REJEITAR")

            while True:
                answer = input("> ").strip()
                if answer == "REJEITAR":
                    gateway.reject(command.command_id, "USER_REJECTED_FROM_REACH_100")
                    print(f"REJEITADO: {command.command_id}")
                    break
                if _approval_matches(answer, command.command_id):
                    approval = gateway.approve(command.command_id)
                    print(f"APROVADO EXPLICITAMENTE: {approval.command_id}")
                    print("Acompanhando fill + ISOLATED + SL + TP + journal...")
                    result = _monitor_after_approval(command.command_id, command.expires_at, owned_process)
                    if result and result.get("hundred_percent") is True:
                        print("\n" + "=" * 80)
                        print("100% ATINGIDO — END-TO-END REAL VALIDADO")
                        print("=" * 80)
                        print(json.dumps(result, ensure_ascii=False, indent=2))
                        live_position = _acceptance_live_position(result)
                        if live_position:
                            print("\nA posição ainda está aberta. O runtime continuará ativo neste terminal.")
                            print("Não encerre o processo enquanto depender do trailing/gerenciamento local.")
                            while True:
                                if owned_process is not None and owned_process.poll() is not None:
                                    print("Runtime encerrou após o 100%; SL/TP já enviados à Binance permanecem no exchange.")
                                    return 0
                                time.sleep(5)
                        if owned_process is not None:
                            _stop_owned_runtime(owned_process)
                        return 0
                    print("A proposta terminou sem completar o gate de 100%; o supervisor aguardará a próxima oportunidade.")
                    break

                print("Confirmação inválida. Nada foi aprovado.")
                print(f"Use exatamente `{expected}` ou `REJEITAR`.")

    except KeyboardInterrupt:
        print("\nInterrompido pelo usuário.")
        if owned_process is not None:
            _stop_owned_runtime(owned_process)
        return 130
    finally:
        if log_handle:
            try:
                log_handle.close()
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
