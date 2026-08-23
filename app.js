/* Market Flows front end - no build step, reads static JSON from /data */

/* Bumped on every front-end change. index.html appends it to the app.js and
   style.css URLs, so a returning browser cannot serve a stale script against
   fresh data - there is no build step here to fingerprint assets for us.
   tests/test_data_store.py enforces that the two stay in step. */
const APP_VERSION = "1.2";

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
    state.docs[id] =
      (await loadJSON(`data/instruments/${encodeURIComponent(id)}.json`)) ||
      { id, metrics: {}, events: [] };
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

function unusualToday() {
  const rows = Object.entries(state.summary.instruments || {})
    .map(([id, row]) => ({
      inst: state.meta.instruments.find((i) => i.id === id),
      ratio: row.volume_ratio ?? null,
      insider: DIRECTIONAL_KINDS.reduce((n, k) => n + ((row.events30 || {})[k] || 0), 0),
    }))
    .filter((r) => r.inst && r.ratio != null)
    .sort((x, y) => y.ratio - x.ratio);
  return rows.slice(0, STRIP_SIZE);
}

function stripMarkup(currentId) {
  const rows = unusualToday();
  if (rows.length < 2) return "";
  return `
    <div class="strip">
      <span class="strip-title" title="Volume vs own 20-day average, from the latest run. Activity, not net buying.">Unusual today</span>
      ${rows.map((r) => `
        <a class="strip-item${r.inst.id === currentId ? " current" : ""}"
           href="#/i/${encodeURIComponent(r.inst.id)}">
          <span class="strip-name">${r.inst.name}</span>
          ${ratioBadge(r.ratio)}
          ${r.insider ? `<span class="badge cool" title="Open-market insider dealings, last 30 days">${r.insider}&#9650;</span>` : ""}
        </a>`).join("")}
    </div>`;
}

/* Simple moving average over [[date, value], ...].

   Points before the window is full are null rather than a partial average,
   so the line simply starts where it becomes real - a 200-day average drawn
   from 40 days of data would be a different statistic wearing the same
   label. Computed over the whole series and clipped afterwards, so the left
   edge of a short range is still a true average rather than one that
   restarts at the edge. */
function round2(v) { return Math.round(v * 100) / 100; }

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

/* The benchmark is just another instrument already in the store - meta.json
   maps region to one, and an instrument may override it. Nothing benchmarks
   against itself. */
function benchmarkFor(inst) {
  const id = inst.benchmark || ((state.meta.benchmarks || {})[inst.region]);
  return id && id !== inst.id ? id : null;
}

/* ---------- instrument picker ---------- */

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
               data-search="${searchKey(i)}">
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
          <span class="pick-search-icon" aria-hidden="true">&#9906;</span>
          <input type="search" id="pick-search" autocomplete="off"
                 placeholder="Search name, ticker or sector"
                 aria-label="Search instruments">
        </div>
        <p class="pick-empty" hidden>No instrument matches that.</p>
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

  const visibleIn = (g) => [...g.querySelectorAll(".pick-item")].some((a) => !a.hidden);

  function apply(query) {
    const q = query.trim().toLowerCase();
    if (!q) {
      items.forEach((a) => { a.hidden = false; });
      groups.forEach((g) => { g.hidden = false; g.open = openByDefault.get(g); });
      empty.hidden = true;
      return;
    }
    let hits = 0;
    for (const a of items) {
      const match = a.dataset.search.includes(q);
      a.hidden = !match;
      if (match) hits += 1;
    }
    for (const g of groups) {
      const any = visibleIn(g);
      g.hidden = !any;
      g.open = any;
    }
    empty.hidden = hits > 0;
  }

  input.addEventListener("input", () => apply(input.value));
  input.addEventListener("keydown", (e) => {
    if (e.key === "Escape") { input.value = ""; apply(""); return; }
    if (e.key !== "Enter") return;
    e.preventDefault();
    const first = items.find((a) => !a.hidden);
    if (first) first.click();
  });
  // Focus the box when the picker opens, but not on a phone - there the
  // keyboard would cover the list it is meant to help you read.
  picker.addEventListener("toggle", () => {
    if (picker.open && window.matchMedia("(min-width: 641px)").matches) input.focus();
    if (!picker.open) { input.value = ""; apply(""); }
  });
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
  await loadInstrument(id);
  if (state.overlays.bench) {
    const benchId = benchmarkFor(inst);
    if (benchId) await loadInstrument(benchId);
  }
  if (token !== undefined && token !== routeToken) return;  // superseded

  document.getElementById("app").innerHTML = `
    <div class="detail">
      ${pickerMarkup(inst)}
      <div class="meta-line">${inst.type.toUpperCase()} · ${inst.sector} · ${inst.region}</div>
      <div class="ranges">${Object.keys(RANGES).map((r) =>
        `<button data-r="${r}" class="${r === DEFAULT_RANGE ? "active" : ""}">${r}</button>`).join("")}
      </div>
      <div class="overlays" role="group" aria-label="Chart overlays">${OVERLAYS.map((o) =>
        `<button data-o="${o.key}" aria-pressed="${state.overlays[o.key] ? "true" : "false"}"
                 class="${state.overlays[o.key] ? "active" : ""}">${o.label}</button>`).join("")}
      </div>
      <div id="chart"></div>
      ${stripMarkup(inst.id)}
      <p class="panel-note">${inst.type === "commodity"
        ? "Net position of non-commercial (speculative) traders, weekly CFTC data. The percentile shows how stretched positioning is against its own 5-year history."
        : inst.type === "etf"
        ? "Net flow = daily change in shares outstanding x price - actual creation/redemption of units, estimated from Yahoo data. The line is cumulative flow over the selected range."
        : "Volume bars are coloured by the day's price direction - suggestive of pressure, not a true buy/sell split."}</p>
    </div>`;

  wirePicker();

  const chartEl = document.getElementById("chart");
  state.chart = echarts.init(chartEl);
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

