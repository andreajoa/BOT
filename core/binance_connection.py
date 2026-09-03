# -*- coding: utf-8 -*-
"""
Conexao Binance Futures.

Esta camada NAO escolhe estrategia. Ela apenas traduz comandos de execucao
para a Binance, respeitando One-Way/Hedge Mode e retornando resultados
estruturados para a camada superior.
"""

import time
from typing import Optional, Dict, Any

from binance.client import Client
from binance.exceptions import BinanceAPIException

from config.settings import BINANCE_API_KEY, BINANCE_API_SECRET, REQUEST_TIMEOUT


class BinanceConnection:
    """Gerenciador de conexao e execucao na Binance USD-M Futures."""

    def __init__(self, api_key: str, api_secret: str):
        self.api_key = api_key
        self.api_secret = api_secret
        self.client = None
        self.time_offset = 0
        self.hedge_mode = False
        self.is_connected = False

    def _create_client(self):
        try:
            self.client = Client(self.api_key, self.api_secret)
            self.client.ping()
            return True, None
        except Exception as e:
            return False, f"Erro ao criar cliente: {e}"

    def connect(self) -> bool:
        if not self.api_key or not self.api_secret:
            print("❌ ERRO: API Keys nao definidas")
            print("💡 Configure BINANCEAPIKEY e BINANCEAPISECRET no .env")
            return False

        if len(self.api_key) < 10 or len(self.api_secret) < 10:
            print("❌ ERRO: API Keys parecem invalidas (muito curtas)")
            return False

        success, error = self._create_client()
        if not success:
            print(f"❌ ERRO: {error}")
            return False

        self.sync_time()
        self.check_hedge_mode()

        try:
            self.client.futures_account()
            self.is_connected = True
            print("✅ Conexao estabelecida com Binance Futures")
            return True
        except BinanceAPIException as e:
            print(f"❌ ERRO DE PERMISSAO (codigo {e.code}): {e.message}")
            return False
        except Exception as e:
            print(f"❌ ERRO ao conectar: {e}")
            return False

    def sync_time(self):
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
        return int(time.time() * 1000) + self.time_offset

    def check_hedge_mode(self):
        try:
            pos_mode = self.client.futures_get_position_mode()
            self.hedge_mode = bool(pos_mode.get("dualSidePosition", False))
            print(f"🔁 Modo de posicao: {'HEDGE' if self.hedge_mode else 'ONE-WAY'}")
            return self.hedge_mode
        except Exception as e:
            print(f"⚠️ Erro ao verificar hedge mode: {e}")
            self.hedge_mode = False
            return False

    @staticmethod
    def _validate_position_side(position_side: Optional[str]) -> Optional[str]:
        if position_side is None:
            return None
        normalized = str(position_side).upper()
        if normalized not in {"LONG", "SHORT", "BOTH"}:
            raise ValueError(f"position_side invalido: {position_side}")
        return normalized

    def _position_params(self, position_side: Optional[str]) -> Dict[str, Any]:
        """Inclui positionSide somente quando necessario/seguro."""
        normalized = self._validate_position_side(position_side)
        if self.hedge_mode:
            if normalized not in {"LONG", "SHORT"}:
                raise ValueError("Hedge Mode exige position_side LONG ou SHORT")
            return {"positionSide": normalized}
        return {}

    def get_price(self, symbol: str) -> Optional[float]:
        try:
            ticker = self.client.futures_symbol_ticker(symbol=symbol)
            return float(ticker["price"])
        except Exception as e:
            print(f"⚠️ Erro ao obter preco {symbol}: {e}")
            return None

    def get_klines(self, symbol: str, interval: str, limit: int = 150):
        try:
            return self.client.futures_klines(symbol=symbol, interval=interval, limit=limit)
        except Exception as e:
            print(f"⚠️ Erro ao obter candles {symbol}: {e}")
            return []

    def get_account_balance(self) -> Dict[str, float]:
        try:
            balance = self.client.futures_account_balance()
            return {b["asset"]: float(b["availableBalance"]) for b in balance}
        except Exception as e:
            print(f"❌ Erro ao obter saldo: {e}")
            return {}

    def get_usdt_balance(self) -> float:
        try:
            balance = self.client.futures_account_balance()
            usdt = next((b for b in balance if b["asset"] == "USDT"), None)
            if usdt:
                available = float(usdt["availableBalance"])
                print(f"💰 Saldo USDT disponivel: {available:.4f}")
                return available
            print("❌ ERRO: Nenhum saldo USDT encontrado na conta Futures")
            return 0.0
        except Exception as e:
            print(f"❌ Erro ao obter saldo USDT: {e}")
            return 0.0

    def create_market_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        position_side: Optional[str] = None,
        reduce_only: bool = False,
    ) -> Dict[str, Any]:
        """Cria MARKET e pede RESULT para receber o fill final."""
        try:
            params: Dict[str, Any] = {
                "symbol": symbol,
                "side": side.upper(),
                "type": "MARKET",
                "quantity": quantity,
                "newOrderRespType": "RESULT",
            }
            params.update(self._position_params(position_side))

            # reduceOnly nao pode ser enviado em Hedge Mode.
            if reduce_only and not self.hedge_mode:
                params["reduceOnly"] = True

            order = self.client.futures_create_order(**params)
            print(f"📝 Ordem MARKET criada: {symbol} {side.upper()} {quantity}")
            return {"success": True, "order": order}
        except BinanceAPIException as e:
            print(f"❌ ERRO ao criar ordem {symbol}: {e.code} - {e.message}")
            return {"success": False, "error": f"{e.code}: {e.message}"}
        except Exception as e:
            print(f"❌ ERRO ao criar ordem {symbol}: {e}")
            return {"success": False, "error": str(e)}

    def create_stop_order(
        self,
        symbol: str,
        side: str,
        stop_price: float,
        position_side: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Cria STOP_MARKET Close-All. Nao envia quantity com closePosition=true."""
        try:
            params: Dict[str, Any] = {
                "symbol": symbol,
                "side": side.upper(),
                "type": "STOP_MARKET",
                "stopPrice": stop_price,
                "closePosition": "true",
                "workingType": "MARK_PRICE",
            }
            params.update(self._position_params(position_side))
            order = self.client.futures_create_order(**params)
            print(f"🛑 Stop Loss criado: {symbol} @ {stop_price}")
            return {"success": True, "order": order}
        except BinanceAPIException as e:
            print(f"❌ ERRO ao criar SL {symbol}: {e.code} - {e.message}")
            return {"success": False, "error": f"{e.code}: {e.message}"}
        except Exception as e:
            print(f"❌ ERRO ao criar SL {symbol}: {e}")
            return {"success": False, "error": str(e)}

    def create_tp_order(
        self,
        symbol: str,
        side: str,
        tp_price: float,
        position_side: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Cria TAKE_PROFIT_MARKET Close-All. Nao envia quantity com closePosition=true."""
        try:
            params: Dict[str, Any] = {
                "symbol": symbol,
                "side": side.upper(),
                "type": "TAKE_PROFIT_MARKET",
                "stopPrice": tp_price,
                "closePosition": "true",
                "workingType": "MARK_PRICE",
            }
            params.update(self._position_params(position_side))
            order = self.client.futures_create_order(**params)
            print(f"🎯 Take Profit criado: {symbol} @ {tp_price}")
            return {"success": True, "order": order}
        except BinanceAPIException as e:
            print(f"❌ ERRO ao criar TP {symbol}: {e.code} - {e.message}")
            return {"success": False, "error": f"{e.code}: {e.message}"}
        except Exception as e:
            print(f"❌ ERRO ao criar TP {symbol}: {e}")
            return {"success": False, "error": str(e)}

    def close_position(
        self,
        symbol: str,
        quantity: float,
        position_side: str,
    ) -> Dict[str, Any]:
        """Fecha explicitamente LONG ou SHORT com MARKET."""
        try:
            normalized = self._validate_position_side(position_side)
            if normalized not in {"LONG", "SHORT"}:
                raise ValueError("close_position exige LONG ou SHORT")

            close_side = "SELL" if normalized == "LONG" else "BUY"
            params: Dict[str, Any] = {
                "symbol": symbol,
                "side": close_side,
                "type": "MARKET",
                "quantity": quantity,
                "newOrderRespType": "RESULT",
            }
            params.update(self._position_params(normalized))
            if not self.hedge_mode:
                params["reduceOnly"] = True

            order = self.client.futures_create_order(**params)
            print(f"🚪 Posicao {normalized} fechada: {symbol} QTY {quantity}")
            return {"success": True, "order": order}
        except BinanceAPIException as e:
            print(f"❌ ERRO ao fechar posicao {symbol}: {e.code} - {e.message}")
            return {"success": False, "error": f"{e.code}: {e.message}"}
        except Exception as e:
            print(f"❌ ERRO ao fechar posicao {symbol}: {e}")
            return {"success": False, "error": str(e)}

    def cancel_all_orders(self, symbol: str) -> Dict[str, Any]:
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
        """Define leverage usando o endpoint TRADE correto: POST /fapi/v1/leverage."""
        try:
            if not 1 <= int(leverage) <= 125:
                return {"success": False, "error": f"Leverage invalido: {leverage}"}

            result = self.client.futures_change_leverage(symbol=symbol, leverage=int(leverage))
            actual = int(result.get("leverage", leverage))
            print(f"⚖️ Alavancagem definida: {symbol} {actual}x")
            return {"success": True, "result": result, "leverage": actual}
        except BinanceAPIException as e:
            print(f"❌ ERRO ao definir leverage {symbol}: {e.code} - {e.message}")
            return {"success": False, "error": f"{e.code}: {e.message}"}
        except Exception as e:
            print(f"❌ ERRO ao definir leverage {symbol}: {e}")
            return {"success": False, "error": str(e)}

    def get_open_positions(self) -> list:
        try:
            positions = self.client.futures_position_information()
            open_pos = [p for p in positions if abs(float(p["positionAmt"])) > 0]
            print(f"📊 Posicoes abertas: {len(open_pos)}")
            for p in open_pos:
                print(f"   {p['symbol']}: {p['positionSide']} {p['positionAmt']}")
            return open_pos
        except Exception as e:
            print(f"❌ Erro ao obter posicoes: {e}")
            return []
