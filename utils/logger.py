# -*- coding: utf-8 -*-
"""
Logger Simplificado
Sistema de logging para o bot.
"""

import os
from datetime import datetime
from config.settings import LOG_DIR, LOG_LEVEL


class Logger:
    """Logger simplificado com níveis."""

    def __init__(self, log_dir: str = None):
        self.log_dir = log_dir or LOG_DIR
        os.makedirs(self.log_dir, exist_ok=True)

        # Mapeamento de cores (apenas para terminal)
        self.colors = {
            "DEBUG": "\033[36m",    # Cyan
            "INFO": "\033[32m",     # Green
            "WARN": "\033[33m",     # Yellow
            "ERROR": "\033[31m",    # Red
            "SUCCESS": "\033[32m",   # Green
            "RESET": "\033[0m"      # Reset
        }

    def _format_time(self) -> str:
        """Retorna timestamp formatado."""
        return datetime.now().strftime('%H:%M:%S')

    def _log(self, level: str, tag: str, msg: str, print_only: bool = False):
        """Método interno de logging."""
        # Filtrar por nível
        level_priority = {"DEBUG": 0, "INFO": 1, "WARN": 2, "ERROR": 3, "SUCCESS": 4}
        current_priority = level_priority.get(LOG_LEVEL, 1)
        msg_priority = level_priority.get(level, 1)

        if msg_priority < current_priority and level != "ERROR":
            return

        timestamp = self._format_time()
        line = f"[{timestamp}] [{tag}] {msg}"

        if not print_only:
            log_file = os.path.join(self.log_dir, "runtime.log")
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(line + "\n")

        # Print com cor
        color = self.colors.get(level, "")
        reset = self.colors["RESET"]
        print(f"{color}{line}{reset}")

    def debug(self, tag: str, msg: str):
        """Log nível DEBUG."""
        self._log("DEBUG", tag, msg)

    def info(self, tag: str, msg: str):
        """Log nível INFO."""
        self._log("INFO", tag, msg)

    def warn(self, tag: str, msg: str):
        """Log nível WARN."""
        self._log("WARN", tag, msg)

    def error(self, tag: str, msg: str):
        """Log nível ERROR."""
        self._log("ERROR", tag, msg)

    def success(self, tag: str, msg: str):
        """Log nível SUCCESS."""
        self._log("SUCCESS", tag, msg)

    def divider(self, title: str):
        """Log divisor."""
        line = "=" * 50
        self.info("INFO", f"{line} {title} {line}")

    def trade(self, symbol: str, side: str, entry: float, sl: float, tp: float,
             reason: str, confidence: str = "N/A"):
        """Log de trade."""
        sl_pct = abs(sl - entry) / entry * 100
        tp_pct = abs(tp - entry) / entry * 100

        msg = (f"ENTRADA {symbol} {side} @ {entry:.4f} | "
                f"SL: {sl:.4f} (-{sl_pct:.2f}%) | "
                f"TP: {tp:.4f} (+{tp_pct:.2f}%) | "
                f"Confiança: {confidence} | {reason}")

        self.info("ENTRY", msg)

    def exit(self, symbol: str, side: str, entry: float, exit_price: float,
            reason: str, pnl_usdt: float):
        """Log de saída."""
        pnl_pct = ((exit_price - entry) / entry * 100) if side == "LONG" else ((entry - exit_price) / entry * 100)
        result = "✅ WIN" if pnl_usdt > 0 else "❌ LOSS"

        msg = (f"SAÍDA {symbol} {side} | "
                f"Entry: {entry:.4f} | Exit: {exit_price:.4f} | "
                f"PnL: {pnl_pct:+.2f}% (${pnl_usdt:+.4f}) | "
                f"{reason} {result}")

        if pnl_usdt > 0:
            self.success("EXIT", msg)
        else:
            self.error("EXIT", msg)
