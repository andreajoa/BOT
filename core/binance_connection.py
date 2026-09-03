# -*- coding: utf-8 -*-
"""
Conexão Binance - Versão Melhorada
Gerenciador de conexão com API da Binance Futures com melhor tratamento de erros.
"""

import time
from typing import Optional, Dict, Any
from binance.client import Client
from binance.exceptions import BinanceAPIException
from config.settings import (
    BINANCE_API_KEY, BINANCE_API_SECRET,
    REQUEST_TIMEOUT
)


class BinanceConnection:
    """Gerenciador de conexão com Binance."""

    def __init__(self, api_key: str, api_secret: str):
        """Inicializa conexão."""
        self.api_key = api_key
        self.api_secret = api_secret
        self.client = None
        self.time_offset = 0
        self.hedge_mode = False
        self.is_connected = False

    def _create_client(self):
        """Cria e valida o cliente Binance."""
        try:
            self.client = Client(self.api_key, self.api_secret)
            # Testar conexão
            self.client.ping()
            return True, None
        except Exception as e:
            return False, f"Erro ao criar cliente: {str(e)}"

    def connect(self) -> bool:
        """Estabelece conexão com Binance."""
        # Validar API keys
        if not self.api_key or not self.api_secret:
            print("❌ ERRO: API Keys não definidas")
            print("💡 Configure BINANCEAPIKEY e BINANCEAPISECRET no .env")
            return False

        if len(self.api_key) < 10 or len(self.api_secret) < 10:
            print("❌ ERRO: API Keys parecem inválidas (muito curtas)")
            return False

        # Criar cliente e testar
        success, error = self._create_client()
        if not success:
            print(f"❌ ERRO: {error}")
            return False

        # Sincronizar tempo
        self.sync_time()

        # Verificar hedge mode
        self.check_hedge_mode()

        # Verificar permissões Futures
        try:
            account_info = self.client.futures_account()
            self.is_connected = True
            print("✅ Conexão estabelecida com Binance Futures")
            return True
        except BinanceAPIException as e:
            code = e.code
            msg = e.message
            print(f"❌ ERRO DE PERMISSÃO (código {code}): {msg}")
            print("💡 Possíveis causas:")
            print("   1. API Keys não têm permissão de Futures")
            print("   2. IP bloqueado para sua região")
            print("   3. Conta não habilitada para Futures")
            print("   4. Verifique nas configurações da Binance")
            return False
        except Exception as e:
            print(f"❌ ERRO ao conectar: {str(e)}")
            return False

    def sync_time(self):
        """Sincroniza tempo com servidor Binance."""
        try:
            local_before = int(time.time() * 1000)
            server_time = self.client.get_server_time()["serverTime"]
            local_after = int(time.time() * 1000)
            local_mid = (local_before + local_after) // 2
            self.time_offset = server_time - local_mid
            print(f"⏱️ Sincronizado: Offset {self.time_offset:+d}ms")
            return self.time_offset
        except Exception as e:
            print(f"⚠️ Erro ao sincronizar tempo: {e}")
            return 0

    def get_timestamp(self) -> int:
        """Retorna timestamp ajustado."""
        return int(time.time() * 1000) + self.time_offset

    def check_hedge_mode(self):
        """Verifica modo de posição (hedge ou one-way)."""
        try:
            pos_mode = self.client.futures_get_position_mode()
            self.hedge_mode = pos_mode.get("dualSidePosition", False)
            print(f"🔁 Modo de posição: {'HEDGE' if self.hedge_mode else 'ONE-WAY'}")
            return self.hedge_mode
        except Exception as e:
            print(f"⚠️ Erro ao verificar hedge mode: {e}")
            self.hedge_mode = False
            return False

    def get_price(self, symbol: str) -> Optional[float]:
        """Obtém preço atual de um símbolo."""
        try:
            ticker = self.client.futures_symbol_ticker(symbol=symbol)
            return float(ticker["price"])
        except Exception as e:
            print(f"⚠️ Erro ao obter preço {symbol}: {e}")
            return None

    def get_klines(self, symbol: str, interval: str, limit: int = 150):
        """Obtém candles."""
        try:
            klines = self.client.futures_klines(
                symbol=symbol,
                interval=interval,
                limit=limit
            )
            return klines
        except Exception as e:
            print(f"⚠️ Erro ao obter candles {symbol}: {e}")
            return []

    def get_account_balance(self) -> Dict[str, float]:
        """Obtém saldo da conta."""
        try:
            balance = self.client.futures_account_balance()
            return {b['asset']: float(b['availableBalance']) for b in balance}
        except Exception as e:
            print(f"❌ Erro ao obter saldo: {e}")
            return {}

    def get_usdt_balance(self) -> float:
        """Obtém saldo disponível em USDT."""
        try:
            balance = self.client.futures_account_balance()
            usdt = next((b for b in balance if b['asset'] == 'USDT'), None)
            if usdt:
                available = float(usdt['availableBalance'])
                print(f"💰 Saldo USDT disponível: {available:.4f}")

                # Verificar saldo em outras moedas
                other_balance = sum(float(b['availableBalance']) for b in balance if b['asset'] != 'USDT')
                if other_balance > 0:
                    print(f"💰 Saldo em outras moedas: {other_balance:.4f} USDT")

                return available
            print("❌ ERRO: Nenhum saldo USDT encontrado na conta Futures")
            print("💡 Possível causa: A conta está em Spot (não Futures)")
            print("   Verifique nas configurações da Binance e habilite Futures Trading")
            return 0.0
        except Exception as e:
            print(f"❌ Erro ao obter saldo USDT: {e}")
            return 0.0

    def create_market_order(self, symbol: str, side: str, quantity: float) -> Dict[str, Any]:
        """Cria ordem de mercado."""
        try:
            order = self.client.futures_create_order(
                symbol=symbol,
                side=side,
                type="MARKET",
                quantity=quantity
            )
            print(f"📝 Ordem de mercado criada: {symbol} {side} {quantity}")
            return {"success": True, "order": order}
        except BinanceAPIException as e:
            print(f"❌ ERRO ao criar ordem {symbol}: {e.code} - {e.message}")
            return {"success": False, "error": f"{e.code}: {e.message}"}
        except Exception as e:
            print(f"❌ ERRO ao criar ordem {symbol}: {e}")
            return {"success": False, "error": str(e)}

    def create_stop_order(self, symbol: str, side: str, stop_price: float, quantity: float) -> Dict[str, Any]:
        """Cria ordem de stop loss."""
        try:
            order = self.client.futures_create_order(
                symbol=symbol,
                side=side,
                type="STOP_MARKET",
                stopPrice=stop_price,
                closePosition="true",
                quantity=quantity
            )
            print(f"🛑️ Stop Loss criado: {symbol} @ {stop_price}")
            return {"success": True, "order": order}
        except BinanceAPIException as e:
            print(f"❌ ERRO ao criar SL {symbol}: {e.code} - {e.message}")
            return {"success": False, "error": f"{e.code}: {e.message}"}
        except Exception as e:
            print(f"❌ ERRO ao criar SL {symbol}: {e}")
            return {"success": False, "error": str(e)}

    def create_tp_order(self, symbol: str, side: str, tp_price: float, quantity: float) -> Dict[str, Any]:
        """Cria ordem de take profit."""
        try:
            order = self.client.futures_create_order(
                symbol=symbol,
                side=side,
                type="TAKE_PROFIT_MARKET",
                stopPrice=tp_price,
                closePosition="true",
                quantity=quantity
            )
            print(f"🎯 Take Profit criado: {symbol} @ {tp_price}")
            return {"success": True, "order": order}
        except BinanceAPIException as e:
            print(f"❌ ERRO ao criar TP {symbol}: {e.code} - {e.message}")
            return {"success": False, "error": f"{e.code}: {e.message}"}
        except Exception as e:
            print(f"❌ ERRO ao criar TP {symbol}: {e}")
            return {"success": False, "error": str(e)}

    def close_position(self, symbol: str, quantity: float, side: str = "SELL") -> Dict[str, Any]:
        """Fecha posição com ordem de mercado."""
        try:
            # Ajustar side baseado no lado da posição
            order = self.client.futures_create_order(
                symbol=symbol,
                side=side,  # Será ajustado no bot
                type="MARKET",
                quantity=quantity,
                reduceOnly=True
            )
            print(f"🚪 Posição fechada: {symbol} QTY {quantity}")
            return {"success": True, "order": order}
        except BinanceAPIException as e:
            print(f"❌ ERRO ao fechar posição {symbol}: {e.code} - {e.message}")
            return {"success": False, "error": f"{e.code}: {e.message}"}
        except Exception as e:
            print(f"❌ ERRO ao fechar posição {symbol}: {e}")
            return {"success": False, "error": str(e)}

    def cancel_all_orders(self, symbol: str) -> Dict[str, Any]:
        """Cancela todas as ordens de um símbolo."""
        try:
            self.client.futures_cancel_all_open_orders(symbol=symbol)
            print(f"🗑️ Ordens canceladas: {symbol}")
            return {"success": True}
        except BinanceAPIException as e:
            print(f"❌ ERRO ao cancelar ordens {symbol}: {e.code} - {e.message}")
            return {"success": False, "error": f"{e.code}: {e.message}"}
        except Exception as e:
            print(f"❌ ERRO ao cancelar ordens {symbol}: {e}")
            return {"success": False, "error": str(e)}

    def set_leverage(self, symbol: str, leverage: int) -> Dict[str, Any]:
        """Define alavancagem."""
        try:
            self.client.futures_leverage_bracket(symbol=symbol, leverage=leverage)
            print(f"⚖️ Alavancagem definida: {symbol} {leverage}x")
            return {"success": True}
        except BinanceAPIException as e:
            print(f"❌ ERRO ao definir leverage {symbol}: {e.code} - {e.message}")
            return {"success": False, "error": f"{e.code}: {e.message}"}
        except Exception as e:
            print(f"❌ ERRO ao definir leverage {symbol}: {e}")
            return {"success": False, "error": str(e)}

    def get_open_positions(self) -> list:
        """Obtém posições abertas."""
        try:
            positions = self.client.futures_position_information()
            open_pos = [p for p in positions if abs(float(p['positionAmt'])) > 0]
            print(f"📊 Posições abertas: {len(open_pos)}")
            for p in open_pos:
                print(f"   {p['symbol']}: {p['positionSide']} {p['positionAmt']}")
            return open_pos
        except Exception as e:
            print(f"❌ Erro ao obter posições: {e}")
            return []
