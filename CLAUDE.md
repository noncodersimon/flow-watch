# flow-watch - project context

Dashboard of buying/selling activity across stocks, ETFs and commodities.
UK lens by default, global available. Owner: Simon (noncodersimon).

## Architecture - do not change without discussion

- No server, no build step, no npm. GitHub Actions (.github/workflows/fetch.yml,
  06:30 UTC Mon-Sat) runs Python adapters that fetch data, compute derived daily
  figures, and commit JSON to /data. GitHub Pages serves the static front end
  (index.html, app.js, style.css at repo root) which reads that JSON.
- Store shape: one JSON file per metric -
  { "updated": "YYYY-MM-DD", "series": { "<id>": [["YYYY-MM-DD", value], ...] } }
  plus data/events.json for event-type data (director dealings, TR-1s).
- History accumulates by MERGING in the repo (adapters/common.py). Sources that
  return only a recent window still build full series over time. Never overwrite
  a series wholesale; always merge.
- data/meta.json is the single source of truth for instruments, hierarchy
  (region/sector/type), currencies and source IDs (cftc_code, yahoo). Add
  instruments there; adapters and UI pick them up.
- New data sources = new adapter + new metric file. Front end reads only from
  /data. This keeps phase 2 (paid tick data) a pure addition.

## Metric semantics - keep the UI honest

- volume / volume_ratio: activity, NOT net buying. UI copy must never imply
  direction from volume alone.
- etf_flow: daily change in shares outstanding x price (yfinance). Genuine
  creation/redemption, but an estimate - keep the caveat notes in the UI.
- cot_net / cot_percentile: CFTC non-commercial net positioning, weekly.
- events.json kinds: pdmr_buy, pdmr_sell, tr1_up, tr1_down.

## Known constraints

- Alpha Vantage free tier ~25 req/day: volume adapter rotates 22 symbols/run
  and pulls "full" history on first seed, "compact" thereafter (150-point
  threshold).
- LSE instruments quote in pence: currency "GBX" in meta.json, scaled /100
  where flows are computed in pounds. Preserve this.
- Yahoo (yfinance) sometimes rate-limits GitHub runners - ETF flow failures
  are per-instrument and must stay non-fatal.
- CFTC COT is weekly (Friday publication, Tuesday data).

## Conventions

- UK English. Short hyphens or " - ", never em dashes - in code comments, UI
  copy, commit messages, everything.
- Front end: vanilla JS + ECharts from CDN. Bump APP_VERSION in app.js on
  every front-end change (visible in footer - used to confirm deploys).
- Palette: navy #1F3864, blue #2E5A9C, up #1a7f4b, down #c0392b.
- Deliver complete working files, not fragments. Mobile-first - check iPhone
  Safari rendering for UI changes.
- Run ./check.sh before committing - it does py_compile on the adapters,
  node --check on app.js, and the test suite. Everything must pass.

## Local development

No build step, so there is nothing to compile - just serve the repo root.

- ./serve.sh            - static preview on http://localhost:8000
- ./check.sh            - full check: syntax + tests (run before every commit)
- python3 -m unittest discover -s tests -v   - tests only

Tests are stdlib unittest, no pip install needed, and never hit the network.
They cover the pure helpers in adapters/common.py and validate that every file
in /data matches the documented store shape and agrees with meta.json. Adapter
fetch functions are deliberately not tested - they are thin wrappers over live
APIs, and mocking them would test the mock.

Only adapters need the runtime dependency: pip install -r requirements.txt.

## Current state (Aug 2026, v0.2)

Live: volume/price/ratio (Alpha Vantage), COT (CFTC Socrata), ETF flows
(yfinance shares-outstanding method - LSE listings may need days of samples
before deltas exist). Stub: adapters/informed_money.py (empty events store).

## Roadmap

1. Verify 10y backfill completes across the rotation (2 runs) and 1Y/5Y ranges
   differentiate.
2. Build informed_money.py: parse LSE RNS / Investegate for PDMR dealings and
   TR-1 holdings notifications for tickers in meta.json; later SEC Form 4 via
   EDGAR. UI already renders event markers and the Insider column.
3. Sector-level aggregation view (category -> sector -> instrument drill-down).
4. Phase 2: paid tick data (Polygon/Databento) -> order-flow imbalance and
   block-trade flags as new adapters. No rework of existing code expected.
