# Market Flows

A dashboard of buying and selling activity across stocks, ETFs and commodities -
UK lens by default, global view available. No server: GitHub Actions fetches data
on a schedule and commits derived JSON to the repo; GitHub Pages serves a static
front end that reads it.

## Metric definitions

Be precise about what each number is - several look similar but mean different things.

| Metric | File | What it actually is |
|---|---|---|
| price | data/price.json | Daily close (GBX for LSE equities, note pence vs pounds) |
| volume | data/volume.json | Total shares traded - activity, NOT net buying |
| volume_ratio | data/volume_ratio.json | Volume / its own 20-day average. The screener's "unusualness" score |
| cot_net | data/cot_net.json | CFTC non-commercial (speculator) net position, contracts, weekly |
| cot_percentile | data/cot_percentile.json | cot_net as a percentile of its own fetched history |
| etf_flow / etf_flow_pct | (stub) | True net creations/redemptions - the only genuine "net buying" here |
| events | data/events.json | Insider activity. UK: PDMR dealings and TR-1 holdings from RNS via Investegate. US: SEC Form 4. Kinds: `pdmr_buy`, `pdmr_sell`, `pdmr_award`, `pdmr_scheduled`, `tr1_up`, `tr1_down`. `value_gbp` covers sterling tranches only and is null for dealings quoted in EUR/USD |

Store shape for time series: `{ "updated": "YYYY-MM-DD", "series": { "<id>": [["YYYY-MM-DD", value], ...] } }`.
History accumulates by merging in the repo, so sources that only return a recent
window still build a full series over time.

## Setup

1. Create the GitHub repo and push this folder.
2. No API key is needed for price and volume - Yahoo, CFTC, Investegate and
   SEC are all free and keyless.
3. Enable Pages: Settings → Pages → Source: Deploy from a branch → main, / (root).
4. Actions tab → "Fetch market data" → Run workflow (first run seeds the data).
5. The site appears at `https://<user>.github.io/<repo>/`.

## Using it

The site opens on a default instrument chart (a FTSE 100 tracker). Use the
picker above the chart to browse by region, then sector, then instrument, or
follow "All instruments and screener" for the sortable table. Ranges are 1W to
Max; there is no 1D because the sources are daily bars.

## Constraints worth knowing

- Price and volume come from Yahoo via yfinance. No per-day quota, so the
  instrument list is not capped, but Yahoo is unofficial and rate-limits at
  times. Failures are per-chunk and non-fatal.
- CFTC COT data is weekly (published Fridays, data as of Tuesday).
- LSE prices are in pence (GBX) from Yahoo, as they were from Alpha Vantage.
  A 20x jump in an instrument's last close is treated as a change of units and
  refused rather than merged.
- `pdmr_award` (option exercises, vests, nil-cost awards, and the sales that
  settle them) is deliberately NOT counted as insider buying or selling. It is
  calendar-driven, and it is the large majority of PDMR announcements - 112 of
  the 143 events in the first live run. Counting it would make every board look
  permanently bearish.
- Investegate lists an announcement under every company it names, so a bank's
  page carries TR-1s about entirely different issuers. The adapter drops any
  event whose issuer does not match the instrument exactly.
- `pdmr_scheduled` (US Rule 10b5-1 plan trades) is likewise not counted as
  signal - the plan is adopted months before it executes.
- SEC wants a contact address in the User-Agent and answers 403 without one.
  Set the `SEC_CONTACT` repository variable (Settings -> Secrets and variables
  -> Actions -> Variables tab) to an address you actually monitor. The bare
  address is enough - the adapter prefixes the tool name itself. Unroutable
  addresses are refused, `users.noreply.github.com` among them.

## Roadmap

- [x] Phase 1: volume + COT live; RNS director dealings / TR-1 parsing live
      (see adapters/informed_money.py)
- [ ] ETF flows (shares-outstanding method - see adapters/etf_flows.py): runs in
      Actions, cannot be validated from a Claude Code web session
- [x] SEC Form 4 for US names (AAPL, MSFT, NVDA, JPM)
- [ ] Sector-level aggregation view (category → sector → instrument drill-down)
- [ ] Phase 2: paid tick data (Polygon / Databento) → true order-flow imbalance
      and block-trade flags as new adapters, no rework
