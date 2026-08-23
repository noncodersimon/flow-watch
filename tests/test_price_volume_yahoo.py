"""Tests for the pure parts of the Yahoo price/volume adapter.

The fetch itself is one call into yfinance and is not tested, per the
standing rule. What is tested is everything around it: the symbol
derivation, the reshaping of a yfinance frame into the store's series
format, and the unit-change guard.

That guard matters more than it looks. Yahoo quotes LSE lines in pence and
meta.json records them as GBX to match. A source that quietly switched to
pounds would divide every UK price by 100, and because the store merges
rather than overwrites, the corrupted points would sit alongside the good
ones with no obvious break.

No network: the frames here are built by hand.

CLAUDE.md promises the suite runs with no pip install. This module is the
one that needs pandas and yfinance - the adapter imports yfinance, and a
frame is the thing under test - so the import is guarded and these tests
skip rather than fail on a bare container. check.sh stays green either way.
"""

import unittest

from context import REPO_ROOT  # noqa: F401 - puts adapters/ on sys.path

try:
    import pandas as pd
    import price_volume_yahoo as pv
    HAVE_DEPS = True
except ImportError:  # pragma: no cover - depends on the environment
    HAVE_DEPS = False


def frame(index, columns):
    """columns: {(field, ticker) or field: [values]}"""
    return pd.DataFrame(columns, index=pd.to_datetime(index))


@unittest.skipUnless(HAVE_DEPS, "yfinance/pandas not installed")
class SymbolTest(unittest.TestCase):
    def test_lse_ids_become_dot_l(self):
        self.assertEqual(pv.yahoo_symbol({"id": "SHEL.LON"}), "SHEL.L")
        self.assertEqual(pv.yahoo_symbol({"id": "VOD.LON"}), "VOD.L")

    def test_bp_keeps_its_awkward_ticker(self):
        """BP's LSE ticker is 'BP.', so the id is BP.LON and Yahoo is BP.L."""
        self.assertEqual(pv.yahoo_symbol({"id": "BP.LON"}), "BP.L")

    def test_us_ids_pass_through(self):
        for sym in ["AAPL", "MSFT", "NVDA", "JPM", "SPY", "QQQ"]:
            self.assertEqual(pv.yahoo_symbol({"id": sym}), sym)

    def test_meta_override_wins(self):
        self.assertEqual(
            pv.yahoo_symbol({"id": "SGLN.LON", "yahoo": "SGLN.L"}), "SGLN.L")
        self.assertEqual(
            pv.yahoo_symbol({"id": "WEIRD.LON", "yahoo": "0A1B.IL"}), "0A1B.IL")


@unittest.skipUnless(HAVE_DEPS, "yfinance/pandas not installed")
class FrameReshapeTest(unittest.TestCase):
    def test_batch_frame_multiindex(self):
        f = frame(["2026-08-20", "2026-08-21"], {
            ("Close", "AAPL"): [300.0, 310.0],
            ("Close", "SHEL.L"): [2800.0, 2810.0],
            ("Volume", "AAPL"): [1000.0, 2000.0],
            ("Volume", "SHEL.L"): [3000.0, 4000.0],
        })
        closes, volumes = pv.frame_to_series(f, "AAPL")
        self.assertEqual(closes, [["2026-08-20", 300.0], ["2026-08-21", 310.0]])
        self.assertEqual(volumes, [["2026-08-20", 1000.0], ["2026-08-21", 2000.0]])

        closes, _ = pv.frame_to_series(f, "SHEL.L")
        self.assertEqual(closes[0], ["2026-08-20", 2800.0])

    def test_single_ticker_flat_frame(self):
        f = frame(["2026-08-21"], {"Close": [123.45], "Volume": [500.0]})
        closes, volumes = pv.frame_to_series(f, "ANY")
        self.assertEqual(closes, [["2026-08-21", 123.45]])
        self.assertEqual(volumes, [["2026-08-21", 500.0]])

    def test_nan_rows_are_dropped(self):
        """A holiday on one exchange puts NaN in a batch frame."""
        f = frame(["2026-08-20", "2026-08-21"], {
            ("Close", "SHEL.L"): [2800.0, float("nan")],
            ("Volume", "SHEL.L"): [3000.0, float("nan")],
        })
        closes, volumes = pv.frame_to_series(f, "SHEL.L")
        self.assertEqual(closes, [["2026-08-20", 2800.0]])
        self.assertEqual(volumes, [["2026-08-20", 3000.0]])

    def test_missing_ticker_is_empty_not_an_error(self):
        f = frame(["2026-08-21"], {("Close", "AAPL"): [300.0]})
        self.assertEqual(pv.frame_to_series(f, "MSFT"), ([], []))

    def test_empty_frame(self):
        self.assertEqual(pv.frame_to_series(None, "AAPL"), ([], []))
        self.assertEqual(pv.frame_to_series(pd.DataFrame(), "AAPL"), ([], []))

    def test_dates_are_iso_strings(self):
        f = frame(["2026-01-05"], {"Close": [1.0], "Volume": [1.0]})
        closes, _ = pv.frame_to_series(f, "X")
        self.assertRegex(closes[0][0], r"^\d{4}-\d{2}-\d{2}$")


