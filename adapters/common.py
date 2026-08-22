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
MAX_EVENTS = 500   # per instrument, plenty for years of RNS filings


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


# ---------------------------------------------------------------------------
# Event store (data/events.json) - director dealings and TR-1 notifications.
# Shape differs from the time series: one list of event dicts per instrument
# rather than [date, value] pairs, so it needs its own merge.
# ---------------------------------------------------------------------------


def load_events():
    path = os.path.join(DATA_DIR, "events.json")
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {"updated": None, "events": {}}


def event_key(event):
    """Identity of an event, for de-duplication across runs.

    RNS announcements carry no stable public id in every feed, so fall back
    to the fields that together make a filing unique in practice. If a source
    does supply an id, it wins - that is the only fully reliable key.
    """
    if event.get("source_id"):
        return ("id", event["source_id"])
    return (
        event.get("date"),
        event.get("kind"),
        event.get("who"),
        event.get("value_gbp"),
    )


def merge_events(store, instrument_id, new_events):
    """Merge event dicts into the store, newest wins on a repeated key.

    Same contract as merge_series: adapters re-poll a recent window every
    run, so this has to be idempotent or the same filing accumulates
    duplicates on every cron.
    """
    by_key = {event_key(e): e for e in store["events"].get(instrument_id, [])}
    for event in new_events:
        by_key[event_key(event)] = event
    merged = sorted(
        by_key.values(), key=lambda e: (e.get("date") or "", e.get("kind") or "")
    )
    store["events"][instrument_id] = merged[-MAX_EVENTS:]


def save_events(store):
    store["updated"] = date.today().isoformat()
    path = os.path.join(DATA_DIR, "events.json")
    with open(path, "w") as f:
        json.dump(store, f, separators=(",", ":"))
    total = sum(len(v) for v in store["events"].values())
    print(f"wrote events.json ({len(store['events'])} instruments, {total} events)")
