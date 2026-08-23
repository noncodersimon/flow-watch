"""Shared helpers for the market-flows data store.

Store shape: one file per INSTRUMENT under data/instruments/, e.g.
data/instruments/BP.LON.json:
  { "id": "BP.LON", "updated": "2026-08-23",
    "metrics": { "price": [["2026-08-21", 549.5], ...], "volume": [...] },
    "events": [ { "date": ..., "kind": ..., ... }, ... ] }

plus data/summary.json - a small digest of latest values and 30-day event
counts, so the screener can draw a table without downloading any history.

Why per instrument rather than per metric. The front end used to fetch
every metric for every instrument on every visit: at five years and 26
instruments that is around 500KB gzipped, and it grows on both axes at
once. A chart needs one instrument and the screener needs one number per
instrument, so neither ever wanted the whole store. This shape serves both
and lets history depth and the instrument list grow independently.

Series are date-ascending. Adapters merge new points into what is already
there, so history accumulates in the repo even where a source only returns
a recent window.

Adapters are unaffected by the layout: load_store / merge_series /
save_store still work a metric at a time, and the per-instrument files are
assembled underneath.
"""

import json
import os
from datetime import date, timedelta

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
INSTRUMENT_DIR = os.path.join(DATA_DIR, "instruments")
MAX_POINTS = 2600  # ~10 years of daily points per instrument
EVENT_WINDOW_DAYS = 30  # what the screener's Insider column counts

_DOCS = None  # id -> document, loaded once per process


def _doc_path(instrument_id):
    return os.path.join(INSTRUMENT_DIR, instrument_id + ".json")


def _blank(instrument_id):
    return {"id": instrument_id, "updated": None, "metrics": {}, "events": []}


def _load_docs():
    global _DOCS
    if _DOCS is not None:
        return _DOCS
    _DOCS = {}
    if os.path.isdir(INSTRUMENT_DIR):
        for name in sorted(os.listdir(INSTRUMENT_DIR)):
            if not name.endswith(".json"):
                continue
            with open(os.path.join(INSTRUMENT_DIR, name)) as f:
                doc = json.load(f)
            doc.setdefault("id", name[:-5])
            doc.setdefault("metrics", {})
            doc.setdefault("events", [])
            _DOCS[doc["id"]] = doc
    return _DOCS


def _doc(instrument_id):
    docs = _load_docs()
    if instrument_id not in docs:
        docs[instrument_id] = _blank(instrument_id)
    return docs[instrument_id]


def _write_docs():
    os.makedirs(INSTRUMENT_DIR, exist_ok=True)
    today = date.today().isoformat()
    written = 0
    for instrument_id, doc in _load_docs().items():
        if not doc["metrics"] and not doc["events"]:
            continue
        doc["updated"] = today
        with open(_doc_path(instrument_id), "w") as f:
            json.dump(doc, f, separators=(",", ":"))
        written += 1
    return written


def load_meta():
    with open(os.path.join(DATA_DIR, "meta.json")) as f:
        return json.load(f)


def load_store(metric):
    """The adapter-facing view: one metric across every instrument."""
    series = {}
    for instrument_id, doc in _load_docs().items():
        points = doc["metrics"].get(metric)
        if points:
            series[instrument_id] = points
    return {"updated": None, "series": series}


def merge_series(store, instrument_id, new_points):
    """Merge [[date, value], ...] into a store, newest values winning."""
    existing = dict(store["series"].get(instrument_id, []))
    existing.update({d: v for d, v in new_points if v is not None})
    merged = sorted(existing.items())[-MAX_POINTS:]
    store["series"][instrument_id] = [[d, v] for d, v in merged]


def save_store(metric, store):
    """Write one metric back into the per-instrument files."""
    for instrument_id, points in store["series"].items():
        _doc(instrument_id)["metrics"][metric] = points
    count = _write_docs()
    print(f"wrote {metric} into {len(store['series'])} of {count} instrument files")


