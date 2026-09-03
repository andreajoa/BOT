# -*- coding: utf-8 -*-
"""Bootstrap local seguro para macOS/Linux com PEP 668.

Cria um ambiente virtual isolado em .venv, instala/atualiza as dependencias,
configura somente o .env LOCAL e inicia o fluxo live protegido.

No modo padrao BRAIN_MODE=external_chatgpt nao existe OPENAI_API_KEY: o runtime
publica estado em Neon privado e aguarda propostas externas. Cada nova operacao
real continua exigindo a aprovacao exata do command_id.
"""

from __future__ import annotations

import getpass
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
VENV = ROOT / ".venv"
ENV_FILE = ROOT / ".env"
TLS_BOOTSTRAP = ROOT / "runtime_bootstrap"


def run(args: list[str], *, env: Optional[dict[str, str]] = None) -> None:
    print("+", " ".join(str(x) for x in args), flush=True)
    subprocess.run(args, cwd=str(ROOT), check=True, env=env)


def venv_python() -> Path:
    if os.name == "nt":
        return VENV / "Scripts" / "python.exe"
    return VENV / "bin" / "python"


def _env_file_value(key: str) -> Optional[str]:
    try:
        lines = ENV_FILE.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    pattern = re.compile(rf"^\s*{re.escape(key)}\s*=\s*(.*)$")
    for line in lines:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = pattern.match(line)
        if not match:
            continue
        value = match.group(1).strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'\"', "'"}:
            value = value[1:-1]
        return value
    return None


def _set_env_file_value(key: str, value: str) -> None:
    if "\n" in value or "\r" in value:
        raise ValueError(f"Valor invalido para {key}")
    try:
        original = ENV_FILE.read_text(encoding="utf-8")
    except OSError:
        original = ""
    lines = original.splitlines()
    pattern = re.compile(rf"^\s*{re.escape(key)}\s*=")
    replacement = f"{key}={value}"
    replaced = False
    output = []
    for line in lines:
        if pattern.match(line) and not replaced:
            output.append(replacement)
            replaced = True
        else:
            output.append(line)
    if not replaced:
        if output and output[-1].strip():
            output.append("")
        output.append(replacement)
    temp = ENV_FILE.with_name(".env.bootstrap.tmp")
    temp.write_text("\n".join(output).rstrip() + "\n", encoding="utf-8")
    try:
        os.chmod(temp, 0o600)
    except OSError:
        pass
    temp.replace(ENV_FILE)
    try:
        os.chmod(ENV_FILE, 0o600)
    except OSError:
        pass


def _configure_local_runtime() -> int:
    """Resolve local-only settings without ever printing secrets."""
    # This project now defaults to the external ChatGPT bridge. No OpenAI API
    # key is requested or required in this mode.
    _set_env_file_value("BRAIN_MODE", "external_chatgpt")
    print("\nBRAIN_MODE=external_chatgpt (sem OPENAI_API_KEY).")

    neon_url = (os.getenv("NEON_DATABASE_URL") or _env_file_value("NEON_DATABASE_URL") or "").strip()
    if not neon_url:
        print("\nNEON_DATABASE_URL nao encontrada.")
        print("Cole a connection string do projeto privado Neon 'binance-bot-telemetry'.")
        print("A entrada fica oculta e sera salva SOMENTE no .env local.")
        value = getpass.getpass("NEON_DATABASE_URL: ").strip()
        if not value.startswith(("postgres://", "postgresql://")):
            print("Connection string Neon ausente/invalida. Encerrando sem enviar ordens.")
            return 6
        _set_env_file_value("NEON_DATABASE_URL", value)
        print("NEON_DATABASE_URL salva localmente (valor nao exibido).")

    current_mode = (_env_file_value("BOT_MODE") or "disabled").strip().lower()
    if current_mode != "live":
        print(f"\nBOT_MODE atual: {current_mode}")
        print("Para habilitar o executor real, digite exatamente: ATIVAR LIVE")
        print("Isso NAO aprova nenhuma operacao; cada command_id continua exigindo aprovacao propria.")
        phrase = input("> ").strip()
        if phrase != "ATIVAR LIVE":
            print("LIVE nao ativado. Encerrando sem enviar ordens.")
            return 5
        _set_env_file_value("BOT_MODE", "live")
        print("BOT_MODE=live salvo somente no .env local.")

    return 0


def _child_environment() -> dict[str, str]:
    """Ensure every bot subprocess uses the OS-native TLS trust store."""
    env = os.environ.copy()
    bootstrap_path = str(TLS_BOOTSTRAP)
    existing = env.get("PYTHONPATH", "").strip()
    env["PYTHONPATH"] = bootstrap_path if not existing else bootstrap_path + os.pathsep + existing
    env["PYTHONUNBUFFERED"] = "1"
    return env


def main() -> int:
    print("=== BOOTSTRAP LOCAL DO BOT ===", flush=True)
    print(f"Projeto: {ROOT}", flush=True)

    if sys.version_info < (3, 10):
        print("ERRO: Python 3.10+ e necessario.")
        return 2

    if not ENV_FILE.exists():
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

    config_result = _configure_local_runtime()
    if config_result != 0:
        return config_result

    child_env = _child_environment()
    print("\nTLS: usando trust store nativo do sistema; verificacao de certificado permanece ATIVA.")
    print("Dependencias/configuracao prontas. Iniciando fluxo live protegido ...", flush=True)
    return subprocess.call(
        [str(py), str(ROOT / "scripts" / "go_live_guarded.py")],
        cwd=str(ROOT),
        env=child_env,
    )


if __name__ == "__main__":
    raise SystemExit(main())
