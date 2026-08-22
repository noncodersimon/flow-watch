"""Adapter-level checks.

Deliberately narrow: the fetch functions are thin wrappers over live APIs
(Alpha Vantage, CFTC Socrata, Yahoo), so mocking them would only test the
mock. What is worth testing is that every adapter is syntactically sound
and importable, plus the pure date-matching helper that decides which
close price a flow is valued at.
"""

import glob
import importlib.util
import os
import py_compile
import unittest

from context import ADAPTERS_DIR

HAS_YFINANCE = importlib.util.find_spec("yfinance") is not None


class AdaptersCompileTest(unittest.TestCase):
    def test_every_adapter_compiles(self):
        sources = sorted(glob.glob(os.path.join(ADAPTERS_DIR, "*.py")))
        self.assertTrue(sources, "no adapter sources found")
        for path in sources:
            with self.subTest(adapter=os.path.basename(path)):
                try:
                    py_compile.compile(path, doraise=True)
                except py_compile.PyCompileError as exc:
                    self.fail(str(exc))

    def test_network_free_adapters_import(self):
        # these two use only the standard library, so importing them is safe
        for name in ("common", "cot_cftc", "informed_money"):
            with self.subTest(module=name):
                __import__(name)


@unittest.skipUnless(HAS_YFINANCE, "yfinance not installed")
class PriceOnOrBeforeTest(unittest.TestCase):
    """etf_flows values a flow at the last close at or before the share-count
    date, because share counts and closes do not always land on the same day."""

    def setUp(self):
        from etf_flows import price_on_or_before

        self.fn = price_on_or_before
        self.closes = [
            ["2026-01-02", 100.0],
            ["2026-01-05", 110.0],
            ["2026-01-06", 120.0],
        ]

    def test_exact_match(self):
        self.assertEqual(self.fn(self.closes, "2026-01-05"), 110.0)

    def test_falls_back_to_the_previous_close(self):
        # 3rd and 4th are a weekend - should use Friday the 2nd
        self.assertEqual(self.fn(self.closes, "2026-01-04"), 100.0)

    def test_date_after_the_last_close_uses_the_last_close(self):
        self.assertEqual(self.fn(self.closes, "2026-03-01"), 120.0)

    def test_date_before_any_close_returns_none(self):
        self.assertIsNone(self.fn(self.closes, "2025-12-31"))

    def test_empty_closes_returns_none(self):
        self.assertIsNone(self.fn([], "2026-01-05"))


if __name__ == "__main__":
    unittest.main()
