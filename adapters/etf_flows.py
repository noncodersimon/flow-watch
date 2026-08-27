"""ETF net flows via the shares-outstanding method.

    flow(t) = (shares_outstanding(t) - shares_outstanding(t-1)) * price(t)

This measures actual creation/redemption of ETF units - the closest free
approximation to true net buying. Source: Yahoo Finance via yfinance.

Strategy:
  1. Try yfinance get_shares_full() for historical shares outstanding
     (coverage is good for US ETFs, patchy for LSE listings).
  2. Fall back to a point-in-time sample (fast_info / info). Because the
     store merges history in the repo, daily point samples still build a
     full series over time - flows simply start accumulating from today
     for instruments without backfill.

Metrics written:
  etf_shares    - shares outstanding (the raw ingredient, kept for audit)
  etf_flow      - signed daily net flow in fund currency (GBX scaled to GBP)
  etf_flow_pct  - flow as % of AUM that day (the normalised screener score)

Honest caveats, so the numbers are never over-read:
  - For cross-listed funds (e.g. VWRL) Yahoo may report fund-wide or
    listing-specific shares depending on the ticker - treat as estimate.
  - Creations settle with a lag, so daily attribution is approximate.
  - Yahoo occasionally rate-limits GitHub runner IPs; failures here are
    per-instrument and non-fatal.
"""

import math
import sys
from datetime import date, timedelta

import yfinance as yf

from common import load_meta, load_store, merge_series, save_store

HISTORY_YEARS = 5


def daily_shares(ticker):
    """Return [[date, shares], ...] - historical if available, else today's sample."""
    start = date.today() - timedelta(days=365 * HISTORY_YEARS)
    try:
        full = ticker.get_shares_full(start=start.isoformat())
        if full is not None and len(full):
            by_day = {}
            for ts, val in full.items():
                by_day[ts.date().isoformat()] = float(val)  # last sample per day wins
            return sorted(by_day.items())
    except Exception as e:  # noqa: BLE001
        print(f"  get_shares_full failed: {e}", file=sys.stderr)

    for source in (
        lambda: ticker.fast_info.get("shares"),
        lambda: ticker.info.get("sharesOutstanding"),
    ):
        try:
            n = source()
            if n:
                return [[date.today().isoformat(), float(n)]]
        except Exception:  # noqa: BLE001
            continue
    return []


def close_prices(ticker):
    """Return {date: close}, skipping any day Yahoo could not price.

    Yahoo pads a day a thin line did not trade with a NaN close, and one of
    those turns every flow computed against it into NaN - which is not even
    valid JSON, so it breaks the front end rather than showing a gap.
    price_volume_yahoo.frame_to_series has guarded this from the start;
    this adapter had not.
    """
    try:
        hist = ticker.history(period=f"{HISTORY_YEARS}y", auto_adjust=False)
    except Exception as e:  # noqa: BLE001
        print(f"  history failed: {e}", file=sys.stderr)
        return {}
    out = {}
    for ts, c in hist["Close"].items():
        if c is None:
            continue
        value = float(c)
        if math.isfinite(value):
            out[ts.date().isoformat()] = value
    return out


def price_on_or_before(closes_sorted, d):
    """closes_sorted: sorted [[date, close]]. Nearest close at or before d."""
    best = None
    for cd, cv in closes_sorted:
        if cd > d:
            break
        best = cv
    return best


def main():
    meta = load_meta()
    etfs = [i for i in meta["instruments"] if i["type"] == "etf" and i.get("yahoo")]

    shares_store = load_store("etf_shares")
    flow_store = load_store("etf_flow")
    pct_store = load_store("etf_flow_pct")

    for inst in etfs:
        print(f"fetching {inst['id']} (yahoo: {inst['yahoo']})")
        try:
            t = yf.Ticker(inst["yahoo"])
            shares = daily_shares(t)
            closes = sorted(close_prices(t).items())
        except Exception as e:  # noqa: BLE001
            print(f"  failed entirely: {e}", file=sys.stderr)
            continue
        if not shares:
            print("  no shares-outstanding data")
            continue

        merge_series(shares_store, inst["id"], shares)

        # recompute flows from the FULL merged shares history, so point
        # samples accumulated across runs turn into real deltas
        merged = shares_store["series"][inst["id"]]
        scale = 0.01 if inst.get("currency") == "GBX" else 1.0  # pence -> pounds

        flows, pcts = [], []
        for (d_prev, s_prev), (d, s) in zip(merged, merged[1:]):
            px = price_on_or_before(closes, d)
            # px > 0 rather than merely present: a zero close would divide
            # by zero on the AUM line below
            if px is None or not math.isfinite(px) or px <= 0 or s <= 0:
                continue
            flow = (s - s_prev) * px * scale
            aum = s * px * scale
            flows.append([d, round(flow, 0)])
            pcts.append([d, round(100.0 * flow / aum, 3)])

        if flows:
            flow_store["series"][inst["id"]] = flows
            pct_store["series"][inst["id"]] = pcts
            print(f"  {len(flows)} flow points")
        else:
            print("  shares series too short for deltas yet (needs 2+ days)")

    save_store("etf_shares", shares_store)
    save_store("etf_flow", flow_store)
    save_store("etf_flow_pct", pct_store)


if __name__ == "__main__":
    main()
