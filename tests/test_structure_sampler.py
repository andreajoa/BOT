# -*- coding: utf-8 -*-
import unittest

from market.structure_sampler import StructureSampler


def _klines(count=60, start=1.0, step=0.01):
    rows = []
    price = start
    for i in range(count):
        open_p = price
        close_p = price + step
        high = max(open_p, close_p) + 0.005
        low = min(open_p, close_p) - 0.005
        volume = 100 + i
        rows.append([
            i * 60_000,
            str(open_p),
            str(high),
            str(low),
            str(close_p),
            str(volume),
            (i + 1) * 60_000 - 1,
        ])
        price = close_p
    return rows


class StructureSamplerTests(unittest.TestCase):
    def test_rising_series_is_bullishly_aligned(self):
        features = StructureSampler.compute_features(_klines())
        self.assertEqual(features["ema_alignment"], "BULLISH")
        self.assertGreater(features["return_12bar_pct"], 0)
        self.assertGreater(features["atr14"], 0)
        self.assertGreater(features["range_position_20"], 0.5)
        self.assertIsNotNone(features["realized_vol_20"])

    def test_insufficient_klines_rejected(self):
        with self.assertRaises(ValueError):
            StructureSampler.compute_features(_klines(count=10))


if __name__ == "__main__":
    unittest.main()
