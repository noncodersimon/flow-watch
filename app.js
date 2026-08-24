/* Market Flows front end - no build step, reads static JSON from /data */

/* Bumped on every front-end change. index.html appends it to the app.js and
   style.css URLs, so a returning browser cannot serve a stale script against
   fresh data - there is no build step here to fingerprint assets for us.
   tests/test_data_store.py enforces that the two stay in step. */
const APP_VERSION = "2.3";

/* Event kinds written by adapters/informed_money.py.
   pdmr_award covers option exercises, vests and nil-cost awards, including a
   sale that only settles one. pdmr_scheduled is its US sibling: a Rule 10b5-1
   trade, adopted months before it executes. Both are calendar-driven, not a
   view on the price, so they are drawn on the chart but never counted as
   insider buying or selling - counting them would make every director look
   like a seller. */
const EVENT_MARKS = {
  pdmr_buy:   { glyph: "▲", role: "up",      label: "Director buy" },
  pdmr_sell:  { glyph: "▼", role: "down",    label: "Director sell" },
  pdmr_award: { glyph: "◆", role: "muted",   label: "Share scheme award or exercise" },
  pdmr_scheduled: { glyph: "◇", role: "muted", label: "Pre-scheduled plan trade (Rule 10b5-1)" },
  tr1_up:     { glyph: "▲", role: "azure",   label: "Major holding increased" },
  tr1_down:   { glyph: "▼", role: "azure",   label: "Major holding decreased" },
};

/* The chart reads its colours from the stylesheet, so the Digitelos tokens in
   style.css are the one source of truth - retheme there and the chart follows.
   Fallbacks keep it drawable if the stylesheet ever fails to load. */
const COLOURS = { up: "#1F7A4D", down: "#C9300C", accent: "#2B57DB",
                  azure: "#17A2E8", amber: "#F98E12", muted: "#55555F" };
const CHART_FONT = "Inter, system-ui, -apple-system, sans-serif";

function readColours() {
  const css = getComputedStyle(document.documentElement);
  for (const [role, token] of Object.entries({
    up: "--up", down: "--down", accent: "--accent",
    azure: "--azure", amber: "--amber", muted: "--neutral",
  })) {
    const v = css.getPropertyValue(token).trim();
    if (v) COLOURS[role] = v;
  }
}
const DIRECTIONAL_KINDS = ["pdmr_buy", "pdmr_sell", "tr1_up", "tr1_down"];

/* What the site opens on. A FTSE 100 tracker is the broadest single line for
   a UK lens; falls back to the first instrument if it ever leaves meta.json. */
const DEFAULT_INSTRUMENT = "VUKE.LON";

const state = {
  meta: null,
  summary: null,       // data/summary.json - latest values, no history
  docs: {},            // id -> data/instruments/<id>.json, fetched on demand
  overlays: {},        // key -> on/off, remembered in localStorage
  watchlists: [],      // [{name, ids}] - personal, per browser, never uploaded
  typeFilter: "all",   // picker type filter, remembered like the lists
  chart: null,
};

/* ---------- data loading ----------

   data/summary.json carries the latest value per metric and 30-day event
   counts, about half a kilobyte gzipped - it feeds the "unusual today"
   strip without touching any history. A chart loads that one instrument's
   file on demand and keeps it, so moving between instruments costs one
   small request each and never the whole store. History depth and the
   instrument count can both grow without making the first paint slower. */

async function loadJSON(path) {
  try {
    const r = await fetch(path, { cache: "no-store" });
    if (!r.ok) return null;
    return await r.json();
  } catch {
    return null;
  }
}

async function loadAll() {
  state.meta = await loadJSON("data/meta.json");
  if (!state.meta) return false;
  state.summary = (await loadJSON("data/summary.json")) || { instruments: {} };
  return true;
}

async function loadInstrument(id) {
  if (!state.docs[id]) {
    const doc = await loadJSON(`data/instruments/${encodeURIComponent(id)}.json`);
    if (!doc) {
      // Deliberately NOT cached. Caching this empty fallback made one flaky
      // fetch look like an instrument with no data for the whole session -
      // leaving navigates as the retry.
      return { id, metrics: {}, events: [], failed: true };
    }
    state.docs[id] = doc;
  }
  return state.docs[id];
}

function summaryRow(id) {
  return (state.summary && state.summary.instruments[id]) || {};
}

/* Only meaningful once the instrument's document has been fetched, which is
   why every caller sits inside the detail view. */
function series(metric, id) {
  const doc = state.docs[id];
  return (doc && doc.metrics && doc.metrics[metric]) || [];
}

function instrumentEvents(id) {
  const doc = state.docs[id];
  return (doc && doc.events) || [];
}

/* ---------- helpers ---------- */

function ratioBadge(v) {
  if (v == null) return '<span class="badge na">-</span>';
  const cls = v >= 2 ? "hot" : v >= 1.4 ? "warm" : "cool";
  return `<span class="badge ${cls}">${v.toFixed(1)}x</span>`;
}

/* Axis units. Raw share volumes and pound flows run to eight or nine digits,
   which is unreadable on a phone-width axis. Pick a unit from the largest
   value the axis actually has to show, so a FTSE 100 volume reads "42"
   against "Vol (m)" while a small ETF reads "180" against "Vol (k)". It is
   chosen per instrument and per range, so switching to 1Y or to a thinly
   traded line rescales rather than flattening to zero. The exact figure is
   always one hover away in the tooltip. */
function axisScale(values) {
  let max = 0;
  for (const v of values) {
    const a = Math.abs(v || 0);
    if (a > max) max = a;
  }
  if (max >= 1e9) return { div: 1e9, unit: "bn" };
  if (max >= 1e6) return { div: 1e6, unit: "m" };
  if (max >= 1e3) return { div: 1e3, unit: "k" };
  return { div: 1, unit: "" };
}

function axisName(label, scale) {
  return scale.unit ? `${label} (${scale.unit})` : label;
}

/* Panel names are vertical text at the far left of their panel. Sat at the
   top of the axis they collided with the panel above's bottom label -
   "Vol (k)" ran into the price axis on a phone. */
const AXIS_NAME_STYLE = { nameLocation: "middle", nameGap: 46, nameRotate: 90 };

/* The price axis says what its numbers are: the store's own unit, so pence
   stay pence with a "p" suffix and pounds and dollars get their sign.
   Converting pence to pounds here was considered and rejected - the chart
   would then disagree with the store, the tooltip and the event cards. */
function priceLabel(inst) {
  const pre = inst.currency === "GBP" ? "£" : inst.currency === "USD" ? "$" : "";
  const suf = inst.currency === "GBX" ? "p" : "";
  return (v) =>
    pre + Number(v).toLocaleString("en-GB", { maximumFractionDigits: 2 }) + suf;
}

function scaledLabel(v, scale) {
  const n = v / scale.div;
  if (scale.div === 1) return String(Math.round(n));
  // keep a decimal while the numbers are small, or 0.5m rounds away to "0"
  return Math.abs(n) < 10 && n !== 0 ? n.toFixed(1) : String(Math.round(n));
}

/* summary.json counts events by kind and leaves the judgement here: awards
   and Rule 10b5-1 plan trades are recorded but are not insider activity. */
function recentEventCount(id) {
  const counts = summaryRow(id).events30 || {};
  return DIRECTIONAL_KINDS.reduce((n, k) => n + (counts[k] || 0), 0);
}

/* ---------- "unusual today" strip ----------

   What remains of the screener page, folded under the chart: the top few
   instruments by volume against their own 20-day average, from summary.json,
   each one a link to its chart. It answers "where is the unusual activity
   today" without being a second destination - the ranking finds the chart,
   the chart tells the story. */

