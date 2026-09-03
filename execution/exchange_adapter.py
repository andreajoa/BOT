# -*- coding: utf-8 -*-
"""Deterministic Binance execution adapter.

No strategy lives here. It only translates already validated execution plans
into exact Binance order requests and returns structured results.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Optional

from binance.exceptions import BinanceAPIException


class ExchangeAdapter:
    def __init__(self, connection: Any):
        self.connection = connection
        self.client = connection.client

    @staticmethod
    def client_order_id(command_id: str, suffix: str) -> str:
        raw = f"brain_{command_id}_{suffix}"
        cleaned = re.sub(r"[^.A-Z:/a-z0-9_-]", "_", raw)
        return cleaned[:36]

    def _position_params(self, position_side: str) -> Dict[str, Any]:
        if self.connection.hedge_mode:
            if position_side not in {"LONG", "SHORT"}:
                raise ValueError("Hedge Mode exige LONG ou SHORT")
            return {"positionSide": position_side}
        return {}

    @staticmethod
    def _open_side(position_side: str) -> str:
        return "BUY" if position_side == "LONG" else "SELL"

    @staticmethod
    def _close_side(position_side: str) -> str:
        return "SELL" if position_side == "LONG" else "BUY"

    def _lookup_client_order(self, symbol: str, client_order_id: str) -> Dict[str, Any]:
        """Pre-create idempotency check.

        -2013 is the normal "order does not exist" result and permits creation.
        Any other lookup error fails closed because creating after an ambiguous
        network/API failure could duplicate a protection order.
        """
        try:
            order = self.client.futures_get_order(
                symbol=symbol.upper(),
                origClientOrderId=client_order_id,
            )
            return {"ok": True, "found": True, "order": order}
        except BinanceAPIException as exc:
            if getattr(exc, "code", None) == -2013:
                return {"ok": True, "found": False}
            return {"ok": False, "found": False, "error": f"{exc.code}: {exc.message}"}
        except Exception as exc:
            return {"ok": False, "found": False, "error": str(exc)}

    def _existing_protection(self, symbol: str, client_order_id: str) -> Optional[Dict[str, Any]]:
        lookup = self._lookup_client_order(symbol, client_order_id)
        if not lookup.get("ok"):
            return {
                "success": False,
                "error": "PROTECTION_IDEMPOTENCY_CHECK_FAILED",
                "details": lookup,
            }
        if not lookup.get("found"):
            return None
        order = lookup.get("order") or {}
        status = str(order.get("status") or "").upper()
        if status in {"NEW", "PARTIALLY_FILLED", "FILLED"}:
            return {
                "success": True,
                "order": order,
                "already_exists": True,
                "order_status": status,
            }
        # CANCELED/EXPIRED/REJECTED are not active protection and may be recreated.
        return None

    def set_margin_type(self, symbol: str, margin_type: str = "ISOLATED") -> Dict[str, Any]:
        normalized = str(margin_type).upper()
        if normalized not in {"ISOLATED", "CROSSED"}:
            return {"success": False, "error": f"margin_type invalido: {margin_type}"}
        try:
            result = self.client.futures_change_margin_type(
                symbol=symbol.upper(),
                marginType=normalized,
            )
            return {"success": True, "margin_type": normalized, "result": result}
        except BinanceAPIException as exc:
            message = str(getattr(exc, "message", "") or "")
            if getattr(exc, "code", None) == -4046 or "No need to change margin type" in message:
                return {
                    "success": True,
                    "margin_type": normalized,
                    "already_set": True,
                    "result": {"code": getattr(exc, "code", None), "message": message},
                }
            return {"success": False, "error": f"{exc.code}: {exc.message}"}
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    def set_leverage(self, symbol: str, leverage: int) -> Dict[str, Any]:
        margin = self.set_margin_type(symbol, "ISOLATED")
        if not margin.get("success"):
            return {
                "success": False,
                "error": "SET_ISOLATED_MARGIN_FAILED",
                "margin_result": margin,
            }
        leverage_result = self.connection.set_leverage(symbol, leverage)
        if not leverage_result.get("success"):
            return leverage_result
        leverage_result = dict(leverage_result)
        leverage_result["margin_type"] = "ISOLATED"
        leverage_result["margin_result"] = margin
        return leverage_result

    def open_market(self, command_id: str, symbol: str, position_side: str, quantity: float) -> Dict[str, Any]:
        try:
            symbol = symbol.upper()
            qty = self.connection.normalize_quantity(symbol, quantity, market=True)
            params = {
                "symbol": symbol,
                "side": self._open_side(position_side),
                "type": "MARKET",
                "quantity": qty,
                "newClientOrderId": self.client_order_id(command_id, "entry"),
                "newOrderRespType": "RESULT",
            }
            params.update(self._position_params(position_side))
            order = self.client.futures_create_order(**params)
            return {"success": True, "order": order, "quantity": qty}
        except BinanceAPIException as exc:
            return {"success": False, "error": f"{exc.code}: {exc.message}"}
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    def open_limit(
        self,
        command_id: str,
        symbol: str,
        position_side: str,
        quantity: float,
        price: float,
    ) -> Dict[str, Any]:
        try:
            symbol = symbol.upper()
            qty = self.connection.normalize_quantity(symbol, quantity, market=False)
            price = self.connection.normalize_price(symbol, price, "nearest")
            params = {
                "symbol": symbol,
                "side": self._open_side(position_side),
                "type": "LIMIT",
                "timeInForce": "GTC",
                "quantity": qty,
                "price": price,
                "newClientOrderId": self.client_order_id(command_id, "entry"),
                "newOrderRespType": "RESULT",
            }
            params.update(self._position_params(position_side))
            order = self.client.futures_create_order(**params)
            return {"success": True, "order": order, "quantity": qty, "price": price}
        except BinanceAPIException as exc:
            return {"success": False, "error": f"{exc.code}: {exc.message}"}
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    def stop_close_all(
        self,
        command_id: str,
        symbol: str,
        position_side: str,
        stop_price: float,
        suffix: str = "sl",
    ) -> Dict[str, Any]:
        try:
            symbol = symbol.upper()
            client_id = self.client_order_id(command_id, suffix)
            existing = self._existing_protection(symbol, client_id)
            if existing is not None:
                if existing.get("success"):
                    existing["stop_price"] = self.connection.normalize_price(symbol, stop_price, "nearest")
                return existing

            side = self._close_side(position_side)
            price = self.connection.normalize_price(symbol, stop_price, "down" if side == "SELL" else "up")
            params = {
                "symbol": symbol,
                "side": side,
                "type": "STOP_MARKET",
                "stopPrice": price,
                "closePosition": "true",
                "workingType": "MARK_PRICE",
                "newClientOrderId": client_id,
            }
            params.update(self._position_params(position_side))
            order = self.client.futures_create_order(**params)
            return {"success": True, "order": order, "stop_price": price}
        except BinanceAPIException as exc:
            return {"success": False, "error": f"{exc.code}: {exc.message}"}
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    def take_profit_close_all(
        self,
        command_id: str,
        symbol: str,
        position_side: str,
        trigger_price: float,
        suffix: str = "tp",
    ) -> Dict[str, Any]:
        try:
            symbol = symbol.upper()
            client_id = self.client_order_id(command_id, suffix)
            existing = self._existing_protection(symbol, client_id)
            if existing is not None:
                if existing.get("success"):
                    existing["tp_price"] = self.connection.normalize_price(symbol, trigger_price, "nearest")
                return existing

            side = self._close_side(position_side)
            price = self.connection.normalize_price(symbol, trigger_price, "down" if side == "SELL" else "up")
            params = {
                "symbol": symbol,
                "side": side,
                "type": "TAKE_PROFIT_MARKET",
                "stopPrice": price,
                "closePosition": "true",
                "workingType": "MARK_PRICE",
                "newClientOrderId": client_id,
            }
            params.update(self._position_params(position_side))
            order = self.client.futures_create_order(**params)
            return {"success": True, "order": order, "tp_price": price}
        except BinanceAPIException as exc:
            return {"success": False, "error": f"{exc.code}: {exc.message}"}
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    def take_profit_partial(
        self,
        command_id: str,
        symbol: str,
        position_side: str,
        trigger_price: float,
        quantity: float,
        suffix: str,
    ) -> Dict[str, Any]:
        try:
            symbol = symbol.upper()
            client_id = self.client_order_id(command_id, suffix)
            existing = self._existing_protection(symbol, client_id)
            if existing is not None:
                if existing.get("success"):
                    existing["tp_price"] = self.connection.normalize_price(symbol, trigger_price, "nearest")
                    existing["quantity"] = self.connection.normalize_quantity(symbol, quantity, market=True)
                return existing

            side = self._close_side(position_side)
            qty = self.connection.normalize_quantity(symbol, quantity, market=True)
            price = self.connection.normalize_price(symbol, trigger_price, "down" if side == "SELL" else "up")
            params = {
                "symbol": symbol,
                "side": side,
                "type": "TAKE_PROFIT_MARKET",
                "stopPrice": price,
                "quantity": qty,
                "workingType": "MARK_PRICE",
                "newClientOrderId": client_id,
            }
            if self.connection.hedge_mode:
                params["positionSide"] = position_side
            else:
                params["reduceOnly"] = True
            order = self.client.futures_create_order(**params)
            return {"success": True, "order": order, "tp_price": price, "quantity": qty}
        except BinanceAPIException as exc:
            return {"success": False, "error": f"{exc.code}: {exc.message}"}
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    def close_market(self, symbol: str, position_side: str, quantity: float) -> Dict[str, Any]:
        return self.connection.close_position(symbol, quantity, position_side)

    def cancel_order(self, symbol: str, order_id: Optional[int] = None, client_order_id: Optional[str] = None) -> Dict[str, Any]:
        if order_id is None and not client_order_id:
            return {"success": False, "error": "order_id ou client_order_id obrigatorio"}
        try:
            params: Dict[str, Any] = {"symbol": symbol.upper()}
            if order_id is not None:
                params["orderId"] = int(order_id)
            else:
                params["origClientOrderId"] = client_order_id
            result = self.client.futures_cancel_order(**params)
            return {"success": True, "order": result}
        except BinanceAPIException as exc:
            return {"success": False, "error": f"{exc.code}: {exc.message}"}
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    def query_order(self, symbol: str, order_id: Optional[int] = None, client_order_id: Optional[str] = None) -> Dict[str, Any]:
        if order_id is None and not client_order_id:
            return {"success": False, "error": "order_id ou client_order_id obrigatorio"}
        try:
            params: Dict[str, Any] = {"symbol": symbol.upper()}
            if order_id is not None:
                params["orderId"] = int(order_id)
            else:
                params["origClientOrderId"] = client_order_id
            order = self.client.futures_get_order(**params)
            return {"success": True, "order": order}
        except BinanceAPIException as exc:
            return {"success": False, "error": f"{exc.code}: {exc.message}"}
        except Exception as exc:
            return {"success": False, "error": str(exc)}
