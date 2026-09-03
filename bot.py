# -*- coding: utf-8 -*-
"""
Crypto Trader Bot - Mercado Lateral (AUTO-DETECT)
Bot 24/7 que detecta automaticamente quando o mercado está em lateral
e opera apenas nesses momentos. Quando em tendência, fica em STAND BY.
"""

import os
import json
import time
import pandas as pd
from datetime import datetime, UTC
from typing import Dict, List, Optional
from enum import Enum

# Módulos internos
from config.settings import *
from core.binance_connection import BinanceConnection
from indicators.technical import LateralIndicators, is_in_lateral_zone, get_reversal_signal
from filters.lateral_filter import score_lateral_entry, filter_for_lateral_market, get_entry_confidence
from risk.risk_manager import RiskManager
from utils.logger import Logger


class BotState(Enum):
    """Estados do bot."""
    STAND_BY = "STAND_BY"      # Aguardando mercado lateral
    ACTIVE = "ACTIVE"          # Operando em mercado lateral
    ANALYZING = "ANALYZING"    # Analisando estado do mercado


class LateralMarketBot:
    """Bot 24/7 com detecção automática de mercado lateral."""

    def __init__(self, logger: Optional[Logger] = None):
        """Inicializa o bot."""
        self.logger = logger or Logger("logs")
        self.logger.info("INIT", "Inicializando LateralMarketBot 24/7...")

        # Conexão Binance
        if not BINANCE_API_KEY or not BINANCE_API_SECRET:
            self.logger.warn("INIT", "API keys não encontradas - modo PAPER ativado")
            global BOT_MODE
            BOT_MODE = "paper"
            self.conn = None
        else:
            self.conn = BinanceConnection(BINANCE_API_KEY, BINANCE_API_SECRET)

        # Gerenciador de risco
        self.risk = RiskManager()

        # Estado do bot
        self.bot_state = BotState.ANALYZING
        self.state_history = []  # Histórico de estados
        self.market_analysis = {
            "btc_lateral": False,
            "last_check": 0,
            "lateral_duration": 0,  # Quanto tempo está em lateral
            "trend_duration": 0     # Quanto tempo está em tendência
        }

        # Estado
        self.active_positions: Dict[str, Dict] = {}
        self.symbol_cooldown: Dict[str, float] = {}
        self.entry_cooldown: Dict[str, float] = {}
        self.running = False
        self.last_scan_time = 0
        self.last_market_check = 0
        self.last_state_change = time.time()

        # Estatísticas por estado
        self.session_stats = {
            'start_time': datetime.now(UTC).isoformat(),
            'scans': 0,
            'standby_scans': 0,
            'active_scans': 0,
            'entries': 0,
            'exits': 0,
            'wins': 0,
            'losses': 0,
            'total_pnl_usdt': 0.0,
            'standby_duration': 0,
            'active_duration': 0
        }

        # Criar diretórios
        os.makedirs(LOG_DIR, exist_ok=True)

    def get_klines(self, symbol: str, interval: str = "1h") -> Optional[pd.DataFrame]:
        """Obtém candles e calcula indicadores."""
        if self.conn:
            klines = self.conn.get_klines(symbol, interval, KLINE_LIMIT)
        else:
            return None

        if not klines or len(klines) < 30:
            return None

        df = pd.DataFrame(klines, columns=[
            "ts", "open", "high", "low", "close", "volume",
            "close_time", "qv", "trades", "tbb", "tbq", "ignore"
        ])

        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = df[col].astype(float)

        df = LateralIndicators.calculate_all(
            df,
            bb_period=BB_PERIOD,
            bb_std=BB_STD_DEV,
            rsi_period=RSI_PERIOD,
            stoch_k=STOCH_K_PERIOD,
            stoch_d=STOCH_D_PERIOD,
            williams_period=WILLIAMS_PERIOD,
            sr_lookback=SR_LOOKBACK
        )

        return df

    def analyze_market_state(self) -> Dict:
        """
        Analisa se o mercado está em lateral ou tendência.

        Critérios para LATERAL:
        - BTC ADX < 25
        - BTC BB width entre 2% e 6%
        - BTC não em extremo da BB (10% < BB_pct < 90%)
        - BTC EMAs entrelaçadas (sem tendência clara)

        Critérios para TENDÊNCIA:
        - BTC ADX >= 25 OU
        - BTC EMAs alinhadas (BULLISH ou BEARISH) OU
        - Grandes movimentos de preço
        """
        # Obter dados BTC
        btc_df_1h = self.get_klines(BTC_SYMBOL, "1h")
        btc_df_4h = self.get_klines(BTC_SYMBOL, "4h")

        if btc_df_4h is None:
            return {"is_lateral": False, "confidence": 0, "reason": "Dados BTC indisponíveis"}

        last_btc_1h = btc_df_1h.iloc[-1].to_dict()
        last_btc_4h = btc_df_4h.iloc[-1].to_dict()

        # Critérios
        adx_4h = last_btc_4h.get("adx", 0)
        bb_width_4h = last_btc_4h.get("bb_width", 0)
        bb_pct_4h = last_btc_4h.get("bb_pct", 0.5)
        ema_9 = last_btc_4h.get("ema_9", 0)
        ema_21 = last_btc_4h.get("ema_21", 0)
        ema_50 = last_btc_4h.get("ema_50", 0)

        # Calcular pontuação de lateralidade
        lateral_score = 0
        reasons = []

        # 1. ADX (peso alto)
        if adx_4h < 20:
            lateral_score += 4
            reasons.append(f"ADX baixo: {adx_4h:.1f}")
        elif adx_4h < 25:
            lateral_score += 2
            reasons.append(f"ADX moderado: {adx_4h:.1f}")
        else:
            lateral_score -= 3
            reasons.append(f"ADX alto (tendência): {adx_4h:.1f}")

        # 2. BB width (peso médio)
        if 0.02 <= bb_width_4h <= 0.06:
            lateral_score += 3
            reasons.append(f"BB width adequada: {bb_width_4h*100:.1f}%")
        elif bb_width_4h < 0.015:
            lateral_score -= 2
            reasons.append(f"BB muito estreita: {bb_width_4h*100:.1f}%")
        elif bb_width_4h > 0.08:
            lateral_score -= 1
            reasons.append(f"BB muito larga: {bb_width_4h*100:.1f}%")

        # 3. Posição na BB (peso alto)
        if 0.2 <= bb_pct_4h <= 0.8:
            lateral_score += 3
            reasons.append(f"Preço no meio da BB")
        elif bb_pct_4h < 0.1 or bb_pct_4h > 0.9:
            lateral_score -= 2
            reasons.append(f"Preço em extremo da BB")

        # 4. EMAs (peso alto)
        # Se EMAs estão alinhadas = tendência
        if ema_9 > ema_21 > ema_50:
            lateral_score -= 3
            reasons.append("EMAs: ALTA (tendência de alta)")
        elif ema_9 < ema_21 < ema_50:
            lateral_score -= 3
            reasons.append("EMAs: BAIXA (tendência de baixa)")
        else:
            lateral_score += 2
            reasons.append("EMAs: ENTRELAÇADAS (lateral)")

        # Determinar estado
        is_lateral = lateral_score >= 6  # Threshold ajustável

        confidence = min(100, max(0, int(lateral_score * 10)))  # 0-100%

        return {
            "is_lateral": is_lateral,
            "confidence": confidence,
            "score": lateral_score,
            "reasons": reasons,
            "adx": adx_4h,
            "bb_width": bb_width_4h,
            "bb_pct": bb_pct_4h,
            "ema_trend": "ALTA" if ema_9 > ema_21 > ema_50 else ("BAIXA" if ema_9 < ema_21 < ema_50 else "NEUTRA")
        }

    def update_bot_state(self, market_analysis: Dict):
        """
        Atualiza estado do bot baseado na análise de mercado.

        Estados:
        - STAND_BY: Mercado em tendência, aguardando lateral
        - ACTIVE: Mercado em lateral, buscando oportunidades
        """
        is_lateral = market_analysis["is_lateral"]
        confidence = market_analysis["confidence"]

        # Estado atual
        current_state = self.bot_state

        # Determinar novo estado
        if is_lateral and confidence >= 60:
            new_state = BotState.ACTIVE
        else:
            new_state = BotState.STAND_BY

        # Verificar se mudou de estado
        if new_state != current_state:
            self.change_state(new_state, market_analysis)

        # Atualizar durações
        now = time.time()
        if current_state == BotState.STAND_BY:
            self.session_stats["standby_duration"] += (now - self.last_state_change)
        elif current_state == BotState.ACTIVE:
            self.session_stats["active_duration"] += (now - self.last_state_change)

        self.last_state_change = now

    def change_state(self, new_state: BotState, analysis: Dict):
        """Muda estado do bot e loga."""
        old_state = self.bot_state
        self.bot_state = new_state

        # Registar no histórico
        self.state_history.append({
            "timestamp": datetime.now(UTC).isoformat(),
            "from_state": old_state.value,
            "to_state": new_state.value,
            "analysis": analysis
        })

        # Log
        state_emoji = {
            BotState.STAND_BY: "⏸️",
            BotState.ACTIVE: "▶️",
            BotState.ANALYZING: "🔍"
        }

        if new_state == BotState.ACTIVE:
            self.logger.success("ESTADO",
                f"{state_emoji[new_state]} MUDOU PARA: {new_state.value}")
            self.logger.info("MERCADO", "MERCADO EM LATERAL DETECTADO")
            self.logger.info("ANÁLISE", f"Confiança: {analysis['confidence']}%")
            self.logger.info("CRITÉRIOS", f"ADX={analysis['adx']:.1f} | BB={analysis['bb_width']*100:.1f}% | "
                             f"BB_pct={analysis['bb_pct']*100:.0f}% | EMAs={analysis['ema_trend']}")
            self.logger.divider("BUSCANDO OPORTUNIDADES")
        elif new_state == BotState.STAND_BY:
            self.logger.warn("ESTADO",
                f"{state_emoji[new_state]} MUDOU PARA: {new_state.value}")
            self.logger.info("MERCADO", "MERCADO EM TENDÊNCIA - AGUARDANDO LATERAL")
            self.logger.info("ANÁLISE", f"Confiança: {analysis['confidence']}%")
            self.logger.info("CRITÉRIOS", f"ADX={analysis['adx']:.1f} | BB={analysis['bb_width']*100:.1f}% | "
                             f"BB_pct={analysis['bb_pct']*100:.0f}% | EMAs={analysis['ema_trend']}")
            self.logger.divider("MODO STAND BY - MONITORANDO MERCADO")

    def get_market_data(self, for_analysis: bool = False) -> Dict:
        """Obtém dados de mercado."""
        data = {
            "btc_1h": None,
            "btc_4h": None,
            "btc_trend": "NEUTRAL",
            "candidates": []
        }

        # Obter dados BTC
        btc_df_1h = self.get_klines(BTC_SYMBOL, "1h")
        btc_df_4h = self.get_klines(BTC_SYMBOL, "4h")

        if btc_df_4h is not None:
            last_btc = btc_df_4h.iloc[-1].to_dict()
            data["btc_4h"] = last_btc
            data["btc_trend"] = get_reversal_signal(last_btc)

        if btc_df_1h is not None:
            data["btc_1h"] = btc_df_1h.iloc[-1].to_dict()

        # Se apenas analisando, retorna dados BTC
        if for_analysis:
            return data

        # Se buscando oportunidades, analisa candidatos
        btc_trend = data["btc_trend"]

        for symbol in WATCHLIST_FALLBACK:
            if symbol in WATCHLIST_BLACKLIST:
                continue

            if symbol in self.active_positions:
                continue

            # Verificar cooldown
            if symbol in self.entry_cooldown:
                if time.time() < self.entry_cooldown[symbol]:
                    continue
                else:
                    del self.entry_cooldown[symbol]

            df_4h = self.get_klines(symbol, "4h")
            df_1h = self.get_klines(symbol, "1h")

            if df_4h is None or df_1h is None:
                continue

            last_4h = df_4h.iloc[-1].to_dict()
            last_1h = df_1h.iloc[-1].to_dict()

            # Verificar se está em zona lateral
            if not is_in_lateral_zone(last_4h):
                continue

            # Score do símbolo
            score_data = score_lateral_entry(
                last_1h, last_4h,
                btc_trend, symbol
            )
            score_data["df_4h"] = last_4h
            score_data["df_1h"] = last_1h

            data["candidates"].append(score_data)

        # Filtrar candidatos
        data["candidates"] = filter_for_lateral_market(data["candidates"], MIN_SCORE_FOR_ENTRY)

        # Ordenar por score
        data["candidates"].sort(key=lambda x: x["score"], reverse=True)

        return data

    def can_open_position(self, candidate: Dict) -> tuple[bool, str]:
        """Verifica se pode abrir posição."""
        # Validações básicas
        can_enter, reason = self.risk.validate_entry_conditions(
            candidate["score"],
            MIN_SCORE_FOR_ENTRY,
            candidate["direction"],
            candidate["adx"],
            ADX_MAX_FOR_LATERAL
        )

        if not can_enter:
            return False, reason

        # Verificar cooldown
        symbol = candidate["symbol"]
        if symbol in self.entry_cooldown and time.time() < self.entry_cooldown[symbol]:
            return False, "Símbolo em cooldown"

        return True, "OK"

    def open_position(self, candidate: Dict):
        """Abre uma posição."""
        try:
            symbol = candidate["symbol"]
            direction = candidate["direction"]
            df_4h = candidate["df_4h"]
            entry_price = float(df_4h["close"])

            self.logger.divider("ABRINDO POSIÇÃO")

            # Calcular tamanho da posição
            if self.conn:
                available_usdt = self.conn.get_usdt_balance()
                qty = self.risk.calculate_position_size(
                    available_usdt=available_usdt,
                    entry_price=entry_price,
                    leverage=LEVERAGE,
                    position_size_pct=POSITION_SIZE_PCT,
                    entry_balance_pct=ENTRY_BALANCE_PCT,
                    min_notional=MIN_NOTIONAL,
                    min_balance=MIN_BALANCE_USDT
                )
            else:
                qty = 0.01  # Padrão para paper

            if qty is None:
                self.logger.warn("ENTRY", f"Saldo insuficiente para {symbol}")
                return

            # Calcular plano de trade
            plan = self.risk.calculate_trade_plan_lateral(entry_price, direction)

            # Calcular RR efetivo
            rr_eff, _ = self.risk.get_effective_rr(
                entry_price, plan["stop_loss"], plan["take_profit"], direction, LEVERAGE
            )

            if rr_eff < MIN_RISK_REWARD:
                self.logger.warn("ENTRY", f"{symbol} RR baixo: {rr_eff:.2f}")
                return

            # Montar posição
            confidence = get_entry_confidence(candidate["score"])

            position = {
                "symbol": symbol,
                "side": direction,
                "entry_price": entry_price,
                "qty": qty,
                "leverage": LEVERAGE,
                "stop_loss": plan["stop_loss"],
                "take_profit": plan["take_profit"],
                "rr_ratio": rr_eff,
                "open_time": datetime.now(UTC).isoformat(),
                "open_reason": " | ".join(candidate["details"]),
                "confidence": confidence
            }

            # Executar ordem (LIVE)
            if self.conn and BOT_MODE == "live":
                try:
                    # Definir alavancagem
                    self.conn.set_leverage(symbol, LEVERAGE)

                    # Ordem principal
                    order_result = self.conn.create_market_order(
                        symbol,
                        "BUY" if direction == "LONG" else "SELL",
                        qty
                    )

                    if not order_result["success"]:
                        self.logger.error("ENTRY", f"Erro ordem {symbol}: {order_result['error']}")
                        return

                    entry_price = float(order_result["order"]["avgPrice"]) if "avgPrice" in order_result["order"] else entry_price

                    # Stop Loss
                    sl_result = self.conn.create_stop_order(
                        symbol,
                        "SELL" if direction == "LONG" else "BUY",
                        plan["stop_loss"],
                        qty
                    )
                    if not sl_result["success"]:
                        self.logger.warn("ENTRY", f"Erro SL {symbol}: {sl_result['error']}")

                    # Take Profit
                    tp_result = self.conn.create_tp_order(
                        symbol,
                        "SELL" if direction == "LONG" else "BUY",
                        plan["take_profit"],
                        qty
                    )
                    if not tp_result["success"]:
                        self.logger.warn("ENTRY", f"Erro TP {symbol}: {tp_result['error']}")

                    position["paper"] = False

                except Exception as e:
                    self.logger.error("ENTRY", f"Erro ao executar {symbol}: {e}")
                    return
            else:
                position["paper"] = True
                self.logger.info("ENTRY", "Modo PAPER - ordem não executada")

            # Registrar posição
            self.active_positions[symbol] = position
            self.entry_cooldown[symbol] = time.time() + COOLDOWN_AFTER_ENTRY_SECONDS
            self.session_stats["entries"] += 1

            # Log
            self.logger.trade(
                symbol, direction, entry_price,
                plan["stop_loss"], plan["take_profit"],
                position["open_reason"], confidence
            )

            # Salvar
            self.save_positions()

        except Exception as e:
            self.logger.error("ENTRY", f"Erro ao abrir posição: {e}")

    def check_positions(self):
        """Verifica posições abertas."""
        if not self.active_positions:
            return

        price_cache = {}

        for symbol, pos in list(self.active_positions.items()):
            try:
                # Obter preço atual
                if symbol not in price_cache:
                    if self.conn:
                        price = self.conn.get_price(symbol)
                    else:
                        price = pos["entry_price"]  # Paper mode
                    price_cache[symbol] = price

                current = price_cache[symbol]
                side = pos["side"]
                entry = pos["entry_price"]
                sl = pos["stop_loss"]
                tp = pos["take_profit"]

                # Verificar saída
                close_reason = None

                if side == "LONG":
                    if current <= sl:
                        close_reason = "STOP_LOSS"
                    elif current >= tp:
                        close_reason = "TAKE_PROFIT"
                else:  # SHORT
                    if current >= sl:
                        close_reason = "STOP_LOSS"
                    elif current <= tp:
                        close_reason = "TAKE_PROFIT"

                if close_reason:
                    self.close_position(symbol, close_reason, current)

            except Exception as e:
                self.logger.debug("POS", f"Erro check {symbol}: {e}")

    def close_position(self, symbol: str, reason: str, exit_price: float):
        """Fecha uma posição."""
        pos = self.active_positions.get(symbol)
        if not pos:
            return

        try:
            # Calcular PnL
            if pos["side"] == "LONG":
                pnl_pct = (exit_price - pos["entry_price"]) / pos["entry_price"]
            else:
                pnl_pct = (pos["entry_price"] - exit_price) / pos["entry_price"]

            pnl_usdt = pos["entry_price"] * pos["qty"] * pnl_pct

            # Fechar posição real (LIVE)
            if self.conn and BOT_MODE == "live" and not pos.get("paper"):
                try:
                    self.conn.cancel_all_orders(symbol)

                    close_result = self.conn.close_position(symbol, pos["qty"])
                    if not close_result["success"]:
                        self.logger.warn("EXIT", f"Erro fechar {symbol}: {close_result['error']}")
                except Exception as e:
                    self.logger.error("EXIT", f"Erro fechar {symbol}: {e}")

            # Atualizar stats
            is_win = pnl_usdt > 0
            self.session_stats["exits"] += 1
            if is_win:
                self.session_stats["wins"] += 1
            else:
                self.session_stats["losses"] += 1
            self.session_stats["total_pnl_usdt"] += pnl_usdt

            # Cooldown
            self.symbol_cooldown[symbol] = time.time() + COOLDOWN_AFTER_CLOSE_SECONDS

            # Log
            self.logger.exit(symbol, pos["side"], pos["entry_price"], exit_price, reason, pnl_usdt)

            # Salvar no histórico
            trade_record = {
                "symbol": symbol,
                "side": pos["side"],
                "entry_price": pos["entry_price"],
                "exit_price": exit_price,
                "pnl_pct": round(pnl_pct, 4),
                "pnl_usdt": round(pnl_usdt, 4),
                "rr_ratio": pos.get("rr_ratio", 0),
                "close_reason": reason,
                "open_time": pos["open_time"],
                "closed_at": datetime.now(UTC).isoformat(),
                "bot_state": self.bot_state.value,
                "confidence": pos.get("confidence", "N/A")
            }

            with open(os.path.join(LOG_DIR, CLOSED_TRADES_LOG), "a", encoding="utf-8") as f:
                f.write(json.dumps(trade_record, ensure_ascii=False) + "\n")

            # Remover posição
            del self.active_positions[symbol]
            self.save_positions()

        except Exception as e:
            self.logger.error("EXIT", f"Erro ao fechar {symbol}: {e}")

    def save_positions(self):
        """Salva posições ativas."""
        try:
            path = os.path.join(LOG_DIR, ACTIVE_POSITION_FILE)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self.active_positions, f, indent=2, default=str)
        except Exception as e:
            self.logger.debug("POS", f"Erro salvar posições: {e}")

    def run(self):
        """
        Loop principal do bot 24/7.

        O bot NUNCA PARA! Ele fica rodando sempre:
        - Em STAND_BY: Monitora mercado, não abre posições
        - Em ACTIVE: Busca e abre oportunidades em lateral
        - Sempre: Gerencia posições abertas
        """
        self.logger.divider("INICIANDO BOT 24/7 - MERCADO LATERAL AUTO-DETECT")
        self.logger.info("INFO", "Bot rodará 24/7 detectando automaticamente mercado lateral")
        self.logger.info("MODE", f"Modo: {BOT_MODE.upper()}")
        self.logger.info("CONFIG", f"Leverage: {LEVERAGE}x | Position Size: {POSITION_SIZE_PCT*100:.0f}%")
        self.logger.info("PARAM", f"SL: {STOP_LOSS_PCT*100:.1f}% | TP: {TAKE_PROFIT_PCT*100:.1f}%")

        # Conectar
        if self.conn:
            if not self.conn.connect():
                self.logger.error("INIT", "Falha na conexão. Encerrando.")
                return

            # Verificar saldo
            balance = self.conn.get_usdt_balance()
            self.logger.info("BALANCE", f"Saldo: {balance:.4f} USDT")

            # Verificar hedge mode
            hedge_mode = self.conn.check_hedge_mode()
            self.logger.info("MODE", f"Modo: {'HEDGE' if hedge_mode else 'ONE-WAY'}")

        self.running = True
        last_position_check = 0
        last_market_check = 0
        last_stats_save = 0

        try:
            while self.running:
                now = time.time()

                # ===== 1. CHECAR POSIÇÕES SEMPRE (independente do estado) =====
                if now - last_position_check >= POSITION_CHECK_INTERVAL_SECONDS:
                    last_position_check = now
                    self.check_positions()

                # ===== 2. ANÁLISE DO MERCADO (a cada 60 segundos) =====
                if now - last_market_check >= SCAN_INTERVAL_SECONDS:
                    last_market_check = now
                    self.last_scan_time = now
                    self.session_stats["scans"] += 1

                    # Analisar estado do mercado
                    market_analysis = self.analyze_market_state()
                    self.update_bot_state(market_analysis)

                    # Log status atual
                    state_emoji = {
                        BotState.STAND_BY: "⏸️",
                        BotState.ACTIVE: "▶️"
                    }

                    if self.bot_state == BotState.STAND_BY:
                        self.session_stats["standby_scans"] += 1
                        self.logger.info("SCAN", f"⏸️ [STAND BY] Scan #{self.session_stats['scans']} | "
                                         f"Posições: {len(self.active_positions)} | "
                                         f"Confiança lateral: {market_analysis['confidence']}%")
                    else:  # ACTIVE
                        self.session_stats["active_scans"] += 1
                        self.logger.info("SCAN", f"▶️ [ACTIVE] Scan #{self.session_stats['scans']} | "
                                         f"Posições: {len(self.active_positions)} | "
                                         f"Confiança lateral: {market_analysis['confidence']}%")

                        # ===== 3. BUSCAR OPORTUNIDADES (só em ACTIVE) =====
                        if len(self.active_positions) < MAX_OPEN_POSITIONS:
                            market_data = self.get_market_data(for_analysis=False)

                            if market_data["candidates"]:
                                # Logar top 3 candidatos
                                for i, c in enumerate(market_data["candidates"][:3]):
                                    direction_emoji = "📈" if c["direction"] == "LONG" else "📉"
                                    self.logger.info("CANDIDATO",
                                        f"{i+1}. {direction_emoji} {c['symbol']} {c['direction']} "
                                        f"score={c['score']:.1f} RSI={c['rsi']:.0f} "
                                        f"ADX={c['adx']:.1f} BB={c['bb_pct']*100:.0f}%")

                                # Tentar abrir posição no melhor candidato
                                best = market_data["candidates"][0]

                                if best["direction"] in ("LONG", "SHORT"):
                                    can_open, reason = self.can_open_position(best)
                                    if can_open:
                                        self.open_position(best)
                                    else:
                                        self.logger.info("SCAN", f"{best['symbol']} rejeitado: {reason}")
                            else:
                                self.logger.info("SCAN", "Nenhum candidato qualificado")
                        else:
                            self.logger.info("SCAN", "Nenhum candidato qualificado")

                # ===== 4. SALVAR ESTATÍSTICAS (a cada 5 minutos) =====
                if now - last_stats_save >= 300:
                    last_stats_save = now
                    self.save_statistics()

                time.sleep(1)

        except KeyboardInterrupt:
            self.logger.info("STOP", "Bot interrompido pelo usuário")

        finally:
            self.print_summary()
            self.save_positions()
            self.save_statistics()

    def save_statistics(self):
        """Salva estatísticas em arquivo."""
        try:
            stats_file = os.path.join(LOG_DIR, "session_stats.json")
            with open(stats_file, "w", encoding="utf-8") as f:
                json.dump(self.session_stats, f, indent=2, default=str)
        except Exception as e:
            self.logger.debug("STATS", f"Erro salvar estatísticas: {e}")

    def print_summary(self):
        """Imprime resumo da sessão."""
        duration = (datetime.now(UTC) - datetime.fromisoformat(self.session_stats["start_time"])).total_seconds()

        self.logger.divider("RESUMO DA SESSÃO")
        self.logger.info("STATS", f"Duração: {duration/3600:.1f}h")
        self.logger.info("STATS", f"Scans totais: {self.session_stats['scans']}")
        self.logger.info("STATS", f"Scans STAND BY: {self.session_stats['standby_scans']}")
        self.logger.info("STATS", f"Scans ACTIVE: {self.session_stats['active_scans']}")
        self.logger.info("STATS", f"Entradas: {self.session_stats['entries']}")
        self.logger.info("STATS", f"Saídas: {self.session_stats['exits']}")
        self.logger.info("STATS", f"Wins: {self.session_stats['wins']} | Losses: {self.session_stats['losses']}")

        if self.session_stats["exits"] > 0:
            wr = self.session_stats["wins"] / self.session_stats["exits"] * 100
            self.logger.info("STATS", f"Win Rate: {wr:.1f}%")

        self.logger.info("STATS", f"PnL Total: ${self.session_stats['total_pnl_usdt']:+.4f}")

        # Tempo em cada estado
        standby_h = self.session_stats["standby_duration"] / 3600
        active_h = self.session_stats["active_duration"] / 3600
        self.logger.info("STATS", f"Tempo STAND BY: {standby_h:.2f}h")
        self.logger.info("STATS", f"Tempo ACTIVE: {active_h:.2f}h")

        self.logger.divider("")
