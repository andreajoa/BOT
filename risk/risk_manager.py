# -*- coding: utf-8 -*-
"""
Gerenciador de Risco - Mercado Lateral
Especializado para stop loss estreito e take profit curto.
"""

import math
from typing import Optional, Dict
from config.settings import (
    STOP_LOSS_PCT, TAKE_PROFIT_PCT,
    LEVERAGE, POSITION_SIZE_PCT,
    ENTRY_BALANCE_PCT, MIN_NOTIONAL,
    MIN_BALANCE_USDT, MIN_RISK_REWARD
)


class RiskManager:
    """Gerenciador de risco para mercado lateral."""

    @staticmethod
    def calculate_position_size(
        available_usdt: float,
        entry_price: float,
        leverage: int,
        position_size_pct: float,
        entry_balance_pct: float,
        min_notional: float,
        min_balance: float
    ) -> Optional[float]:
        """
        Calcula tamanho da posição baseado em % do saldo.

        Retorna quantidade em unidades do ativo.
        """
        if available_usdt < min_balance:
            return None

        # Margem para entrada
        entry_margin = available_usdt * entry_balance_pct
        notional = entry_margin * leverage

        # Quantidade
        qty = notional / entry_price

        # Arredondar para 4 casas decimais (padrão crypto)
        qty = round(qty, 4)

        # Verificar notional mínimo
        if qty * entry_price < min_notional:
            return None

        return qty

    @staticmethod
    def calculate_trade_plan_lateral(
        entry_price: float,
        side: str,
        atr: float = None
    ) -> Dict:
        """
        Calcula plano de trade para mercado lateral.

        - Stop Loss: 0.5% (estreito, movimento limitado)
        - Take Profit: 1.0% (curto, lucrar em pequenos movimentos)
        """
        sl_pct = STOP_LOSS_PCT
        tp_pct = TAKE_PROFIT_PCT

        if side == "LONG":
            stop_loss = entry_price * (1 - sl_pct)
            take_profit = entry_price * (1 + tp_pct)
        else:  # SHORT
            stop_loss = entry_price * (1 + sl_pct)
            take_profit = entry_price * (1 - tp_pct)

        # Calcular RR
        risk = abs(entry_price - stop_loss)
        reward = abs(take_profit - entry_price)
        rr_ratio = reward / risk if risk > 0 else MIN_RISK_REWARD

        # Se usar ATR, ajustar SL (mas não menor que mínimo)
        if atr and atr > 0:
            atr_pct = atr / entry_price
            sl_atr = atr * 0.75  # 0.75 ATR para SL
            if side == "LONG":
                sl_atr = entry_price - sl_atr
            else:
                sl_atr = entry_price + sl_atr

            # Usar o maior entre o SL fixo e o SL por ATR
            if side == "LONG":
                stop_loss = max(stop_loss, sl_atr)
            else:
                stop_loss = min(stop_loss, sl_atr)

        return {
            "entry_price": entry_price,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "rr_ratio": rr_ratio,
            "sl_pct": sl_pct,
            "tp_pct": tp_pct
        }

    @staticmethod
    def calculate_position_size_simple(
        available_usdt: float,
        entry_price: float,
        leverage: int
    ) -> float:
        """
        Cálculo simplificado de posição.
        """
        # Usar 50% do saldo disponível
        margin = available_usdt * 0.5
        notional = margin * leverage
        qty = notional / entry_price
        return round(qty, 4)

    @staticmethod
    def validate_entry_conditions(
        score: float,
        min_score: float,
        direction: str,
        adx: float,
        adx_max: float
    ) -> Tuple[bool, str]:
        """
        Valida condições de entrada.

        Retorna (pode_entrar, razão)
        """
        if score < min_score:
            return False, f"Score baixo: {score:.2f} < {min_score}"

        if direction not in ("LONG", "SHORT"):
            return False, f"Direção indefinida: {direction}"

        if adx > adx_max:
            return False, f"ADX alto para lateral: {adx:.1f} > {adx_max}"

        return True, "OK"

    @staticmethod
    def get_effective_rr(
        entry_price: float,
        stop_loss: float,
        take_profit: float,
        side: str,
        leverage: int
    ) -> Tuple[float, float]:
        """
        Calcula RR efetivo considerando fees da Binance.

        Fee: 0.02% (maker) ou 0.04% (taker) por operação
        """
        fee_rate = 0.0004  # 0.04% taker (conservador)

        # Risco efetivo com fee
        risk = abs(entry_price - stop_loss) / entry_price
        risk_with_fee = risk * (1 + fee_rate * leverage)

        # Recompensa efetiva com fee
        reward = abs(take_profit - entry_price) / entry_price
        reward_with_fee = reward * (1 - fee_rate)

        # RR efetivo
        if risk_with_fee > 0:
            rr_eff = reward_with_fee / risk_with_fee
        else:
            rr_eff = 0

        fee_impact = (risk_with_fee + reward_with_fee) - (risk + reward)

        return rr_eff, fee_impact
