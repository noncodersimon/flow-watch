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
