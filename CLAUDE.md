# flow-watch - project context

Dashboard of buying/selling activity across stocks, ETFs and commodities.
UK lens by default, global available. Owner: Simon (noncodersimon).

## Architecture - do not change without discussion

- No server, no build step, no npm. GitHub Actions (.github/workflows/fetch.yml,
  06:30 UTC Mon-Sat) runs Python adapters that fetch data, compute derived daily
  figures, and commit JSON to /data. GitHub Pages serves the static front end
  (index.html, app.js, style.css at repo root) which reads that JSON.
- Store shape: one JSON file per INSTRUMENT, data/instruments/<id>.json -
  { "id", "updated", "metrics": { "<metric>": [["YYYY-MM-DD", value], ...] },
  "events": [ ... ] } - plus data/summary.json, a latest-value digest the
  screener reads so a phone draws the table without downloading history.
  Adapters still work a metric at a time through load_store/save_store in
  common.py; the per-instrument layout is assembled underneath.
- History accumulates by MERGING in the repo (adapters/common.py). Sources that
  return only a recent window still build full series over time. Never overwrite
  a series wholesale; always merge.
- data/meta.json is the single source of truth for instruments, hierarchy
  (region/sector/type), currencies and source IDs (cftc_code, yahoo). Add
  instruments there; adapters and UI pick them up.
- New data sources = new adapter + a new metric name inside the instrument
  documents. Front end reads only from /data. This keeps phase 2 (paid tick
  data) a pure addition. build_summary.py runs last and must stay last.

## Metric semantics - keep the UI honest

- volume / volume_ratio: activity, NOT net buying. UI copy must never imply
  direction from volume alone.
- etf_flow: daily change in shares outstanding x price (yfinance). Genuine
  creation/redemption, but an estimate - keep the caveat notes in the UI.
- cot_net / cot_percentile: CFTC non-commercial net positioning, weekly.
- events.json kinds: pdmr_buy, pdmr_sell, pdmr_award, pdmr_scheduled,
  tr1_up, tr1_down. pdmr_award is share-scheme activity (option exercise,
  vest, nil-cost award, and any sale that only settles one).
  pdmr_scheduled is a US Rule 10b5-1 plan trade. Both are calendar noise:
  never counted as insider buying or selling, and drawn on the chart only
  via the off-by-default "Award events" overlay - see the decision log. Only pdmr_buy, pdmr_sell, tr1_up and tr1_down feed the Insider
  column.

## Known constraints

- Price and volume come from Yahoo via yfinance, which has no per-day
  request quota, so the instrument list is no longer capped. Yahoo is an
  unofficial API and does rate-limit: failures are per-chunk and non-fatal,
  and a missed run costs freshness, not history, because the store merges.
- Yahoo quotes LSE lines in pence, matching the GBX in meta.json. A source
  that silently switched to pounds would corrupt the store quietly, so
  price_volume_yahoo.py refuses any merge that moves an instrument's last
  close by 20x or more and reports it instead.
- SEC needs a User-Agent naming a contact address; it rejects unroutable
  ones, users.noreply.github.com among them. A bare address is accepted, so
  sec_form4.py normalises SEC_CONTACT to "flow-watch <address>" and falls
  back to a placeholder. Set the repo variable to a real address.
- LSE quotes ordinary shares in pence (GBX) but quotes many ETFs in whole
  pounds (GBP). Both appear in meta.json and both are correct - do not
  "tidy" every .LON instrument to GBX. GBX drives the /100 scaling in
  etf_flows.py and the "p" suffix in the UI, so the label has to match what
  the exchange actually quotes.
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
  every front-end change AND in the ?v= query on app.js and style.css in
  index.html - a test enforces they match. The query is the cache-buster:
  with no build step nothing fingerprints assets, so without it a returning
  browser runs stale JS against fresh data and a shipped feature looks
  missing (visible in footer - used to confirm deploys).
  Routes: #/ is the default instrument chart, #/i/<id> a chosen one.
  There is no screener page - its ranking survives as the "Unusual today"
  strip under the chart, fed by summary.json. Chart overlays live in OVERLAYS in app.js and are
  remembered per browser; all are derived client-side except the benchmark,
  which is another instrument named by meta.json benchmarks[region].
