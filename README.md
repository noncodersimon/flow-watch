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
| events | data/events.json | Director dealings (PDMR) and TR-1 major holding notifications, from RNS via Investegate. Kinds: `pdmr_buy`, `pdmr_sell`, `pdmr_award`, `tr1_up`, `tr1_down`. `value_gbp` covers sterling tranches only and is null for dealings quoted in EUR/USD |

Store shape for time series: `{ "updated": "YYYY-MM-DD", "series": { "<id>": [["YYYY-MM-DD", value], ...] } }`.
History accumulates by merging in the repo, so sources that only return a recent
window still build a full series over time.

## Setup

1. Create the GitHub repo and push this folder.
2. Add the Actions secret: repo Settings → Secrets and variables → Actions →
   New repository secret → name `ALPHAVANTAGE_KEY`.
3. Enable Pages: Settings → Pages → Source: Deploy from a branch → main, / (root).
4. Actions tab → "Fetch market data" → Run workflow (first run seeds the data).
5. The site appears at `https://<user>.github.io/<repo>/`.

## Constraints worth knowing

- Alpha Vantage free tier allows ~25 requests/day. The volume adapter rotates
  through the instrument list (22 calls/run), so with more than ~22 equities+ETFs
  each one refreshes every couple of days rather than daily. Fine at current list
  size; upgrade the key or switch source if the list grows a lot.
- CFTC COT data is weekly (published Fridays, data as of Tuesday).
- LSE prices from Alpha Vantage are in pence (GBX).
- `pdmr_award` (option exercises, vests, nil-cost awards, and the sales that
  settle them) is deliberately NOT counted as insider buying or selling. It is
  calendar-driven, and it is the large majority of PDMR announcements - 112 of
  the 143 events in the first live run. Counting it would make every board look
  permanently bearish.
- Investegate lists an announcement under every company it names, so a bank's
  page carries TR-1s about entirely different issuers. The adapter drops any
  event whose issuer does not match the instrument exactly.

## Roadmap

- [x] Phase 1: volume + COT live; RNS director dealings / TR-1 parsing live
      (see adapters/informed_money.py)
- [ ] ETF flows (shares-outstanding method - see adapters/etf_flows.py): runs in
      Actions, cannot be validated from a Claude Code web session
- [ ] SEC Form 4 for US names via EDGAR full-text search
- [ ] Sector-level aggregation view (category → sector → instrument drill-down)
- [ ] Phase 2: paid tick data (Polygon / Databento) → true order-flow imbalance
      and block-trade flags as new adapters, no rework
