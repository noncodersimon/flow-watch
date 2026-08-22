"""Tests for the pure helpers in adapters/common.py.

These are the functions every adapter depends on, so a regression here
silently corrupts every metric file. No network is touched.
"""

import unittest
from datetime import date, timedelta

from context import DATA_DIR  # noqa: F401 - import for sys.path side effect

import common
from common import merge_series, percentile_of_last, rolling_mean


def store(series=None):
    return {"updated": None, "series": dict(series or {})}


class MergeSeriesTest(unittest.TestCase):
    def test_merges_into_an_empty_store(self):
        s = store()
        merge_series(s, "AAA", [["2026-01-02", 2.0], ["2026-01-01", 1.0]])
        self.assertEqual(
            s["series"]["AAA"], [["2026-01-01", 1.0], ["2026-01-02", 2.0]]
        )

    def test_sorts_output_by_date_ascending(self):
        s = store()
        merge_series(s, "AAA", [["2026-03-01", 3], ["2026-01-01", 1], ["2026-02-01", 2]])
        dates = [d for d, _ in s["series"]["AAA"]]
        self.assertEqual(dates, sorted(dates))

    def test_new_value_wins_on_a_duplicate_date(self):
        s = store({"AAA": [["2026-01-01", 1.0]]})
        merge_series(s, "AAA", [["2026-01-01", 99.0]])
        self.assertEqual(s["series"]["AAA"], [["2026-01-01", 99.0]])

    def test_preserves_existing_points_outside_the_new_window(self):
        # the whole point of merging - a source returning only a recent
        # window must never truncate accumulated history
        s = store({"AAA": [["2020-01-01", 1.0], ["2020-01-02", 2.0]]})
        merge_series(s, "AAA", [["2026-01-01", 3.0]])
        self.assertEqual(len(s["series"]["AAA"]), 3)
        self.assertEqual(s["series"]["AAA"][0], ["2020-01-01", 1.0])

    def test_drops_none_values_from_new_points(self):
        s = store()
        merge_series(s, "AAA", [["2026-01-01", 1.0], ["2026-01-02", None]])
        self.assertEqual(s["series"]["AAA"], [["2026-01-01", 1.0]])

    def test_none_does_not_overwrite_an_existing_value(self):
        s = store({"AAA": [["2026-01-01", 1.0]]})
        merge_series(s, "AAA", [["2026-01-01", None]])
        self.assertEqual(s["series"]["AAA"], [["2026-01-01", 1.0]])

    def test_caps_at_max_points_keeping_the_most_recent(self):
        n = common.MAX_POINTS + 50
        start = date(2000, 1, 1)
        points = [
            [(start + timedelta(days=i)).isoformat(), float(i)] for i in range(n)
        ]
        s = store()
        merge_series(s, "AAA", points)
        merged = s["series"]["AAA"]
        self.assertEqual(len(merged), common.MAX_POINTS)
        self.assertEqual(merged[-1], points[-1])   # newest survives
        self.assertEqual(merged[0], points[50])    # oldest 50 dropped

    def test_does_not_disturb_other_instruments(self):
        s = store({"BBB": [["2026-01-01", 7.0]]})
        merge_series(s, "AAA", [["2026-01-01", 1.0]])
        self.assertEqual(s["series"]["BBB"], [["2026-01-01", 7.0]])


class RollingMeanTest(unittest.TestCase):
    def test_expands_before_the_window_is_full(self):
        # first points average over what exists so far, they are not dropped
        self.assertEqual(rolling_mean([1, 2, 3, 4], 2), [1.0, 1.5, 2.5, 3.5])

    def test_window_larger_than_the_series(self):
        self.assertEqual(rolling_mean([1, 2, 3], 20), [1.0, 1.5, 2.0])

    def test_output_length_matches_input(self):
        self.assertEqual(len(rolling_mean(list(range(50)), 20)), 50)

    def test_empty_input(self):
        self.assertEqual(rolling_mean([], 20), [])

    def test_constant_series_is_its_own_mean(self):
        self.assertEqual(rolling_mean([5.0] * 10, 20), [5.0] * 10)


class PercentileOfLastTest(unittest.TestCase):
    def test_highest_value_is_100(self):
        self.assertEqual(percentile_of_last([1, 2, 3, 4, 5]), 100.0)

    def test_lowest_value_is_0(self):
        self.assertEqual(percentile_of_last([5, 4, 3, 2, 1]), 0.0)

    def test_middle_value(self):
        self.assertEqual(percentile_of_last([1, 3, 2]), 50.0)

    def test_too_short_to_rank_returns_neutral(self):
        self.assertEqual(percentile_of_last([]), 50.0)
        self.assertEqual(percentile_of_last([42]), 50.0)

    def test_always_within_bounds(self):
        for values in ([1, 1, 1, 1], [0, 100, 50], [-5, -1, -3]):
            p = percentile_of_last(values)
            self.assertGreaterEqual(p, 0.0)
            self.assertLessEqual(p, 100.0)

    def test_ties_count_as_not_below(self):
        # all equal - nothing is strictly below, so 0
        self.assertEqual(percentile_of_last([2, 2, 2]), 0.0)


if __name__ == "__main__":
    unittest.main()