- Theme follows the Digitelos design system (github.com/noncodersimon/digitelos,
  css/main.css). Brand: cobalt #2B57DB, azure #17A2E8, vermilion #EF3F18,
  amber #F98E12, ink #0B0B10, parchment #ECEAE1. Chrome uses cobalt and azure
  only; amber is a highlight; data up #1F7A4D and down #C9300C. Fonts are
  Space Grotesk (display) and Inter (body) from Google Fonts. Chart colours
  are read from the CSS custom properties at run time, so style.css is the
  single source of truth - do not hardcode a colour in app.js.
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

## Current state (Aug 2026, v2.3)

The universe is the FTSE 350 (100 + 250), the S&P 500 (minus GOOG, FOX and
NWS - same-company share classes, see the decision log) and 82 LSE-listed
ETFs/ETCs alongside the six COT commodities: 937 instruments. The ~660
added on 2026-08-24 seed on their first run, with each new instrument's
currency label verified against Yahoo before anything merges (see
data/health.json and the decision log). Expect the first run after this
expansion to take two hours or more, dominated by Investegate pacing over
350 UK names; a few FTSE 250 lines may be refused for serving USD like
CPG/IHG did, which turns CI red by design - relabel and re-run.

Live: price/volume/ratio (Yahoo via yfinance), COT (CFTC Socrata), ETF flows
(yfinance shares-outstanding method - LSE listings may need days of samples
before deltas exist), and informed money on both sides of the Atlantic -
Investegate RNS for the 350 UK equities (PDMR dealings and TR-1 major
holdings) and SEC Form 4 for the 499 US equities.

## Roadmap

Near term - personal lists, all client-side (see the 2026-08-24 decision
before touching this area):

1. Watchlists (v2.0): named lists in localStorage, a "My lists" group above
   the regions in the picker, a star on the chart header to add and remove,
   and a #/w/ share URL that doubles as the backup.
2. Portfolio values (v2.1): optional quantity per holding set from the star
   card; a "My lists" strip under the chart prices every list from
   summary.json (price and price_prev), with per-currency totals and a day
   change.
3. Universe growth (v2.3): FTSE 250, rest of the S&P 500 and more ETFs are
   in - 937 instruments - with a "not listed? request it" link (prefilled
   GitHub issue) on an empty picker search. Remaining tail grows by request.

Standing:

4. Verify 10y backfill completes across the rotation (2 runs) and 1Y/5Y ranges
   differentiate.
5. Sector-level aggregation view (category -> sector -> instrument drill-down).
6. Phase 2: paid tick data (Polygon/Databento) -> order-flow imbalance and
   block-trade flags as new adapters. No rework of existing code expected.

Parked - a personal finance hub (tracking contracts: energy, insurance,
ISP, renewal dates). A different product, not an expansion of this one:
entirely user-entered private data, and it genuinely needs accounts, server
storage and notifications. If pursued it becomes a separate app under the
Digitelos umbrella that consumes this site's public /data as an API -
flow-watch stays the market-data engine. Recorded here so the idea cannot
quietly drag a backend into this codebase before demand proves it.

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

### 2026-08-23 - The front screen is a chart, not the screener
The site used to open on the screener table. It now opens on a default
instrument (VUKE.LON, a FTSE 100 tracker - the broadest single line for a
UK lens) with a region > sector > instrument picker above the chart, and
the table moved to #/screener. Rationale: the table answers "what is
unusual today" but it is a poor first impression on a phone, where seven
columns do not fit and most cells read "-" until every adapter has run.
A chart answers "what is this doing" immediately. The screener is one tap
away and unchanged. The picker is built from meta.json, so adding an
instrument there puts it in the menu with no UI edit.

### 2026-08-23 - A 1D range is not possible on daily bars, so there is no button
1W was added and 1D deliberately was not. Every source here is daily: a
one-day range is a single close, which draws nothing. 1W is about five
points - sparse but real. A genuine intraday range needs tick data, which
is phase 2; add the button then, not before, because a range button that
renders an empty chart reads as a bug.

### 2026-08-23 - Themed to the Digitelos design system, with one deliberate divergence
The dashboard now uses the Digitelos tokens rather than its own navy/blue,
so Simon's projects look like one family: Fibonacci spiral mark, cobalt and
azure chrome, Space Grotesk over Inter, warm off-white ground.

