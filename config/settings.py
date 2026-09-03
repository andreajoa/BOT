# -*- coding: utf-8 -*-
"""
Configurações do Bot - Mercado Lateral
Especializado para trading em mercados laterais/ranging.
"""

import os
from dotenv import load_dotenv

load_dotenv()

# ============================================================================
# CONFIGURAÇÕES DE API
# ============================================================================
BINANCE_API_KEY = os.getenv("BINANCEAPIKEY")
BINANCE_API_SECRET = os.getenv("BINANCEAPISECRET")

# ============================================================================
# MODO DE OPERAÇÃO
# ============================================================================
BOT_MODE = os.getenv("BOT_MODE", "live").lower()

# ============================================================================
# CONFIGURAÇÕES DE RISCO - Mercado Lateral
# ============================================================================
LEVERAGE = int(os.getenv("LEVERAGE", "2"))  # 2x (conservador para lateral)
POSITION_SIZE_PCT = float(os.getenv("POSITION_SIZE_PCT", "0.05"))  # 5%
MIN_BALANCE_USDT = float(os.getenv("MIN_BALANCE_USDT", "0.60"))
MAX_OPEN_POSITIONS = int(os.getenv("MAX_OPEN_POSITIONS", "3"))  # 3 posições simultâneas

ENTRY_BALANCE_PCT = 0.60      # 60% do saldo para entrada
MIN_NOTIONAL = 0.60           # $0.60 mínimo notional

# ============================================================================
# PARÂMETROS DE TRADING - Mercado Lateral
# ============================================================================
MIN_SCORE_FOR_ENTRY = 2.5       # Acessível para lateral

# Stop Loss e Take Profit para lateral (movimentos pequenos)
STOP_LOSS_PCT = 0.005           # 0.5% Stop Loss (estreito)
TAKE_PROFIT_PCT = 0.010          # 1.0% Take Profit (curto)
MIN_RISK_REWARD = 1.5             # RR mínimo de 1.5

# ============================================================================
# INDICADORES - Mercado Lateral
# ============================================================================
# Bollinger Bands
BB_PERIOD = int(os.getenv("BB_PERIOD", "20"))
BB_STD_DEV = float(os.getenv("BB_STD_DEV", "2.0"))

# RSI
RSI_PERIOD = int(os.getenv("RSI_PERIOD", "14"))
RSI_OVERBOUGHT = 70     # Sobrecomprado
RSI_OVERSOLD = 30       # Sobrevendido

# Stochastic
STOCH_K_PERIOD = int(os.getenv("STOCH_K_PERIOD", "14"))
STOCH_D_PERIOD = int(os.getenv("STOCH_D_PERIOD", "3"))
STOCH_OVERBOUGHT = 80   # Sobrecomprado
STOCH_OVERSOLD = 20     # Sobrevendido

# Williams %R
WILLIAMS_PERIOD = int(os.getenv("WILLIAMS_PERIOD", "14"))
WILLIAMS_OVERBOUGHT = -20
WILLIAMS_OVERSOLD = -80

# Support/Resistance Levels
SR_LOOKBACK = int(os.getenv("SR_LOOKBACK", "50"))  # Últimos 50 candles para S/R

# ============================================================================
# PROXIMIDADE DAS BANDAS PARA ENTRADA
# ============================================================================
MIN_BB_PROXIMITY = float(os.getenv("MIN_BB_PROXIMITY", "0.02"))  # 2% de proximidade

# ============================================================================
# TIMEFRAMES E DADOS
# ============================================================================
TIMEFRAMES = ["1h", "4h"]
KLINE_LIMIT = 150
BTC_SYMBOL = "BTCUSDT"

# ============================================================================
# WATCHLIST
# ============================================================================
WATCHLIST_FALLBACK = [
    "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT",
    "ADAUSDT", "AVAXUSDT", "DOTUSDT", "LINKUSDT", "ATOMUSDT"
]
WATCHLIST_BLACKLIST = {"BTCUSDT", "USDCUSDT", "BUSDUSDT", "TUSDUSDT", "FDUSDUSDT"}

# ============================================================================
# INTERVALOS
# ============================================================================
SCAN_INTERVAL_SECONDS = 60          # 1 minuto (scan rápido para lateral)
POSITION_CHECK_INTERVAL_SECONDS = 5
REQUEST_TIMEOUT = 60

# ============================================================================
# COOLDOWNS
# ============================================================================
COOLDOWN_AFTER_CLOSE_SECONDS = 60    # 1 minuto (lateral tem oportunidades rápidas)
COOLDOWN_AFTER_ENTRY_SECONDS = 120   # 2 minutos entre entradas do mesmo símbolo

# ============================================================================
# FILTROS DE MERCADO LATERAL
# ============================================================================
ADX_MAX_FOR_LATERAL = 25          # ADX < 25 indica lateral
ADX_MIN_FOR_ENTRY = 15           # Mínimo de ADX (mas não pode ser muito fraco)

# ============================================================================
# CAMINHOS DE ARQUIVO
# ============================================================================
LOG_DIR = "logs"
RUNTIME_LOG = "runtime.log"
CLOSED_TRADES_LOG = "closed_trades.jsonl"
ACTIVE_POSITION_FILE = "active_position.json"
MARKET_DATA_FILE = "market_data.json"

# ============================================================================
# LOGGING
# ============================================================================
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
