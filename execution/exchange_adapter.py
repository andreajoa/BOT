# -*- coding: utf-8 -*-
"""Deterministic Binance USD-M execution adapter.

Normal entries (MARKET/LIMIT) use ``/fapi/v1/order``. Conditional protection
(STOP/TP) uses the post-2025 USD-M Algo Order API (``/fapi/v1/algoOrder``).
No strategy lives here.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any, Dict, Optional

from binance.exceptions import BinanceAPIException


class ExchangeAdapter:
    def __init__(self, connection: Any):
        self.connection = connection
        self.client = connection.client

    @staticmethod
    def client_order_id(command_id: str, suffix: str) -> str:
        """Deterministic <=36-char id that always preserves the order role."""
        digest = hashlib.sha256(str(command_id).encode("utf-8")).hexdigest()[:18]
        role = re.sub(r"[^.A-Z:/a-z0-9_-]", "_", str(suffix))[:10] or "order"
        return f"brain_{digest}_{role}"[:36]

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

    @staticmethod
    def _api_error(exc: Exception) -> str:
        code = getattr(exc, "code", None)
        message = getattr(exc, "message", None) or str(exc)
        return f"{code}: {message}" if code is not None else str(message)

    @staticmethod
    def _not_found_exception(exc: Exception) -> bool:
        code = getattr(exc, "code", None)
        message = str(getattr(exc, "message", "") or str(exc)).lower()
        return code in {-2011, -2013} or "does not exist" in message or "not found" in message

    # ------------------------------------------------------------------
    # Normal-order lookups: MARKET/LIMIT entries only
    # ------------------------------------------------------------------
    def _lookup_client_order(self, symbol: str, client_order_id: str) -> Dict[str, Any]:
        try:
            order = self.client.futures_get_order(
                symbol=symbol.upper(),
                origClientOrderId=client_order_id,
            )
            return {"ok": True, "found": True, "order": order}
        except BinanceAPIException as exc:
            if self._not_found_exception(exc):
                return {"ok": True, "found": False}
            return {"ok": False, "found": False, "error": self._api_error(exc)}
        except Exception as exc:
            return {"ok": False, "found": False, "error": str(exc)}

    # ------------------------------------------------------------------
    # Algo-order lookups: STOP/TP protections only
    # ------------------------------------------------------------------
    def _lookup_algo_order(self, symbol: str, client_algo_id: str) -> Dict[str, Any]:
        try:
            order = self.client.futures_get_algo_order(
                symbol=symbol.upper(),
                clientAlgoId=client_algo_id,
            )
            return {"ok": True, "found": True, "order": order}
        except BinanceAPIException as exc:
            if self._not_found_exception(exc):
                return {"ok": True, "found": False}
            return {"ok": False, "found": False, "error": self._api_error(exc)}
        except Exception as exc:
            return {"ok": False, "found": False, "error": str(exc)}

    def _existing_protection(self, symbol: str, client_algo_id: str) -> Optional[Dict[str, Any]]:
        lookup = self._lookup_algo_order(symbol, client_algo_id)
        if not lookup.get("ok"):
            return {
                "success": False,
                "error": "PROTECTION_IDEMPOTENCY_CHECK_FAILED",
                "details": lookup,
                "is_algo": True,
            }
        if not lookup.get("found"):
            return None

        order = lookup.get("order") or {}
        status = str(order.get("algoStatus") or "").upper()
        # If the deterministic algo already exists, never duplicate it. Even a
        # terminal status is evidence that this exact protection was submitted;
        # account reconciliation decides whether the position still exists.
        return {
            "success": True,
            "order": order,
            "already_exists": True,
            "algo_status": status,
            "is_algo": True,
        }

    # ------------------------------------------------------------------
    # Account configuration
    # ------------------------------------------------------------------
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
            return {"success": False, "error": self._api_error(exc)}
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

    # ------------------------------------------------------------------
    # Entry orders: normal USD-M order endpoint
    # ------------------------------------------------------------------
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
            return {"success": True, "order": order, "quantity": qty, "is_algo": False}
        except BinanceAPIException as exc:
            return {"success": False, "error": self._api_error(exc)}
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
            return {"success": True, "order": order, "quantity": qty, "price": price, "is_algo": False}
        except BinanceAPIException as exc:
            return {"success": False, "error": self._api_error(exc)}
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    # ------------------------------------------------------------------
    # Protective conditional orders: USD-M Algo Order endpoint
    # ------------------------------------------------------------------
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
            side = self._close_side(position_side)
            price = self.connection.normalize_price(symbol, stop_price, "down" if side == "SELL" else "up")

            existing = self._existing_protection(symbol, client_id)
            if existing is not None:
                if existing.get("success"):
                    existing["stop_price"] = price
                return existing

            params: Dict[str, Any] = {
                "algoType": "CONDITIONAL",
                "symbol": symbol,
                "side": side,
                "type": "STOP_MARKET",
                "triggerPrice": price,
                "closePosition": "true",
                "workingType": "MARK_PRICE",
                "clientAlgoId": client_id,
                "newOrderRespType": "RESULT",
            }
            params.update(self._position_params(position_side))
            order = self.client.futures_create_algo_order(**params)
            return {"success": True, "order": order, "stop_price": price, "is_algo": True}
        except BinanceAPIException as exc:
            return {"success": False, "error": self._api_error(exc), "is_algo": True}
        except Exception as exc:
            return {"success": False, "error": str(exc), "is_algo": True}

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
            side = self._close_side(position_side)
            price = self.connection.normalize_price(symbol, trigger_price, "down" if side == "SELL" else "up")

            existing = self._existing_protection(symbol, client_id)
            if existing is not None:
                if existing.get("success"):
                    existing["tp_price"] = price
                return existing

            params: Dict[str, Any] = {
                "algoType": "CONDITIONAL",
                "symbol": symbol,
                "side": side,
                "type": "TAKE_PROFIT_MARKET",
                "triggerPrice": price,
                "closePosition": "true",
                "workingType": "MARK_PRICE",
                "clientAlgoId": client_id,
                "newOrderRespType": "RESULT",
            }
            params.update(self._position_params(position_side))
            order = self.client.futures_create_algo_order(**params)
            return {"success": True, "order": order, "tp_price": price, "is_algo": True}
        except BinanceAPIException as exc:
            return {"success": False, "error": self._api_error(exc), "is_algo": True}
        except Exception as exc:
            return {"success": False, "error": str(exc), "is_algo": True}

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
            side = self._close_side(position_side)
            qty = self.connection.normalize_quantity(symbol, quantity, market=True)
            price = self.connection.normalize_price(symbol, trigger_price, "down" if side == "SELL" else "up")

            existing = self._existing_protection(symbol, client_id)
            if existing is not None:
                if existing.get("success"):
                    existing["tp_price"] = price
                    existing["quantity"] = qty
                return existing

            params: Dict[str, Any] = {
                "algoType": "CONDITIONAL",
                "symbol": symbol,
                "side": side,
                "type": "TAKE_PROFIT_MARKET",
                "triggerPrice": price,
                "quantity": qty,
                "workingType": "MARK_PRICE",
                "clientAlgoId": client_id,
                "newOrderRespType": "RESULT",
            }
            if self.connection.hedge_mode:
                params["positionSide"] = position_side
            else:
                params["reduceOnly"] = "true"
            order = self.client.futures_create_algo_order(**params)
            return {
                "success": True,
                "order": order,
                "tp_price": price,
                "quantity": qty,
                "is_algo": True,
            }
        except BinanceAPIException as exc:
            return {"success": False, "error": self._api_error(exc), "is_algo": True}
        except Exception as exc:
            return {"success": False, "error": str(exc), "is_algo": True}

    # ------------------------------------------------------------------
    # Position close remains a normal MARKET reduce order
    # ------------------------------------------------------------------
    def close_market(self, symbol: str, position_side: str, quantity: float) -> Dict[str, Any]:
        return self.connection.close_position(symbol, quantity, position_side)

    # ------------------------------------------------------------------
    # Normal order management (entries)
    # ------------------------------------------------------------------
    def cancel_order(
        self,
        symbol: str,
        order_id: Optional[int] = None,
        client_order_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        if order_id is None and not client_order_id:
            return {"success": False, "error": "order_id ou client_order_id obrigatorio"}
        try:
            params: Dict[str, Any] = {"symbol": symbol.upper()}
            if order_id is not None:
                params["orderId"] = int(order_id)
            else:
                params["origClientOrderId"] = client_order_id
            result = self.client.futures_cancel_order(**params)
            return {"success": True, "order": result, "is_algo": False}
        except BinanceAPIException as exc:
            return {"success": False, "error": self._api_error(exc)}
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    def query_order(
        self,
        symbol: str,
        order_id: Optional[int] = None,
        client_order_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        if order_id is None and not client_order_id:
            return {"success": False, "error": "order_id ou client_order_id obrigatorio"}
        try:
            params: Dict[str, Any] = {"symbol": symbol.upper()}
            if order_id is not None:
                params["orderId"] = int(order_id)
            else:
                params["origClientOrderId"] = client_order_id
            order = self.client.futures_get_order(**params)
            return {"success": True, "order": order, "is_algo": False}
        except BinanceAPIException as exc:
            not_found = self._not_found_exception(exc)
            return {"success": False, "error": self._api_error(exc), "not_found": not_found}
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    # ------------------------------------------------------------------
    # Algo protection management
    # ------------------------------------------------------------------
    def cancel_algo_order(
        self,
        symbol: str,
        algo_id: Optional[int] = None,
        client_algo_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        if algo_id is None and not client_algo_id:
            return {"success": False, "error": "algo_id ou client_algo_id obrigatorio", "is_algo": True}
        try:
            params: Dict[str, Any] = {"symbol": symbol.upper()}
            if algo_id is not None:
                params["algoId"] = int(algo_id)
            else:
                params["clientAlgoId"] = client_algo_id
            order = self.client.futures_cancel_algo_order(**params)
            return {"success": True, "order": order, "is_algo": True}
        except BinanceAPIException as exc:
            return {"success": False, "error": self._api_error(exc), "is_algo": True}
        except Exception as exc:
            return {"success": False, "error": str(exc), "is_algo": True}

    def query_algo_order(
        self,
        symbol: str,
        algo_id: Optional[int] = None,
        client_algo_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        if algo_id is None and not client_algo_id:
            return {"success": False, "error": "algo_id ou client_algo_id obrigatorio", "is_algo": True}
        try:
            params: Dict[str, Any] = {"symbol": symbol.upper()}
            if algo_id is not None:
                params["algoId"] = int(algo_id)
            else:
                params["clientAlgoId"] = client_algo_id
            order = self.client.futures_get_algo_order(**params)
            return {"success": True, "order": order, "is_algo": True}
        except BinanceAPIException as exc:
            not_found = self._not_found_exception(exc)
            return {
                "success": False,
                "error": self._api_error(exc),
                "not_found": not_found,
                "is_algo": True,
            }
        except Exception as exc:
            return {"success": False, "error": str(exc), "is_algo": True}

    def open_algo_orders(self, symbol: Optional[str] = None) -> Dict[str, Any]:
        try:
            params: Dict[str, Any] = {}
            if symbol:
                params["symbol"] = symbol.upper()
            orders = self.client.futures_get_open_algo_orders(**params)
            return {"success": True, "orders": orders, "is_algo": True}
        except BinanceAPIException as exc:
            return {"success": False, "error": self._api_error(exc), "is_algo": True}
        except Exception as exc:
            return {"success": False, "error": str(exc), "is_algo": True}