The house rule there is "cobalt and azure dominate, vermilion sparingly for
buttons, amber for highlights only". Followed, except that chrome here uses
cobalt and azure ONLY and never vermilion. A dashboard cannot have a red
button competing with a red price move, so the vermilion family is reserved
for "down" in the data. Buttons are cobalt instead.

Vermilion itself is not used raw: #EF3F18 on white is 3.89:1, which fails
SC 1.4.3, and the Digitelos kit already darkens it to #C9300C for CTAs.
That darkened value is what "down" uses (5.37:1), and up was pitched to
match at #1F7A4D (5.32:1) so neither side of the market shouts louder.
Every pair in the theme was measured; the numbers are in the commit.

Chart colours are read from the CSS custom properties at run time rather
than duplicated in app.js, so a retheme means editing style.css only.

### 2026-08-23 - Google Fonts is a second CDN dependency, accepted
The architecture note says no build step, and that still holds, but the
front end now depends on two CDNs rather than one: ECharts and Google
Fonts. Both are stylesheet/script tags with local fallbacks in the font
stack, so a blocked CDN degrades to system fonts rather than breaking.
Self-hosting the two families is the alternative if that ever matters -
it costs about 200KB in the repo and was judged not worth it yet.

### 2026-08-23 - Price and volume moved from Alpha Vantage to Yahoo
The free Alpha Vantage tier allowed about 25 requests a day, one per
instrument, which is why the rotation logic existed and why meta.json sat
at exactly 22 equity+ETF symbols. Every new instrument was a trade-off
against refresh frequency, and lifting it costs 499 USD a year. Yahoo has
no comparable per-day quota and yfinance batches tickers into one
download, so the ceiling is gone for nothing. yfinance was already a
dependency for etf_flows.py, so nothing new was added.

The cost is that Yahoo is an unofficial API that does break and does
rate-limit. Failures are per-chunk and non-fatal, and because the store
merges rather than overwrites, a missed run costs freshness rather than
history. volume_alphavantage.py is kept in the repo, out of the workflow,
as the fallback if Yahoo goes away - the 22-symbol ceiling comes back with
it. Rejected: paying for Alpha Vantage premium, which buys convenience
rather than capability that is not otherwise free.

Switching source is exactly where a store gets quietly corrupted, so the
adapter refuses any merge that moves an instrument's last close by 20x or
more. Yahoo quotes LSE lines in pence, matching the GBX already in
meta.json, but a silent switch to pounds would divide every UK price by
100 and the bad points would merge in alongside the good ones with no
visible break.

### 2026-08-23 - Five years of history, not ten, until the store is split
The stored series is now five years rather than the 100 points the repo
had. Ten was measured and rejected for now, on page weight: the front end
loads every metric file on every visit, and at 26 instruments that is
about 500KB gzipped for five years against 1MB for ten. Adding regions
makes it worse - 40 instruments at ten years is about 1.5MB, which is a
poor first load on a phone.

The real unlock is not a smaller cap but a different shape: per-instrument
series files loaded on demand, with the screener reading a small
latest-value summary. That would make history depth free and let the
instrument list grow without limit. Worth doing before either goes much
further; until then five years is the honest compromise, and MAX_POINTS
stays at 2600 so nothing is thrown away if the period is raised.

### 2026-08-23 - LSE ETFs quote in pounds, and a test had locked in the opposite
meta.json labelled every .LON instrument GBX, and a test in
test_data_store.py asserted exactly that, with a comment explaining that a
.LON instrument in GBP would misprice by 100x. The rule is right for
ordinary shares and wrong for ETFs: LSE quotes shares in pence but quotes
most ETFs in whole pounds. VUKE at 47.14 is 47 pounds, not 47 pence - a
FTSE 100 tracker at 47p is absurd on its face, and the store agreed:
equities came back as 549.50 for BP and 12240 for AstraZeneca, plainly
pence, while VUKE, VWRL and VUSA came back around 47, 138 and 107, plainly
pounds. SGLN at 6552 really is pence and stays GBX.

Two things were wrong as a result: the UI printed "47.14p" for a 47 pound
ETF, and etf_flows.py would have divided those funds' flows by 100. The
second had not bitten yet only because etf_flow.json is still empty.

The test was the more interesting half. It did not catch the bug, it
enforced it - a blanket assertion that encoded an assumption nobody had
checked against the data. It now asserts what is actually true: currencies
must be a code the UI and adapters understand, .LON may be GBX or GBP, and
ordinary shares specifically must be GBX.