function defaultInstrumentId() {
  const has = (id) => state.meta.instruments.some((i) => i.id === id);
  return has(DEFAULT_INSTRUMENT) ? DEFAULT_INSTRUMENT : state.meta.instruments[0].id;
}

const STRIP_SIZE = 5;
const EXPANDED_SIZE = 20;

function rankedByRatio(field, n) {
  return Object.entries(state.summary.instruments || {})
    .map(([id, row]) => ({
      inst: state.meta.instruments.find((i) => i.id === id),
      ratio: row[field] ?? null,
      insider: DIRECTIONAL_KINDS.reduce((sum, k) => sum + ((row.events30 || {})[k] || 0), 0),
    }))
    .filter((r) => r.inst && r.ratio != null)
    .sort((x, y) => y.ratio - x.ratio)
    .slice(0, n);
}

function stripItems(rows, currentId) {
  return rows.map((r) => `
      <a class="strip-item${r.inst.id === currentId ? " current" : ""}"
         href="#/i/${encodeURIComponent(r.inst.id)}">
        <span class="strip-name">${r.inst.name}</span>
        ${ratioBadge(r.ratio)}
        ${r.insider ? `<span class="badge cool" title="Open-market insider dealings, last 30 days">${r.insider}&#9650;</span>` : ""}
      </a>`).join("");
}

function stripMarkup(currentId) {
  const today = rankedByRatio("volume_ratio", STRIP_SIZE);
  if (today.length < 2) return "";
  return `
    <div class="strip">
      <button class="strip-toggle" id="strip-toggle" aria-expanded="false"
              title="Volume vs own 20-day average. Activity, not net buying.">
        Unusual today <span class="strip-chevron" aria-hidden="true">&#9662;</span>
      </button>
      ${stripItems(today, currentId)}
    </div>
    <div class="strip-expanded" id="strip-expanded" hidden>
      <div class="strip-section">
        <h3>Top ${EXPANDED_SIZE} today</h3>
        <div class="strip-grid">${stripItems(rankedByRatio("volume_ratio", EXPANDED_SIZE), currentId)}</div>
      </div>
      <div class="strip-section">
        <h3>Top ${EXPANDED_SIZE} this week
          <span class="strip-note">volume vs 20-day average, averaged over the last 5 sessions</span></h3>
        <div class="strip-grid">${stripItems(rankedByRatio("volume_ratio_week", EXPANDED_SIZE), currentId)}</div>
      </div>
    </div>`;
}

function wireStrip() {
  const toggle = document.getElementById("strip-toggle");
  const panel = document.getElementById("strip-expanded");
  if (!toggle || !panel) return;
  toggle.addEventListener("click", () => {
    panel.hidden = !panel.hidden;
    toggle.setAttribute("aria-expanded", panel.hidden ? "false" : "true");
    toggle.classList.toggle("open", !panel.hidden);
  });
}

/* ---------- insider event details ----------

   Tapping a marker shows what it is: every event on that date, with who,
   what and how much, and a link to the source announcement. The card sits
   under the chart rather than floating - a popover following a tap point
   is exactly the clutter the quiet tooltip avoided. */

function fmtEventValue(ev) {
  if (ev.value_gbp != null) {
    return "£" + ev.value_gbp.toLocaleString("en-GB", { maximumFractionDigits: 0 });
  }
  return null;  // dollar and euro dealings carry their numbers in the detail text
}

function showEventCard(inst, date) {
  const card = document.getElementById("event-card");
  if (!card) return;
  const events = instrumentEvents(inst.id).filter((e) => e.date === date);
  if (!events.length) return;
  card.innerHTML = `
    <div class="event-card-head">
      <strong>${date}</strong>
      <button class="event-card-close" aria-label="Close">&times;</button>
    </div>
    ${events.map((e) => {
      const mark = EVENT_MARKS[e.kind] || { glyph: "●", role: "accent", label: e.kind };
      const value = fmtEventValue(e);
      return `
      <div class="event-row">
        <span class="event-glyph" style="color:${COLOURS[mark.role] || COLOURS.accent}">${mark.glyph}</span>
        <div class="event-body">
          <div><strong>${mark.label}</strong>${value ? ` · ${value}` : ""}</div>
          <div class="event-who">${e.who || ""}${e.role ? ` · ${e.role}` : ""}</div>
          ${e.detail ? `<div class="event-detail">${e.detail}</div>` : ""}
          ${e.url ? `<a class="event-src" href="${e.url}" target="_blank" rel="noopener">Source announcement &nearr;</a>` : ""}
        </div>
      </div>`;
    }).join("")}`;
  card.hidden = false;
  card.querySelector(".event-card-close").addEventListener("click", () => { card.hidden = true; });
}

let markPointClickedAt = 0;

function attachMarkerClicks(chart, inst) {
  chart.on("click", (params) => {
    if (params.componentType !== "markPoint" || !params.data || !params.data.date) return;
    markPointClickedAt = Date.now();
    showEventCard(inst, params.data.date);
  });
}

/* The key: which glyph means what, built from the kinds actually on the
   chart, so it never lists a marker that is not there. */
function renderMarkerKey(evs) {
  const el = document.getElementById("marker-key");
  if (!el) return;
  const kinds = [...new Set(evs.map((e) => e.kind))].filter((k) => EVENT_MARKS[k]);
  if (!kinds.length) { el.hidden = true; el.innerHTML = ""; return; }
  el.hidden = false;
  el.innerHTML = kinds.map((k) => {
    const m = EVENT_MARKS[k];
    return `<span class="key-item"><span style="color:${COLOURS[m.role] || COLOURS.accent}">${m.glyph}</span> ${m.label}</span>`;
  }).join("") + `<span class="key-item key-hint">tap a marker for details</span>`;
}

/* Simple moving average over [[date, value], ...].

   Points before the window is full are null rather than a partial average,
   so the line simply starts where it becomes real - a 200-day average drawn
   from 40 days of data would be a different statistic wearing the same
   label. Computed over the whole series and clipped afterwards, so the left
   edge of a short range is still a true average rather than one that
   restarts at the edge. */
function round2(v) { return Math.round(v * 100) / 100; }

/* The hover box follows the cursor and, with several panels up, covered half
   a phone screen. So it opens as just the date, and a tap anywhere on the
   chart switches it to the full values and back. The formatter reads the
   flag at call time, so toggling needs no re-render. */
let tooltipDetail = false;

function tooltipConfig() {
  return {
    trigger: "axis",
    confine: true,
    formatter: (params) => {
      const items = Array.isArray(params) ? params : [params];
      if (!items.length) return "";
      const date = items[0].axisValueLabel ?? items[0].name;
      if (!tooltipDetail) return `<b>${date}</b>`;
      const lines = items
        .filter((it) => it.value != null && it.value !== "-")
        .map((it) => {
          const v = typeof it.value === "number"
            ? it.value.toLocaleString("en-GB", { maximumFractionDigits: 2 })
            : it.value;
          return `${it.marker} ${it.seriesName}&nbsp;&nbsp;<b>${v}</b>`;
        });
      return [`<b>${date}</b>`, ...lines].join("<br>");
    },
  };
}

function attachTooltipToggle(chart) {
  chart.getZr().on("click", (e) => {
    // only a tap on the plot itself toggles - the legend keeps its own job
    const grids = chart.getOption().grid || [];
    const inside = grids.some((_, i) =>
      chart.containPixel({ gridIndex: i }, [e.offsetX, e.offsetY]));
    if (!inside) return;
    // defer so a tap on an event marker opens its card without also
    // flipping the tooltip mode underneath it
    setTimeout(() => {
      if (Date.now() - markPointClickedAt < 150) return;
      tooltipDetail = !tooltipDetail;
    }, 0);
  });
}

function sma(points, window) {
  const out = [];
  let sum = 0;
  for (let i = 0; i < points.length; i++) {
    sum += points[i][1];
    if (i >= window) sum -= points[i - window][1];
    out.push([points[i][0], i >= window - 1 ? sum / window : null]);
  }
  return out;
}

/* Annualised realised volatility from closes: the standard deviation of
   daily log returns over the window, scaled by sqrt(252). ATR would be the
   other candidate, but it needs the day's high and low and the store keeps
   only closes and volume - adding it is a data change, not a chart change. */
function realisedVol(points, window, periodsPerYear = 252) {
  const rets = [];
  for (let i = 1; i < points.length; i++) {
    const prev = points[i - 1][1], cur = points[i][1];
    rets.push([points[i][0], prev > 0 && cur > 0 ? Math.log(cur / prev) : null]);
  }
  const out = [];
  for (let i = 0; i < rets.length; i++) {
    if (i < window - 1) { out.push([rets[i][0], null]); continue; }
    const w = rets.slice(i - window + 1, i + 1).map((r) => r[1]);
    if (w.some((v) => v == null)) { out.push([rets[i][0], null]); continue; }
    const mean = w.reduce((a, b) => a + b, 0) / w.length;
    const variance = w.reduce((a, b) => a + (b - mean) * (b - mean), 0) / (w.length - 1);
    out.push([rets[i][0], Math.sqrt(variance) * Math.sqrt(periodsPerYear) * 100]);
  }
  return out;
}

/* Cumulative percent return from the first point of whatever is shown, so it
   answers "how has this done over the range I am looking at". */
function cumulativeReturn(points) {
  if (!points.length || !points[0][1]) return [];
  const base = points[0][1];
  return points.map((p) => [p[0], (p[1] / base - 1) * 100]);
}

const MOVING_AVERAGES = [
  { days: 50, role: "azure", label: "50d avg" },
  { days: 200, role: "amber", label: "200d avg" },
];

/* Optional chart overlays. Everything here is derived in the browser from
   series that are already loaded, except the benchmark, which costs one
   extra instrument document. Defaults follow the same principle as the rest
   of the dashboard: show what helps you read the flow, keep the rest one tap
   away rather than crowding the chart. */
const OVERLAYS = [
  { key: "ma50",    label: "50d avg",   on: true },
  { key: "ma200",   label: "200d avg",  on: true },
  { key: "bench",   label: "Benchmark", on: true },
  { key: "volavg",  label: "Vol avg",   on: true },
  { key: "relvol",  label: "Rel volume", on: false },
  { key: "returns", label: "Return",    on: false },
  { key: "vol",     label: "Volatility", on: false },
  /* Scheme awards and 10b5-1 plan trades are calendar noise, not a view on
     the price - that is settled in the decision log. Drawing them by default
     cluttered the chart with grey diamonds that mean nothing directional,
     so they are opt-in. The events stay in the store either way. */
  { key: "awards",  label: "Award events", on: false },
];

function loadOverlayPrefs() {
  const defaults = Object.fromEntries(OVERLAYS.map((o) => [o.key, o.on]));
  try {
    const saved = JSON.parse(localStorage.getItem("mf-overlays") || "{}");
    for (const k of Object.keys(defaults)) {
      if (typeof saved[k] === "boolean") defaults[k] = saved[k];
    }
  } catch { /* a corrupt preference is not worth breaking the chart over */ }
  return defaults;
}

function saveOverlayPrefs() {
  try { localStorage.setItem("mf-overlays", JSON.stringify(state.overlays)); } catch {}
}

/* ---------- watchlists ----------

   Personal lists live in the browser, deliberately: a watchlist is the same
   class of data as the overlay toggles - small, personal, not authoritative
   - and keeping it client-side means portfolios never leave the device.
   One versioned blob so a future sync backend has exactly one thing to
   store. The #/w/ share URL is both transfer and backup. See the
   2026-08-24 decision before adding accounts or a server here. */

const WATCHLIST_KEY = "mf-watchlists";
const MAX_LIST_NAME = 40;

/* List names are the one piece of user-typed text the UI renders. */
function esc(s) {
  return String(s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function loadWatchlists() {
  try {
    const saved = JSON.parse(localStorage.getItem(WATCHLIST_KEY) || "null");
    if (saved && saved.version === 1 && Array.isArray(saved.lists)) {
      return saved.lists
        .filter((l) => l && typeof l.name === "string" && Array.isArray(l.ids))
        .map((l) => {
          // quantities are optional and arrived in v2.1 - older blobs have
          // none, and anything that is not a positive number is dropped
          const qty = {};
          for (const [id, q] of Object.entries(l.qty || {})) {
            const n = Number(q);
            if (Number.isFinite(n) && n > 0) qty[id] = n;
          }
          return { name: l.name, ids: l.ids, qty };
        });
    }
  } catch { /* a corrupt blob is not worth breaking the site over */ }
  return [];
}

function saveWatchlists() {
  try {
    localStorage.setItem(WATCHLIST_KEY,
      JSON.stringify({ version: 1, lists: state.watchlists }));
  } catch {}
}

function onAnyList(id) {
  return state.watchlists.some((l) => l.ids.includes(id));
}

/* The URL is the only backup, so it carries quantities too - a backup that
   dropped your holdings would defeat the point. The card copy says so, for
   anyone sharing a list rather than backing one up. */
function shareUrl(list) {
  return location.origin + location.pathname +
    "#/w/" + encodeURIComponent(list.name) + "/" +
    list.ids.map((id) =>
      encodeURIComponent(id) + (list.qty[id] ? ":" + list.qty[id] : "")).join(",");
}

/* "My lists" sits above the regions in the picker, same accordion, built
   from the browser's own lists. Items carry an empty data-search so the
   search box never shows the same instrument twice - the canonical entry
   under its region already matches. */
function watchlistTree(currentId) {
  if (!state.watchlists.length) return "";
  const byId = new Map(state.meta.instruments.map((i) => [i.id, i]));
  const total = state.watchlists.reduce((n, l) => n + l.ids.length, 0);
  const body = state.watchlists.map((l) => {
    const items = l.ids.map((id) => byId.get(id)).filter(Boolean);
    return `
      <details class="pick-sector"${items.some((i) => i.id === currentId) ? " open" : ""}>
        <summary>${esc(l.name)} <span class="pick-count">${items.length}</span></summary>
        ${items.map((i) => `
          <a class="pick-item${i.id === currentId ? " current" : ""}"
             href="#/i/${encodeURIComponent(i.id)}" data-search="" data-type="${i.type}">
            <span class="pick-name">${i.name}</span>
            <span class="pick-type">${i.type}</span>
          </a>`).join("")}
      </details>`;
  }).join("");
  return `
    <details class="pick-region pick-mylists"${state.watchlists.some((l) => l.ids.includes(currentId)) ? " open" : ""}>
      <summary>My lists <span class="pick-count">${total}</span></summary>
      ${body}
    </details>`;
}

/* The star on the chart header opens a card listing every list with a
   tick for membership, plus create, delete and copy-link. It re-renders
   itself after each action - cheap, and it keeps one source of truth. */
function renderListPopover(inst) {
  const pop = document.getElementById("list-popover");
  if (!pop) return;
  const rows = state.watchlists.map((l, idx) => `
    <div class="lp-row">
      <label class="lp-name">
        <input type="checkbox" data-idx="${idx}"${l.ids.includes(inst.id) ? " checked" : ""}>
        ${esc(l.name)} <span class="pick-count">${l.ids.length}</span>
      </label>
      <span class="lp-actions">
        ${l.ids.includes(inst.id) ? `
        <input class="lp-qty" type="number" min="0" step="any" inputmode="decimal"
               placeholder="qty" value="${l.qty[inst.id] ?? ""}" data-idx="${idx}"
               aria-label="Quantity held in ${esc(l.name)}"
               title="Optional: how many you hold - gives the list a value">` : ""}
        <button class="lp-share" data-idx="${idx}"
                title="Copy a link that opens or restores this list">Copy link</button>
        <button class="lp-del" data-idx="${idx}" aria-label="Delete ${esc(l.name)}"
                title="Delete this list">&times;</button>
      </span>
    </div>`).join("");
  pop.innerHTML = `
    <div class="lp-head"><strong>Watchlists</strong>
      <span class="lp-note">saved in this browser only - Copy link to back up or share
        (the link includes quantities)</span>
    </div>
    ${rows || '<p class="lp-empty">No lists yet.</p>'}
    <button class="lp-new">+ New list</button>`;

  pop.querySelectorAll(".lp-qty").forEach((inp) =>
    inp.addEventListener("change", () => {
      const l = state.watchlists[Number(inp.dataset.idx)];
      if (!l) return;
      const n = Number(inp.value);
      if (Number.isFinite(n) && n > 0) l.qty[inst.id] = n;
      else delete l.qty[inst.id];
      saveWatchlists();
      renderMyLists();
    }));

  pop.querySelectorAll("input[type=checkbox]").forEach((box) =>
    box.addEventListener("change", () => {
      const l = state.watchlists[Number(box.dataset.idx)];
      if (!l) return;
      if (box.checked) { if (!l.ids.includes(inst.id)) l.ids.push(inst.id); }
      else { l.ids = l.ids.filter((id) => id !== inst.id); delete l.qty[inst.id]; }
      saveWatchlists();
      refreshStar(inst);
      rebuildPicker(inst);
      renderListPopover(inst);
      renderMyLists();
    }));
  pop.querySelectorAll(".lp-share").forEach((b) =>
    b.addEventListener("click", async () => {
      const l = state.watchlists[Number(b.dataset.idx)];
      if (!l) return;
      const url = shareUrl(l);
      try { await navigator.clipboard.writeText(url); b.textContent = "Copied"; }
      catch { prompt("Copy this link:", url); }
      setTimeout(() => { b.textContent = "Copy link"; }, 1500);
    }));
  pop.querySelectorAll(".lp-del").forEach((b) =>
    b.addEventListener("click", () => {
      const idx = Number(b.dataset.idx);
      const l = state.watchlists[idx];
      if (!l || !confirm(`Delete the list "${l.name}"?`)) return;
      state.watchlists.splice(idx, 1);
      saveWatchlists();
      refreshStar(inst);
      rebuildPicker(inst);
      renderListPopover(inst);
      renderMyLists();
    }));
  pop.querySelector(".lp-new").addEventListener("click", () => {
    const name = (prompt("Name the new list:", "My watchlist") || "")
      .trim().slice(0, MAX_LIST_NAME);
    if (!name) return;
    state.watchlists.push({ name, ids: [inst.id], qty: {} });
    saveWatchlists();
    refreshStar(inst);
    rebuildPicker(inst);
    renderListPopover(inst);
    renderMyLists();
  });
}

/* ---------- portfolio values ----------

   Everything here prices off summary.json alone - latest close and previous
   close per instrument - so the front screen values a portfolio without
   fetching a single series. Money stays in the instrument's own currency:
   GBX is a unit, not a currency, so pence holdings divide by 100 into the
   sterling total, but dollars are never converted - an FX source is a
   second data dependency and a daily failure mode this dashboard has
   already rejected twice. Totals are therefore per currency. */

function holdingCurrency(inst) {
  if (inst.currency === "GBX" || inst.currency === "GBP") return "£";
  if (inst.currency === "USD") return "$";
  return "";
}

function holdingUnitPrice(inst, price) {
  return inst.currency === "GBX" ? price / 100 : price;
}

function fmtMoney(cur, v) {
  const abs = Math.abs(v);
  const dp = abs >= 100 ? 0 : 2;
  return (v < 0 ? "-" : "") + cur +
    abs.toLocaleString("en-GB", { minimumFractionDigits: dp, maximumFractionDigits: dp });
}

function fmtTotals(totals, signed) {
  const parts = Object.entries(totals)
    .filter(([, v]) => v !== 0 || !signed)
    .map(([cur, v]) => (signed && v > 0 ? "+" : "") + fmtMoney(cur, v));
  // "+" reads naturally between plain sums; signed day changes get a dot,
  // or "£12 + -$5" happens
  return parts.join(signed ? " · " : " + ");
}

/* Green only when every currency is up, red only when every one is down -
   summing pounds and dollars to pick a colour would be FX by the back door. */
function dayClass(dayTotals) {
  const vs = Object.values(dayTotals);
  if (vs.every((v) => v >= 0)) return "up";
  if (vs.every((v) => v <= 0)) return "down";
  return "";
}

/* One list, priced: rows for every member, and per-currency totals over the
   ones that have a quantity. Day change is qty x (last - previous close). */
function listValues(l) {
  const byId = new Map(state.meta.instruments.map((i) => [i.id, i]));
  const rows = [], totals = {}, dayTotals = {};
  for (const id of l.ids) {
    const inst = byId.get(id);
    if (!inst) continue;
    const s = summaryRow(id);
    const qty = l.qty[id];
    const row = { inst, qty: qty || null, value: null, cur: "", change: null, changePct: null };
    if (qty && s.price != null) {
      row.cur = holdingCurrency(inst);
      row.value = qty * holdingUnitPrice(inst, s.price);
      totals[row.cur] = (totals[row.cur] || 0) + row.value;
      if (s.price_prev) {
        row.change = qty * holdingUnitPrice(inst, s.price - s.price_prev);
        row.changePct = (s.price / s.price_prev - 1) * 100;
        dayTotals[row.cur] = (dayTotals[row.cur] || 0) + row.change;
      }
    }
    rows.push(row);
  }
  return { rows, totals, dayTotals };
}

function myListsMarkup() {
  if (!state.watchlists.length) return "";
  const valued = state.watchlists.map((l) => ({ list: l, ...listValues(l) }));
  const pills = valued.map(({ list, totals }) => `
    <span class="strip-item mylist-pill">
      <span class="strip-name">${esc(list.name)}</span>
      ${Object.keys(totals).length ? `<span class="mylist-total">${fmtTotals(totals)}</span>` : ""}
    </span>`).join("");
  const sections = valued.map(({ list, rows, totals, dayTotals }) => `
    <div class="strip-section">
      <h3>${esc(list.name)}
        ${Object.keys(totals).length ? `<span class="mylist-total">${fmtTotals(totals)}</span>` : ""}
        ${Object.keys(dayTotals).length ? `<span class="mylist-day ${dayClass(dayTotals)}">today ${fmtTotals(dayTotals, true)}</span>` : ""}
      </h3>
      <table class="mylist-table">
        <thead><tr><th>Instrument</th><th>Qty</th><th>Value</th><th>Today</th></tr></thead>
        <tbody>
        ${rows.map((r) => `
          <tr>
            <td><a href="#/i/${encodeURIComponent(r.inst.id)}">${r.inst.name}</a></td>
            <td>${r.qty ?? "-"}</td>
            <td>${r.value != null ? fmtMoney(r.cur, r.value) : "-"}</td>
            <td class="${r.change == null ? "" : r.change >= 0 ? "up" : "down"}">${
              r.change != null
                ? `${r.change >= 0 ? "+" : ""}${fmtMoney(r.cur, r.change)} (${r.changePct >= 0 ? "+" : ""}${r.changePct.toFixed(1)}%)`
                : "-"}</td>
          </tr>`).join("")}
        </tbody>
      </table>
    </div>`).join("");
  const anyQty = valued.some(({ totals }) => Object.keys(totals).length);
  return `
    <div class="strip">
      <button class="strip-toggle" id="mylists-toggle" aria-expanded="false"
              title="Your lists, valued from the latest close">
        My lists <span class="strip-chevron" aria-hidden="true">&#9662;</span>
      </button>
      ${pills}
    </div>
    <div class="strip-expanded" id="mylists-expanded" hidden>
      ${sections}
      ${anyQty ? "" : `<p class="mylist-hint">Set a quantity from the ☆ card on an
        instrument's chart and the list gains a value and a day change.</p>`}
    </div>`;
}

/* Rendered into its own container so the star card can refresh it in place
   after a quantity or membership change, keeping the open/closed state. */
function renderMyLists() {
  const box = document.getElementById("mylists");
  if (!box) return;
  const panel = document.getElementById("mylists-expanded");
  const wasOpen = panel ? !panel.hidden : false;
  box.innerHTML = myListsMarkup();
  const toggle = document.getElementById("mylists-toggle");
  const fresh = document.getElementById("mylists-expanded");
  if (!toggle || !fresh) return;
  if (wasOpen) {
    fresh.hidden = false;
    toggle.setAttribute("aria-expanded", "true");
    toggle.classList.add("open");
  }
  toggle.addEventListener("click", () => {
    fresh.hidden = !fresh.hidden;
    toggle.setAttribute("aria-expanded", fresh.hidden ? "false" : "true");
    toggle.classList.toggle("open", !fresh.hidden);
  });
}

function refreshStar(inst) {
  const b = document.getElementById("star");
  if (!b) return;
  const on = onAnyList(inst.id);
  b.textContent = on ? "★" : "☆";
  b.classList.toggle("on", on);
  b.setAttribute("aria-pressed", on ? "true" : "false");
}

function rebuildPicker(inst) {
  const picker = document.getElementById("picker");
  if (!picker) return;
  picker.outerHTML = pickerMarkup(inst);
  wirePicker();
}

function wireStar(inst) {
  const b = document.getElementById("star");
  const pop = document.getElementById("list-popover");
  if (!b || !pop) return;
  refreshStar(inst);
  b.addEventListener("click", () => {
    if (pop.hidden) renderListPopover(inst);
    pop.hidden = !pop.hidden;
  });
}

/* The benchmark is just another instrument already in the store - meta.json
   maps region to one, and an instrument may override it. Nothing benchmarks
   against itself. */
function benchmarkFor(inst) {
  const id = inst.benchmark || ((state.meta.benchmarks || {})[inst.region]);
  return id && id !== inst.id ? id : null;
}

/* ---------- instrument picker ---------- */

/* The type filter is the same class of small personal preference as the
   lists and the overlays, so it persists the same way. Single-select with
   an All: "just show me ETFs" is one tap, and there is no ambiguous state
   where every type is toggled off. */
const TYPE_FILTER_KEY = "mf-typefilter";
const TYPE_LABELS = { equity: "Equity", etf: "ETF", commodity: "Cmdty" };

function loadTypeFilter() {
  try {
    const v = localStorage.getItem(TYPE_FILTER_KEY);
    if (v) return v; // validated against meta.json once it is loaded
  } catch {}
  return "all";
}

function saveTypeFilter() {
  try { localStorage.setItem(TYPE_FILTER_KEY, state.typeFilter); } catch {}
}

function instrumentTypes() {
  const seen = new Set(state.meta.instruments.map((i) => i.type));
  const order = Object.keys(TYPE_LABELS).filter((t) => seen.has(t));
  for (const t of seen) if (!order.includes(t)) order.push(t);
  return order;
}

/* region -> sector -> instrument, straight off meta.json, which is the single
   source of truth for the hierarchy. <details> gives the accordion with no
   library and no JS, and keyboard support comes free. */
function groupInstruments() {
  const byRegion = new Map();
  for (const inst of state.meta.instruments) {
    if (!byRegion.has(inst.region)) byRegion.set(inst.region, new Map());
    const sectors = byRegion.get(inst.region);
    if (!sectors.has(inst.sector)) sectors.set(inst.sector, []);
    sectors.get(inst.sector).push(inst);
  }
  return byRegion;
}

function pickerTree(currentId) {
  const byRegion = groupInstruments();
  const regions = state.meta.regions.filter((r) => byRegion.has(r));
  for (const r of byRegion.keys()) if (!regions.includes(r)) regions.push(r);

  return regions.map((region) => {
    const sectors = byRegion.get(region);
    const all = [...sectors.values()].flat();
    const regionHasCurrent = all.some((i) => i.id === currentId);
    const body = [...sectors.keys()].sort().map((sector) => {
      const items = sectors.get(sector);
      const sectorHasCurrent = items.some((i) => i.id === currentId);
      return `
        <details class="pick-sector"${sectorHasCurrent ? " open" : ""}>
          <summary>${sector} <span class="pick-count">${items.length}</span></summary>
          ${items.map((i) => `
            <a class="pick-item${i.id === currentId ? " current" : ""}"
               href="#/i/${encodeURIComponent(i.id)}"
               data-search="${searchKey(i)}" data-type="${i.type}">
              <span class="pick-name">${i.name}</span>
              <span class="pick-type">${i.type}</span>
            </a>`).join("")}
        </details>`;
    }).join("");
    return `
      <details class="pick-region"${regionHasCurrent ? " open" : ""}>
        <summary>${region} <span class="pick-count">${all.length}</span></summary>
        ${body}
      </details>`;
  }).join("");
}

/* Name, ticker and sector are all worth matching - "vodafone", "vod" and
   "telecoms" should each find the same line. */
function searchKey(inst) {
  return [inst.name, inst.id, inst.sector, inst.type]
    .join(" ").toLowerCase().replace(/"/g, "");
}

function pickerMarkup(inst) {
  return `
    <details class="picker" id="picker">
      <summary>
        <span class="picker-current">${inst.name}</span>
        <span class="picker-hint">change</span>
      </summary>
      <div class="picker-body">
        <div class="pick-search">
          <div class="pick-search-box">
            <span class="pick-search-icon" aria-hidden="true">&#9906;</span>
            <input type="search" id="pick-search" autocomplete="off"
                   placeholder="Search"
                   aria-label="Search instruments">
          </div>
          <div class="pick-types" role="group" aria-label="Filter by type">
            <button class="pick-type-btn${state.typeFilter === "all" ? " active" : ""}"
                    data-t="all">All</button>
            ${instrumentTypes().map((t) => `
            <button class="pick-type-btn${state.typeFilter === t ? " active" : ""}"
                    data-t="${t}">${TYPE_LABELS[t] || t}</button>`).join("")}
          </div>
        </div>
        <p class="pick-empty" hidden>No instrument matches that.
          <a id="pick-request" target="_blank" rel="noopener">Not listed? Request it</a></p>
        ${watchlistTree(inst.id)}
        ${pickerTree(inst.id)}
      </div>
    </details>`;
}

/* Search filters the tree in place: non-matching lines are hidden, and a
   group is hidden when nothing in it survives. Groups are forced open while
   searching, because a match buried in a collapsed section reads as no match
   at all. Clearing the box restores the collapsed-by-default state. */
function wirePicker() {
  const picker = document.getElementById("picker");
  const input = picker && picker.querySelector("#pick-search");
  if (!input) return;

  const empty = picker.querySelector(".pick-empty");
  const items = [...picker.querySelectorAll(".pick-item")];
  const groups = [...picker.querySelectorAll(".pick-sector, .pick-region")];
  const openByDefault = new Map(groups.map((g) => [g, g.open]));
  const baseCounts = new Map(groups.map((g) => {
    const c = g.querySelector(":scope > summary .pick-count");
    return [g, c ? c.textContent : ""];
  }));
  const typeBtns = [...picker.querySelectorAll(".pick-type-btn")];

  function apply() {
    const q = input.value.trim().toLowerCase();
    const t = state.typeFilter;
    const filtering = Boolean(q) || t !== "all";
    let hits = 0;
    for (const a of items) {
      const match = (!q || a.dataset.search.includes(q)) &&
                    (t === "all" || a.dataset.type === t);
      a.hidden = !match;
      if (match) hits += 1;
    }
    for (const g of groups) {
      const n = [...g.querySelectorAll(".pick-item")].filter((a) => !a.hidden).length;
      g.hidden = filtering && n === 0;
      // a query opens the surviving groups, because a match buried in a
      // collapsed section reads as no match at all - the type filter alone
      // keeps the calm collapsed-by-default browsing
      g.open = q ? n > 0 : openByDefault.get(g);
      // the counts follow the filter, or "UK 109" lies next to three ETFs
      const c = g.querySelector(":scope > summary .pick-count");
      if (c) c.textContent = filtering ? String(n) : baseCounts.get(g);
    }
    empty.hidden = hits > 0;
    // an empty search is the moment someone knows what is missing - hand
    // them a prefilled issue rather than a dead end
    const req = empty.querySelector("#pick-request");
    if (req && !empty.hidden) {
      const title = encodeURIComponent(`Instrument request: ${input.value.trim()}`);
      const body = encodeURIComponent(
        `Please add "${input.value.trim()}" to the flow-watch universe.\n\n` +
        `Name and exchange (if you know them):\n`);
      req.href = `https://github.com/noncodersimon/flow-watch/issues/new?title=${title}&body=${body}`;
    }
  }

  typeBtns.forEach((b) =>
    b.addEventListener("click", () => {
      state.typeFilter = b.dataset.t;
      saveTypeFilter();
      typeBtns.forEach((x) =>
        x.classList.toggle("active", x.dataset.t === state.typeFilter));
      apply();
    }));

  input.addEventListener("input", apply);
  input.addEventListener("keydown", (e) => {
    if (e.key === "Escape") { input.value = ""; apply(); return; }
    if (e.key !== "Enter") return;
    e.preventDefault();
    const first = items.find((a) => !a.hidden);
    if (first) first.click();
  });
  // Focus the box when the picker opens, but not on a phone - there the
  // keyboard would cover the list it is meant to help you read.
  picker.addEventListener("toggle", () => {
    if (picker.open && window.matchMedia("(min-width: 641px)").matches) input.focus();
    if (!picker.open) { input.value = ""; apply(); }
  });

  // the remembered type filter shapes the tree from the first open
  apply();
}

