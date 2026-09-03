# -*- coding: utf-8 -*-
import unittest

from market.symbol_scanner import ExecutableSymbolScanner


class _Client:
    def futures_exchange_info(self):
        return {
            "symbols": [
                {
                    "symbol": "AAAUSDT",
                    "status": "TRADING",
                    "contractType": "PERPETUAL",
                    "quoteAsset": "USDT",
                    "filters": [
                        {"filterType": "MARKET_LOT_SIZE", "minQty": "1", "stepSize": "1"},
                        {"filterType": "MIN_NOTIONAL", "notional": "5"},
                    ],
                },
                {
                    "symbol": "BBBUSDT",
                    "status": "TRADING",
                    "contractType": "PERPETUAL",
                    "quoteAsset": "USDT",
                    "filters": [
                        {"filterType": "MARKET_LOT_SIZE", "minQty": "100", "stepSize": "100"},
                        {"filterType": "MIN_NOTIONAL", "notional": "5"},
                    ],
                },
            ]
        }

    def futures_ticker(self):
        return [
            {"symbol": "AAAUSDT", "quoteVolume": "1000000"},
            {"symbol": "BBBUSDT", "quoteVolume": "2000000"},
        ]

    def futures_symbol_ticker(self):
        return [
            {"symbol": "AAAUSDT", "price": "1"},
            {"symbol": "BBBUSDT", "price": "1"},
        ]


class _Conn:
    def __init__(self):
        self.client = _Client()

    def get_usdt_balance(self):
        return 0.57


class SymbolScannerTests(unittest.TestCase):
    def test_small_balance_keeps_only_executable_contracts(self):
        scanner = ExecutableSymbolScanner(_Conn(), max_leverage=10, max_margin_usage_pct=0.95)
        result = scanner.scan(limit=10)
        self.assertEqual([row["symbol"] for row in result], ["AAAUSDT"])
        self.assertEqual(result[0]["min_leverage_for_balance"], 10)


if __name__ == "__main__":
    unittest.main()
