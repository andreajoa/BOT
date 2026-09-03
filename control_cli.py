# -*- coding: utf-8 -*-
"""Local CLI to inspect/approve/reject the exact pending trade command."""

from __future__ import annotations

import json
import sys
from dataclasses import asdict
from enum import Enum

from control.approval_gateway import ApprovalGateway


def _jsonable(value):
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


def show_status(gateway: ApprovalGateway) -> int:
    pending = gateway.pending()
    if pending is None:
        print("Nenhum comando pendente valido.")
        return 0
    command, wrapper = pending
    print(json.dumps({"command": _jsonable(asdict(command)), "preflight": wrapper.get("preflight")}, ensure_ascii=False, indent=2))
    return 0


def main(argv=None) -> int:
    argv = list(argv or sys.argv[1:])
    gateway = ApprovalGateway()
    action = (argv[0] if argv else "status").lower()

    if action == "status":
        return show_status(gateway)

    pending = gateway.pending()
    if pending is None:
        print("Nenhum comando pendente valido.")
        return 2
    command, _ = pending
    command_id = argv[1] if len(argv) > 1 else command.command_id

    try:
        if action == "approve":
            approval = gateway.approve(command_id)
            print(f"APROVADO: {approval.command_id} ate {approval.expires_at}")
            return 0
        if action == "reject":
            reason = argv[2] if len(argv) > 2 else "USER_REJECTED"
            gateway.reject(command_id, reason)
            print(f"REJEITADO: {command_id} ({reason})")
            return 0
    except ValueError as exc:
        print(f"ERRO: {exc}")
        return 2

    print("Uso: python control_cli.py [status|approve [command_id]|reject [command_id] [motivo]]")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