/* ---------- detail view ---------- */

/* Sources are daily bars, so a week is about five points - real, but sparse.
   There is deliberately no 1D: one daily close is a single point, and an
   intraday range needs tick data (phase 2). */
const RANGES = { "1W": 7, "1M": 31, "6M": 183, "1Y": 366, "5Y": 1830, "Max": 99999 };
const DEFAULT_RANGE = "1Y";

async function renderDetail(id, token) {
  const inst = state.meta.instruments.find((i) => i.id === id);
  if (!inst) { location.hash = "#/"; return; }
  const doc = await loadInstrument(id);
  if (state.overlays.bench) {
    const benchId = benchmarkFor(inst);
    if (benchId) await loadInstrument(benchId);
  }
  if (token !== undefined && token !== routeToken) return;  // superseded

  document.getElementById("app").innerHTML = `
    <div class="detail">
      ${pickerMarkup(inst)}
      <div class="meta-line">
        <span>${inst.type.toUpperCase()} · ${inst.sector} · ${inst.region}</span>
        <button class="star" id="star" aria-pressed="false"
                title="Add to a watchlist">☆</button>
      </div>
      <div class="list-popover" id="list-popover" hidden></div>
      <div class="ranges">${Object.keys(RANGES).map((r) =>
        `<button data-r="${r}" class="${r === DEFAULT_RANGE ? "active" : ""}">${r}</button>`).join("")}
      </div>
      <div class="overlays" role="group" aria-label="Chart overlays">${OVERLAYS.map((o) =>
        `<button data-o="${o.key}" aria-pressed="${state.overlays[o.key] ? "true" : "false"}"
                 class="${state.overlays[o.key] ? "active" : ""}">${o.label}</button>`).join("")}
      </div>
      ${doc.failed ? `<p class="empty">This instrument's data did not load - likely a
        connection hiccup rather than missing data. Pick another instrument and
        come back to retry.</p>` : ""}
      <div id="chart"></div>
      <p class="marker-key" id="marker-key" hidden></p>
      <div class="event-card" id="event-card" hidden></div>
      ${stripMarkup(inst.id)}
      <div id="mylists"></div>
      <p class="panel-note">${inst.type === "commodity"
        ? "Net position of non-commercial (speculative) traders, weekly CFTC data. The percentile shows how stretched positioning is against its own 5-year history."
        : inst.type === "etf"
        ? "Net flow = daily change in shares outstanding x price - actual creation/redemption of units, estimated from Yahoo data. The line is cumulative flow over the selected range."
        : "Volume bars are coloured by the day's price direction - suggestive of pressure, not a true buy/sell split."}
      Tap the chart to switch the hover box between date-only and full values.</p>
    </div>`;

  wirePicker();
  wireStrip();
  wireStar(inst);
  renderMyLists();

  const chartEl = document.getElementById("chart");
  state.chart = echarts.init(chartEl);
  attachTooltipToggle(state.chart);
  attachMarkerClicks(state.chart, inst);
  drawChart(inst, DEFAULT_RANGE);

  let currentRange = DEFAULT_RANGE;
  document.querySelectorAll(".ranges button").forEach((b) =>
    b.addEventListener("click", () => {
      document.querySelectorAll(".ranges button").forEach((x) => x.classList.remove("active"));
      b.classList.add("active");
      currentRange = b.dataset.r;
      drawChart(inst, currentRange);
    })
  );

  document.querySelectorAll(".overlays button").forEach((b) =>
    b.addEventListener("click", async () => {
      const key = b.dataset.o;
      state.overlays[key] = !state.overlays[key];
      saveOverlayPrefs();
      b.classList.toggle("active", state.overlays[key]);
      b.setAttribute("aria-pressed", state.overlays[key] ? "true" : "false");
      // switching the benchmark on is the only overlay that needs data
      if (key === "bench" && state.overlays.bench) {
        const benchId = benchmarkFor(inst);
        if (benchId) await loadInstrument(benchId);
      }
      drawChart(inst, currentRange);
    })
  );
}

