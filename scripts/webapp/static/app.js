const state = {
  rows: [],
  columns: [],
  plot: {
    xName: "",
    yName: "count",
    xValues: [],
    curves: [],
  },
};

function q(id) {
  return document.getElementById(id);
}

function value(id) {
  return q(id).value.trim();
}

function globalReac() {
  return value("reacFile");
}

function globalMinTp() {
  const v = Number.parseInt(value("minTp"), 10);
  return Number.isFinite(v) && v > 0 ? v : 1;
}

function asQuery(params) {
  const usp = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => {
    if (v === undefined || v === null || v === "") return;
    if (Array.isArray(v)) {
      v.forEach((item) => {
        if (item === undefined || item === null || item === "") return;
        usp.append(k, String(item));
      });
    } else {
      usp.set(k, String(v));
    }
  });
  return usp.toString();
}

async function fetchJson(path, params) {
  const url = `${path}?${asQuery(params)}`;
  const resp = await fetch(url);
  const data = await resp.json();
  if (!resp.ok || !data.ok) {
    const err = data.error || `HTTP ${resp.status}`;
    throw new Error(err);
  }
  return data;
}

function setMeta(obj) {
  q("metaBox").textContent = JSON.stringify(obj, null, 2);
}

function setPlotMeta(obj) {
  q("plotMeta").textContent = JSON.stringify(obj, null, 2);
}

function escapeHtml(str) {
  return String(str)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function parseReactionSmiles(rxn) {
  const out = [];
  if (!rxn || !rxn.includes("->")) return out;
  const [left, right] = rxn.split("->").map((s) => s.trim());
  const lhs = left ? left.split("+").map((s) => s.trim()).filter(Boolean) : [];
  const rhs = right ? right.split("+").map((s) => s.trim()).filter(Boolean) : [];
  lhs.forEach((s) => out.push({ side: "reactant", smiles: s }));
  rhs.forEach((s) => out.push({ side: "product", smiles: s }));
  return out;
}

function looksLikeSmiles(s) {
  const t = String(s || "");
  return t.includes("[") || t.includes("=") || t.includes("#") || /\d/.test(t);
}

function renderTable(rows) {
  const thead = q("resultTable").querySelector("thead");
  const tbody = q("resultTable").querySelector("tbody");
  thead.innerHTML = "";
  tbody.innerHTML = "";

  if (!rows || rows.length === 0) {
    thead.innerHTML = "<tr><th>Result</th></tr>";
    tbody.innerHTML = "<tr><td>无数据</td></tr>";
    state.rows = [];
    state.columns = [];
    q("btnExport").disabled = true;
    return;
  }

  const cols = Array.from(
    rows.reduce((acc, row) => {
      Object.keys(row).forEach((k) => acc.add(k));
      return acc;
    }, new Set())
  );

  state.rows = rows;
  state.columns = cols;
  q("btnExport").disabled = false;

  const header = `<tr>${cols.map((c) => `<th>${escapeHtml(c)}</th>`).join("")}<th>Action</th></tr>`;
  thead.innerHTML = header;

  const bodyHtml = rows
    .map((row) => {
      const tds = cols
        .map((col) => {
          const v = row[col] ?? "";
          const text = String(v);
          const short = text.length > 180 ? `${text.slice(0, 177)}...` : text;
          return `<td><code>${escapeHtml(short)}</code></td>`;
        })
        .join("");

      let action = `<button class="view-smiles" data-smiles="" disabled>N/A</button>`;
      if (row.smiles && looksLikeSmiles(row.smiles)) {
        action = `<button class="view-smiles" data-smiles="${encodeURIComponent(row.smiles)}">View</button>`;
      } else if (row.reaction_smiles && row.reaction_smiles.includes("->")) {
        action = `<button class="view-rxn" data-rxn="${encodeURIComponent(row.reaction_smiles)}">View Rxn</button>`;
      }
      return `<tr>${tds}<td class="cell-actions">${action}</td></tr>`;
    })
    .join("");

  tbody.innerHTML = bodyHtml;

  tbody.querySelectorAll(".view-smiles").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const smi = decodeURIComponent(btn.dataset.smiles || "").trim();
      if (!smi) return;
      q("viewerSmiles").value = smi;
      await renderOneSmiles();
    });
  });

  tbody.querySelectorAll(".view-rxn").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const rxn = decodeURIComponent(btn.dataset.rxn || "").trim();
      if (!rxn) return;
      q("viewerSmiles").value = rxn;
      await renderReactionSmiles();
    });
  });
}

