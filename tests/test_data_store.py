"""Validates the committed contents of /data against the documented store
shape, and checks that meta.json agrees with the adapters and the UI.

This is the contract between the adapters and the front end. The adapters
write it unattended on a cron and commit straight to the repo, so a broken
shape reaches the live site before anyone looks. These tests read only what
is already committed - no network.
"""

import json
import os
import re
import unittest
from datetime import date

from context import DATA_DIR, REPO_ROOT

import common

ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# metric files using the time-series shape; events.json is checked separately
SERIES_METRICS = [
    "price",
    "volume",
    "volume_ratio",
    "cot_net",
    "cot_percentile",
    "etf_flow",
    "etf_flow_pct",
]

EVENT_KINDS = {"pdmr_buy", "pdmr_sell", "tr1_up", "tr1_down"}
INSTRUMENT_TYPES = {"equity", "etf", "commodity"}


def load(name):
    path = os.path.join(DATA_DIR, name)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def is_iso_date(value):
    if not isinstance(value, str) or not ISO_DATE.match(value):
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


class MetaTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.meta = load("meta.json")

    def test_meta_exists_and_has_required_keys(self):
        self.assertIsNotNone(self.meta, "data/meta.json is missing")
        for key in ("version", "regions", "default_region", "instruments"):
            self.assertIn(key, self.meta)

    def test_default_region_is_a_known_region(self):
        self.assertIn(self.meta["default_region"], self.meta["regions"])

    def test_instrument_ids_are_unique(self):
        ids = [i["id"] for i in self.meta["instruments"]]
        dupes = {i for i in ids if ids.count(i) > 1}
        self.assertEqual(dupes, set(), "duplicate instrument ids")

    def test_every_instrument_has_the_required_fields(self):
        for inst in self.meta["instruments"]:
            with self.subTest(instrument=inst.get("id")):
                for key in ("id", "name", "type", "region", "sector", "currency"):
                    self.assertIn(key, inst)
                self.assertIn(inst["type"], INSTRUMENT_TYPES)
                self.assertIn(inst["region"], self.meta["regions"])

    def test_commodities_carry_a_cftc_code(self):
        # cot_cftc.py silently skips any commodity without one
        for inst in self.meta["instruments"]:
            if inst["type"] == "commodity":
                with self.subTest(instrument=inst["id"]):
                    self.assertTrue(inst.get("cftc_code"))

    def test_etfs_carry_a_yahoo_ticker(self):
        # etf_flows.py filters on this, so a missing one means no flow data
        for inst in self.meta["instruments"]:
            if inst["type"] == "etf":
                with self.subTest(instrument=inst["id"]):
                    self.assertTrue(inst.get("yahoo"))

    def test_lse_instruments_are_priced_in_pence(self):
        # GBX drives the /100 scaling in etf_flows.py and the "p" suffix in
        # the UI - a .LON instrument in GBP would misprice by 100x
        for inst in self.meta["instruments"]:
            if inst["id"].endswith(".LON"):
                with self.subTest(instrument=inst["id"]):
                    self.assertEqual(inst["currency"], "GBX")


class SeriesStoreTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.meta = load("meta.json")
        cls.known_ids = {i["id"] for i in cls.meta["instruments"]}

    def stores(self):
        for metric in SERIES_METRICS:
            store = load(f"{metric}.json")
            if store is not None:
                yield metric, store

    def test_every_series_metric_file_exists(self):
        for metric in SERIES_METRICS:
            with self.subTest(metric=metric):
                self.assertIsNotNone(
                    load(f"{metric}.json"), f"data/{metric}.json is missing"
                )

    def test_top_level_shape(self):
        for metric, store in self.stores():
            with self.subTest(metric=metric):
                self.assertIn("updated", store)
                self.assertIn("series", store)
                self.assertIsInstance(store["series"], dict)
                if store["updated"] is not None:
                    self.assertTrue(is_iso_date(store["updated"]))

    def test_points_are_date_value_pairs(self):
        for metric, store in self.stores():
            for sid, points in store["series"].items():
                with self.subTest(metric=metric, instrument=sid):
                    self.assertIsInstance(points, list)
                    for point in points:
                        self.assertEqual(len(point), 2)
                        self.assertTrue(is_iso_date(point[0]), f"bad date {point[0]!r}")
                        self.assertIsInstance(point[1], (int, float))
                        self.assertNotIsInstance(point[1], bool)

    def test_dates_ascend_with_no_duplicates(self):
        # merge_series guarantees this; a violation means something wrote
        # a series directly instead of merging
        for metric, store in self.stores():
            for sid, points in store["series"].items():
                with self.subTest(metric=metric, instrument=sid):
                    dates = [p[0] for p in points]
                    self.assertEqual(dates, sorted(dates), "dates out of order")
                    self.assertEqual(len(dates), len(set(dates)), "duplicate dates")

    def test_series_stay_within_the_point_cap(self):
        for metric, store in self.stores():
            for sid, points in store["series"].items():
                with self.subTest(metric=metric, instrument=sid):
                    self.assertLessEqual(len(points), common.MAX_POINTS)

    def test_series_ids_are_all_known_instruments(self):
        # an orphan id means an instrument was renamed or removed from
        # meta.json without its accumulated history being dealt with
        for metric, store in self.stores():
            orphans = set(store["series"]) - self.known_ids
            with self.subTest(metric=metric):
                self.assertEqual(orphans, set(), f"unknown ids in {metric}.json")

    def test_percentiles_are_within_bounds(self):
        store = load("cot_percentile.json")
        for sid, points in store["series"].items():
            with self.subTest(instrument=sid):
                for d, v in points:
                    self.assertGreaterEqual(v, 0.0, f"{sid} {d}")
                    self.assertLessEqual(v, 100.0, f"{sid} {d}")

    def test_volume_ratios_are_positive(self):
        store = load("volume_ratio.json")
        for sid, points in store["series"].items():
            with self.subTest(instrument=sid):
                for d, v in points:
                    self.assertGreater(v, 0.0, f"{sid} {d} ratio should be > 0")

    def test_cot_series_belong_to_commodities(self):
        commodities = {
            i["id"] for i in self.meta["instruments"] if i["type"] == "commodity"
        }
        for metric in ("cot_net", "cot_percentile"):
            store = load(f"{metric}.json")
            with self.subTest(metric=metric):
                self.assertTrue(set(store["series"]) <= commodities)

    def test_etf_flow_series_belong_to_etfs(self):
        etfs = {i["id"] for i in self.meta["instruments"] if i["type"] == "etf"}
        for metric in ("etf_flow", "etf_flow_pct"):
            store = load(f"{metric}.json")
            with self.subTest(metric=metric):
                self.assertTrue(set(store["series"]) <= etfs)


class EventStoreTest(unittest.TestCase):
    def test_shape(self):
        store = load("events.json")
        self.assertIsNotNone(store, "data/events.json is missing")
        self.assertIn("updated", store)
        self.assertIn("events", store)
        self.assertIsInstance(store["events"], dict)

    def test_events_are_well_formed(self):
        # currently empty - informed_money.py is a stub. These assertions
        # start doing real work the moment the RNS adapter lands.
        store = load("events.json")
        meta = load("meta.json")
        known = {i["id"] for i in meta["instruments"]}
        for sid, events in store["events"].items():
            with self.subTest(instrument=sid):
                self.assertIn(sid, known)
                for ev in events:
                    self.assertTrue(is_iso_date(ev.get("date")))
                    self.assertIn(ev.get("kind"), EVENT_KINDS)


class FrontEndAgreementTest(unittest.TestCase):
    """app.js reads /data directly, so its metric list has to match reality."""

    def test_every_metric_app_js_loads_exists_on_disk(self):
        with open(os.path.join(REPO_ROOT, "app.js"), encoding="utf-8") as f:
            source = f.read()
        match = re.search(r"const METRICS\s*=\s*\[(.*?)\]", source, re.S)
        self.assertIsNotNone(match, "could not find METRICS in app.js")
        metrics = re.findall(r'"([^"]+)"', match.group(1))
        self.assertTrue(metrics, "METRICS list parsed empty")
        for metric in metrics:
            with self.subTest(metric=metric):
                self.assertTrue(
                    os.path.exists(os.path.join(DATA_DIR, f"{metric}.json")),
                    f"app.js loads data/{metric}.json but it does not exist",
                )


if __name__ == "__main__":
    unittest.main()
