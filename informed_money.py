"""Informed-money events - director dealings (PDMR) and major
shareholding notifications (TR-1) - v0 stub.

Store shape differs from the time-series metrics - this is an event
store, data/events.json:
  { "updated": "...", "events": { "<instrument id>": [
      { "date": "2026-08-20", "kind": "pdmr_buy", "who": "J Smith (CFO)",
        "value_gbp": 250000, "detail": "..." }, ... ] } }

Event kinds: pdmr_buy, pdmr_sell, tr1_up, tr1_down.

Implementation route: poll the LSE RNS feed (or Investegate's
announcement listings) for "Director/PDMR Shareholding" and "Holding(s)
in Company" announcements matching the tickers in meta.json, then parse
the standard-format tables. US equivalents later: SEC Form 4 via EDGAR
full-text search (free, JSON).

Until wired up, writes an empty events file so the UI renders the
insider panel in its empty state.
"""

import json
import os
from datetime import date

from common import DATA_DIR


def main():
    path = os.path.join(DATA_DIR, "events.json")
    if os.path.exists(path):
        with open(path) as f:
            store = json.load(f)
    else:
        store = {"updated": None, "events": {}}
    store["updated"] = date.today().isoformat()
    with open(path, "w") as f:
        json.dump(store, f, separators=(",", ":"))
    print("informed_money: stub run complete (no source wired up yet)")


if __name__ == "__main__":
    main()