function clip(points, days) {
  if (!points.length) return points;
  const cutoff = new Date(Date.now() - days * 86400e3).toISOString().slice(0, 10);
  return points.filter((p) => p[0] >= cutoff);
}

/* How many rows the chart key needs at a given width. ECharts wraps a plain
   legend when it runs out of room but will not say where, so the wrap is
   re-measured here with the same font and the plot moved clear of it. */
function legendRowCount(names, width) {
  if (!names.length) return 0;
  const ctx = legendRowCount.ctx ||
    (legendRowCount.ctx = document.createElement("canvas").getContext("2d"));
  ctx.font = "11px " + CHART_FONT;
  const usable = width - 56 - 8; // the key's own left and right insets
  let rows = 1, x = 0;
  for (const name of names) {
    const w = 18 + 5 + ctx.measureText(name).width + 12; // marker, pad, text, gap
    if (x > 0 && x + w > usable) { rows += 1; x = 0; }
    x += w;
  }
  return rows;
}

function drawChart(inst, rangeKey) {
  const days = RANGES[rangeKey];

  if (inst.type === "commodity") {
    const net = clip(series("cot_net", inst.id), days);
    const pct = clip(series("cot_percentile", inst.id), days);
    state.chart.setOption({
      tooltip: tooltipConfig(),
      legend: { data: ["Net position", "Percentile"] },
      xAxis: { type: "category", data: net.map((p) => p[0]) },
      yAxis: [
        { type: "value", name: "Contracts" },
        { type: "value", name: "%ile", min: 0, max: 100 },
      ],
      series: [
        { name: "Net position", type: "bar", data: net.map((p) => p[1]),
          itemStyle: { color: (d) => (d.value >= 0 ? COLOURS.up : COLOURS.down) } },
        { name: "Percentile", type: "line", yAxisIndex: 1, showSymbol: false,
          data: pct.map((p) => p[1]), lineStyle: { color: COLOURS.azure } },
      ],
      grid: { left: 60, right: 55, top: 40, bottom: 30 },
    }, true);
    return;
  }

  const fullPrice = series("price", inst.id);
  const fullVolume = series("volume", inst.id);
  const price = clip(fullPrice, days);
  const vol = clip(fullVolume, days);
  const flow = inst.type === "etf" ? clip(series("etf_flow", inst.id), days) : [];
  const volByDate = Object.fromEntries(vol);
  const dates = price.map((p) => p[0]);
  const visible = new Set(dates);
  const on = state.overlays;

  // Derived series are computed over the WHOLE history and clipped after, so
  // the left edge of a short range is a true average rather than one that
  // restarts at the edge of what is shown.
  const clipDerived = (points) =>
    points.filter((p) => visible.has(p[0])).map((p) => (p[1] == null ? null : round2(p[1])));
  const hasAny = (arr) => arr.some((v) => v != null);

  // colour volume by day's price direction
  const volData = dates.map((d, i) => {
    const up = i === 0 || price[i][1] >= price[i - 1][1];
    return { value: volByDate[d] ?? 0, itemStyle: { color: up ? COLOURS.up : COLOURS.down } };
  });

  // insider event markers - awards and plan trades only when asked for
  const evs = instrumentEvents(inst.id)
    .filter((e) => dates.includes(e.date))
    .filter((e) => on.awards || DIRECTIONAL_KINDS.includes(e.kind));
  const markPoints = evs.map((e) => {
    const mark = EVENT_MARKS[e.kind] || { glyph: "●", role: "accent", label: e.kind };
    return {
      coord: [e.date, price[dates.indexOf(e.date)][1]],
      value: mark.glyph,
      // an invisible 18px CIRCLE behind the glyph - a 1px hit target cannot
      // be tapped on a phone. Circle, not the default pin: a pin's body sits
      // above its anchor, which dragged the glyphs off the price line.
      symbol: "circle",
      symbolSize: 18,
      itemStyle: { color: "rgba(0,0,0,0)" },
      label: { color: COLOURS[mark.role] || COLOURS.accent, fontSize: 14 },
      name: mark.label,
      date: e.date,
    };
  });

  /* ----- price panel ----- */
  const priceSeries = [
    { name: "Price", type: "line", data: price.map((p) => p[1]), showSymbol: false,
      lineStyle: { color: COLOURS.accent, width: 2 }, z: 3,
      markPoint: { data: markPoints, symbolSize: 1, label: { fontSize: 14 } } },
  ];
  for (const ma of MOVING_AVERAGES) {
    if (!on[ma.days === 50 ? "ma50" : "ma200"]) continue;
    const data = clipDerived(sma(fullPrice, ma.days));
    if (hasAny(data)) {
      priceSeries.push({ name: ma.label, type: "line", data, showSymbol: false, z: 2,
        lineStyle: { color: COLOURS[ma.role], width: 1.4 } });
    }
  }

  // Benchmark, rebased to this instrument's price on the first day they share,
  // so the two start together and the gap between them IS the relative
  // performance. It is not the index level - the legend says "rebased".
  const benchId = on.bench ? benchmarkFor(inst) : null;
  const benchPoints = benchId ? series("price", benchId) : [];
  if (benchPoints.length) {
    const benchByDate = Object.fromEntries(benchPoints);
    const anchor = dates.find((d) => benchByDate[d] != null);
    if (anchor != null && benchByDate[anchor]) {
      const anchorPrice = price[dates.indexOf(anchor)][1];
      const factor = anchorPrice / benchByDate[anchor];
      const data = dates.map((d) =>
        benchByDate[d] != null ? round2(benchByDate[d] * factor) : null);
      if (hasAny(data)) {
        const benchInst = state.meta.instruments.find((i) => i.id === benchId);
        priceSeries.push({
          name: `${benchInst ? benchInst.name : benchId} (rebased)`,
          type: "line", data, showSymbol: false, z: 1,
          lineStyle: { color: COLOURS.muted, width: 1.2, type: "dashed" },
        });
      }
    }
  }

  const panels = [{
    weight: 3,
    axis: { type: "value", scale: true, axisLabel: { formatter: priceLabel(inst) } },
    series: priceSeries,
  }];

  /* ----- volume panel ----- */
  const volScale = axisScale(volData.map((v) => v.value));
  const volumeSeries = [{ name: "Volume", type: "bar", data: volData }];
  if (on.volavg) {
    const data = clipDerived(sma(fullVolume, 20));
    if (hasAny(data)) {
      volumeSeries.push({ name: "Vol 20d avg", type: "line", data, showSymbol: false,
        lineStyle: { color: COLOURS.accent, width: 1.3 }, z: 3 });
    }
  }
  panels.push({
    weight: 1.3,
    axis: { type: "value", splitNumber: 2, name: axisName("Vol", volScale), ...AXIS_NAME_STYLE,
            axisLabel: { formatter: (v) => scaledLabel(v, volScale) } },
    series: volumeSeries,
  });

  /* ----- ETF net flow panel ----- */
  if (flow.length) {
    const flowByDate = Object.fromEntries(flow);
    let cum = 0;
    const cumFlow = dates.map((d) => { cum += flowByDate[d] ?? 0; return Math.round(cum); });
    const flowBars = dates.map((d) => {
      const v = flowByDate[d] ?? 0;
      return { value: v, itemStyle: { color: v >= 0 ? COLOURS.up : COLOURS.down } };
    });
    const flowScale = axisScale(flowBars.map((v) => v.value).concat(cumFlow));
    panels.push({
      weight: 1.3,
      axis: { type: "value", splitNumber: 2, name: axisName("Flow", flowScale), ...AXIS_NAME_STYLE,
              axisLabel: { formatter: (v) => scaledLabel(v, flowScale) } },
      series: [
        { name: "Net flow", type: "bar", data: flowBars },
        { name: "Cumulative flow", type: "line", data: cumFlow, showSymbol: false,
          lineStyle: { color: COLOURS.azure, width: 1.5 } },
      ],
    });
  }

  /* ----- optional panels, each with its own units ----- */
  if (on.relvol) {
    // the stored metric, so the panel and the screener's Vol vs avg agree
    const data = clipDerived(series("volume_ratio", inst.id));
    if (hasAny(data)) {
      panels.push({
        weight: 1.2,
        axis: { type: "value", splitNumber: 2, name: "Rel vol", ...AXIS_NAME_STYLE,
                axisLabel: { formatter: (v) => v + "x" } },
        series: [{ name: "Rel volume", type: "line", data, showSymbol: false, step: "middle",
                   lineStyle: { color: COLOURS.azure, width: 1.3 },
                   markLine: { silent: true, symbol: "none",
                     lineStyle: { color: COLOURS.muted, type: "dashed", width: 1 },
                     data: [{ yAxis: 1 }], label: { show: false } } }],
      });
    }
  }
  if (on.returns) {
    const data = cumulativeReturn(price).map((p) => round2(p[1]));
    if (hasAny(data)) {
      panels.push({
        weight: 1.2,
        axis: { type: "value", splitNumber: 2, name: "Return %", ...AXIS_NAME_STYLE },
        series: [{ name: "Return over range", type: "line", data, showSymbol: false,
                   lineStyle: { color: COLOURS.accent, width: 1.4 },
                   areaStyle: { opacity: 0.06 },
                   markLine: { silent: true, symbol: "none",
                     lineStyle: { color: COLOURS.muted, type: "dashed", width: 1 },
                     data: [{ yAxis: 0 }], label: { show: false } } }],
      });
    }
  }
  if (on.vol) {
    const data = clipDerived(realisedVol(fullPrice, 20));
    if (hasAny(data)) {
      panels.push({
        weight: 1.2,
        axis: { type: "value", splitNumber: 2, name: "Vol %", ...AXIS_NAME_STYLE },
        series: [{ name: "20d realised vol", type: "line", data, showSymbol: false,
                   lineStyle: { color: COLOURS.amber, width: 1.4 } }],
      });
    }
  }

  /* ----- lay the panels out ----- */
  const el = document.getElementById("chart");

  // Every price-panel line is named in the key, Price included - an unnamed
  // main line makes the reader guess. The lower panels stay out of it: each
  // carries its own axis label, and a scrolling legend that shows two of
  // five names is worse than no legend at all.
  const lineNames = panels[0].series
    .filter((sr) => sr.type === "line")
    .map((sr) => sr.name);

  // On a phone the key wraps onto a second row, and a key drawn over the
  // plot is unreadable twice over - measure the rows it needs and push the
  // plot down by that much, growing the chart so no panel pays for it.
  const legendRows = legendRowCount(lineNames, (el && el.clientWidth) || 600);
  const legendPx = legendRows ? 6 + legendRows * 23 : 16;

  // the chart has to grow as panels are switched on, or they squash
  const perPanel = window.innerWidth < 640 ? 96 : 118;
  const heightPx = 170 + perPanel * panels.length + Math.max(0, legendRows - 1) * 23;
  if (el) {
    el.style.height = heightPx + "px";
    state.chart.resize({ height: heightPx });
  }

  const TOP = (legendPx / heightPx) * 100, BOTTOM = 12, GAP = 6;
  const available = 100 - TOP - BOTTOM - GAP * (panels.length - 1);
  const totalWeight = panels.reduce((sum, p) => sum + p.weight, 0);
  let y = TOP;
  const grids = panels.map((p) => {
    const h = available * (p.weight / totalWeight);
    const grid = { left: 62, right: 18, top: y + "%", height: h + "%" };
    y += h + GAP;
    return grid;
  });
  const xAxes = grids.map((_, i) => ({
    type: "category", data: dates, gridIndex: i, show: i === grids.length - 1,
  }));
  const yAxes = panels.map((p, i) => ({ ...p.axis, gridIndex: i }));
  const chartSeries = panels.flatMap((p, i) =>
    p.series.map((sr) => ({ ...sr, xAxisIndex: i, yAxisIndex: i })));

  renderMarkerKey(evs);

  state.chart.setOption({
    textStyle: { fontFamily: CHART_FONT },
    legend: lineNames.length
      ? { data: lineNames, top: 0, left: 56, right: 8,
          itemWidth: 18, itemHeight: 2, itemGap: 12,
          textStyle: { fontSize: 11 } }
      : undefined,
    tooltip: tooltipConfig(),
    axisPointer: { link: [{ xAxisIndex: "all" }] },
    grid: grids,
    xAxis: xAxes,
    yAxis: yAxes,
    series: chartSeries,
  }, true);
}

