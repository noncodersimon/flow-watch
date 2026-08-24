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
    "etf_shares",
]

EVENT_KINDS = {"pdmr_buy", "pdmr_sell", "pdmr_award", "pdmr_scheduled", "tr1_up", "tr1_down"}
INSTRUMENT_TYPES = {"equity", "etf", "commodity", "fund"}


def store(metric):
    """Per-metric view, assembled from the per-instrument files the way the
    adapters see it."""
    common._DOCS = None
    return common.load_store(metric)


def instrument_docs():
    d = common.INSTRUMENT_DIR
    if not os.path.isdir(d):
        return {}
    out = {}
    for name in sorted(os.listdir(d)):
        if name.endswith(".json"):
            with open(os.path.join(d, name), encoding="utf-8") as f:
                out[name[:-5]] = json.load(f)
    return out


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

    def test_every_region_has_a_benchmark_that_exists(self):
        """app.js rebases the chart against meta.benchmarks[region]. A region
        with no entry silently loses the overlay; an entry naming an
        instrument that is not in the store silently draws nothing."""
        benchmarks = self.meta.get("benchmarks", {})
        known = {i["id"] for i in self.meta["instruments"]}
        for region in self.meta["regions"]:
            with self.subTest(region=region):
                self.assertIn(region, benchmarks, "region has no benchmark")
                self.assertIn(benchmarks[region], known, "benchmark is not an instrument")

    def test_benchmarks_are_instruments_with_a_price_series(self):
        # a benchmark with no price history rebases against nothing
        docs = instrument_docs()
        for region, bid in self.meta.get("benchmarks", {}).items():
            with self.subTest(region=region, benchmark=bid):
                doc = docs.get(bid)
                if doc is None:
                    continue  # not fetched yet; the adapter reports it
                self.assertIn("price", doc["metrics"])

    def test_instrument_benchmark_overrides_are_valid(self):
        known = {i["id"] for i in self.meta["instruments"]}
        for inst in self.meta["instruments"]:
            if "benchmark" in inst:
                with self.subTest(instrument=inst["id"]):
                    self.assertIn(inst["benchmark"], known)
                    self.assertNotEqual(inst["benchmark"], inst["id"],
                                        "an instrument cannot benchmark against itself")

    def test_currencies_are_ones_the_ui_and_adapters_understand(self):
        # GBX drives the /100 scaling in etf_flows.py and the "p" suffix in
        # the UI, so an unknown code would silently fall through both.
        for inst in self.meta["instruments"]:
            with self.subTest(instrument=inst["id"]):
                self.assertIn(inst["currency"], {"GBX", "GBP", "USD"})

    def test_lse_lines_carry_a_real_quote_currency(self):
        """This has been wrong twice, each time by assuming a blanket rule.
        First it asserted every .LON instrument was GBX, which mislabelled
        the pound-quoted ETFs (VUKE at 47 is 47 pounds, not 47 pence). Then
        it asserted GBX-or-GBP, which is still not true: plenty of LSE ETF
        and ETC lines trade in dollars - the WisdomTree commodity ETCs and
        iShares USD lines among them. The honest rule is that the label must
        be a currency the LSE actually quotes lines in, and the seed-time
        check in price_volume_yahoo.py verifies each label against what
        Yahoo reports, so a wrong guess is refused and surfaced rather than
        merged."""
        for inst in self.meta["instruments"]:
            if inst["id"].endswith(".LON"):
                with self.subTest(instrument=inst["id"]):
                    self.assertIn(inst["currency"], {"GBX", "GBP", "USD"})

    # LSE quotes these in pence like every other ordinary share, but Yahoo -
    # our price source - serves their history converted to US dollars (first
    # seed came back at 30.93 for Compass against a ~2,300p quote). The label
    # describes the stored data, so these two are USD until the source
    # changes. See the decision log.
    UK_EQUITIES_YAHOO_SERVES_IN_USD = {"CPG.LON", "IHG.LON"}

    def test_uk_equities_are_still_priced_in_pence(self):
        # the pence convention does hold for ordinary shares, and that is what
        # makes BP read 549.50p rather than 549 pounds
        for inst in self.meta["instruments"]:
            if inst["id"].endswith(".LON") and inst["type"] == "equity":
                if inst["id"] in self.UK_EQUITIES_YAHOO_SERVES_IN_USD:
                    continue
                with self.subTest(instrument=inst["id"]):
                    self.assertEqual(inst["currency"], "GBX")


class SeriesStoreTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.meta = load("meta.json")
        cls.known_ids = {i["id"] for i in cls.meta["instruments"]}

    def stores(self):
        for metric in SERIES_METRICS:
            s = store(metric)
            if s["series"]:
                yield metric, s

    def test_there_are_instrument_documents(self):
        self.assertTrue(instrument_docs(), "data/instruments/ is empty")

    def test_document_shape(self):
        for iid, doc in instrument_docs().items():
            with self.subTest(instrument=iid):
                self.assertEqual(doc.get("id"), iid, "id must match the filename")
                self.assertIn(iid, self.known_ids, "not an instrument in meta.json")
                self.assertIsInstance(doc.get("metrics"), dict)
                self.assertIsInstance(doc.get("events"), list)
                self.assertTrue(is_iso_date(doc.get("updated")))

    def test_documents_hold_only_known_metrics(self):
        known = set(SERIES_METRICS)
        for iid, doc in instrument_docs().items():
            with self.subTest(instrument=iid):
                self.assertEqual(set(doc["metrics"]) - known, set())

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
        for sid, points in store("cot_percentile")["series"].items():
            with self.subTest(instrument=sid):
                for d, v in points:
                    self.assertGreaterEqual(v, 0.0, f"{sid} {d}")
                    self.assertLessEqual(v, 100.0, f"{sid} {d}")

    def test_volume_ratios_are_never_negative(self):
        """Used to assert strictly positive, which assumed every instrument
        trades every day. Thinly traded LSE ETF lines have genuine
        zero-volume days - 29 of the 58 funds added on 2026-08-23 have at
        least one - and a ratio of exactly 0 is the honest record of that.
        Negative is still impossible."""
        for sid, points in store("volume_ratio")["series"].items():
            with self.subTest(instrument=sid):
                for d, v in points:
                    self.assertGreaterEqual(v, 0.0, f"{sid} {d}")

    def test_share_counts_are_positive(self):
        for sid, points in store("etf_shares")["series"].items():
            with self.subTest(instrument=sid):
                for d, v in points:
                    self.assertGreater(v, 0.0, f"{sid} {d} share count should be > 0")

    def test_cot_series_belong_to_commodities(self):
        commodities = {
            i["id"] for i in self.meta["instruments"] if i["type"] == "commodity"
        }
        for metric in ("cot_net", "cot_percentile"):
            with self.subTest(metric=metric):
                self.assertTrue(set(store(metric)["series"]) <= commodities)

    def test_etf_flow_series_belong_to_etfs(self):
        etfs = {i["id"] for i in self.meta["instruments"] if i["type"] == "etf"}
        for metric in ("etf_flow", "etf_flow_pct", "etf_shares"):
            with self.subTest(metric=metric):
                self.assertTrue(set(store(metric)["series"]) <= etfs)


class EventStoreTest(unittest.TestCase):
    def test_events_are_well_formed(self):
        meta = load("meta.json")
        known = {i["id"] for i in meta["instruments"]}
        for sid, events in instrument_docs().items():
            with self.subTest(instrument=sid):
                self.assertIn(sid, known)
                for ev in events["events"]:
                    self.assertTrue(is_iso_date(ev.get("date")))
                    self.assertIn(ev.get("kind"), EVENT_KINDS)