function drawChart(inst, rangeKey) {
  const days = RANGES[rangeKey];

  if (inst.type === "commodity") {
    const net = clip(series("cot_net", inst.id), days);
    const pct = clip(series("cot_percentile", inst.id), days);
    state.chart.setOption({
      tooltip: {
      trigger: "axis",
      valueFormatter: (v) =>
        typeof v === "number"
          ? v.toLocaleString("en-GB", { maximumFractionDigits: 2 })
          : v,
    },
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

  // insider event markers
  const evs = instrumentEvents(inst.id).filter((e) => dates.includes(e.date));
  const markPoints = evs.map((e) => {
    const mark = EVENT_MARKS[e.kind] || { glyph: "●", role: "accent", label: e.kind };
    return {
      coord: [e.date, price[dates.indexOf(e.date)][1]],
      value: mark.glyph,
      itemStyle: { color: COLOURS[mark.role] || COLOURS.accent },
      name: mark.label,
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

  const panels = [{ weight: 3, axis: { type: "value", scale: true }, series: priceSeries }];

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
    axis: { type: "value", splitNumber: 2, name: axisName("Vol", volScale), nameGap: 8,
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
      axis: { type: "value", splitNumber: 2, name: axisName("Flow", flowScale), nameGap: 8,
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
        axis: { type: "value", splitNumber: 2, name: "Rel vol", nameGap: 8,
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
        axis: { type: "value", splitNumber: 2, name: "Return %", nameGap: 8 },
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
        axis: { type: "value", splitNumber: 2, name: "Vol %", nameGap: 8 },
        series: [{ name: "20d realised vol", type: "line", data, showSymbol: false,
                   lineStyle: { color: COLOURS.amber, width: 1.4 } }],
      });
    }
  }

  /* ----- lay the panels out ----- */
  const TOP = 8, BOTTOM = 12, GAP = 6;
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

  // the chart has to grow as panels are switched on, or they squash
  const el = document.getElementById("chart");
  if (el) {
    const perPanel = window.innerWidth < 640 ? 96 : 118;
    el.style.height = (170 + perPanel * panels.length) + "px";
    state.chart.resize({ height: el.clientHeight });
  }

  // Only the price panel's overlays need naming - the lower panels each carry
  // their own axis label, and a scrolling legend that shows two of five names
  // is worse than no legend at all.
  const lineNames = panels[0].series
    .filter((sr) => sr.type === "line" && sr.name !== "Price")
    .map((sr) => sr.name);

  state.chart.setOption({
    textStyle: { fontFamily: CHART_FONT },
    legend: lineNames.length
      ? { data: lineNames, top: 0, left: 56, right: 8,
          itemWidth: 18, itemHeight: 2, itemGap: 12,
          textStyle: { fontSize: 11 } }
      : undefined,
    tooltip: { trigger: "axis" },
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
  // #/screener was a destination until v1.2 - old links land on the chart
  const id = h.startsWith("#/i/") ? decodeURIComponent(h.slice(4)) : defaultInstrumentId();
  await renderDetail(id, token);
}

(async function init() {
  readColours();
  state.overlays = loadOverlayPrefs();
  const ok = await loadAll();
  if (!ok) {
    document.getElementById("app").innerHTML =
      '<p class="empty">Could not load data/meta.json - is the site being served from the repo root?</p>';
    return;
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