### 2026-08-23 - Regions widened to six, populated with LSE-listed ETFs
Regions are UK, US, Europe, Asia, Emerging Markets and Global. The picker
and the screener filter both build from meta.json, so this was config
rather than code. Populated with Vanguard LSE lines - VERX, VAPX, VJPN,
VFEM - rather than foreign equities, deliberately: informed money only
covers the UK through RNS and the US through Form 4, so a European or
Asian equity would carry a permanently blank Insider column and look
broken where it is merely out of scope. ETFs never had insider data, so
nothing looks missing. Revisit foreign equities if an insider source that
covers them ever arrives.

### 2026-08-23 - The store is per instrument, not per metric
The front end used to fetch every metric for every instrument on every
visit. That was fine at 100 points and 18 instruments; at five years and
26 it is about 500KB gzipped, and it grows on both axes at once, so
history depth and coverage were competing for the same budget. Neither
consumer ever wanted the whole store: a chart needs one instrument, and
the screener needs one number per instrument.

So data/<metric>.json is gone and data/instruments/<id>.json holds that
instrument's metrics and events, with data/summary.json carrying latest
values and 30-day event counts. The screener now loads meta.json and
summary.json - half a kilobyte gzipped against the 264KB it used to pull -
and a chart fetches one instrument file on demand and keeps it.

Adapters were not touched. load_store, merge_series and save_store still
present a metric at a time and the per-instrument files are assembled
underneath, which is what kept a change of this size to one module plus the
front end. The migration was verified by reading every old series back
through that API and comparing: no mismatches.

Two consequences worth remembering. build_summary.py must run last in the
workflow, because it reads what the other adapters wrote. And summary.json
counts events by kind rather than totalling them, so the rule about which
kinds are a signal stays in app.js alone instead of being restated in
Python where the two could drift.

### 2026-08-23 - Moving averages are computed in the browser, not stored
50 and 200 day averages are drawn on the price chart. They are derived in
app.js from the price series that is already loaded, rather than stored as
metrics: storing them would add two more series to every instrument file to
carry numbers that a loop can produce for free.

Two details that matter. They are computed over the whole series and
clipped afterwards, so the left edge of a 1M view is a true 200-day average
rather than one that restarts at the edge of what is shown. And points
before the window is full are null rather than a partial average, so a line
starts where it becomes real - which is why only the 50 day average draws
today, on 100 points of history. A 200-day average taken from 40 days is a
different statistic wearing the same label.

Worth being honest about what they are: a price trend overlay, not a flow
signal. This dashboard is about who is buying, and an average of price says
nothing about that. They earn their place as context for reading the flow
signals against, and because the 50/200 relationship is the one trend
reference most people already have in their head.

### 2026-08-23 - Indicators are derived in the browser, and default to quiet
The chart gained a 20-day volume average, a rebased benchmark, relative
volume, cumulative return and 20-day realised volatility. Every one is
computed in app.js from series already loaded; only the benchmark costs
anything, and that is one extra instrument document because a benchmark is
just another instrument in the store. Nothing new is written to /data.

Defaults follow what the dashboard is for. The 50 and 200 day averages, the
volume average and the benchmark are on, because they are context you read
the flow against. Relative volume, return and volatility are off, because a
chart that opens with five stacked panels answers no question at all. The
choices are remembered in localStorage.

The benchmark is rebased to the instrument's price on the first day the two
share, so both lines start together and the gap between them is the relative
performance. It is deliberately not the index level, and the legend says
"(rebased)" so it cannot be misread as one. Rejected: a second y-axis, which
lets you make any two lines look alike by scaling.

Panels are now built from a list rather than hardcoded, with heights from
weights, so adding an indicator is one entry rather than a layout rewrite.
The legend names only the price panel's overlays: the lower panels carry
their own axis labels, and a scrolling legend showing two of five names is
worse than none.

ATR was asked for and is not here. It needs the day's high and low, and the
store keeps only closes and volume, so it is a data change rather than a
chart change - realised volatility from closes is the same question answered
with what we already have.