function csvEscape(v) {
  const s = String(v ?? "");
  if (s.includes(",") || s.includes("\n") || s.includes('"')) {
    return `"${s.replaceAll('"', '""')}"`;
  }
  return s;
}

function exportCurrentCsv() {
  if (!state.rows.length || !state.columns.length) return;
  const lines = [state.columns.join(",")];
  state.rows.forEach((row) => {
    lines.push(state.columns.map((c) => csvEscape(row[c])).join(","));
  });
  const blob = new Blob([lines.join("\n")], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  const stamp = new Date().toISOString().replace(/[.:]/g, "-");
  a.href = url;
  a.download = `rng_query_${stamp}.csv`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

function clearGallery() {
  q("svgGallery").innerHTML = "";
}

function viewerShowHEnabled() {
  const el = q("viewerShowH");
  return !el || el.checked;
}

function appendSvgCard(title, smiles) {
  const gallery = q("svgGallery");
  const wrapper = document.createElement("div");
  wrapper.className = "svg-card";

  const showH = viewerShowHEnabled() ? 1 : 0;
  const imgSrc = `/api/smiles_svg?smiles=${encodeURIComponent(smiles)}&w=360&h=220&show_h=${showH}`;
  wrapper.innerHTML = `
    <strong>${escapeHtml(title)}</strong>
    <div><img src="${imgSrc}" alt="smiles" loading="lazy" /></div>
    <div class="smiles"><code>${escapeHtml(smiles)}</code></div>
  `;
  gallery.appendChild(wrapper);
}

async function renderOneSmiles() {
  clearGallery();
  const smi = value("viewerSmiles");
  if (!smi) return;
  appendSvgCard("SMILES", smi);
}

async function renderReactionSmiles() {
  clearGallery();
  const rxn = value("viewerSmiles");
  const terms = parseReactionSmiles(rxn);
  if (!terms.length) {
    appendSvgCard("SMILES", rxn);
    return;
  }
  terms.forEach((obj, idx) => appendSvgCard(`${obj.side} #${idx + 1}`, obj.smiles));
}

function parseTargetsForPlot(raw) {
  return String(raw || "")
    .split(/\n+/)
    .map((x) => x.trim())
    .filter(Boolean);
}

function fmtTick(v) {
  if (!Number.isFinite(v)) return "";
  const av = Math.abs(v);
  if (av >= 1000 || (av > 0 && av < 0.01)) return v.toExponential(2);
  if (av >= 10) return v.toFixed(2);
  return v.toFixed(3);
}

const PLOT_COLORS = [
  "#177e89",
  "#d66853",
  "#3a86ff",
  "#ff8c42",
  "#6a994e",
  "#8338ec",
  "#ff006e",
  "#2a9d8f",
  "#6d597a",
  "#118ab2",
  "#f77f00",
  "#4361ee",
];

let plotChart = null;
let echartsLoadPromise = null;

function loadExternalScript(src) {
  return new Promise((resolve, reject) => {
    const script = document.createElement("script");
    const timeout = window.setTimeout(() => {
      script.remove();
      reject(new Error(`load timeout: ${src}`));
    }, 3500);
    script.src = src;
    script.async = true;
    script.onload = () => {
      window.clearTimeout(timeout);
      resolve(true);
    };
    script.onerror = () => {
      window.clearTimeout(timeout);
      reject(new Error(`failed to load: ${src}`));
    };
    document.head.appendChild(script);
  });
}

async function ensureECharts() {
  if (window.echarts) return true;
  if (echartsLoadPromise) return echartsLoadPromise;
  echartsLoadPromise = (async () => {
    const cdns = [
      "https://cdn.jsdelivr.net/npm/echarts@5.5.1/dist/echarts.min.js",
      "https://unpkg.com/echarts@5.5.1/dist/echarts.min.js",
    ];
    for (const src of cdns) {
      try {
        await loadExternalScript(src);
        if (window.echarts) return true;
      } catch (err) {
        // Try next CDN endpoint.
      }
    }
    return false;
  })();
  return echartsLoadPromise;
}

function renderPlotLegend(curves) {
  const legend = q("plotLegend");
  legend.innerHTML = "";
  curves.forEach((c, i) => {
    const color = PLOT_COLORS[i % PLOT_COLORS.length];
    const item = document.createElement("div");
    item.className = "legend-item";
    item.innerHTML = `
      <span class="legend-swatch" style="background:${color}"></span>
      <span><code>${escapeHtml(c.name)}</code> (max=${fmtTick(c.max_value)})</span>
    `;
    legend.appendChild(item);
  });
}

function drawPlotCanvas(xValues, curves, xName, yName = "count") {
  const chartHost = q("plotChart");
  const canvas = q("plotCanvas");
  chartHost.style.display = "none";
  canvas.style.display = "block";
  const wrap = canvas.parentElement;
  const dpr = window.devicePixelRatio || 1;
  const width = Math.max(300, Math.floor(wrap.clientWidth - 4));
  const height = 360;
  canvas.style.width = `${width}px`;
  canvas.style.height = `${height}px`;
  canvas.width = Math.floor(width * dpr);
  canvas.height = Math.floor(height * dpr);

  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, width, height);
  renderPlotLegend(curves || []);

  if (!xValues || !xValues.length || !curves || !curves.length) {
    ctx.fillStyle = "#666";
    ctx.font = "14px sans-serif";
    ctx.fillText("无曲线数据", 18, 28);
    q("btnPlotExportCsv").disabled = true;
    q("btnPlotExportPng").disabled = true;
    return;
  }

  const padL = 62;
  const padR = 20;
  const padT = 20;
  const padB = 44;
  const plotW = width - padL - padR;
  const plotH = height - padT - padB;

  const xMin = Math.min(...xValues);
  const xMax = Math.max(...xValues);
  let yMin = Number.POSITIVE_INFINITY;
  let yMax = Number.NEGATIVE_INFINITY;
  curves.forEach((c) => {
    c.values.forEach((v) => {
      if (v < yMin) yMin = v;
      if (v > yMax) yMax = v;
    });
  });
  if (!Number.isFinite(yMin) || !Number.isFinite(yMax)) {
    yMin = 0;
    yMax = 1;
  }
  if (yMin === yMax) {
    yMin -= 1;
    yMax += 1;
  }
  if (xMin === xMax) {
    q("btnPlotExportCsv").disabled = true;
    q("btnPlotExportPng").disabled = true;
    return;
  }

  const xToPx = (x) => padL + ((x - xMin) / (xMax - xMin)) * plotW;
  const yToPx = (y) => padT + plotH - ((y - yMin) / (yMax - yMin)) * plotH;

  // background panel
  ctx.fillStyle = "#fff";
  ctx.fillRect(padL, padT, plotW, plotH);

  // grid + ticks
  ctx.strokeStyle = "#e7decd";
  ctx.fillStyle = "#5a5349";
  ctx.font = "12px sans-serif";
  ctx.lineWidth = 1;
  const nY = 5;
  for (let i = 0; i <= nY; i += 1) {
    const r = i / nY;
    const y = padT + plotH * (1 - r);
    ctx.beginPath();
    ctx.moveTo(padL, y);
    ctx.lineTo(padL + plotW, y);
    ctx.stroke();
    const val = yMin + (yMax - yMin) * r;
    ctx.fillText(fmtTick(val), 8, y + 4);
  }

  const nX = 6;
  for (let i = 0; i <= nX; i += 1) {
    const r = i / nX;
    const x = padL + plotW * r;
    ctx.beginPath();
    ctx.moveTo(x, padT);
    ctx.lineTo(x, padT + plotH);
    ctx.stroke();
    const val = xMin + (xMax - xMin) * r;
    ctx.fillText(fmtTick(val), x - 18, padT + plotH + 18);
  }

  // axes
  ctx.strokeStyle = "#9f9380";
  ctx.lineWidth = 1.2;
  ctx.beginPath();
  ctx.moveTo(padL, padT);
  ctx.lineTo(padL, padT + plotH);
  ctx.lineTo(padL + plotW, padT + plotH);
  ctx.stroke();

  // labels
  ctx.fillStyle = "#4c463e";
  ctx.font = "13px sans-serif";
  ctx.fillText(xName || "x", padL + plotW - 40, padT + plotH + 36);
  ctx.save();
  ctx.translate(18, padT + 16);
  ctx.rotate(-Math.PI / 2);
  ctx.fillText(yName || "count", 0, 0);
  ctx.restore();

  // lines
  curves.forEach((c, i) => {
    const color = PLOT_COLORS[i % PLOT_COLORS.length];
    ctx.strokeStyle = color;
    ctx.lineWidth = 1.8;
    ctx.beginPath();
    for (let j = 0; j < xValues.length; j += 1) {
      const x = xToPx(xValues[j]);
      const y = yToPx(c.values[j]);
      if (j === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    }
    ctx.stroke();

  });

  q("btnPlotExportCsv").disabled = false;
  q("btnPlotExportPng").disabled = false;
}

function drawPlotECharts(xValues, curves, xName, yName = "count") {
  const chartHost = q("plotChart");
  const canvas = q("plotCanvas");
  chartHost.style.display = "block";
  canvas.style.display = "none";

  if (!plotChart) {
    plotChart = window.echarts.init(chartHost);
  }

  if (!xValues || !xValues.length || !curves || !curves.length) {
    plotChart.clear();
    plotChart.setOption({
      title: {
        text: "无曲线数据",
        left: "center",
        top: "middle",
        textStyle: { fontSize: 16, color: "#7a7267" },
      },
    });
    renderPlotLegend([]);
    q("btnPlotExportCsv").disabled = true;
    q("btnPlotExportPng").disabled = true;
    return;
  }

  const series = curves.map((c) => ({
    name: c.name,
    type: "line",
    smooth: false,
    showSymbol: false,
    emphasis: { focus: "series" },
    data: xValues.map((x, idx) => [x, c.values[idx] ?? null]),
  }));

  plotChart.setOption(
    {
      color: PLOT_COLORS,
      animation: false,
      grid: { left: 64, right: 26, top: 56, bottom: 60 },
      tooltip: {
        trigger: "axis",
        axisPointer: { type: "cross" },
      },
      legend: {
        type: "scroll",
        top: 10,
      },
      toolbox: {
        right: 10,
        feature: {
          dataZoom: { yAxisIndex: "none" },
          restore: {},
          saveAsImage: { type: "png" },
        },
      },
      xAxis: {
        type: "value",
        name: xName || "x",
        nameLocation: "end",
        nameGap: 16,
      },
      yAxis: {
        type: "value",
        name: yName || "count",
        nameLocation: "end",
      },
      dataZoom: [
        { type: "inside", xAxisIndex: 0 },
        { type: "slider", xAxisIndex: 0, bottom: 20, height: 20 },
      ],
      series,
    },
    true
  );
  renderPlotLegend(curves);
  q("btnPlotExportCsv").disabled = false;
  q("btnPlotExportPng").disabled = false;
}

async function drawPlot(xValues, curves, xName, yName = "count") {
  const hasECharts = await ensureECharts();
  if (hasECharts) {
    drawPlotECharts(xValues, curves, xName, yName);
    return;
  }
  drawPlotCanvas(xValues, curves, xName, yName);
}

function exportPlotCsv() {
  const { xName, xValues, curves } = state.plot;
  if (!xValues.length || !curves.length) return;
  const cols = [xName || "x", ...curves.map((c) => c.name)];
  const lines = [cols.map(csvEscape).join(",")];
  for (let i = 0; i < xValues.length; i += 1) {
    const row = [xValues[i], ...curves.map((c) => c.values[i])];
    lines.push(row.map(csvEscape).join(","));
  }
  const blob = new Blob([lines.join("\n")], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  const stamp = new Date().toISOString().replace(/[.:]/g, "-");
  a.href = url;
  a.download = `rng_plot_${stamp}.csv`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

function exportPlotPng() {
  if (plotChart && q("plotChart").style.display !== "none") {
    const a = document.createElement("a");
    const stamp = new Date().toISOString().replace(/[.:]/g, "-");
    a.href = plotChart.getDataURL({
      type: "png",
      pixelRatio: 2,
      backgroundColor: "#ffffff",
    });
    a.download = `rng_plot_${stamp}.png`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    return;
  }
  const canvas = q("plotCanvas");
  if (!canvas.width || !canvas.height) return;
  const a = document.createElement("a");
  const stamp = new Date().toISOString().replace(/[.:]/g, "-");
  a.href = canvas.toDataURL("image/png");
  a.download = `rng_plot_${stamp}.png`;
  document.body.appendChild(a);
  a.click();
  a.remove();
}

async function runSpecies() {
  const data = await fetchJson("/api/species", {
    reac: globalReac(),
    min_tp: globalMinTp(),
    formula: value("qFormula"),
    top: value("qSpeciesTop") || 20,
  });
  setMeta({ query: data.query, meta: data.meta });
  renderTable(data.rows || []);
}

async function runMass() {
  const data = await fetchJson("/api/species_mass", {
    reac: globalReac(),
    min_tp: globalMinTp(),
    mass: value("qMass"),
    mode: q("qMassMode").value,
    tol: value("qMassTol"),
    top: value("qMassTop") || 50,
  });
  setMeta({ query: data.query, meta: data.meta });
  renderTable(data.rows || []);
}

async function runNext() {
  const data = await fetchJson("/api/next", {
    reac: globalReac(),
    min_tp: globalMinTp(),
    smiles: value("qSmiles"),
    role: q("qRole").value,
    top: value("qNextTop") || 20,
    net_positive_only: q("qNetPositive").checked ? 1 : 0,
  });
  setMeta({ query: data.query, meta: data.meta });
  renderTable(data.rows || []);
}

async function runIntermediate() {
  const data = await fetchJson("/api/intermediate_candidates", {
    reac: globalReac(),
    min_tp: globalMinTp(),
    species_file: value("qInterSpeciesFile"),
    kind: q("qInterKind").value,
    top: value("qInterTop") || 120,
    abundance_threshold: value("qInterAbundance") || 5,
    start_ratio_max: value("qInterStartRatio") || 0.1,
    decay_alpha: value("qInterDecayAlpha") || 0.8,
    fwhm_min_ps: value("qInterFwhmMin") || 0.5,
    timestep_ps: value("qInterTimestepPs") || 0.0001,
    require_fwhm: q("qInterRequireFwhm").checked ? 1 : 0,
    with_flux: q("qInterWithFlux").checked ? 1 : 0,
    flux_top: value("qInterFluxTop") || 10,
  });
  setMeta({ query: data.query, meta: data.meta });
  renderTable(data.rows || []);
}

async function runRxnFormula() {
  const data = await fetchJson("/api/rxn_formula", {
    reac: globalReac(),
    min_tp: globalMinTp(),
    reactants: value("qReactants"),
    products: value("qProducts"),
    mode: q("qMode").value,
    top: value("qRxnTop") || 30,
    with_share: q("qRxnWithShare").checked ? 1 : 0,
    share_metric: q("qRxnShareMetric").value,
    share_abs_metric: q("qRxnShareAbs").checked ? 1 : 0,
    share_positive_only: q("qRxnSharePositive").checked ? 1 : 0,
  });
  setMeta({ query: data.query, meta: data.meta });
  renderTable(data.rows || []);
}

async function runPlot() {
  const targets = parseTargetsForPlot(value("qPlotTarget"));
  const data = await fetchJson("/api/plot", {
    reac: globalReac(),
    min_tp: globalMinTp(),
    species_file: value("qPlotSpeciesFile"),
    target: targets,
    formula_mode: q("qPlotFormulaMode").value,
    x_axis: q("qPlotXAxis").value,
    normalize: q("qPlotNormalize").value,
    smooth_window: value("qPlotSmooth") || 1,
    downsample: value("qPlotDownsample") || 1800,
  });

  // Left table shows formula->SMILES mapping for auditing
  renderTable(data.mapping || []);
  setMeta({ query: data.query, mapping_rows: (data.mapping || []).length });
  setPlotMeta({ query: data.query, meta: data.meta });

  const curves = (data.curves || []).map((c) => ({
    name: c.name,
    values: c.values || [],
    max_value: c.max_value ?? 0,
  }));
  state.plot = {
    xName: data.x_name || "x",
    yName: data.query && ["initial", "max"].includes(data.query.normalize) ? "normalized_count" : "count",
    xValues: data.x_values || [],
    curves,
  };
  await drawPlot(state.plot.xValues, state.plot.curves, state.plot.xName, state.plot.yName);
}

async function runWithToast(fn) {
  try {
    document.body.style.cursor = "progress";
    await fn();
  } catch (err) {
    setMeta({ error: String(err) });
    renderTable([]);
  } finally {
    document.body.style.cursor = "default";
  }
}

function bindEvents() {
  q("btnSpecies").addEventListener("click", () => runWithToast(runSpecies));
  q("btnMass").addEventListener("click", () => runWithToast(runMass));
  q("btnNext").addEventListener("click", () => runWithToast(runNext));
  q("btnIntermediate").addEventListener("click", () => runWithToast(runIntermediate));
  q("btnRxn").addEventListener("click", () => runWithToast(runRxnFormula));
  q("btnPlot").addEventListener("click", () => runWithToast(runPlot));

  q("btnRenderOne").addEventListener("click", () => runWithToast(renderOneSmiles));
  q("btnRenderReaction").addEventListener("click", () => runWithToast(renderReactionSmiles));

  q("btnExport").addEventListener("click", exportCurrentCsv);
  q("btnPlotExportCsv").addEventListener("click", exportPlotCsv);
  q("btnPlotExportPng").addEventListener("click", exportPlotPng);

  q("qMassMode").addEventListener("change", () => {
    q("qMassTol").value = q("qMassMode").value === "exact" ? "0.5" : "0";
  });

  window.addEventListener("resize", () => {
    if (plotChart && q("plotChart").style.display !== "none") {
      plotChart.resize();
      return;
    }
    if (state.plot.xValues.length && state.plot.curves.length) {
      drawPlotCanvas(state.plot.xValues, state.plot.curves, state.plot.xName, state.plot.yName);
    }
  });
}

async function init() {
  bindEvents();
  setMeta({ status: "ready", hint: "选择一个查询面板后点击“查询”" });
  setPlotMeta({ status: "ready", hint: "使用 Plot 面板查询后在此显示曲线" });
  renderTable([]);
  await drawPlot([], [], "x", "count");
}

init();
