# -*- coding: utf-8 -*-
"""
Indicadores Técnicos - Mercado Lateral
Especializado em detectar extremos para mean reversion.
"""

import pandas as pd
import numpy as np
import ta


class LateralIndicators:
    """Indicadores especializados para mercado lateral."""

    @staticmethod
    def add_bollinger_bands(df: pd.DataFrame, period: int = 20, std_dev: float = 2.0) -> pd.DataFrame:
        """Adiciona Bollinger Bands."""
        bb = ta.volatility.BollingerBands(df["close"], window=period, window_dev=std_dev)
        df["bb_upper"] = bb.bollinger_hband()
        df["bb_middle"] = bb.bollinger_mavg()
        df["bb_lower"] = bb.bollinger_lband()
        df["bb_width"] = (df["bb_upper"] - df["bb_lower"]) / df["bb_middle"]
        df["bb_pct"] = (df["close"] - df["bb_lower"]) / (df["bb_upper"] - df["bb_lower"])
        return df

    @staticmethod
    def add_rsi(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
        """Adiciona RSI."""
        df["rsi"] = ta.momentum.rsi(df["close"], window=period)
        return df

    @staticmethod
    def add_stochastic(df: pd.DataFrame, k_period: int = 14, d_period: int = 3) -> pd.DataFrame:
        """Adiciona Stochastic."""
        stoch = ta.momentum.StochasticOscillator(
            df["high"], df["low"], df["close"],
            window=k_period, smooth_window=d_period
        )
        df["stoch_k"] = stoch.stoch()
        df["stoch_d"] = stoch.stoch_signal()
        return df

    @staticmethod
    def add_williams_r(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
        """Adiciona Williams %R."""
        high = df["high"].rolling(window=period).max()
        low = df["low"].rolling(window=period).min()
        df["williams_r"] = -100 * (high - df["close"]) / (high - low)
        return df

    @staticmethod
    def add_ema(df: pd.DataFrame, period: int) -> pd.DataFrame:
        """Adiciona EMA simples."""
        df[f"ema_{period}"] = ta.trend.ema_indicator(df["close"], window=period)
        return df

    @staticmethod
    def add_atr(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
        """Adiciona ATR para stop loss dinâmico."""
        atr = ta.volatility.AverageTrueRange(df["high"], df["low"], df["close"], window=period)
        df["atr"] = atr.average_true_range()
        return df

    @staticmethod
    def add_volume(df: pd.DataFrame, period: int = 20) -> pd.DataFrame:
        """Adiciona indicadores de volume."""
        df["vol_sma"] = df["volume"].rolling(period).mean()
        df["vol_ratio"] = df["volume"] / df["vol_sma"]
        return df

    @staticmethod
    def add_adx(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
        """Adiciona ADX para detectar força de tendência."""
        adx = ta.trend.ADXIndicator(df["high"], df["low"], df["close"], window=period)
        df["adx"] = adx.adx()
        return df

    @staticmethod
    def detect_support_resistance(df: pd.DataFrame, lookback: int = 50) -> pd.DataFrame:
        """Detecta níveis de suporte e resistência."""
        high = df["high"].tail(lookback)
        low = df["low"].tail(lookback)

        # Suporte: mínimo mais frequente nos últimos N candles
        support_level = low.min()
        support_count = (low <= support_level * 1.005).sum()  # Tolerância 0.5%

        # Resistência: máximo mais frequente nos últimos N candles
        resistance_level = high.max()
        resistance_count = (high >= resistance_level * 0.995).sum()  # Tolerância 0.5%

        df["support"] = support_level
        df["resistance"] = resistance_level
        df["support_strength"] = support_count
        df["resistance_strength"] = resistance_count

        return df

    @staticmethod
    def add_band_position(df: pd.DataFrame, min_proximity: float = 0.02) -> pd.DataFrame:
        """Determina posição relativa às bandas de Bollinger."""
        df["dist_from_upper"] = (df["bb_upper"] - df["close"]) / df["close"]
        df["dist_from_lower"] = (df["close"] - df["bb_lower"]) / df["close"]
        df["near_upper_band"] = df["dist_from_upper"] <= min_proximity
        df["near_lower_band"] = df["dist_from_lower"] <= min_proximity
        df["at_upper_band"] = df["dist_from_upper"] <= min_proximity * 0.5  # Muito perto
        df["at_lower_band"] = df["dist_from_lower"] <= min_proximity * 0.5  # Muito perto
        return df

    @staticmethod
    def calculate_all(df: pd.DataFrame, bb_period: int = 20, bb_std: float = 2.0,
                    rsi_period: int = 14, stoch_k: int = 14, stoch_d: int = 3,
                    williams_period: int = 14, sr_lookback: int = 50) -> pd.DataFrame:
        """Calcula todos os indicadores para mercado lateral."""
        df = df.copy().reset_index(drop=True)
        df = df.ffill().fillna(0)

        # EMAs básicas
        df = LateralIndicators.add_ema(df, 20)
        df = LateralIndicators.add_ema(df, 50)

        # Indicadores principais para lateral
        df = LateralIndicators.add_bollinger_bands(df, bb_period, bb_std)
        df = LateralIndicators.add_rsi(df, rsi_period)
        df = LateralIndicators.add_stochastic(df, stoch_k, stoch_d)
        df = LateralIndicators.add_williams_r(df, williams_period)
        df = LateralIndicators.add_atr(df, 14)
        df = LateralIndicators.add_adx(df, 14)
        df = LateralIndicators.add_volume(df, 20)
        df = LateralIndicators.detect_support_resistance(df, sr_lookback)
        df = LateralIndicators.add_band_position(df, 0.02)

        return df


def is_in_lateral_zone(row: dict, adx_max: float = 25) -> bool:
    """Verifica se o mercado está em zona lateral."""
    adx = row.get("adx", 0)
    bb_width = row.get("bb_width", 0)
    bb_pct = row.get("bb_pct", 0.5)

    # ADX baixo indica lateral
    if adx > adx_max:
        return False

    # BB width moderada (nem muito estreito nem muito largo)
    if bb_width < 0.01 or bb_width > 0.08:
        return False

    # Preço não nas extremidades (indica lateral)
    if bb_pct < 0.1 or bb_pct > 0.9:
        return False

    return True


def get_reversal_signal(row: dict,
                       rsi_ob: float = 70, rsi_os: float = 30,
                       stoch_ob: float = 80, stoch_os: float = 20,
                       williams_ob: float = -20, williams_os: float = -80) -> str:
    """
    Determina sinal de reversão baseado em múltiplos indicadores.

    Retorna: "LONG" (comprar), "SHORT" (vender) ou "NEUTRAL"
    """
    rsi = row.get("rsi", 50)
    stoch_k = row.get("stoch_k", 50)
    stoch_d = row.get("stoch_d", 50)
    williams = row.get("williams_r", -50)
    bb_pct = row.get("bb_pct", 0.5)
    near_upper = row.get("near_upper_band", False)
    near_lower = row.get("near_lower_band", False)

    # Sinais de SOBREVENDIDO → LONG
    oversold_signals = 0
    if rsi < rsi_os:
        oversold_signals += 1
    if stoch_k < stoch_os:
        oversold_signals += 1
    if williams < williams_os:
        oversold_signals += 1
    if near_lower and bb_pct < 0.3:
        oversold_signals += 1

    # Sinais de SOBRECOMPRADO → SHORT
    overbought_signals = 0
    if rsi > rsi_ob:
        overbought_signals += 1
    if stoch_k > stoch_ob:
        overbought_signals += 1
    if williams > williams_ob:
        overbought_signals += 1
    if near_upper and bb_pct > 0.7:
        overbought_signals += 1

    # Decisão
    if oversold_signals >= 3:
        return "LONG"
    if overbought_signals >= 3:
        return "SHORT"

    return "NEUTRAL"
