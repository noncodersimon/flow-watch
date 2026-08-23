"""RETIRED - kept as a fallback, not run by the workflow.

Superseded by price_volume_yahoo.py on 2026-08-23. Alpha Vantage's free
tier allowed about 25 requests a day, one per instrument, which capped the
dashboard at 22 instruments. Yahoo has no comparable quota.

Yahoo is an unofficial API and does break. If it does, put this back in
.github/workflows/fetch.yml in place of price_volume_yahoo.py and restore
the ALPHAVANTAGE_KEY env line - the repository secret is still there. Note
the 22-symbol ceiling comes back with it.

Fetch daily price + volume from Alpha Vantage and compute the
unusual-volume ratio (today's volume / 20-day average).

Free-tier limit is ~25 requests/day, so instruments are processed in a
daily rotation: each run handles up to MAX_CALLS symbols, starting at an
offset based on the day of the year. Because merged history accumulates
in the repo, every instrument still builds a complete series - it just
refreshes every couple of days rather than daily once the list outgrows
the quota.

Requires env var ALPHAVANTAGE_KEY (set as a GitHub Actions secret).
"""

import os
import sys
import time
import json
import urllib.request
from datetime import date

from common import load_meta, load_store, merge_series, save_store, rolling_mean

MAX_CALLS = 22  # leave headroom under the 25/day free tier
PAUSE_SECONDS = 15  # free tier also rate-limits per minute


def fetch_daily(symbol, api_key, outputsize):
    url = (
        "https://www.alphavantage.co/query?function=TIME_SERIES_DAILY"
        f"&symbol={symbol}&outputsize={outputsize}&apikey={api_key}"
    )
    with urllib.request.urlopen(url, timeout=30) as r:
        payload = json.load(r)
    ts = payload.get("Time Series (Daily)")
    if not ts:
        print(f"  {symbol}: no data ({list(payload.keys())})", file=sys.stderr)
        return None
    rows = sorted(ts.items())  # date ascending
    closes = [[d, float(v["4. close"])] for d, v in rows]
    volumes = [[d, float(v["5. volume"])] for d, v in rows]
    return closes, volumes


def main():
    api_key = os.environ.get("ALPHAVANTAGE_KEY")
    if not api_key:
        sys.exit("ALPHAVANTAGE_KEY not set")

    meta = load_meta()
    symbols = [i["id"] for i in meta["instruments"] if i["type"] in ("equity", "etf")]

    # daily rotation through the symbol list
    offset = (date.today().toordinal() * MAX_CALLS) % len(symbols)
    todays = [symbols[(offset + n) % len(symbols)] for n in range(min(MAX_CALLS, len(symbols)))]
    print(f"processing {len(todays)} of {len(symbols)} symbols (rotation offset {offset})")

    price_store = load_store("price")
    volume_store = load_store("volume")
    ratio_store = load_store("volume_ratio")

    for sym in todays:
        # first seed pulls full history (20y, capped at MAX_POINTS on merge);
        # thereafter compact daily top-ups keep responses small
        seeded = len(price_store["series"].get(sym, [])) >= 150
        size = "compact" if seeded else "full"
        print(f"fetching {sym} ({size})")
        result = fetch_daily(sym, api_key, size)
        if result:
            closes, volumes = result
            merge_series(price_store, sym, closes)
            merge_series(volume_store, sym, volumes)
        time.sleep(PAUSE_SECONDS)

    # recompute ratio from full merged history so the rotation doesn't
    # leave stale averages behind
    for sym, points in volume_store["series"].items():
        dates = [p[0] for p in points]
        vols = [p[1] for p in points]
        avg20 = rolling_mean(vols, 20)
        ratios = [
            [d, round(v / a, 3)] for d, v, a in zip(dates, vols, avg20) if a > 0
        ]
        ratio_store["series"][sym] = ratios

    save_store("price", price_store)
    save_store("volume", volume_store)
    save_store("volume_ratio", ratio_store)


if __name__ == "__main__":
    main()