### 2026-08-23 - The screener page is gone; its ranking lives under the chart
Supersedes the entry above that moved the table to #/screener and kept it
one tap away. In practice it was a second destination nobody would visit:
the chart screen with the picker and search already covers "show me this
instrument", and seven columns never fitted a phone anyway. What the table
alone could answer - "where is the unusual activity today?" - now lives as
the "Unusual today" strip under the chart: the top five instruments by
volume against their own 20-day average, from summary.json, each a link to
its chart. The ranking finds the chart; the chart tells the story.

summary.json stays load-bearing - the strip reads it, so the decision that
the screener's digest file must exist and be rebuilt last is unchanged.
#/screener still routes, to the default chart, so old links do not 404.
Region filter buttons went with the table; the picker groups by region.
If richer cross-instrument comparison is ever wanted again, the table is
one git revert away - but try widening the strip first.

### 2026-08-23 - Award markers are opt-in; the hover box opens as a date
Two chart-legibility calls in one pass. The grey award/plan-trade diamonds
were drawn by default and, on a board like Unilever, outnumbered the
directional markers 79 to 7 - the signal was wallpapered by its own
context. They are now behind an off-by-default "Award events" overlay;
the events stay in the store and the Insider column is unaffected, since
it never counted them. Rejected: dropping the markers entirely, for the
same reason pdmr_award exists at all - real dilution should stay visible
to anyone who asks for it.

The axis tooltip likewise: with several panels up it listed every series
and covered half a phone screen while following the finger. It now opens
as just the date, and a tap on the plot switches it to full values and
back. The formatter reads the flag at show time, so the toggle costs no
re-render. Taps on the legend still do what legends do.

### 2026-08-23 - The universe is FTSE 100 + S&P 100 + 68 curated ETFs
Constituents were taken from Wikipedia (via a server-side fetch - the
sandbox proxy blocks wikipedia.org) and CIKs from SEC's own
company_tickers.json, so nothing was typed from memory. UK sectors are
mapped from ICB names onto the coarse vocabulary the original twelve
already used; US sectors are GICS as published. Ticker quirks are two:
BT.A becomes id BTA.LON with rns "BT.A" and yahoo "BT-A.L", and BRK.B
keeps its dot with yahoo "BRK-B". GOOG was dropped deliberately: it is the
same company, CIK and insiders as GOOGL, so keeping both would fetch and
attribute every Form 4 twice.

"All ETFs" was scoped to a curated set rather than the ~2,000 LSE lines,
which are mostly duplicate share classes and currency hedges. The wider
tier chosen (~120) landed at 68: the set that could be named with
confidence rather than padded with guessed tickers - a wrong ticker costs
a silent hole, and adding a fund later is one meta.json entry. A wrong
listing simply reports "no data" in the run log and data/health.json.

### 2026-08-23 - New instruments verify their currency against Yahoo before seeding
The pence/pounds label cannot be inferred from prices - 47.14 is a fine
number in either unit - and the 20x unit-change guard only protects series
that already exist. So the one unguarded moment was a NEW instrument with
a wrong label, which would show pounds as pence and mis-scale ETF flows
while looking exactly like data. price_volume_yahoo.py now checks Yahoo's
reported trade currency (GBp meaning pence) on first seed, refuses the
merge on a mismatch, and writes the result to data/health.json - where a
test fails, and since check.yml runs after every fetch, a wrong label
turns CI red instead of rendering quietly wrong. If Yahoo cannot be asked
(rate limits), the instrument seeds on the meta.json label with a warning
rather than stalling. This is what made seeding 58 ETFs with best-guess
currencies safe: the guesses are policed by the source itself.

### 2026-08-23 - Yahoo serves two FTSE lines in dollars, and the label follows the data
The seed-time verifier refused CPG.L and IHG.L because Yahoo's metadata
said USD for what are pence-quoted FTSE ordinary shares. Yahoo's own quote
pages show GBp, so trust_currency was added to override the metadata - and
the first seeded values proved the metadata right and the override wrong:
Compass arrived at 30.93 against a ~2,300p quote. Yahoo genuinely serves
these two lines' price history converted to US dollars, whatever its quote
page displays. The labels are now USD, because the currency field describes
the numbers in the store, not what the LSE would quote - a GBX label on
dollar data is precisely the corruption this machinery exists to prevent.
The trust_currency escape hatch stays in the adapter for a genuine
metadata-only quirk, but the lesson recorded here is the opposite one:
when metadata and served data agree with each other and disagree with your
prior, the source is telling the truth. Both instruments' stored series are
coherent (a single currency throughout), so nothing needs re-seeding.

