/* Market Flows front end - no build step, reads static JSON from /data */

const APP_VERSION = "0.4";

/* Event kinds written by adapters/informed_money.py.
   pdmr_award covers option exercises, vests and nil-cost awards, including a
   sale that only settles one. Those are calendar-driven, not a view on the
   price, so they are drawn on the chart but never counted as insider buying
   or selling - counting them would make every director look like a seller. */
const EVENT_MARKS = {
  pdmr_buy:   { glyph: "▲", color: "#1a7f4b", label: "Director buy" },
  pdmr_sell:  { glyph: "▼", color: "#c0392b", label: "Director sell" },
  pdmr_award: { glyph: "◆", color: "#667",    label: "Share scheme award or exercise" },
  tr1_up:     { glyph: "▲", color: "#2E5A9C", label: "Major holding increased" },
  tr1_down:   { glyph: "▼", color: "#2E5A9C", label: "Major holding decreased" },
};
const DIRECTIONAL_KINDS = ["pdmr_buy", "pdmr_sell", "tr1_up", "tr1_down"];

const state = {
  meta: null,
  stores: {},          // metric -> { updated, series: { id: [[date, value], ...] } }
  events: {},          // id -> [event, ...]
  region: null,
  sort: { key: "volRatio", dir: -1 },
  chart: null,
};

const METRICS = ["price", "volume", "volume_ratio", "cot_net", "cot_percentile", "etf_flow", "etf_flow_pct"];

/* ---------- data loading ---------- */

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
  state.region = localStorage.getItem("mf-region") || state.meta.default_region;

  const results = await Promise.all(METRICS.map((m) => loadJSON(`data/${m}.json`)));
  METRICS.forEach((m, i) => { state.stores[m] = results[i] || { updated: null, series: {} }; });

  const ev = await loadJSON("data/events.json");
  state.events = (ev && ev.events) || {};
  return true;
}

function series(metric, id) {
  return (state.stores[metric] && state.stores[metric].series[id]) || [];
}

function lastValue(metric, id) {
  const s = series(metric, id);
  return s.length ? s[s.length - 1][1] : null;
}

/* ---------- helpers ---------- */

function fmtPrice(v, currency) {
  if (v == null) return "-";
  const n = v >= 1000 ? v.toFixed(0) : v.toFixed(2);
  if (currency === "GBX") return n + "p";
  if (currency === "GBP") return "£" + n;
  if (currency === "USD") return "$" + n;
  return n;
}

function ratioBadge(v) {
  if (v == null) return '<span class="badge na">-</span>';
  const cls = v >= 2 ? "hot" : v >= 1.4 ? "warm" : "cool";
  return `<span class="badge ${cls}">${v.toFixed(1)}x</span>`;
}

function pctBadge(v) {
  if (v == null) return '<span class="badge na">-</span>';
  const cls = v >= 85 || v <= 15 ? "hot" : v >= 70 || v <= 30 ? "warm" : "cool";
  return `<span class="badge ${cls}">${Math.round(v)}</span>`;
}

