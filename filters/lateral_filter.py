# -*- coding: utf-8 -*-
"""
Filtros de Entrada - Mercado Lateral
Especializado em identificar extremos para mean reversion.
"""

from typing import Dict, Tuple
from config.settings import (
    ADX_MAX_FOR_LATERAL, ADX_MIN_FOR_ENTRY,
    RSI_OVERBOUGHT, RSI_OVERSOLD,
    STOCH_OVERBOUGHT, STOCH_OVERSOLD,
    WILLIAMS_OVERBOUGHT, WILLIAMS_OVERSOLD,
    MIN_BB_PROXIMITY, MIN_SCORE_FOR_ENTRY
)


def score_lateral_entry(df_1h: dict, df_4h: dict, btc_trend: str, symbol: str) -> Dict:
    """
    Avalia um símbolo para entrada em mercado lateral.

    Retorna score e direção baseados em:
    - Proximidade das bandas de Bollinger
    - RSI extremos
    - Stochastic extremos
    - Williams %R extremos
    - ADX (deve ser baixo para confirmar lateral)
    - Volume (deve ser normal, não excessivamente baixo)
    """
    score = 0.0
    details = []
    direction = "NEUTRAL"

    # ===== 1. Verificar se está em zona lateral =====
    adx_4h = df_4h.get("adx", 0)
    bb_width_4h = df_4h.get("bb_width", 0)
    bb_pct_4h = df_4h.get("bb_pct", 0.5)

    # ADX deve ser baixo para confirmar lateral
    if adx_4h <= ADX_MAX_FOR_LATERAL:
        score += 2.0  # Bônus por estar em lateral
        details.append("ADX baixo (lateral)")
    elif adx_4h < ADX_MIN_FOR_ENTRY:
        score -= 1.0
        details.append("ADX muito fraco")
    else:
        score -= 2.0
        details.append("ADX alto (tendência)")
        return {"symbol": symbol, "score": round(score, 2), "direction": direction, "details": details}

    # BB width deve ser moderada (nem muito estreito nem muito largo)
    if 0.02 <= bb_width_4h <= 0.06:
        score += 1.0
        details.append("BB width adequada")
    elif bb_width_4h < 0.015:
        score -= 0.5
        details.append("BB muito estreita")
    elif bb_width_4h > 0.08:
        score -= 1.0
        details.append("BB muito larga")

    # ===== 2. Avaliar sinais de extremos =====
    rsi_1h = df_1h.get("rsi", 50)
    stoch_k_1h = df_1h.get("stoch_k", 50)
    stoch_d_1h = df_1h.get("stoch_d", 50)
    williams_1h = df_1h.get("williams_r", -50)

    # Sinais de SOBREVENDIDO → LONG
    oversold_count = 0
    oversold_signals = []

    if rsi_1h < RSI_OVERSOLD:
        oversold_count += 1
        oversold_signals.append(f"RSI={rsi_1h:.0f}")

    if stoch_k_1h < STOCH_OVERSOLD:
        oversold_count += 1
        oversold_signals.append(f"Stoch={stoch_k_1h:.0f}")

    if williams_1h < WILLIAMS_OVERSOLD:
        oversold_count += 1
        oversold_signals.append(f"WillR={williams_1h:.0f}")

    # Sinais de SOBRECOMPRADO → SHORT
    overbought_count = 0
    overbought_signals = []

    if rsi_1h > RSI_OVERBOUGHT:
        overbought_count += 1
        overbought_signals.append(f"RSI={rsi_1h:.0f}")

    if stoch_k_1h > STOCH_OVERBOUGHT:
        overbought_count += 1
        overbought_signals.append(f"Stoch={stoch_k_1h:.0f}")

    if williams_1h > WILLIAMS_OVERBOUGHT:
        overbought_count += 1
        overbought_signals.append(f"WillR={williams_1h:.0f}")

    # ===== 3. Proximidade das bandas =====
    near_upper = df_1h.get("near_upper_band", False)
    near_lower = df_1h.get("near_lower_band", False)
    at_upper = df_1h.get("at_upper_band", False)
    at_lower = df_1h.get("at_lower_band", False)

    # Preço muito perto da banda inferior + sobrevendido = LONG
    if near_lower and oversold_count >= 2:
        score += 3.0
        direction = "LONG"
        details.append("BB lower + oversold")
        details.extend(oversold_signals)
        if at_lower:
            score += 1.0
            details.append("AT lower band")

    # Preço muito perto da banda superior + sobrecomprado = SHORT
    elif near_upper and overbought_count >= 2:
        score += 3.0
        direction = "SHORT"
        details.append("BB upper + overbought")
        details.extend(overbought_signals)
        if at_upper:
            score += 1.0
            details.append("AT upper band")

    # Se não perto das bandas, penalizar
    elif not near_lower and not near_upper:
        score -= 1.0
        details.append("Preço no meio da BB")

    # ===== 4. Posição relativa na BB =====
    if 0.1 <= bb_pct_4h <= 0.9:
        score += 0.5
        details.append("Preço em zona lateral (BB)")
    elif bb_pct_4h < 0.1 or bb_pct_4h > 0.9:
        score -= 0.5
        details.append("Preço em extremo da BB")

    # ===== 5. Volume =====
    vol_ratio = df_4h.get("vol_ratio", 1.0)
    if vol_ratio >= 0.8:
        score += 0.5
        details.append("Volume OK")
    elif vol_ratio < 0.5:
        score -= 1.0
        details.append("Volume muito baixo")

    # ===== 6. Alinhamento com BTC =====
    # Em lateral, BTC não importa tanto, mas evita contra-tendência forte
    if btc_trend == "BULLISH" and direction == "LONG":
        score += 0.5
        details.append("Alinhado BTC")
    elif btc_trend == "BEARISH" and direction == "SHORT":
        score += 0.5
        details.append("Alinhado BTC")
    elif btc_trend in ("BULLISH", "BEARISH") and direction != "NEUTRAL":
        # Penalidade leve se indo contra tendência do BTC
        if btc_trend == "BULLISH" and direction == "SHORT":
            score -= 0.5
            details.append("Contra BTC")
        elif btc_trend == "BEARISH" and direction == "LONG":
            score -= 0.5
            details.append("Contra BTC")

    return {
        "symbol": symbol,
        "score": round(score, 2),
        "direction": direction,
        "details": details,
        "rsi": rsi_1h,
        "adx": adx_4h,
        "vol_ratio": vol_ratio,
        "bb_pct": bb_pct_4h,
        "near_lower": near_lower,
        "near_upper": near_upper,
        "df_1h": df_1h,
        "df_4h": df_4h
    }


def filter_for_lateral_market(candidates: list, min_score: float = None) -> list:
    """
    Filtra candidatos para mercado lateral.

    Critérios:
    - Score acima do mínimo
    - Direção LONG ou SHORT (não NEUTRAL)
    - ADX < 25 (confirma lateral)
    """
    min_score = min_score or MIN_SCORE_FOR_ENTRY

    filtered = []
    for c in candidates:
        # Score mínimo
        if c["score"] < min_score:
            continue

        # Direção definida
        if c["direction"] not in ("LONG", "SHORT"):
            continue

        # ADX confirma lateral
        if c["adx"] > ADX_MAX_FOR_LATERAL:
            continue

        filtered.append(c)

    return filtered


def get_entry_confidence(score: float, max_score: float = 8.0) -> str:
    """
    Retorna nível de confiança baseado no score.
    """
    if score >= max_score * 0.8:
        return "ALTA"
    elif score >= max_score * 0.6:
        return "MÉDIA-ALTA"
    elif score >= max_score * 0.4:
        return "MÉDIA"
    elif score >= max_score * 0.2:
        return "BAIXA"
    else:
        return "MUITO BAIXA"