/* ---------- shell ---------- */

let routeToken = 0;

async function route() {
  // an instrument document is fetched on demand, so two quick taps can have
  // two renders in flight - only the newest may touch the DOM
  const token = ++routeToken;
  if (state.chart) { state.chart.dispose(); state.chart = null; }
  const h = location.hash;
  window.scrollTo(0, 0);
  // #/w/<name>/<id,id,...> - a shared watchlist. Offer to save it, then land
  // on its first instrument. Unknown ids are dropped rather than stored: the
  // universe is meta.json's, and a stale link should not plant dead entries.
  if (h.startsWith("#/w/")) {
    const rest = h.slice(4);
    const slash = rest.indexOf("/");
    const name = (decodeURIComponent(slash < 0 ? rest : rest.slice(0, slash)) || "Shared list")
      .slice(0, MAX_LIST_NAME);
    // each token is <encoded id>[:qty] - split before decoding, so a colon
    // inside an id (percent-encoded) can never masquerade as the separator
    const pairs = slash < 0 ? [] : rest.slice(slash + 1).split(",").map((tok) => {
      const [rawId, rawQty] = tok.split(":");
      return { id: decodeURIComponent(rawId), qty: Number(rawQty) };
    });
    const known = new Set(state.meta.instruments.map((i) => i.id));
    const seen = new Set();
    const good = [], qty = {};
    for (const p of pairs) {
      if (!known.has(p.id) || seen.has(p.id)) continue;
      seen.add(p.id);
      good.push(p.id);
      if (Number.isFinite(p.qty) && p.qty > 0) qty[p.id] = p.qty;
    }
    if (good.length) {
      const dropped = new Set(pairs.map((p) => p.id)).size - good.length;
      const already = state.watchlists.some(
        (l) => l.name === name && l.ids.join() === good.join());
      if (!already && confirm(`Save the watchlist "${name}" (${good.length} instrument${good.length === 1 ? "" : "s"}${Object.keys(qty).length ? ", with quantities" : ""}${dropped ? `; ${dropped} not on this site` : ""}) to this browser?`)) {
        state.watchlists.push({ name, ids: good, qty });
        saveWatchlists();
      }
      location.hash = "#/i/" + encodeURIComponent(good[0]);
    } else {
      location.hash = "#/";
    }
    return; // the hash change above re-routes
  }
  // #/screener was a destination until v1.2 - old links land on the chart
  const id = h.startsWith("#/i/") ? decodeURIComponent(h.slice(4)) : defaultInstrumentId();
  await renderDetail(id, token);
}

