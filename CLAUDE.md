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
- events.json kinds: pdmr_buy, pdmr_sell, pdmr_award, pdmr_scheduled,
  tr1_up, tr1_down. pdmr_award is share-scheme activity (option exercise,
  vest, nil-cost award, and any sale that only settles one).
  pdmr_scheduled is a US Rule 10b5-1 plan trade. Both are drawn on the
  chart but never counted as insider buying or selling - see the decision
  log. Only pdmr_buy, pdmr_sell, tr1_up and tr1_down feed the Insider
  column.

## Known constraints

- Alpha Vantage free tier ~25 req/day: volume adapter rotates 22 symbols/run
  and pulls "full" history on first seed, "compact" thereafter (150-point
  threshold). meta.json now holds exactly 22 equity+ETF symbols, so every
  instrument still refreshes daily - the 23rd starts the rotation and
  everything slows down. Check this before adding instruments.
- SEC needs a User-Agent naming a contact address; it rejects unroutable
  ones, users.noreply.github.com among them. A bare address is accepted, so
  sec_form4.py normalises SEC_CONTACT to "flow-watch <address>" and falls
  back to a placeholder. Set the repo variable to a real address.
- LSE instruments quote in pence: currency "GBX" in meta.json, scaled /100
  where flows are computed in pounds. Preserve this.
- Yahoo (yfinance) sometimes rate-limits GitHub runners - ETF flow failures
  are per-instrument and must stay non-fatal.
- CFTC COT is weekly (Friday publication, Tuesday data).
- Web session network access is partial - check before assuming. As of
  2026-08-22 plain HTTP clients (urllib, curl) reach CFTC, Investegate, LSE
  and Alpha Vantage fine. Two things still do not work in a web session:
  yfinance, whose curl_cffi transport is reset by the egress proxy
  ("Recv failure: Connection reset by peer"), and Yahoo itself, which
  rate-limits the shared sandbox IP (429) even over plain urllib. So ETF
  flows remain Actions-only. SEC needs a declared User-Agent with a contact
  address or it returns 403; www.sec.gov and efts.sec.gov then work, but
  data.sec.gov is blocked at the proxy.
- Keep the fetch a thin, separable layer regardless, with parsing as pure
  functions over saved fixtures. That is what makes a source testable when
  the sandbox cannot reach it, and it is how informed_money.py is built.

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

## Current state (Aug 2026, v0.3)

Live: volume/price/ratio (Alpha Vantage), COT (CFTC Socrata), ETF flows
(yfinance shares-outstanding method - LSE listings may need days of samples
before deltas exist), and informed money on both sides of the Atlantic -
Investegate RNS for the 12 UK equities (PDMR dealings and TR-1 major
holdings) and SEC Form 4 for the 4 US equities.

## Roadmap

1. Verify 10y backfill completes across the rotation (2 runs) and 1Y/5Y ranges
   differentiate.
2. Sector-level aggregation view (category -> sector -> instrument drill-down).
3. Phase 2: paid tick data (Polygon/Databento) -> order-flow imbalance and
   block-trade flags as new adapters. No rework of existing code expected.

### 2026-08-22 - The web sandbox network constraint is superseded, but only partly
The previous entry in Known constraints said every source host 403s at the
egress proxy. That is no longer true: CFTC, Investegate, LSE and Alpha
Vantage all answer plain urllib/curl, and the COT adapter was validated
live this session. Two blockers remain and they are different from each
other - yfinance uses curl_cffi to impersonate a browser TLS fingerprint,
which the MITM proxy resets outright, and Yahoo separately rate-limits the
shared sandbox IP with a 429. So "no network" was the wrong diagnosis to
carry forward, but "ETF flows are Actions-only" happens to still hold.
Check the specific host and client before assuming either way.

### 2026-08-22 - Share-scheme dealings are recorded but never counted as a signal
PDMR announcements are dominated by option exercises, vests and the
immediate sales that settle the tax on them. Those are calendar-driven, so
counting them as selling makes every board look permanently bearish - the
opposite of informed. They get their own kind, pdmr_award, are drawn on the
chart in muted grey, and are excluded from the Insider column and from any
buy/sell total. Classification puts scheme wording ahead of buy/sell
wording, so "sale of shares on exercise of nil cost options" is an award,
not a sell. Rejected: dropping them entirely, which would hide real
dilution and make the chart look emptier than the RNS record actually is.

### 2026-08-22 - RNS issuer matching is exact, and it is not optional
Investegate indexes an announcement under every company it names, so a
bank's page is full of TR-1s where the bank is the holder and the issuer is
somebody else. Both TR-1s found on the HSBC and Barclays pages this session
were exactly that - the issuers were Informa and Central Asia Metals. Every
parsed event is therefore dropped unless the issuer named in the form
matches the instrument exactly on a normalised name. Rejected: prefix or
substring matching, which is how "BP" silently swallows "BP Marsh &
Partners plc" and invents insider activity that never happened. Where a
company needs a different spelling, set "issuer" on it in meta.json.

