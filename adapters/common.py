"""Shared helpers for the market-flows data store.

Store shape: one JSON file per metric in /data, e.g. data/volume.json:
  { "updated": "2026-08-22", "series": { "<instrument id>": [["2026-08-21", 1234.0], ...] } }

Series are date-ascending. Adapters merge new points into existing files,
so history accumulates in the repo even where the source API only returns
a recent window.
"""

import json
import os
from datetime import date

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
MAX_POINTS = 2600  # ~10 years of daily points per instrument


def load_meta():
    with open(os.path.join(DATA_DIR, "meta.json")) as f:
        return json.load(f)


def load_store(metric):
    path = os.path.join(DATA_DIR, f"{metric}.json")
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {"updated": None, "series": {}}


def merge_series(store, instrument_id, new_points):
    """Merge [[date, value], ...] into a store, newest values winning."""
    existing = dict(store["series"].get(instrument_id, []))
    existing.update({d: v for d, v in new_points if v is not None})
    merged = sorted(existing.items())[-MAX_POINTS:]
    store["series"][instrument_id] = [[d, v] for d, v in merged]


def save_store(metric, store):
    store["updated"] = date.today().isoformat()
    path = os.path.join(DATA_DIR, f"{metric}.json")
    with open(path, "w") as f:
        json.dump(store, f, separators=(",", ":"))
    print(f"wrote {metric}.json ({len(store['series'])} instruments)")


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
    path = os.path.join(DATA_DIR, "events.json")
    if os.path.exists(path):
        with open(path) as f:
            store = json.load(f)
    else:
        store = {"updated": None, "events": {}}
    store.setdefault("events", {})
    return store


def save_events(store):
    store["updated"] = date.today().isoformat()
    path = os.path.join(DATA_DIR, "events.json")
    with open(path, "w") as f:
        json.dump(store, f, separators=(",", ":"))
    print(f"wrote events.json ({len(store['events'])} instruments)")


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
