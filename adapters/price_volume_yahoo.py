"""Daily price and volume from Yahoo Finance via yfinance.

Replaces volume_alphavantage.py. Alpha Vantage's free tier allowed about 25
requests a day, one per instrument, which capped the dashboard at 22
instruments and turned every addition into a trade-off against refresh
frequency. Yahoo has no comparable per-day quota and yfinance batches many
tickers into a single download, so the instrument list can now grow without
the rotation logic and without paying anyone.

The old adapter is kept in the repo but is no longer in the workflow - see
the decision log. Yahoo is an unofficial API and does break; if it does,
that file is the fallback.

Symbols come from meta.json ("yahoo" on the instrument), falling back to a
derivation: an LSE id like SHEL.LON is SHEL.L on Yahoo, and a US id is
already the Yahoo symbol.

Metrics written, unchanged from before so the front end needs no edit:
  price         - daily close in the instrument's own currency
  volume        - shares traded
  volume_ratio  - volume / its own 20-day average

Two things to watch, both handled below:
  - Yahoo quotes LSE lines in pence (GBp), which is what meta.json already
    records as GBX. A source that silently switched to pounds would divide
    every UK price by 100 and quietly corrupt the store, so a merge that
    moves an instrument's last close by more than 20x is refused and
    reported rather than written.
  - Yahoo rate-limits aggressively. Failures are per-chunk and non-fatal,
    and the merge means a missed run costs freshness, not history.
"""

import json
import os
import sys
import time

import yfinance as yf

from common import (DATA_DIR, load_meta, load_store, merge_series, save_store,
                    rolling_mean)

HISTORY_PERIOD = "5y"   # MAX_POINTS in common.py caps the stored series anyway
CHUNK = 12              # tickers per download call
PAUSE_SECONDS = 2.0
UNIT_JUMP = 20.0        # a bigger move than this on the last close is a unit change, not a price

# Yahoo's currency codes for the ones meta.json uses. "GBp" is pence.
YAHOO_CURRENCY = {"GBp": "GBX", "GBX": "GBX", "GBP": "GBP", "USD": "USD"}


def yahoo_symbol(inst):
    """Yahoo ticker for an instrument.

    meta.json wins; otherwise derive. ".LON" is our own suffix for an LSE
    listing and Yahoo spells that ".L".
    """
    explicit = inst.get("yahoo")
    if explicit:
        return explicit
    if inst["id"].endswith(".LON"):
        return inst["id"][:-4] + ".L"
    return inst["id"]


def _column(frame, field, symbol):
    """One field's series for one ticker, from either frame shape.

    yfinance returns MultiIndex columns (field, ticker) for a batch and flat
    columns for a single ticker, so both have to be handled.
    """
    cols = frame.columns
    if hasattr(cols, "levels") and cols.nlevels > 1:
        if (field, symbol) in cols:
            return frame[(field, symbol)]
        return None
    if field in cols:
        return frame[field]
    return None


def frame_to_series(frame, symbol):
    """(closes, volumes) as [[iso date, value], ...] - pure, no network."""
    if frame is None or not len(frame):
        return [], []
    closes_col = _column(frame, "Close", symbol)
    volumes_col = _column(frame, "Volume", symbol)
    if closes_col is None:
        return [], []

    closes, volumes = [], []
    for ts, value in closes_col.items():
        if value is None or value != value:      # NaN
            continue
        closes.append([ts.date().isoformat(), round(float(value), 4)])
    if volumes_col is not None:
        for ts, value in volumes_col.items():
            if value is None or value != value:
                continue
            volumes.append([ts.date().isoformat(), float(value)])
    return closes, volumes


def unit_change(existing_points, new_points):
    """True when the last close moves by a factor that can only be a change
    of units - pence to pounds, say - rather than a price move."""
    if not existing_points or not new_points:
        return False
    old = existing_points[-1][1]
    new = new_points[-1][1]
    if not old or not new or old <= 0 or new <= 0:
        return False
    factor = max(old, new) / min(old, new)
    return factor >= UNIT_JUMP


def currency_matches(meta_currency, yahoo_currency):
    """None = could not verify; True/False = verified against Yahoo.

    The pence/pounds label cannot be inferred from prices alone - 47.14 is a
    fine number in either unit - so a new instrument's label is checked
    against what Yahoo itself reports before its first points are merged.
    Existing series are protected by the 20x unit-change guard instead.
    """
    mapped = YAHOO_CURRENCY.get((yahoo_currency or "").strip() or None)
    if mapped is None:
        return None
    return mapped == meta_currency


def fetch_currency(symbol):
    """Thin network wrapper - Yahoo's reported trade currency for a symbol."""
    info = yf.Ticker(symbol).fast_info
    return info.get("currency")


def fetch_frame(symbols, period=HISTORY_PERIOD):
    """Thin wrapper over the one network call."""
    return yf.download(
        symbols, period=period, interval="1d", auto_adjust=False,
        progress=False, threads=False, group_by="column",
    )


VOLUME_GAP_DAYS = 30  # a zero-run at least this long is missing data, not quiet trading