function flowBadge(v) {
  if (v == null) return '<span class="badge na">-</span>';
  const cls = Math.abs(v) >= 0.5 ? "hot" : Math.abs(v) >= 0.15 ? "warm" : "cool";
  const sign = v > 0 ? "+" : "";
  const col = v > 0 ? "color:#1a7f4b" : v < 0 ? "color:#c0392b" : "";
  return `<span class="badge ${cls}" style="${col}">${sign}${v.toFixed(2)}%</span>`;
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

function recentEventCount(id, days = 30) {
  const evs = state.events[id] || [];
  const cutoff = new Date(Date.now() - days * 86400e3).toISOString().slice(0, 10);
  return evs.filter((e) => e.date >= cutoff && DIRECTIONAL_KINDS.includes(e.kind)).length;
}

/* ---------- screener view ---------- */

function screenerRows() {
  const inRegion = (inst) =>
    state.region === "All" || inst.region === state.region;
  return state.meta.instruments.filter(inRegion).map((inst) => ({
    inst,
    price: lastValue("price", inst.id),
    volRatio: lastValue("volume_ratio", inst.id),
    flowPct: lastValue("etf_flow_pct", inst.id),
    cotPct: lastValue("cot_percentile", inst.id),
    events: recentEventCount(inst.id),
  }));
}

function renderScreener() {
  const rows = screenerRows();
  const { key, dir } = state.sort;
  rows.sort((a, b) => {
    const av = a[key], bv = b[key];
    if (av == null && bv == null) return 0;
    if (av == null) return 1;
    if (bv == null) return -1;
    return (av < bv ? -1 : av > bv ? 1 : 0) * dir;
  });

  const anyData = rows.some((r) => r.price != null || r.cotPct != null);
  const arrow = (k) => (k === key ? `<span class="arrow">${dir === -1 ? "▼" : "▲"}</span>` : "");

  const html = `
    ${anyData ? "" : `<p class="empty">No data yet - run the "Fetch market data"
      workflow in GitHub Actions, wait for it to finish, then refresh.</p>`}
    <table>
      <thead><tr>
        <th data-k="name">Instrument ${arrow("name")}</th>
        <th class="hide-sm">Sector</th>
        <th class="num" data-k="price">Price ${arrow("price")}</th>
        <th class="num" data-k="volRatio" title="Volume vs own 20-day average">Vol vs avg ${arrow("volRatio")}</th>
        <th class="num" data-k="flowPct" title="ETF net flow as % of fund AUM (creations/redemptions)">Flow ${arrow("flowPct")}</th>
        <th class="num" data-k="cotPct" title="Speculative net positioning percentile, 5y">COT %ile ${arrow("cotPct")}</th>
        <th class="num" data-k="events" title="Open-market director dealings and TR-1 holding changes, last 30 days - share-scheme awards excluded">Insider ${arrow("events")}</th>
      </tr></thead>
      <tbody>
        ${rows.map((r) => `
          <tr data-id="${r.inst.id}">
            <td>${r.inst.name}<div class="sector hide-sm">${r.inst.type.toUpperCase()}</div></td>
            <td class="sector hide-sm">${r.inst.sector}</td>
            <td class="num">${fmtPrice(r.price, r.inst.currency)}</td>
            <td class="num">${ratioBadge(r.volRatio)}</td>
            <td class="num">${r.inst.type === "etf" ? flowBadge(r.flowPct) : '<span class="badge na">-</span>'}</td>
            <td class="num">${r.inst.type === "commodity" ? pctBadge(r.cotPct) : '<span class="badge na">-</span>'}</td>
            <td class="num">${r.events || '<span class="badge na">-</span>'}</td>
          </tr>`).join("")}
      </tbody>
    </table>
    <p class="panel-note">Vol vs avg is trading activity, not net buying - direction has to be read
    alongside price. ETF flow is estimated from daily changes in shares outstanding - genuine net creation/redemption, but an estimate. Insider counts open-market director dealings and TR-1 holding
    changes from RNS. Share-scheme awards, vestings and option exercises are marked on the chart
    but not counted - they are calendar-driven, not a view on the price.</p>`;

  document.getElementById("app").innerHTML = html;

  document.querySelectorAll("th[data-k]").forEach((th) =>
    th.addEventListener("click", () => {
      const k = th.dataset.k;
      state.sort = { key: k, dir: state.sort.key === k ? -state.sort.dir : -1 };
      renderScreener();
    })
  );
  document.querySelectorAll("tbody tr").forEach((tr) =>
    tr.addEventListener("click", () => { location.hash = "#/i/" + tr.dataset.id; })
  );
}

/* ---------- detail view ---------- */

const RANGES = { "1M": 31, "6M": 183, "1Y": 366, "5Y": 1830, "Max": 99999 };

function renderDetail(id) {
  const inst = state.meta.instruments.find((i) => i.id === id);
  if (!inst) { location.hash = "#/"; return; }

  document.getElementById("app").innerHTML = `
    <div class="detail">
      <a class="back" href="#/">&larr; Back to screener</a>
      <h2>${inst.name}</h2>
      <div class="meta-line">${inst.type.toUpperCase()} · ${inst.sector} · ${inst.region}</div>
      <div class="ranges">${Object.keys(RANGES).map((r) =>
        `<button data-r="${r}" class="${r === "1Y" ? "active" : ""}">${r}</button>`).join("")}
      </div>
      <div id="chart"></div>
      <p class="panel-note">${inst.type === "commodity"
        ? "Net position of non-commercial (speculative) traders, weekly CFTC data. The percentile shows how stretched positioning is against its own 5-year history."
        : inst.type === "etf"
        ? "Net flow = daily change in shares outstanding x price - actual creation/redemption of units, estimated from Yahoo data. The line is cumulative flow over the selected range."
        : "Volume bars are coloured by the day's price direction - suggestive of pressure, not a true buy/sell split."}</p>
    </div>`;

  const chartEl = document.getElementById("chart");
  if (inst.type === "etf" && series("etf_flow", inst.id).length) chartEl.style.height = "540px";
  state.chart = echarts.init(chartEl);
  drawChart(inst, "1Y");

  document.querySelectorAll(".ranges button").forEach((b) =>
    b.addEventListener("click", () => {
      document.querySelectorAll(".ranges button").forEach((x) => x.classList.remove("active"));
      b.classList.add("active");
      drawChart(inst, b.dataset.r);
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
          itemStyle: { color: (d) => (d.value >= 0 ? "#1a7f4b" : "#c0392b") } },
        { name: "Percentile", type: "line", yAxisIndex: 1, showSymbol: false,
          data: pct.map((p) => p[1]), lineStyle: { color: "#2E5A9C" } },
      ],
      grid: { left: 60, right: 55, top: 40, bottom: 30 },
    }, true);
    return;
  }

  const price = clip(series("price", inst.id), days);
  const vol = clip(series("volume", inst.id), days);
  const flow = inst.type === "etf" ? clip(series("etf_flow", inst.id), days) : [];
  const volByDate = Object.fromEntries(vol);
  const dates = price.map((p) => p[0]);

  // colour volume by day's price direction
  const volData = dates.map((d, i) => {
    const up = i === 0 || price[i][1] >= price[i - 1][1];
    return { value: volByDate[d] ?? 0, itemStyle: { color: up ? "#1a7f4b" : "#c0392b" } };
  });

  // insider event markers
  const evs = (state.events[inst.id] || []).filter((e) => dates.includes(e.date));
  const markPoints = evs.map((e) => {
    const mark = EVENT_MARKS[e.kind] || { glyph: "●", color: "#1F3864", label: e.kind };
    return {
      coord: [e.date, price[dates.indexOf(e.date)][1]],
      value: mark.glyph,
      itemStyle: { color: mark.color },
      name: mark.label,
    };
  });

  const hasFlow = flow.length > 0;
  const flowByDate = Object.fromEntries(flow);
  let cum = 0;
  const cumFlow = dates.map((d) => { cum += flowByDate[d] ?? 0; return Math.round(cum); });
  const flowBars = dates.map((d) => {
    const v = flowByDate[d] ?? 0;
    return { value: v, itemStyle: { color: v >= 0 ? "#1a7f4b" : "#c0392b" } };
  });

  const grids = hasFlow
    ? [
        { left: 70, right: 20, top: 25, height: "40%" },
        { left: 70, right: 20, top: "54%", height: "16%" },
        { left: 70, right: 20, top: "78%", height: "16%" },
      ]
    : [
        { left: 70, right: 20, top: 25, height: "55%" },
        { left: 70, right: 20, top: "72%", height: "20%" },
      ];

  const xAxes = grids.map((_, i) => ({
    type: "category", data: dates, gridIndex: i, show: i === grids.length - 1,
  }));

  const volScale = axisScale(volData.map((v) => v.value));
  const flowScale = axisScale(flowBars.map((v) => v.value).concat(cumFlow));

  const yAxes = [
    { type: "value", scale: true, gridIndex: 0 },
    { type: "value", gridIndex: 1, splitNumber: 2,
      name: axisName("Vol", volScale), nameGap: 8,
      axisLabel: { formatter: (v) => scaledLabel(v, volScale) } },
  ];
  if (hasFlow) {
    yAxes.push({ type: "value", gridIndex: 2, splitNumber: 2,
      name: axisName("Flow", flowScale), nameGap: 8,
      axisLabel: { formatter: (v) => scaledLabel(v, flowScale) } });
  }

  const chartSeries = [
    { name: "Price", type: "line", data: price.map((p) => p[1]),
      showSymbol: false, lineStyle: { color: "#1F3864", width: 2 },
      xAxisIndex: 0, yAxisIndex: 0,
      markPoint: { data: markPoints, symbolSize: 1, label: { fontSize: 14 } } },
    { name: "Volume", type: "bar", data: volData, xAxisIndex: 1, yAxisIndex: 1 },
  ];
  if (hasFlow) {
    chartSeries.push(
      { name: "Net flow", type: "bar", data: flowBars, xAxisIndex: 2, yAxisIndex: 2 },
      { name: "Cumulative flow", type: "line", data: cumFlow, showSymbol: false,
        xAxisIndex: 2, yAxisIndex: 2, lineStyle: { color: "#2E5A9C", width: 1.5 } },
    );
  }

  state.chart.setOption({
    tooltip: { trigger: "axis" },
    axisPointer: { link: [{ xAxisIndex: "all" }] },
    grid: grids,
    xAxis: xAxes,
    yAxis: yAxes,
    series: chartSeries,
  }, true);
}