@unittest.skipUnless(HAVE_DEPS, "yfinance/pandas not installed")
class UnitChangeGuardTest(unittest.TestCase):
    def test_pence_to_pounds_is_caught(self):
        existing = [["2026-08-21", 2810.0]]      # pence
        incoming = [["2026-08-22", 28.10]]       # pounds
        self.assertTrue(pv.unit_change(existing, incoming))

    def test_pounds_to_pence_is_caught(self):
        self.assertTrue(pv.unit_change([["2026-08-21", 5.49]],
                                       [["2026-08-22", 549.0]]))

    def test_an_ordinary_move_is_not(self):
        for old, new in [(100.0, 105.0), (100.0, 80.0), (100.0, 50.0), (100.0, 300.0)]:
            self.assertFalse(pv.unit_change([["d", old]], [["d", new]]),
                             f"{old} -> {new} should be allowed through")

    def test_a_crash_is_still_allowed_through(self):
        """A 90% fall is real and must not be mistaken for a unit change."""
        self.assertFalse(pv.unit_change([["d", 100.0]], [["d", 10.0]]))

    def test_seeding_an_empty_series_is_allowed(self):
        self.assertFalse(pv.unit_change([], [["d", 2810.0]]))
        self.assertFalse(pv.unit_change([["d", 2810.0]], []))

    def test_zero_and_missing_values_do_not_divide_by_zero(self):
        self.assertFalse(pv.unit_change([["d", 0.0]], [["d", 10.0]]))
        self.assertFalse(pv.unit_change([["d", 10.0]], [["d", 0.0]]))


@unittest.skipUnless(HAVE_DEPS, "yfinance/pandas not installed")
@unittest.skipUnless(HAVE_DEPS, "yfinance/pandas not installed")
class CurrencyVerificationTest(unittest.TestCase):
    """Yahoo reports pence as "GBp". A new instrument's meta.json label is
    checked against that before its first points merge - the one moment a
    wrong pence/pounds label cannot be caught by the 20x guard, because there
    is nothing to compare against yet."""

    def test_agreement(self):
        self.assertTrue(pv.currency_matches("GBX", "GBp"))
        self.assertTrue(pv.currency_matches("GBP", "GBP"))
        self.assertTrue(pv.currency_matches("USD", "USD"))

    def test_the_pence_pounds_trap_is_caught(self):
        self.assertFalse(pv.currency_matches("GBX", "GBP"))
        self.assertFalse(pv.currency_matches("GBP", "GBp"))
        self.assertFalse(pv.currency_matches("USD", "GBp"))

    def test_unknown_or_missing_reports_are_unverifiable_not_failures(self):
        self.assertIsNone(pv.currency_matches("GBX", None))
        self.assertIsNone(pv.currency_matches("GBX", ""))
        self.assertIsNone(pv.currency_matches("GBX", "EUR"))


class ChunkTest(unittest.TestCase):
    def test_chunks_cover_everything_exactly_once(self):
        items = [str(n) for n in range(29)]
        out = [c for c in pv.chunks(items, 12)]
        self.assertEqual([len(c) for c in out], [12, 12, 5])
        self.assertEqual([x for c in out for x in c], items)


if __name__ == "__main__":
    unittest.main()