(async function init() {
  readColours();
  state.overlays = loadOverlayPrefs();
  state.watchlists = loadWatchlists();
  state.typeFilter = loadTypeFilter();
  // one document-level listener, so the watchlist card closes on any tap
  // outside it - attached once here rather than per render
  document.addEventListener("click", (e) => {
    const pop = document.getElementById("list-popover");
    if (!pop || pop.hidden) return;
    // a tap on a control inside the card re-renders it, detaching the
    // target before this listener runs - detached means it was inside
    if (!e.target.isConnected) return;
    if (pop.contains(e.target) || e.target.closest("#star")) return;
    pop.hidden = true;
  });
  const ok = await loadAll();
  if (!ok) {
    document.getElementById("app").innerHTML =
      '<p class="empty">Could not load data/meta.json - is the site being served from the repo root?</p>';
    return;
  }
  // a stored filter naming a type that no longer exists must not blank the picker
  if (state.typeFilter !== "all" &&
      !state.meta.instruments.some((i) => i.type === state.typeFilter)) {
    state.typeFilter = "all";
  }
  document.getElementById("updated").textContent =
    state.summary && state.summary.updated
      ? "Data updated " + state.summary.updated
      : "No data yet";
  document.getElementById("version").textContent = "v" + APP_VERSION;
  window.addEventListener("hashchange", route);
  window.addEventListener("resize", () => state.chart && state.chart.resize());
  route();
})();