/* ---------- shell ---------- */

function renderRegions() {
  const el = document.getElementById("regions");
  const opts = [...state.meta.regions, "All"];
  el.innerHTML = opts.map((r) =>
    `<button class="${r === state.region ? "active" : ""}">${r}</button>`).join("");
  el.querySelectorAll("button").forEach((b) =>
    b.addEventListener("click", () => {
      state.region = b.textContent;
      localStorage.setItem("mf-region", state.region);
      renderRegions();
      if (!location.hash.startsWith("#/i/")) renderScreener();
    })
  );
}

function route() {
  if (state.chart) { state.chart.dispose(); state.chart = null; }
  const h = location.hash;
  if (h.startsWith("#/i/")) renderDetail(decodeURIComponent(h.slice(4)));
  else renderScreener();
}

(async function init() {
  const ok = await loadAll();
  if (!ok) {
    document.getElementById("app").innerHTML =
      '<p class="empty">Could not load data/meta.json - is the site being served from the repo root?</p>';
    return;
  }
  const updates = METRICS.map((m) => state.stores[m].updated).filter(Boolean);
  document.getElementById("updated").textContent =
    updates.length ? "Data updated " + updates.sort().slice(-1)[0] : "No data yet";
  document.getElementById("version").textContent = "v" + APP_VERSION;
  renderRegions();
  window.addEventListener("hashchange", route);
  window.addEventListener("resize", () => state.chart && state.chart.resize());
  route();
})();
