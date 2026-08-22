"""Informed-money events - director dealings (PDMR) and major shareholding
notifications (TR-1).

Status: the store plumbing below is complete and runs on every cron. What is
missing is fetch_announcements() - the one function that needs a live feed.
Everything else (instrument matching, headline classification, de-duplication,
merging, writing) is done and exercised each run, so wiring a source up is a
single-function job.

Store shape - data/events.json, distinct from the time-series metrics:
  { "updated": "...", "events": { "<instrument id>": [
      { "date": "2026-08-20", "kind": "pdmr_buy", "who": "J Smith (CFO)",
        "value_gbp": 250000, "detail": "...", "source_id": "..." }, ... ] } }

Event kinds: pdmr_buy, pdmr_sell, tr1_up, tr1_down.

Intended route: poll the LSE RNS feed (or Investegate's announcement listings)
for the two standard headlines below, matched against the tickers in
meta.json, then parse the standard-format tables in the body. US equivalent
later: SEC Form 4 via EDGAR full-text search (free, JSON).

Until a source is wired up this writes the events file unchanged, so the UI
keeps rendering the insider panel in its empty state rather than erroring.
"""

import sys
from datetime import date, timedelta

from common import load_events, load_meta, merge_events, save_events

# RNS headlines are standardised, so the family of an announcement can be read
# from the headline alone. Direction (bought vs sold, increased vs decreased)
# only appears in the body, so the parser decides that, not this map.
ANNOUNCEMENT_TYPES = {
    "Director/PDMR Shareholding": "pdmr",
    "Holding(s) in Company": "tr1",
}

# how far back to re-poll on each run; overlap is harmless because
# merge_events de-duplicates, and it covers a missed or failed run
LOOKBACK_DAYS = 14


def uk_tickers(meta):
    """Map RNS ticker -> instrument id, e.g. "SHEL" -> "SHEL.LON".

    RNS identifies companies by bare ticker; meta.json ids carry the .LON
    suffix. Only UK equities file RNS announcements, so ETFs and commodities
    are excluded.
    """
    return {
        inst["id"].split(".")[0]: inst["id"]
        for inst in meta["instruments"]
        if inst["type"] == "equity" and inst["id"].endswith(".LON")
    }


def fetch_announcements(ticker, since):
    """Return [event dict, ...] for one ticker since a date. NOT YET WIRED UP.

    When implementing: return dicts matching the store shape in the module
    docstring. Set "source_id" to the RNS announcement id if the feed exposes
    one - merge_events prefers it over the (date, kind, who, value) fallback,
    which is the difference between reliable de-duplication and occasional
    duplicate filings.

    Keep failures per-ticker and non-fatal, as the ETF adapter does: one
    unreachable page must not lose the whole run.
    """
    raise NotImplementedError("no RNS source wired up yet")


def main():
    meta = load_meta()
    tickers = uk_tickers(meta)
    store = load_events()
    since = (date.today() - timedelta(days=LOOKBACK_DAYS)).isoformat()

    wired = True
    found = 0
    for ticker, instrument_id in sorted(tickers.items()):
        try:
            events = fetch_announcements(ticker, since)
        except NotImplementedError:
            wired = False
            break
        except Exception as e:  # noqa: BLE001 - keep the run alive per-ticker
            print(f"  {ticker}: failed: {e}", file=sys.stderr)
            continue
        if events:
            merge_events(store, instrument_id, events)
            found += len(events)

    if wired:
        print(f"informed_money: {found} events across {len(tickers)} tickers")
    else:
        print(
            f"informed_money: no source wired up yet - "
            f"{len(tickers)} UK tickers ready, store left unchanged"
        )

    save_events(store)


if __name__ == "__main__":
    main()