### 2026-08-22 - Parser tests are back, as fixture tests
This does not reverse the 2026-08-22 decision to trim tests to /data
integrity - it is the case that entry anticipated, where "a fetch function
grows real logic, that logic should move into a pure function and be tested
there". The RNS parsers are several hundred lines of pure text handling
with no network, and the issuer guard above is the kind of bug that fails
silently and poisons the store. tests/fixtures holds real Investegate pages
saved on 2026-08-22 with scripts and styles stripped, about 190KB in total.

### 2026-08-22 - PDMR value is price x volume, not the stated total
Section 4(d) "Aggregated information" is not laid out consistently between
issuers. Mitie lists the total value first; BP lists the average price
first and the total last. Taking the first pound figure therefore recorded
a GBP 1.96m BP director sale as GBP 5.59 - a real bug caught only because
the number looked absurd next to the share price. Price x volume is the one
figure that always agrees, so it computes the value, and a stated pound
figure is used only when it corroborates to within 2%. Do not "simplify"
this back to reading the stated total.

### 2026-08-22 - PDMR values are sterling-only, and the store says which
Cross-listed issuers quote the same dealing in several currencies - one
Unilever announcement carries GBP, EUR and USD tranches of the same award,
and its ADR purchases are priced in dollars. Treating an unmarked number as
sterling is right here (every instrument covered is LSE listed), but
treating "EUR 55.82" as GBP 55.82 silently invented a GBP 150k sale. So
value_gbp is now the sum of the sterling tranches only, null when a dealing
has none, and a currency field records what the form actually said. The
event is still recorded either way - only the money figure is withheld.
Rejected: converting at an FX rate, which bolts a second data source and a
daily failure mode onto what is meant to be a signal count.

### 2026-08-22 - SEC Form 4 is its own adapter, not more of informed_money.py
The roadmap said "a new parse_form4 plus a fetch, not a rewrite", which
read as extending informed_money.py. Splitting it out instead. The two
sources share nothing but the event store: Investegate is HTML built for
humans and takes several hundred lines of text wrangling, Form 4 is
structured XML where classification is a lookup on a transaction code.
Bolting the second onto the first would have made a 900-line module with
two unrelated halves. The event-store helpers moved to common.py, which
already owns the store shape, and both adapters now use them.

### 2026-08-22 - Rule 10b5-1 trades get their own kind and are not a signal
The US analogue of the share-scheme decision above. A 10b5-1 plan is
adopted months before it executes, so the sale says something about the
calendar, not about the price. Those become pdmr_scheduled and are left
out of the buy/sell totals. It matters: of 82 US events in the first run
only 9 were discretionary dealings, 21 were plan trades and 52 were
scheme activity. Apple's filings show both kinds side by side - a
director's own 50,000 share sale against another insider's routine
vest-and-sell. Rejected: counting plan trades as ordinary sells, which
would bury the 9 that actually mean something.

### 2026-08-22 - browse-edgar type=4 is a prefix match, so owner=only is required
EDGAR matches "type" as a prefix, so type=4 also returns 424B2
prospectuses. JPMorgan files so many of those that its feed contained not
one real Form 4 and the adapter silently produced nothing for it. Apple
worked only because it files few other 4xx forms - which is exactly how
this would have shipped unnoticed. owner=only restricts the feed to
ownership filings. The exact filing-type == "4" guard stays as well, to
keep 4/A amendments out: an amendment restates a transaction already
filed, so counting it would double up.

### 2026-08-22 - Form 4 events need a stable ref to de-duplicate
The event key was date + kind + who + value_gbp. US trades are in dollars
and therefore carry a null value_gbp, so one insider filing two sales on
the same day collapsed into a single event - Levinson sold 149,527 and
100,473 Apple shares on 2026-05-06 and one of them vanished. Events may
now carry "ref", a stable source reference (the filing accession plus the
transaction's index within it), and that wins when present. UK events have
no ref and keep the old key, so committed data is unaffected.

### 2026-08-22 - The SEC contact address is configuration, not a constant
SEC fair access wants a User-Agent naming a contact it can reach, and it
refuses unroutable ones - users.noreply.github.com is rejected outright.
Hardcoding a personal address into a public repo is the wrong default, so
sec_form4.py reads SEC_CONTACT and falls back to a placeholder that works
today but is not what the policy asks for. Set the repo variable.

### 2026-08-22 - US values stay in dollars, so value_gbp is null for them
Consistent with the sterling-only decision above, and for the same reason:
converting would bolt an FX source and a daily failure mode onto a signal
count. Form 4 gives exact share counts and prices, so those go into the
detail string and nothing is lost - "Open-market sale 50,000 shares at
$311.02". The UI counts events rather than summing money, so nothing
downstream needs the figure.
