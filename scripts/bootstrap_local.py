# -*- coding: utf-8 -*-
"""Bootstrap local seguro para macOS/Linux com PEP 668.

Cria um ambiente virtual isolado em .venv, instala/atualiza as dependencias
somente dentro dele e inicia o fluxo live protegido.

Nao envia ordens durante o bootstrap. A eventual ordem real continua exigindo
a aprovacao exata do command_id dentro de go_live_guarded.py.

Uso:
    python3 scripts/bootstrap_local.py
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VENV = ROOT / ".venv"


def run(args: list[str]) -> None:
    print("+", " ".join(str(x) for x in args), flush=True)
    subprocess.run(args, cwd=str(ROOT), check=True)


def venv_python() -> Path:
    if os.name == "nt":
        return VENV / "Scripts" / "python.exe"
    return VENV / "bin" / "python"


def main() -> int:
    print("=== BOOTSTRAP LOCAL DO BOT ===", flush=True)
    print(f"Projeto: {ROOT}", flush=True)

    if sys.version_info < (3, 10):
        print("ERRO: Python 3.10+ e necessario.")
        return 2

    env_file = ROOT / ".env"
    if not env_file.exists():
        print("ERRO: .env local nao encontrado. As chaves nunca devem ser colocadas no GitHub.")
        return 3

    py = venv_python()
    if not py.exists():
        print("Criando ambiente virtual isolado em .venv ...", flush=True)
        run([sys.executable, "-m", "venv", str(VENV)])

    py = venv_python()
    if not py.exists():
        print("ERRO: ambiente virtual nao foi criado corretamente.")
        return 4

    print("Atualizando pip dentro da .venv ...", flush=True)
    run([str(py), "-m", "pip", "install", "--upgrade", "pip"])

    print("Instalando dependencias dentro da .venv ...", flush=True)
    run([str(py), "-m", "pip", "install", "-r", str(ROOT / "requirements.txt")])

    print("\nDependencias prontas. Iniciando fluxo live protegido ...", flush=True)
    return subprocess.call(
        [str(py), str(ROOT / "scripts" / "go_live_guarded.py")],
        cwd=str(ROOT),
    )


if __name__ == "__main__":
    raise SystemExit(main())