class CacheBustingTest(unittest.TestCase):
    """There is no build step to fingerprint assets, so index.html carries the
    app version on the asset URLs by hand. If the two drift, a returning
    browser serves a stale app.js against fresh data and the footer version
    silently disagrees with what is deployed - which is exactly how a shipped
    feature looks missing."""

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(REPO_ROOT, "app.js"), encoding="utf-8") as f:
            cls.app = f.read()
        with open(os.path.join(REPO_ROOT, "index.html"), encoding="utf-8") as f:
            cls.html = f.read()

    def version(self):
        m = re.search(r'const APP_VERSION\s*=\s*"([^"]+)"', self.app)
        self.assertIsNotNone(m, "APP_VERSION not found in app.js")
        return m.group(1)

    def test_assets_carry_the_app_version(self):
        version = self.version()
        for asset in ("app.js", "style.css"):
            with self.subTest(asset=asset):
                self.assertIn(f"{asset}?v={version}", self.html,
                              f"index.html must load {asset}?v={version}")

    def test_no_asset_is_loaded_unversioned(self):
        for asset in ("app.js", "style.css"):
            with self.subTest(asset=asset):
                self.assertNotRegex(self.html, rf'"{re.escape(asset)}"',
                                    f"{asset} is loaded without a version query")


class HealthTest(unittest.TestCase):
    """price_volume_yahoo.py writes data/health.json on every run. A currency
    mismatch there means meta.json labels an instrument in a currency Yahoo
    says it does not trade in - the adapter refuses to seed it, and this test
    turns CI red (check.yml runs after every fetch) so the wrong label gets
    fixed instead of quietly showing pounds as pence."""

    def test_no_currency_mismatches(self):
        health = load("health.json")
        if health is None:
            self.skipTest("no fetch has written health.json yet")
        self.assertEqual(health.get("currency_mismatches", []), [],
                         "fix the currency in meta.json for these instruments")


class SummaryTest(unittest.TestCase):
    """summary.json is the only file the screener loads, so if it drifts from
    the instrument documents the table silently shows stale numbers."""

    @classmethod
    def setUpClass(cls):
        cls.summary = load("summary.json")
        cls.meta = load("meta.json")
        cls.docs = instrument_docs()

    def test_exists_and_is_shaped(self):
        self.assertIsNotNone(self.summary, "data/summary.json is missing")
        self.assertTrue(is_iso_date(self.summary["updated"]))
        self.assertIsInstance(self.summary["instruments"], dict)

    def test_rows_are_known_instruments(self):
        known = {i["id"] for i in self.meta["instruments"]}
        self.assertEqual(set(self.summary["instruments"]) - known, set())

    def test_latest_values_match_the_documents(self):
        for iid, row in self.summary["instruments"].items():
            doc = self.docs.get(iid, {"metrics": {}})
            for metric in common.SUMMARY_METRICS:
                if metric not in row:
                    continue
                points = doc["metrics"].get(metric)
                with self.subTest(instrument=iid, metric=metric):
                    self.assertTrue(points, f"{iid} summarises {metric} it does not have")
                    self.assertEqual(row[metric], points[-1][1],
                                     "summary is stale against the series")

    def test_previous_close_matches_the_series(self):
        for iid, row in self.summary["instruments"].items():
            if "price_prev" not in row:
                continue
            points = (self.docs.get(iid, {"metrics": {}})["metrics"].get("price")) or []
            with self.subTest(instrument=iid):
                self.assertGreaterEqual(len(points), 2,
                                        f"{iid} has price_prev but under two price points")
                self.assertEqual(row["price_prev"], points[-2][1])

    def test_weekly_ratio_is_sane_where_present(self):
        for iid, row in self.summary["instruments"].items():
            if "volume_ratio_week" in row:
                with self.subTest(instrument=iid):
                    self.assertGreaterEqual(row["volume_ratio_week"], 0.0)

    def test_event_counts_use_known_kinds(self):
        for iid, row in self.summary["instruments"].items():
            for kind in row.get("events30", {}):
                with self.subTest(instrument=iid):
                    self.assertIn(kind, EVENT_KINDS)

    def test_every_instrument_with_metrics_is_summarised(self):
        """Metrics only, deliberately. An instrument can hold events but no
        series - Compass Group's first seed was refused by the currency check
        while its RNS events landed fine - and if all its events are older
        than the 30-day window there is nothing to summarise. Requiring a row
        for it would force empty rows into summary.json."""
        for iid, doc in self.docs.items():
            if doc["metrics"]:
                with self.subTest(instrument=iid):
                    self.assertIn(iid, self.summary["instruments"])


if __name__ == "__main__":
    unittest.main()
