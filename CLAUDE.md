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
- Claude Code web sandboxes block outbound market data. Every source host
  (Yahoo, CFTC, Alpha Vantage, LSE, Investegate, SEC) returns 403 at the
  egress proxy; pypi and GitHub are allowed. Adapters therefore cannot be
  validated against live data in a web session - only in Actions, which has
  open network. Build parsers so the fetch is a thin, separable layer and
  the parsing runs against saved fixtures offline.

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
- Record meaningful design decisions in the log below - anything a future
  session might otherwise undo or re-litigate: what was chosen, what was
  rejected, and why. Routine implementation detail does not belong there.
  Date each entry and append; do not rewrite past entries, supersede them.

## Local development

No build step, so there is nothing to compile - just serve the repo root.

- ./serve.sh            - static preview on http://localhost:8000
- ./check.sh            - full check: syntax + tests (run before every commit)
- python3 -m unittest discover -s tests -t tests -v   - tests only

Tests are stdlib unittest, no pip install needed, and never hit the network.
Scope is deliberately narrow: they validate that what the adapters committed
to /data matches the documented store shape and agrees with meta.json. Adapter
logic is not unit tested - see the decision log for why.

Only adapters need the runtime dependency: pip install -r requirements.txt.

## Design decisions

Append-only. Newest last.

### 2026-08-22 - Scaffolding added, existing code left alone
The repo already held a working v0.2. Chose to add tooling around it rather
than restructure anything - no existing file was modified. Rejected: a
rewrite, which would have thrown away accumulated /data history that cannot
be re-fetched (Alpha Vantage only serves a recent window on the free tier).

### 2026-08-22 - Tests are stdlib unittest, not pytest
Zero pip install, so ./check.sh runs anywhere including a cold container.
Rejected pytest: it is nicer to write, but adding a dev dependency to a
project whose whole premise is "no build step, one runtime dep" is a poor
trade at this size.

### 2026-08-22 - Adapter fetch functions are deliberately not tested
They are thin wrappers over live APIs. Mocking them would assert that the
mock returns what the mock was told to return. What is tested instead: the
pure helpers in common.py, and the shape of what actually landed in /data.
If a fetch function grows real logic, that logic should move into a pure
function and be tested there.

### 2026-08-22 - CI also runs after the fetch workflow, not just on push
The adapters commit to the repo unattended on a cron, so a malformed store
reaches the live site with nobody watching. check.yml therefore triggers on
workflow_run of "Fetch market data" as well as on push. This is the main
reason the /data integrity tests exist at all.

### 2026-08-22 - No tests/__init__.py
Discovery runs with -t tests, so test modules import as top-level and
"import common" resolves via tests/context.py. Adding __init__.py makes them
package-relative and breaks running a test file directly. Do not add one.

### 2026-08-22 - SessionStart hook is synchronous and remote-only
Synchronous so dependencies are guaranteed present before the session starts,
avoiding a race where tests run before yfinance is installed. Remote-only so
it never touches a local machine's environment. Async is a one-line change if
session startup latency becomes annoying.

### 2026-08-22 - data/etf_shares.json was missing because the adapter never ran
Not a bug. Commit e316a70 shipped an early etf_flows.py; the bot run fa91e5d
wrote etf_flow.json and etf_flow_pct.json from it. Commit 9233c6b then
rewrote the adapter to add the shares-outstanding method and the etf_shares
audit store, and no run has happened since. The next Actions run populates it.

### 2026-08-22 - Test scope trimmed to /data integrity only
Narrows the two entries above rather than reversing them. Dropped the
common.py helper tests and the adapter compile/import tests; kept the ~20
that validate /data and meta.json. Reason: this early the project is one
person iterating fast, and tests over internal pure functions mostly slow
that down. The /data tests stay because the fetch workflow commits
unattended on a cron, so nothing else is watching what lands. Adapter syntax
is still checked by py_compile in check.sh, just not as a unit test. If
common.py starts changing often the merge_series tests are worth restoring -
they are in git history at commit d904319.

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