### 2026-08-23 - Yahoo's LSE volume history has holes, and zeros there are not quiet days
Simon spotted SSLN's volume bars stopping about four months back. The
price history is complete; the volume history is Yahoo's gap - one real
day in March 2023, then nothing until spring 2026, served as zeros. SGLN
alongside has full coverage, so it is per-line, not systematic. The chart
drawing nothing for the missing years is honest and stays.

What was ours to fix was the ratio: folding years of placeholder zeros
into the 20-day average understated it and inflated SSLN's first real
ratios to exactly 20x. ratio_points in price_volume_yahoo.py now removes
any run of 30 or more consecutive zero-volume days as a data gap wherever
it falls, keeps shorter runs as the genuine quiet days they are on thin
lines, and drops leading zeros outright. SSLN's series max fell from 20.0
to its real 2.99 spike. Stored ratios were recomputed locally (the
recompute is pure) rather than waiting a run.

### 2026-08-23 - The strip expands to top 20 today and top 20 this week
Tapping the "Unusual today" label opens a panel with two ranked lists of
20: today, and the week - the same ratio averaged over the last five
sessions, so one quiet Friday cannot hide a busy week. The weekly figure
is computed by build_summary.py into summary.json (volume_ratio_week)
rather than in the browser, because the browser only holds summary.json
on the front screen and computing it there would mean fetching every
instrument document. Collapsed by default: five pills answer the everyday
question, twenty are there when asked.

### 2026-08-24 - Watchlists are client-side, and the schema is the future-proofing
"My lists" in the picker needs no accounts and no server: a watchlist is
the same class of data as the overlay toggles - small, personal, not
authoritative - so it lives in localStorage as one versioned blob
({version, lists}) under the mf-watchlists key, with a #/w/ share URL as
both transfer and backup. The honest costs are accepted and should not be
"fixed" casually: lists are per browser, and clearing site data loses them
- the share URL is the mitigation, and "portfolios never leave your device"
is the privacy story, worth keeping true. Rejected now: a sync backend
(it arrives alongside the static site if demand proves it, never as a
rewrite) and on-demand fetching of arbitrary tickers (which would trade
the whole static architecture for the long tail - the universe grows by
curation and request instead). The parked personal-contracts idea changes
nothing here: if it happens it is a separate product consuming /data.

### 2026-08-24 - Portfolio totals are per currency, and GBX/100 is not FX
Quantities (v2.1) are optional per holding, entered on the star card, and
the "My lists" panel prices everything from summary.json alone - price and
the new price_prev - so the front screen values a portfolio without
fetching any history. Totals are per currency ("£7,064 + $6,187") because
converting dollars to pounds needs an FX source and a daily failure mode,
which this project has already rejected twice; pence to pounds is a unit
change, not a conversion, so GBX holdings divide by 100 into the sterling
total. The day-change figure is coloured only when every currency agrees
on direction - summing pounds and dollars to pick green or red would be
FX by the back door. Share URLs carry quantities (#/w/name/id:qty,...):
the URL is the only backup, and a backup that drops holdings defeats it -
the card copy says so, for anyone sharing rather than backing up.

### 2026-08-24 - The universe is the FTSE 350 + S&P 500 + 82 curated ETFs
Same method as the first expansion: constituents fetched from Wikipedia
(server-side - the sandbox proxy still blocks it), CIKs straight from the
S&P table, nothing typed from memory, and the seed-time currency verifier
as the safety net for 250 new GBX guesses. Three judgements worth keeping:
investment trusts get their own UK sector rather than drowning Financials
(77 of the 250 are trusts, and a Financials group of 120 helps nobody);
FOX and NWS are dropped exactly as GOOG was, because a shared CIK means
every Form 4 would be fetched and attributed twice; and trailing-dot LSE
tickers (TW., QQ., AO.) carry rns overrides so Investegate matching works,
the BT.A lesson generalised. One table error was overridden knowingly:
Wikipedia files Bytes Technology under Aerospace & Defence. The accepted
cost is run time: ~350 UK names through Investegate pacing pushes the
unattended daily fetch towards two hours. ETF additions stay curated on
the confidence rule - 14 lines that could be named with certainty, not a
padded hundred.