def ratio_points(points):
    """volume_ratio series from a volume series: value / own 20-day average.

    Yahoo carries no volume history at all for some LSE ETC lines - SSLN has
    a single real day in March 2023 and then nothing until spring 2026 - and
    serves the missing stretch as zeros. Folding those into the average
    understates it and inflates the first real ratios by up to 20x, so any
    run of VOLUME_GAP_DAYS or more consecutive zeros is treated as a data
    gap and removed before the rolling average, wherever it falls. Short
    zero runs are kept: a thin line's quiet day is a genuine observation.
    Leading zeros are dropped outright - there is nothing to average before
    the first trade.
    """
    live = []
    zero_run = []
    for point in points:
        if point[1] > 0:
            if len(zero_run) < VOLUME_GAP_DAYS and live:
                live.extend(zero_run)
            zero_run = []
            live.append(point)
        else:
            zero_run.append(point)
    # a trailing zero-run shorter than a gap is recent quiet days, keep it
    if live and len(zero_run) < VOLUME_GAP_DAYS:
        live.extend(zero_run)
    if not live:
        return []
    dates = [p[0] for p in live]
    vols = [p[1] for p in live]
    avg20 = rolling_mean(vols, 20)
    return [[d, round(v / a, 3)] for d, v, a in zip(dates, vols, avg20) if a > 0]


def chunks(items, size):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def main():
    meta = load_meta()
    instruments = [i for i in meta["instruments"]
                   if i["type"] in ("equity", "etf", "fund")]
    by_symbol = {yahoo_symbol(i): i for i in instruments}
    print(f"price_volume_yahoo: {len(by_symbol)} symbols, period {HISTORY_PERIOD}")

    price_store = load_store("price")
    volume_store = load_store("volume")
    ratio_store = load_store("volume_ratio")

    fetched = 0
    mismatches, no_data = [], []
    for group in chunks(sorted(by_symbol), CHUNK):
        try:
            frame = fetch_frame(group)
        except Exception as e:  # noqa: BLE001 - per-chunk, never fatal
            print(f"  chunk {group[0]}..{group[-1]} failed: {e}", file=sys.stderr)
            continue

        for symbol in group:
            inst = by_symbol[symbol]
            closes, volumes = frame_to_series(frame, symbol)
            if not closes:
                print(f"  {inst['id']} ({symbol}): no data", file=sys.stderr)
                no_data.append(inst["id"])
                continue
            existing = price_store["series"].get(inst["id"], [])
            if not existing:
                # First points for this instrument: verify the meta.json
                # currency label against Yahoo before anything is merged. A
                # wrong label shows pounds as pence and mis-scales ETF flows,
                # and once merged it looks exactly like data.
                try:
                    reported = fetch_currency(symbol)
                except Exception as e:  # noqa: BLE001
                    reported, e_note = None, e
                verdict = currency_matches(inst.get("currency"), reported)
                if verdict is False and inst.get("trust_currency"):
                    # Yahoo's metadata is sometimes wrong about its own lines -
                    # fast_info says USD for CPG.L and IHG.L while Yahoo's own
                    # quote pages show pence. trust_currency marks a label a
                    # human has verified against the quote page, so it seeds
                    # on the meta.json label and only logs the disagreement.
                    print(f"  {inst['id']} ({symbol}): Yahoo metadata says "
                          f"{reported} but the label is human-verified "
                          f"({inst.get('currency')}) - seeding on meta.json",
                          file=sys.stderr)
                elif verdict is False:
                    print(f"  {inst['id']} ({symbol}): meta.json says "
                          f"{inst.get('currency')} but Yahoo trades it in "
                          f"{reported} - refusing to seed until meta.json is "
                          f"corrected", file=sys.stderr)
                    # last_close makes the fix self-evident from health.json:
                    # 2456.0 is plainly pence, 33.1 plainly dollars
                    mismatches.append({"id": inst["id"], "meta": inst.get("currency"),
                                       "yahoo": reported,
                                       "last_close": closes[-1][1]})
                    continue
                if verdict is None:
                    print(f"  {inst['id']} ({symbol}): currency could not be "
                          f"verified - seeding on the meta.json label", file=sys.stderr)
            if unit_change(existing, closes):
                print(f"  {inst['id']} ({symbol}): last close moved from "
                      f"{existing[-1][1]} to {closes[-1][1]} - looks like a "
                      f"change of units, refusing to merge", file=sys.stderr)
                continue
            merge_series(price_store, inst["id"], closes)
            # An OEIC has no exchange volume - it prices once a day at NAV.
            # Yahoo pads fund volume with zeros; storing them would draw a
            # meaningless panel and feed fake quiet days into the ratio.
            if inst["type"] != "fund":
                merge_series(volume_store, inst["id"], volumes)
            fetched += 1
            print(f"  {inst['id']:10} ({symbol:9}) {len(closes):5} closes "
                  f"{closes[0][0]} -> {closes[-1][0]}")
        time.sleep(PAUSE_SECONDS)

    # recompute the ratio over the full merged history, so a partial run does
    # not leave stale averages behind
    for sym, points in volume_store["series"].items():
        ratio_store["series"][sym] = ratio_points(points)

    save_store("price", price_store)
    save_store("volume", volume_store)
    save_store("volume_ratio", ratio_store)

    # health.json makes the failures loud: check.yml runs the test suite after
    # every fetch, and a currency mismatch fails a test there, so a wrong
    # label in meta.json turns CI red instead of quietly showing wrong prices.
    with open(os.path.join(DATA_DIR, "health.json"), "w") as f:
        json.dump({"currency_mismatches": mismatches, "no_data": sorted(no_data)},
                  f, separators=(",", ":"))
    print(f"price_volume_yahoo: {fetched} of {len(by_symbol)} instruments updated, "
          f"{len(mismatches)} currency mismatch(es), {len(no_data)} with no data")


if __name__ == "__main__":
    main()