def rolling_mean(values, window):
    out = []
    for i in range(len(values)):
        chunk = values[max(0, i - window + 1): i + 1]
        out.append(sum(chunk) / len(chunk))
    return out


def percentile_of_last(values):
    """Rank of the final value within the whole list, 0-100."""
    if len(values) < 2:
        return 50.0
    last = values[-1]
    below = sum(1 for v in values if v < last)
    return round(100.0 * below / (len(values) - 1), 1)

# --------------------------------------------------------------------------
# event store (data/events.json) - shared by the RNS and SEC adapters
# --------------------------------------------------------------------------

MAX_EVENTS_PER_ID = 400


def load_events():
    """The adapter-facing view: events across every instrument."""
    events = {}
    for instrument_id, doc in _load_docs().items():
        if doc["events"]:
            events[instrument_id] = doc["events"]
    return {"updated": None, "events": events}


def save_events(store):
    for instrument_id, events in store["events"].items():
        _doc(instrument_id)["events"] = events
    count = _write_docs()
    print(f"wrote events into {len(store['events'])} of {count} instrument files")


def event_key(ev):
    """Identity of an event for de-duplication.

    A source that can mint a stable reference (the SEC adapter uses the
    filing accession plus the transaction's position in it) sets "ref" and
    that wins. Without one, fall back to the value-based key. That fallback
    is not safe on its own for Form 4, where one insider can file two sales
    on the same day at different prices and both carry a null value_gbp -
    they would collapse into a single event.
    """
    if ev.get("ref"):
        return ("ref", ev["ref"])
    return (ev.get("date"), ev.get("kind"), ev.get("who"), ev.get("value_gbp"))


def merge_events(store, instrument_id, new_events):
    """Merge events for one instrument, de-duplicating and keeping date order."""
    existing = store["events"].get(instrument_id, [])
    seen = {event_key(e) for e in existing}
    for ev in new_events:
        k = event_key(ev)
        if k in seen:
            continue
        seen.add(k)
        existing.append(ev)
    existing.sort(key=lambda e: (e.get("date") or "", e.get("kind") or ""))
    store["events"][instrument_id] = existing[-MAX_EVENTS_PER_ID:]

# --------------------------------------------------------------------------
# summary - the small file the screener reads instead of any history
# --------------------------------------------------------------------------

SUMMARY_METRICS = ("price", "volume_ratio", "etf_flow_pct", "cot_percentile")


def build_summary():
    """Latest value per metric, plus recent event counts broken down by kind.

    The counts are by kind rather than totalled here on purpose. Which kinds
    count as insider activity is a judgement the UI already encodes - awards
    and Rule 10b5-1 plan trades are excluded - and duplicating that rule in
    Python would give it two places to drift.
    """
    cutoff = (date.today() - timedelta(days=EVENT_WINDOW_DAYS)).isoformat()
    rows = {}
    for instrument_id, doc in sorted(_load_docs().items()):
        row = {}
        for metric in SUMMARY_METRICS:
            points = doc["metrics"].get(metric)
            if points:
                row[metric] = points[-1][1]
                if metric == "price":
                    row["date"] = points[-1][0]
        counts = {}
        for ev in doc["events"]:
            if (ev.get("date") or "") >= cutoff:
                counts[ev["kind"]] = counts.get(ev["kind"], 0) + 1
        if counts:
            row["events30"] = counts
        if row:
            rows[instrument_id] = row
    return {"updated": date.today().isoformat(),
            "window_days": EVENT_WINDOW_DAYS,
            "instruments": rows}


def save_summary():
    summary = build_summary()
    with open(os.path.join(DATA_DIR, "summary.json"), "w") as f:
        json.dump(summary, f, separators=(",", ":"))
    print(f"wrote summary.json ({len(summary['instruments'])} instruments)")
    return summary
