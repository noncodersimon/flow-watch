"""ETF net flows - v0 stub.

There is no clean free API for daily ETF creations/redemptions. Two
routes to implement here, in order of preference:

1. Shares-outstanding method (best free approximation):
      flow(t) = (shares_outstanding(t) - shares_outstanding(t-1)) * NAV(t)
   Shares outstanding is published daily by most issuers (iShares and
   Vanguard product pages expose it, and some data APIs carry it).
   This measures actual creation/redemption - the real thing.

2. Scrape headline weekly flows from a public aggregator and store at
   weekly granularity. Fragile, but quick.

Until one is wired up, this adapter writes an empty store so the front
end can render the ETF flow panel with a "no data yet" state rather
than erroring.

Metrics (once live):
  etf_flow      - signed daily/weekly net flow, fund currency
  etf_flow_pct  - flow as % of fund AUM (the normalised screener score)
"""

from common import load_store, save_store


def main():
    for metric in ("etf_flow", "etf_flow_pct"):
        store = load_store(metric)
        save_store(metric, store)
    print("etf_flows: stub run complete (no source wired up yet)")


if __name__ == "__main__":
    main()
