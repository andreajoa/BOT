# -*- coding: utf-8 -*-
"""
Configuracoes do bot legado.

A nova arquitetura esta migrando a decisao de estrategia para comandos
estruturados. Enquanto a migracao nao termina, configuracoes antigas sao
mantidas por compatibilidade.
"""

import os
from dotenv import load_dotenv

load_dotenv()

# ============================================================================
# API
# ============================================================================
BINANCE_API_KEY = os.getenv("BINANCEAPIKEY")
BINANCE_API_SECRET = os.getenv("BINANCEAPISECRET")

# ============================================================================
# MODO DE OPERACAO
# ============================================================================
# Fail-closed: nunca assumir LIVE quando a variavel nao existe.
BOT_MODE = os.getenv("BOT_MODE", "disabled").lower()
VALID_BOT_MODES = {"disabled", "paper", "live"}
if BOT_MODE not in VALID_BOT_MODES:
    raise ValueError(
        f"BOT_MODE invalido: {BOT_MODE!r}. Use disabled, paper ou live."
    )

# ============================================================================
# CONFIGURACOES LEGADAS DE RISCO
# ============================================================================
LEVERAGE = int(os.getenv("LEVERAGE", "2"))
POSITION_SIZE_PCT = float(os.getenv("POSITION_SIZE_PCT", "0.05"))
MIN_BALANCE_USDT = float(os.getenv("MIN_BALANCE_USDT", "0.60"))
MAX_OPEN_POSITIONS = int(os.getenv("MAX_OPEN_POSITIONS", "3"))

ENTRY_BALANCE_PCT = 0.60
MIN_NOTIONAL = 0.60

# ============================================================================
# PARAMETROS LEGADOS DE MEAN REVERSION
# ============================================================================
MIN_SCORE_FOR_ENTRY = 2.5
STOP_LOSS_PCT = 0.005
TAKE_PROFIT_PCT = 0.010
MIN_RISK_REWARD = 1.5

# Bollinger Bands
BB_PERIOD = int(os.getenv("BB_PERIOD", "20"))
BB_STD_DEV = float(os.getenv("BB_STD_DEV", "2.0"))

# RSI
RSI_PERIOD = int(os.getenv("RSI_PERIOD", "14"))
RSI_OVERBOUGHT = 70
RSI_OVERSOLD = 30

# Stochastic
STOCH_K_PERIOD = int(os.getenv("STOCH_K_PERIOD", "14"))
STOCH_D_PERIOD = int(os.getenv("STOCH_D_PERIOD", "3"))
STOCH_OVERBOUGHT = 80
STOCH_OVERSOLD = 20

# Williams %R
WILLIAMS_PERIOD = int(os.getenv("WILLIAMS_PERIOD", "14"))
WILLIAMS_OVERBOUGHT = -20
WILLIAMS_OVERSOLD = -80

SR_LOOKBACK = int(os.getenv("SR_LOOKBACK", "50"))
MIN_BB_PROXIMITY = float(os.getenv("MIN_BB_PROXIMITY", "0.02"))

# ============================================================================
# TIMEFRAMES / WATCHLIST LEGADOS
# ============================================================================
TIMEFRAMES = ["1h", "4h"]
KLINE_LIMIT = 150
BTC_SYMBOL = "BTCUSDT"

WATCHLIST_FALLBACK = [
    "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT",
    "ADAUSDT", "AVAXUSDT", "DOTUSDT", "LINKUSDT", "ATOMUSDT"
]
WATCHLIST_BLACKLIST = {"BTCUSDT", "USDCUSDT", "BUSDUSDT", "TUSDUSDT", "FDUSDUSDT"}

# ============================================================================
# INTERVALOS
# ============================================================================
SCAN_INTERVAL_SECONDS = 60
POSITION_CHECK_INTERVAL_SECONDS = 5
REQUEST_TIMEOUT = 60

COOLDOWN_AFTER_CLOSE_SECONDS = 60
COOLDOWN_AFTER_ENTRY_SECONDS = 120

# ============================================================================
# FILTROS LEGADOS
# ============================================================================
ADX_MAX_FOR_LATERAL = 25
ADX_MIN_FOR_ENTRY = 15

# ============================================================================
# ARQUIVOS / LOGGING
# ============================================================================
LOG_DIR = "logs"
RUNTIME_LOG = "runtime.log"
CLOSED_TRADES_LOG = "closed_trades.jsonl"
ACTIVE_POSITION_FILE = "active_position.json"
MARKET_DATA_FILE = "market_data.json"
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
