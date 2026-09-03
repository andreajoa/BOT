# -*- coding: utf-8 -*-
"""
Conexao Binance USD-M Futures.

Esta camada NAO escolhe estrategia. Ela traduz comandos de execucao para a
Binance, respeita as regras reais de cada contrato e devolve resultados
estruturados para as camadas superiores.
"""

import time
from decimal import Decimal, ROUND_DOWN, ROUND_UP, ROUND_HALF_UP
from typing import Optional, Dict, Any

from binance.client import Client
from binance.exceptions import BinanceAPIException

from config.settings import REQUEST_TIMEOUT


class BinanceConnection:
    """Gerenciador de conexao e execucao na Binance USD-M Futures."""

    def __init__(self, api_key: str, api_secret: str):
        self.api_key = api_key
        self.api_secret = api_secret
        self.client = None
        self.time_offset = 0
        self.hedge_mode = False
        self.is_connected = False
        self._exchange_info: Optional[Dict[str, Any]] = None
        self._symbol_rules_cache: Dict[str, Dict[str, Any]] = {}

    def _create_client(self):
        try:
            self.client = Client(
                self.api_key,
                self.api_secret,
                requests_params={"timeout": REQUEST_TIMEOUT},
            )
            # Testa especificamente o ambiente Futures, nao apenas Spot.
            self.client.futures_ping()
            return True, None
        except Exception as e:
            return False, f"Erro ao criar cliente Futures: {e}"

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
            self.refresh_exchange_info()
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
            server_time = self.client.futures_time()["serverTime"]
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
        normalized = self._validate_position_side(position_side)
        if self.hedge_mode:
            if normalized not in {"LONG", "SHORT"}:
                raise ValueError("Hedge Mode exige position_side LONG ou SHORT")
            return {"positionSide": normalized}
        return {}

    def _infer_open_position_side(self, symbol: str) -> Optional[str]:
        positions = self.client.futures_position_information(symbol=symbol)
        active = [p for p in positions if abs(float(p.get("positionAmt", 0))) > 0]
        if len(active) != 1:
            return None

        pos = active[0]
        if self.hedge_mode:
            side = str(pos.get("positionSide", "")).upper()
            return side if side in {"LONG", "SHORT"} else None

        amount = float(pos.get("positionAmt", 0))
        if amount > 0:
            return "LONG"
        if amount < 0:
            return "SHORT"
        return None

    # ------------------------------------------------------------------
    # Exchange rules / precision
    # ------------------------------------------------------------------
    def refresh_exchange_info(self) -> Dict[str, Any]:
        self._exchange_info = self.client.futures_exchange_info()
        self._symbol_rules_cache.clear()
        return self._exchange_info

    def get_symbol_rules(self, symbol: str, refresh: bool = False) -> Dict[str, Any]:
        symbol = symbol.upper()
        if refresh:
            self.refresh_exchange_info()
        if symbol in self._symbol_rules_cache:
            return self._symbol_rules_cache[symbol]
        if self._exchange_info is None:
            self.refresh_exchange_info()

        raw = next(
            (s for s in self._exchange_info.get("symbols", []) if s.get("symbol") == symbol),
            None,
        )
        if raw is None:
            raise ValueError(f"Simbolo Futures inexistente: {symbol}")

        filters = {f.get("filterType"): f for f in raw.get("filters", [])}
        price_filter = filters.get("PRICE_FILTER", {})
        lot_filter = filters.get("LOT_SIZE", {})
        market_lot = filters.get("MARKET_LOT_SIZE", {})
        notional_filter = filters.get("MIN_NOTIONAL") or filters.get("NOTIONAL") or {}

        min_notional = (
            notional_filter.get("notional")
            or notional_filter.get("minNotional")
            or "0"
        )

        rules = {
            "symbol": symbol,
            "status": raw.get("status"),
            "contract_type": raw.get("contractType"),
            "base_asset": raw.get("baseAsset"),
            "quote_asset": raw.get("quoteAsset"),
            "margin_asset": raw.get("marginAsset"),
            "price_precision": int(raw.get("pricePrecision", 8)),
            "quantity_precision": int(raw.get("quantityPrecision", 8)),
            "tick_size": float(price_filter.get("tickSize", 0) or 0),
            "min_price": float(price_filter.get("minPrice", 0) or 0),
            "max_price": float(price_filter.get("maxPrice", 0) or 0),
            "step_size": float(lot_filter.get("stepSize", 0) or 0),
            "min_qty": float(lot_filter.get("minQty", 0) or 0),
            "max_qty": float(lot_filter.get("maxQty", 0) or 0),
            "market_step_size": float(market_lot.get("stepSize", 0) or 0),
            "market_min_qty": float(market_lot.get("minQty", 0) or 0),
            "market_max_qty": float(market_lot.get("maxQty", 0) or 0),
            "min_notional": float(min_notional),
        }
        self._symbol_rules_cache[symbol] = rules
        return rules

    @staticmethod
    def _quantize_to_step(value: float, step: float, mode: str = "down") -> float:
        if step <= 0:
            return float(value)
        d_value = Decimal(str(value))
        d_step = Decimal(str(step))
        rounding = {
            "down": ROUND_DOWN,
            "up": ROUND_UP,
            "nearest": ROUND_HALF_UP,
        }.get(mode, ROUND_DOWN)
        units = (d_value / d_step).to_integral_value(rounding=rounding)
        return float(units * d_step)

    def normalize_quantity(self, symbol: str, quantity: float, market: bool = True) -> float:
        rules = self.get_symbol_rules(symbol)
        if rules["status"] != "TRADING":
            raise ValueError(f"{symbol} nao esta em status TRADING")

        step = rules["market_step_size"] if market and rules["market_step_size"] > 0 else rules["step_size"]
        min_qty = rules["market_min_qty"] if market and rules["market_min_qty"] > 0 else rules["min_qty"]
        max_qty = rules["market_max_qty"] if market and rules["market_max_qty"] > 0 else rules["max_qty"]

        normalized = self._quantize_to_step(quantity, step, "down")
        if normalized <= 0:
            raise ValueError(f"Quantidade normalizada zerou para {symbol}")
        if min_qty > 0 and normalized < min_qty:
            raise ValueError(f"Quantidade {normalized} abaixo do minimo {min_qty} para {symbol}")
        if max_qty > 0 and normalized > max_qty:
            raise ValueError(f"Quantidade {normalized} acima do maximo {max_qty} para {symbol}")
        return normalized

    def normalize_price(self, symbol: str, price: float, mode: str = "nearest") -> float:
        rules = self.get_symbol_rules(symbol)
        normalized = self._quantize_to_step(price, rules["tick_size"], mode)
        if rules["min_price"] > 0 and normalized < rules["min_price"]:
            raise ValueError(f"Preco {normalized} abaixo do minimo de {symbol}")
        if rules["max_price"] > 0 and normalized > rules["max_price"]:
            raise ValueError(f"Preco {normalized} acima do maximo de {symbol}")
        return normalized

    def validate_notional(self, symbol: str, quantity: float, price: Optional[float] = None) -> Dict[str, Any]:
        rules = self.get_symbol_rules(symbol)
        if price is None:
            price = self.get_price(symbol)
        if price is None:
            return {"valid": False, "reason": "PRICE_UNAVAILABLE"}

        notional = float(quantity) * float(price)
        minimum = float(rules.get("min_notional", 0))
        return {
            "valid": notional >= minimum,
            "notional": notional,
            "min_notional": minimum,
            "reason": "OK" if notional >= minimum else "MIN_NOTIONAL_NOT_MET",
        }

    def quantity_from_margin(
        self,
        symbol: str,
        margin_usdt: float,
        leverage: int,
        price: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Calcula quantidade executavel a partir da margem real desejada."""
        if margin_usdt <= 0:
            return {"success": False, "error": "margin_usdt deve ser > 0"}
        if leverage < 1:
            return {"success": False, "error": "leverage deve ser >= 1"}
        if price is None:
            price = self.get_price(symbol)
        if price is None or price <= 0:
            return {"success": False, "error": "Preco indisponivel"}

        requested_notional = float(margin_usdt) * int(leverage)
        raw_qty = requested_notional / float(price)
        try:
            qty = self.normalize_quantity(symbol, raw_qty, market=True)
        except ValueError as e:
            return {"success": False, "error": str(e)}

        notional_check = self.validate_notional(symbol, qty, price)
        if not notional_check["valid"]:
            return {
                "success": False,
                "error": "MIN_NOTIONAL_NOT_MET",
                "quantity": qty,
                "price": price,
                "notional": notional_check["notional"],
                "min_notional": notional_check["min_notional"],
            }

        actual_notional = qty * float(price)
        actual_margin = actual_notional / int(leverage)
        return {
            "success": True,
            "symbol": symbol.upper(),
            "price": float(price),
            "quantity": qty,
            "leverage": int(leverage),
            "requested_margin": float(margin_usdt),
            "actual_margin": actual_margin,
            "notional": actual_notional,
            "min_notional": notional_check["min_notional"],
        }

    # ------------------------------------------------------------------
    # Market/account data
    # ------------------------------------------------------------------
    def get_price(self, symbol: str) -> Optional[float]:
        try:
            ticker = self.client.futures_symbol_ticker(symbol=symbol.upper())
            return float(ticker["price"])
        except Exception as e:
            print(f"⚠️ Erro ao obter preco {symbol}: {e}")
            return None

    def get_klines(self, symbol: str, interval: str, limit: int = 150):
        try:
            return self.client.futures_klines(symbol=symbol.upper(), interval=interval, limit=limit)
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

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------
    def create_market_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        position_side: Optional[str] = None,
        reduce_only: bool = False,
    ) -> Dict[str, Any]:
        try:
            symbol = symbol.upper()
            normalized_side = side.upper()
            quantity = self.normalize_quantity(symbol, quantity, market=True)

            if self.hedge_mode and position_side is None:
                position_side = "LONG" if normalized_side == "BUY" else "SHORT"

            params: Dict[str, Any] = {
                "symbol": symbol,
                "side": normalized_side,
                "type": "MARKET",
                "quantity": quantity,
                "newOrderRespType": "RESULT",
            }
            params.update(self._position_params(position_side))
            if reduce_only and not self.hedge_mode:
                params["reduceOnly"] = True

            order = self.client.futures_create_order(**params)
            print(f"📝 Ordem MARKET criada: {symbol} {normalized_side} {quantity}")
            return {"success": True, "order": order, "quantity": quantity}
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
        quantity: Optional[float] = None,
        position_side: Optional[str] = None,
    ) -> Dict[str, Any]:
        """STOP_MARKET Close-All. `quantity` legado e aceito, mas nunca enviado."""
        try:
            symbol = symbol.upper()
            normalized_side = side.upper()
            # SELL stop e arredondado para baixo; BUY stop para cima.
            stop_price = self.normalize_price(symbol, stop_price, "down" if normalized_side == "SELL" else "up")
            if self.hedge_mode and position_side is None:
                position_side = "LONG" if normalized_side == "SELL" else "SHORT"

            params: Dict[str, Any] = {
                "symbol": symbol,
                "side": normalized_side,
                "type": "STOP_MARKET",
                "stopPrice": stop_price,
                "closePosition": "true",
                "workingType": "MARK_PRICE",
            }
            params.update(self._position_params(position_side))
            order = self.client.futures_create_order(**params)
            print(f"🛑 Stop Loss criado: {symbol} @ {stop_price}")
            return {"success": True, "order": order, "stop_price": stop_price}
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
        quantity: Optional[float] = None,
        position_side: Optional[str] = None,
    ) -> Dict[str, Any]:
        """TAKE_PROFIT_MARKET Close-All. `quantity` legado e aceito, mas nunca enviado."""
        try:
            symbol = symbol.upper()
            normalized_side = side.upper()
            tp_price = self.normalize_price(symbol, tp_price, "down" if normalized_side == "SELL" else "up")
            if self.hedge_mode and position_side is None:
                position_side = "LONG" if normalized_side == "SELL" else "SHORT"

            params: Dict[str, Any] = {
                "symbol": symbol,
                "side": normalized_side,
                "type": "TAKE_PROFIT_MARKET",
                "stopPrice": tp_price,
                "closePosition": "true",
                "workingType": "MARK_PRICE",
            }
            params.update(self._position_params(position_side))
            order = self.client.futures_create_order(**params)
            print(f"🎯 Take Profit criado: {symbol} @ {tp_price}")
            return {"success": True, "order": order, "tp_price": tp_price}
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
        position_side: Optional[str] = None,
    ) -> Dict[str, Any]:
        try:
            symbol = symbol.upper()
            normalized = self._validate_position_side(position_side)
            if normalized is None:
                normalized = self._infer_open_position_side(symbol)
            if normalized not in {"LONG", "SHORT"}:
                return {
                    "success": False,
                    "error": "Nao foi possivel determinar unicamente se a posicao e LONG ou SHORT",
                }

            quantity = self.normalize_quantity(symbol, quantity, market=True)
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
            return {"success": True, "order": order, "quantity": quantity}
        except BinanceAPIException as e:
            print(f"❌ ERRO ao fechar posicao {symbol}: {e.code} - {e.message}")
            return {"success": False, "error": f"{e.code}: {e.message}"}
        except Exception as e:
            print(f"❌ ERRO ao fechar posicao {symbol}: {e}")
            return {"success": False, "error": str(e)}

    def cancel_all_orders(self, symbol: str) -> Dict[str, Any]:
        try:
            self.client.futures_cancel_all_open_orders(symbol=symbol.upper())
            print(f"🗑️ Ordens canceladas: {symbol.upper()}")
            return {"success": True}
        except BinanceAPIException as e:
            print(f"❌ ERRO ao cancelar ordens {symbol}: {e.code} - {e.message}")
            return {"success": False, "error": f"{e.code}: {e.message}"}
        except Exception as e:
            print(f"❌ ERRO ao cancelar ordens {symbol}: {e}")
            return {"success": False, "error": str(e)}

    def set_leverage(self, symbol: str, leverage: int) -> Dict[str, Any]:
        """Define leverage usando POST /fapi/v1/leverage."""
        try:
            if not 1 <= int(leverage) <= 125:
                return {"success": False, "error": f"Leverage invalido: {leverage}"}

            result = self.client.futures_change_leverage(symbol=symbol.upper(), leverage=int(leverage))
            actual = int(result.get("leverage", leverage))
            print(f"⚖️ Alavancagem definida: {symbol.upper()} {actual}x")
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
