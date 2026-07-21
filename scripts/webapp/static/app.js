const RESULT_MODULES = [
  { key: "species", label: "分子式检索" },
  { key: "mass", label: "质量数检索" },
  { key: "next", label: "路径检索" },
  { key: "intermediate", label: "中间体筛选" },
  { key: "rxn", label: "公式反应检索" },
  { key: "context_species", label: "物种事件" },
  { key: "context_reaction", label: "反应事件" },
  { key: "context_extract", label: "轨迹提取" },
  { key: "plot", label: "Plot 映射" },
];

const DEFAULT_RESULT_MODULE = "species";
const WORKBENCH_STACK_BREAKPOINT = 980;
const DEFAULT_GENERAL_QUERY_MODE = "formula";
const WORKSPACE_MODULES = [
  { key: "dataset", label: "数据集", hint: "文件与可用性" },
  { key: "species", label: "物种分析", hint: "公式、质量、中间体" },
  { key: "reaction", label: "反应路径", hint: "路径与公式反应" },
  { key: "events", label: "事件轨迹", hint: "定位与提取" },
  { key: "evolution", label: "时间演化", hint: "Species 与 Carbon" },
  { key: "transition", label: "转移网络", hint: "Matrix 与 Network" },
];
const VIEWER_CONTEXTS = {
  general: {
    cardId: "generalStructureCard",
    noteId: "generalStructureNote",
    showHId: "generalViewerShowH",
    galleryId: "generalSvgGallery",
  },
  intermediate: {
    cardId: "intermediateStructureCard",
    noteId: "intermediateStructureNote",
    showHId: "intermediateViewerShowH",
    galleryId: "intermediateSvgGallery",
  },
  next: {
    cardId: "nextStructureCard",
    noteId: "nextStructureNote",
    showHId: "nextViewerShowH",
    galleryId: "nextSvgGallery",
  },
  rxn: {
    cardId: "rxnStructureCard",
    noteId: "rxnStructureNote",
    showHId: "rxnViewerShowH",
    galleryId: "rxnSvgGallery",
  },
  plot: {
    cardId: "plotStructureCard",
    noteId: "plotStructureNote",
    showHId: "plotViewerShowH",
    galleryId: "plotSvgGallery",
  },
  carbon: {
    cardId: "carbonStructureCard",
    noteId: "carbonStructureNote",
    showHId: "carbonViewerShowH",
    galleryId: "carbonSvgGallery",
  },
};

const STRUCTURE_LIST_LIMIT = 48;

const state = {
  intermediateTaskId: "",
  results: {
    active: DEFAULT_RESULT_MODULE,
    byModule: {},
  },
  ui: {
    generalQueryMode: DEFAULT_GENERAL_QUERY_MODE,
    workspace: "dataset",
    dataset: null,
  },
  plot: {
    taskId: "",
    xName: "",
    yName: "count",
    xValues: [],
    curves: [],
    allCurves: [],
    selectedSeriesKeys: [],
    mappingRows: [],
  },
  carbonPlot: {
    taskId: "",
    svgText: "",
    svgUrl: "",
    query: {},
    plotData: [],
    formulaIndex: [],
    formulaLookup: null,
    baseRows: [],
    compareRows: [],
    compareXValues: [],
    compareCurves: [],
    selectedSeriesKeys: [],
    dragMergeItem: null,
    mergeBasket: [],
    summary: null,
  },
  transition: {
    data: null,
    mode: "heatmap",
    selected: null,
  },
  contextSpeciesTaskId: "",
  contextReactionTaskId: "",
  contextExtract: {
    taskId: "",
    isRunning: false,
    selectedEventRow: null,
    selectedEventConfig: null,
    atomGroups: null,
    frameRows: [],
    trajectoryText: "",
    trajectoryPreviewText: "",
    trajectoryFilename: "",
    framesFilename: "",
    trajectoryPath: "",
    vmdScriptPath: "",
    typeMapPath: "",
    parsedFrames: [],
    frameIndex: 0,
    viewMode: "3d",
    zoom: 1,
    rotX: -0.45,
    rotY: 0.65,
    showBox: true,
    highlightMode: "route_target",
    focusEventAtoms: true,
    showTrails: true,
    trailWindow: 8,
    hoverAtom: null,
    storyboardItems: [],
    snapshotItems: [],
  },
  structurePreviews: {},
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

function globalSpeciesFile() {
  return value("sharedSpeciesFile");
}

function globalTrajectoryFile() {
  return value("sharedTrajectoryFile");
}

function globalRouteFile() {
  return value("sharedRouteFile");
}

function globalTableFile() {
  return value("sharedTableFile");
}

function effectiveSpeciesFile(overrideId = "") {
  const overrideValue = overrideId ? value(overrideId) : "";
  return overrideValue || globalSpeciesFile();
}

function effectiveTrajectoryFile(overrideId = "") {
  const overrideValue = overrideId ? value(overrideId) : "";
  return overrideValue || globalTrajectoryFile();
}

function effectiveRouteFile(overrideId = "") {
  const overrideValue = overrideId ? value(overrideId) : "";
  return overrideValue || globalRouteFile();
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

function sleep(ms) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

async function waitTaskResult(taskId, { pollMs = 500, onProgress } = {}) {
  while (true) {
    const task = await fetchJson("/api/task_status", { task_id: taskId });
    if (typeof onProgress === "function") {
      onProgress(task);
    }
    if (task.status === "completed") {
      return task.result || {};
    }
    if (task.status === "error") {
      const err = new Error(task.error || "task failed");
      err.task = task;
      throw err;
    }
    await sleep(pollMs);
  }
}

function resultModuleLabel(key) {
  const hit = RESULT_MODULES.find((item) => item.key === key);
  return hit ? hit.label : key;
}

function ensureResultSlot(key) {
  const validKey = RESULT_MODULES.some((item) => item.key === key) ? key : DEFAULT_RESULT_MODULE;
  if (!state.results.byModule[validKey]) {
    state.results.byModule[validKey] = {
      key: validKey,
      label: resultModuleLabel(validKey),
      rows: [],
      columns: [],
      meta: { status: "idle", module: resultModuleLabel(validKey) },
    };
  }
  return state.results.byModule[validKey];
}

function buildColumns(rows) {
  return Array.from(
    rows.reduce((acc, row) => {
      Object.keys(row || {}).forEach((k) => acc.add(k));
      return acc;
    }, new Set())
  );
}

function normalizeViewerKey(key) {
  if (key === "species" || key === "mass" || key === "general" || !key) return "general";
  if (key === "intermediate" || key === "next" || key === "rxn" || key === "plot" || key === "carbon") return key;
  return "general";
}

function viewerContext(key) {
  return VIEWER_CONTEXTS[normalizeViewerKey(key)] || VIEWER_CONTEXTS.general;
}

function safeDecodeDataValue(value) {
  const raw = String(value || "");
  try {
    return decodeURIComponent(raw);
  } catch (_) {
    return raw;
  }
}

function looksLikeExplicitSmiles(text) {
  const value = String(text || "");
  return value.includes("[") || value.includes("]") || value.includes("=") || value.includes("#") || value.includes("(") || value.includes(")") || value.includes("->");
}

function extractPreviewableSmilesValue(col, text) {
  const key = String(col || "").toLowerCase();
  const value = String(text || "").trim();
  if (!value) return "";

  if (key.includes("matched_smiles")) {
    if (value.includes(";")) return "";
    const stripped = value.replace(/\(\d+\)\s*$/, "").trim();
    return looksLikeExplicitSmiles(stripped) ? stripped : "";
  }
  if (key.includes("smiles")) {
    if (value.includes(";")) return "";
    return value;
  }
  if ((key === "species" || key === "label") && looksLikeExplicitSmiles(value)) return value;
  return "";
}

function renderTableCellCode(col, text, short) {
  const previewValue = extractPreviewableSmilesValue(col, text);
  if (!previewValue) {
    return `<code>${escapeHtml(short)}</code>`;
  }
  const encoded = encodeURIComponent(previewValue);
  return `<code class="smiles-preview-token" data-smiles-preview="${encoded}">${escapeHtml(short)}</code>`;
}

function parseAtomIdText(text) {
  const raw = String(text || "").trim();
  if (!raw) return [];
  const normalized = raw
    .replace(/[\[\]\(\)]/g, " ")
    .replace(/[，；]/g, " ")
    .replace(/(\d)\s*[-:~]\s*(\d)/g, "$1-$2");
  const ids = new Set();
  normalized.split(/[\s,;]+/).forEach((token) => {
    const item = String(token || "").trim();
    if (!item) return;
    const m = item.match(/^(\d+)-(\d+)$/);
    if (m) {
      const lo = Number.parseInt(m[1], 10);
      const hi = Number.parseInt(m[2], 10);
      const start = Math.min(lo, hi);
      const end = Math.max(lo, hi);
      for (let value = start; value <= end; value += 1) {
        ids.add(value);
      }
      return;
    }
    if (/^\d+$/.test(item)) {
      ids.add(Number.parseInt(item, 10));
    }
  });
  return Array.from(ids).filter((value) => Number.isFinite(value) && value > 0).sort((a, b) => a - b);
}

function buildOvitoParticleIdentifierExpression(text) {
  const ids = parseAtomIdText(text);
  if (!ids.length) return "";
  const clauses = [];
  let start = ids[0];
  let prev = ids[0];
  for (let idx = 1; idx <= ids.length; idx += 1) {
    const value = idx < ids.length ? ids[idx] : null;
    if (value === prev + 1) {
      prev = value;
      continue;
    }
    if (start === prev) {
      clauses.push(`ParticleIdentifier==${start}`);
    } else {
      clauses.push(`(ParticleIdentifier>=${start} && ParticleIdentifier<=${prev})`);
    }
    start = value;
    prev = value;
  }
  return clauses.join(" || ");
}

function booleanish(value) {
  if (typeof value === "boolean") return value;
  if (typeof value === "number") return Number.isFinite(value) && value !== 0;
  const text = String(value ?? "").trim().toLowerCase();
  return ["1", "true", "yes", "y", "on"].includes(text);
}

function numericOrZero(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number : 0;
}

function atomIdTextValue(value) {
  if (Array.isArray(value)) {
    return value
      .map((item) => Number.parseInt(String(item), 10))
      .filter((item) => Number.isFinite(item) && item > 0)
      .join(",");
  }
  return String(value || "").trim();
}

function summarizeContextAtomGroups(atomGroups) {
  const groups = atomGroups && typeof atomGroups === "object" ? atomGroups : {};
  const orderedKeys = [
    ["core_atom_ids", "core"],
    ["context_atom_ids", "context"],
    ["reactant_atom_ids", "reactant"],
    ["product_atom_ids", "product"],
  ];
  const parts = orderedKeys
    .map(([key, label]) => {
      const values = Array.isArray(groups[key]) ? groups[key] : [];
      return `${label}=${values.length}`;
    });
  return parts.join(" | ");
}

function resolveContextEventResolution(row) {
  const safeRow = row || {};
  const verificationStatus = String(safeRow.verification_status || "").trim();
  if (verificationStatus) {
    const selectedEventClass = String(safeRow.selected_event_class || "").trim()
      || (verificationStatus === "verified_exact"
        ? "verified"
        : (verificationStatus.startsWith("discarded_") ? "discarded" : "candidate"));
    const label = String(safeRow.event_resolution_label || "").trim()
      || (selectedEventClass === "verified"
        ? "严格反应事件"
        : (selectedEventClass === "candidate" ? "相关候选过程" : "已拒绝"));
    const reason = String(safeRow.event_resolution_reason || "").trim()
      || String(safeRow.failure_reason || "").trim()
      || verificationStatus;
    const step2Visualizable = safeRow.step2_visualizable == null
      ? booleanish(safeRow.visualization_ready)
      : booleanish(safeRow.step2_visualizable);
    const step2Extractable = safeRow.step2_extractable == null
      ? selectedEventClass !== "discarded"
      : booleanish(safeRow.step2_extractable);
    return {
      key: selectedEventClass,
      label,
      reason,
      step2Visualizable,
      step2Extractable,
      verificationStatus,
      selectedEventClass,
    };
  }
  const explicitKey = String(safeRow.event_resolution || "").trim();
  const routeResolved = (
    numericOrZero(safeRow.core_atom_count) > 0
    || numericOrZero(safeRow.context_atom_count) > 0
    || numericOrZero(safeRow.route_event_atom_count) > 0
    || numericOrZero(safeRow.route_changed_target_atoms) > 0
    || numericOrZero(safeRow.route_reactant_to_product_atoms) > 0
    || numericOrZero(safeRow.route_product_to_reactant_atoms) > 0
    || safeRow.route_event_start_frame != null
    || safeRow.route_event_end_frame != null
  );
  const anchorOnly = (
    !routeResolved
    && (
      numericOrZero(safeRow.route_target_atom_count) > 0
      || numericOrZero(safeRow.route_anchor_reactant_atom_count) > 0
      || numericOrZero(safeRow.route_anchor_product_atom_count) > 0
      || numericOrZero(safeRow.route_context_atom_count) > 0
    )
  );
  const key = explicitKey || (routeResolved ? "route_resolved" : (anchorOnly ? "anchor_only" : "species_only"));
  const label = String(safeRow.event_resolution_label || "").trim()
    || (key === "route_resolved" ? "可原子级可视化" : (key === "anchor_only" ? "仅锚点上下文" : "仅物种定位"));
  const reason = String(safeRow.event_resolution_reason || "").trim()
    || (key === "route_resolved"
      ? "route 已解析到事件变化原子"
      : (key === "anchor_only"
        ? "只解析到锚点原子或上下文簇"
        : ".species 时间事件没有对应的 route 原子变化"));
  const step2Visualizable = safeRow.step2_visualizable == null ? (key === "route_resolved") : booleanish(safeRow.step2_visualizable);
  const step2Extractable = safeRow.step2_extractable == null ? (key !== "species_only") : booleanish(safeRow.step2_extractable);
  return { key, label, reason, step2Visualizable, step2Extractable, verificationStatus: "", selectedEventClass: "" };
}

function selectedEventModeValue(baseSelectId, advancedSelectId) {
  const advanced = value(advancedSelectId);
  if (advanced) return advanced;
  const base = q(baseSelectId);
  return base instanceof HTMLSelectElement ? String(base.value || "") : "";
}

function resolveContextEventAtomIds(row) {
  const safeRow = row || {};
  const contextIds = atomIdTextValue(safeRow.route_context_atom_ids || safeRow.context_atom_ids_text || safeRow.context_atom_ids);
  const eventIds = String(
    safeRow.route_event_atom_ids
      || safeRow.core_atom_ids_text
      || atomIdTextValue(safeRow.core_atom_ids)
      || safeRow.route_changed_target_atom_ids
      || safeRow.route_reactant_to_product_atom_ids
      || safeRow.route_product_to_reactant_atom_ids
      || ""
  ).trim();
  const anchorTargetIds = String(
    safeRow.route_target_atom_ids
      || safeRow.reactant_atom_ids_text
      || atomIdTextValue(safeRow.reactant_atom_ids)
      || ""
  ).trim();
  if (contextIds) {
    return { text: contextIds, source: "context", eventIds, anchorTargetIds, contextIds };
  }
  if (eventIds) {
    return { text: eventIds, source: "event", eventIds, anchorTargetIds, contextIds };
  }
  if (anchorTargetIds) {
    return { text: anchorTargetIds, source: "anchor_target", eventIds, anchorTargetIds, contextIds };
  }
  return { text: "", source: "", eventIds, anchorTargetIds, contextIds };
}

function contextRowDefaultFrameRangeText(row) {
  const safeRow = row || {};
  const windowStart = Number(safeRow.window_start);
  const windowEnd = Number(safeRow.window_end);
  if (Number.isFinite(windowStart) && Number.isFinite(windowEnd)) {
    return `${Math.trunc(windowStart)}-${Math.trunc(windowEnd)}`;
  }
  const routeStart = Number(safeRow.route_event_start_frame);
  const routeEnd = Number(safeRow.route_event_end_frame);
  if (Number.isFinite(routeStart) && Number.isFinite(routeEnd)) {
    return `${Math.trunc(routeStart)}-${Math.trunc(routeEnd)}`;
  }
  const requestedStart = Number(safeRow.requested_start);
  const requestedEnd = Number(safeRow.requested_end);
  if (Number.isFinite(requestedStart) && Number.isFinite(requestedEnd)) {
    return `${Math.trunc(requestedStart)}-${Math.trunc(requestedEnd)}`;
  }
  return "";
}

function contextHasManualFrameRanges() {
  return !!String(value("qContextFrameRanges") || "").trim();
}

function syncContextExtractActionState() {
  const autoBtn = q("btnContextExtractAuto");
  const openOvitoBtn = q("btnContextExtractOpenOvito");
  const openVmdBtn = q("btnContextExtractOpenVmd");
  const manualBtn = q("btnContextExtractManual");
  const clearBtn = q("btnContextClearSelection");
  const hint = q("contextExtractModeHint");
  const running = !!state.contextExtract.isRunning;
  const selectedEvent = state.contextExtract.selectedEventRow;
  const hasSelectedEvent = !!selectedEvent;
  const selectedResolution = resolveContextEventResolution(selectedEvent);
  const selectedClass = String(selectedResolution.selectedEventClass || "");
  const hasManualRanges = contextHasManualFrameRanges();
  const canAutoExtract = hasSelectedEvent && selectedResolution.step2Extractable;
  const canDirectOpen = hasSelectedEvent && selectedResolution.step2Visualizable;

  if (autoBtn instanceof HTMLButtonElement) {
    autoBtn.disabled = running || !canAutoExtract;
  }
  if (openOvitoBtn instanceof HTMLButtonElement) {
    openOvitoBtn.disabled = running || !canDirectOpen;
  }
  if (openVmdBtn instanceof HTMLButtonElement) {
    openVmdBtn.disabled = running || !canDirectOpen;
  }
  if (manualBtn instanceof HTMLButtonElement) {
    manualBtn.disabled = running || !hasManualRanges;
  }
  if (clearBtn instanceof HTMLButtonElement) {
    clearBtn.disabled = running || !hasSelectedEvent;
  }
  if (!(hint instanceof HTMLElement)) return;
  if (running) {
    hint.textContent = "后台正在生成事件子轨迹，Step 2 按钮暂时锁定。";
    return;
  }
  if (hasSelectedEvent && !selectedResolution.step2Extractable) {
    hint.textContent = `当前已选事件属于“${selectedResolution.label}”：${selectedResolution.reason}。这条记录不能进入 Step 2 主流程。`;
    return;
  }
  if (hasSelectedEvent && !selectedResolution.step2Visualizable) {
    hint.textContent = `当前已选事件属于“${selectedResolution.label}”：可以导出候选过程上下文，但不能作为“严格反应事件”直接打开 OVITO/VMD。`;
    return;
  }
  if (hasSelectedEvent && selectedClass === "candidate") {
    hint.textContent = "当前已选的是候选过程。你可以继续导出局部轨迹做人工核查，但它不会被宣称为严格净反应事件。";
    return;
  }
  if (hasSelectedEvent && hasManualRanges) {
    hint.textContent = "主流程已就绪：你可以直接生成当前事件子轨迹，或在高级区按手工 frame/atom 覆盖切片。";
    return;
  }
  if (hasSelectedEvent) {
    hint.textContent = "当前已选中事件。下一步直接点击“生成事件子轨迹”或“生成并用 OVITO/VMD 打开”。";
    return;
  }
  if (hasManualRanges) {
    hint.textContent = "当前未选事件，但高级区已具备手工切片条件，可直接按 frame/atom ids 生成子轨迹。";
    return;
  }
  hint.textContent = "先在 Step 1 结果中选中一条事件，再到 Step 2 生成并查看事件子轨迹。";
}

async function copyTextToClipboard(text) {
  const payload = String(text || "");
  if (!payload.trim()) {
    throw new Error("没有可复制的内容");
  }
  if (navigator.clipboard && navigator.clipboard.writeText) {
    await navigator.clipboard.writeText(payload);
    return;
  }
  const ta = document.createElement("textarea");
  ta.value = payload;
  ta.setAttribute("readonly", "");
  ta.style.position = "fixed";
  ta.style.opacity = "0";
  document.body.appendChild(ta);
  ta.focus();
  ta.select();
  const ok = document.execCommand("copy");
  ta.remove();
  if (!ok) {
    throw new Error("复制失败");
  }
}

function flashButtonLabel(button, successText = "已复制", delayMs = 900) {
  if (!(button instanceof HTMLButtonElement)) return;
  const original = button.textContent || "";
  button.textContent = successText;
  window.setTimeout(() => {
    button.textContent = original;
  }, delayMs);
}

function pathDirname(path) {
  const text = String(path || "").trim();
  if (!text) return "";
  const normalized = text.replace(/\\/g, "/").replace(/\/+$/, "");
  const idx = normalized.lastIndexOf("/");
  if (idx <= 0) return normalized || "";
  return normalized.slice(0, idx);
}

function renderRowsToTable(tableEl, rows, viewerKey = "general") {
  const thead = tableEl.querySelector("thead");
  const tbody = tableEl.querySelector("tbody");
  thead.innerHTML = "";
  tbody.innerHTML = "";

  if (!rows || rows.length === 0) {
    thead.innerHTML = "<tr><th>Result</th></tr>";
    tbody.innerHTML = "<tr><td>无数据</td></tr>";
    return;
  }

  const cols = buildColumns(rows);
  const header = `<tr>${cols.map((c) => `<th>${escapeHtml(c)}</th>`).join("")}</tr>`;
  thead.innerHTML = header;

  const bodyHtml = rows
    .map((row) => {
      const tds = cols
        .map((col) => {
          const v = row[col] ?? "";
          const text = String(v);
          const short = text.length > 180 ? `${text.slice(0, 177)}...` : text;
          return `<td>${renderTableCellCode(col, text, short)}</td>`;
        })
        .join("");
      return `<tr>${tds}</tr>`;
    })
    .join("");

  tbody.innerHTML = bodyHtml;
}

function contextPreferredColumns(rows) {
  const safeRows = Array.isArray(rows) ? rows : [];
  const hasVerificationStatus = safeRows.some((row) => row && Object.prototype.hasOwnProperty.call(row, "verification_status"));
  const hasReactionEventId = safeRows.some((row) => row && Object.prototype.hasOwnProperty.call(row, "event_id"));
  const hasCandidateId = safeRows.some((row) => row && Object.prototype.hasOwnProperty.call(row, "candidate_id"));
  const hasEventIndex = safeRows.some((row) => row && Object.prototype.hasOwnProperty.call(row, "event_index"));
  const hasRangeIndex = safeRows.some((row) => row && Object.prototype.hasOwnProperty.call(row, "range_index"));
  const base = hasVerificationStatus
    ? [
        "event_index",
        "candidate_index",
        "event_id",
        "reaction_match_mode",
        "route_event_start_frame",
        "route_event_end_frame",
        "comparison_before_frame",
        "comparison_after_frame",
        "trajectory_pre_frame",
        "trajectory_anchor_frame",
        "trajectory_post_frame",
        "window_start",
        "window_end",
        "n_window_frames",
        "expected_delta_summary",
        "observed_delta_summary",
        "verification_status",
        "route_confidence",
        "reaction_confidence",
        "trajectory_sampling_status",
        "context_reconstruction_mode",
        "visualization_ready",
        "matched_smiles_at_anchor",
      ]
    : hasReactionEventId
    ? [
        "event_index",
        "event_id",
        "reaction_match_mode",
        "anchor_frame",
        "route_event_start_frame",
        "route_event_end_frame",
        "comparison_before_frame",
        "comparison_after_frame",
        "window_start",
        "window_end",
        "n_window_frames",
        "from_multiset_summary",
        "to_multiset_summary",
        "net_reaction_summary",
        "core_atom_count",
        "context_atom_count",
        "event_quality",
        "confidence",
        "matched_smiles_at_anchor",
      ]
    : hasCandidateId
    ? [
        "candidate_index",
        "candidate_id",
        "anchor_frame",
        "route_event_start_frame",
        "route_event_end_frame",
        "comparison_before_frame",
        "comparison_after_frame",
        "from_multiset_summary",
        "to_multiset_summary",
        "net_reaction_summary",
        "failure_reason",
        "event_quality",
        "confidence",
        "matched_smiles_at_anchor",
      ]
    : hasEventIndex
    ? [
        "event_index",
        "event_type",
        "anchor_frame",
        "window_start",
        "window_end",
        "n_window_frames",
        "count_at_frame",
        "delta_from_prev",
        "matched_smiles_at_anchor",
      ]
    : hasRangeIndex
      ? [
          "range_index",
          "event_type",
          "requested_start",
          "requested_end",
          "first_frame_found",
          "last_frame_found",
          "n_window_frames",
          "count_at_frame",
          "matched_smiles_at_anchor",
        ]
      : ["event_type", "anchor_frame", "count_at_frame", "matched_smiles_at_anchor"];
  const existing = new Set(buildColumns(safeRows));
  const cols = base.filter((col) => existing.has(col));
  const resolutionCols = [
    "event_resolution_label",
    "route_context_atom_source",
  ].filter((col) => existing.has(col));
  const routeCols = [
    "route_event_start_frame",
    "route_event_end_frame",
    "route_context_atom_count",
    "route_context_group_mode",
    "route_context_selected_group_count",
    "route_context_group_count",
    "route_event_atom_count",
    "route_target_atom_count",
    "route_changed_target_atoms",
    "route_reactant_to_product_atoms",
    "route_product_to_reactant_atoms",
  ].filter((col) => existing.has(col));
  return [...cols, ...resolutionCols, ...routeCols].filter((col, index, arr) => arr.indexOf(col) === index);
}

function sameContextEventRow(a, b) {
  if (!a || !b) return false;
  const eventIdA = String(a.event_id || "").trim();
  const eventIdB = String(b.event_id || "").trim();
  if (eventIdA && eventIdB) return eventIdA === eventIdB;
  const anchorA = Number(a.anchor_frame ?? a.first_frame_found ?? a.requested_start);
  const anchorB = Number(b.anchor_frame ?? b.first_frame_found ?? b.requested_start);
  const typeA = String(a.event_type || "");
  const typeB = String(b.event_type || "");
  return Number.isFinite(anchorA) && Number.isFinite(anchorB) && anchorA === anchorB && typeA === typeB;
}

function renderContextRowsToTable(tableEl, rows, { actionLabel = "", actionClass = "", selectedRow = null } = {}) {
  const thead = tableEl.querySelector("thead");
  const tbody = tableEl.querySelector("tbody");
  thead.innerHTML = "";
  tbody.innerHTML = "";

  const safeRows = Array.isArray(rows) ? rows : [];
  if (!safeRows.length) {
    thead.innerHTML = "<tr><th>Result</th></tr>";
    tbody.innerHTML = "<tr><td>无数据</td></tr>";
    return;
  }

  const cols = contextPreferredColumns(safeRows);
  const showAction = !!actionLabel;
  const header = `<tr>${cols.map((c) => `<th>${escapeHtml(c)}</th>`).join("")}${showAction ? "<th>Action</th>" : ""}</tr>`;
  thead.innerHTML = header;

  const bodyHtml = safeRows
    .map((row, rowIndex) => {
      const resolution = resolveContextEventResolution(row);
      const tds = cols
        .map((col) => {
          const v = row[col] ?? "";
          const text = String(v);
          const short = text.length > 180 ? `${text.slice(0, 177)}...` : text;
          return `<td>${renderTableCellCode(col, text, short)}</td>`;
        })
        .join("");
      const isSelected = sameContextEventRow(row, selectedRow);
      const rowActionLabel = isSelected ? "已载入" : actionLabel;
      const actionTd = showAction
        ? `
        <td class="context-row-actions">
          <button
            type="button"
            class="ghost ${escapeHtml(actionClass)}${isSelected ? " is-selected" : ""}"
            data-row-index="${rowIndex}"
            title="${escapeHtml(resolution.reason)}"
            ${(!resolution.step2Extractable && !isSelected) ? "disabled" : ""}
          >${escapeHtml(rowActionLabel)}</button>
        </td>
      `
        : "";
      return `<tr class="context-result-row${isSelected ? " is-selected" : ""}">${tds}${actionTd}</tr>`;
    })
    .join("");
  tbody.innerHTML = bodyHtml;
}

function renderModuleResultCard(moduleKey, metaId, tableId, exportBtnId) {
  const metaEl = q(metaId);
  const tableEl = q(tableId);
  const exportBtn = q(exportBtnId);
  if (!metaEl || !tableEl || !exportBtn) return;
  const slot = ensureResultSlot(moduleKey);
  const card = metaEl.closest(".module-result-card");
  const hasRows = !!(slot.rows || []).length;
  const status = String(slot.meta?.status || "").toLowerCase();
  const shouldShow = hasRows || (status && !["idle", "ready"].includes(status));
  if (card instanceof HTMLElement) {
    card.classList.toggle("hidden", !shouldShow);
  }
  metaEl.textContent = JSON.stringify(slot.meta || {}, null, 2);
  renderRowsToTable(tableEl, slot.rows || [], moduleKey);
  renderStructureListFromRows(moduleKey, slot.rows || []);
  exportBtn.disabled = !hasRows;
}

function activeGeneralResultKey() {
  const active = state.results.active || DEFAULT_RESULT_MODULE;
  if (active === "species" || active === "mass") return active;
  return state.ui.generalQueryMode === "mass" ? "mass" : "species";
}

function renderGeneralResultCard() {
  const key = activeGeneralResultKey();
  const slot = ensureResultSlot(key);
  const metaEl = q("generalMetaBox");
  const tableEl = q("generalResultTable");
  const exportBtn = q("btnExportGeneral");
  const titleEl = q("generalResultTitle");
  const card = q("generalResultCard");
  if (!metaEl || !tableEl || !exportBtn || !titleEl || !card) return;
  const hasRows = !!(slot.rows || []).length;
  const status = String(slot.meta?.status || "").toLowerCase();
  const shouldShow = hasRows || (status && !["idle", "ready"].includes(status));
  card.classList.toggle("hidden", !shouldShow);
  titleEl.textContent = key === "mass" ? "质量数检索结果" : "分子式检索结果";
  metaEl.textContent = JSON.stringify(slot.meta || {}, null, 2);
  renderRowsToTable(tableEl, slot.rows || [], "general");
  renderStructureListFromRows("general", slot.rows || []);
  exportBtn.disabled = !hasRows;
}

function renderContextSelectedEventBox() {
  const box = q("contextSelectedEventBox");
  const resolvedAtomIdsBox = q("qContextResolvedAtomIds");
  const ovitoExprBox = q("qContextOvitoSelectionExpr");
  if (!box) return;
  const row = state.contextExtract.selectedEventRow;
  const selectedConfig = state.contextExtract.selectedEventConfig || {};
  if (!row) {
    box.textContent = "尚未选择事件。请先在 Step 1 的“反应事件候选实例”中载入一条事件，或直接填写帧范围。";
    if (resolvedAtomIdsBox instanceof HTMLTextAreaElement) {
      resolvedAtomIdsBox.value = "";
    }
    if (ovitoExprBox instanceof HTMLTextAreaElement) {
      ovitoExprBox.value = "";
    }
    syncContextExtractActionState();
    return;
  }
  const parts = [];
  const resolution = resolveContextEventResolution(row);
  const verificationStatus = String(row.verification_status || "").trim();
  if (selectedConfig.source_label) parts.push(selectedConfig.source_label);
  if (row.event_id) parts.push(`event_id=${row.event_id}`);
  if (row.event_index != null) parts.push(`事件 #${row.event_index}`);
  if (row.range_index != null) parts.push(`范围 #${row.range_index}`);
  if (row.event_type) parts.push(`type=${row.event_type}`);
  if (row.reaction_match_mode) parts.push(`match=${row.reaction_match_mode}`);
  parts.push(`status=${resolution.label}`);
  if (row.anchor_frame != null) parts.push(`anchor=${row.anchor_frame}`);
  if (row.window_start != null || row.window_end != null) {
    parts.push(`window=${row.window_start ?? "?"}-${row.window_end ?? "?"}`);
  } else if (row.requested_start != null || row.requested_end != null) {
    parts.push(`range=${row.requested_start ?? "?"}-${row.requested_end ?? "?"}`);
  }
  if (row.route_event_start_frame != null || row.route_event_end_frame != null) {
    parts.push(`event=${row.route_event_start_frame ?? "?"}-${row.route_event_end_frame ?? "?"}`);
  }
  if (row.route_context_atom_count != null) parts.push(`context_atoms=${row.route_context_atom_count}`);
  if (row.core_atom_count != null) parts.push(`core_atoms=${row.core_atom_count}`);
  if (row.route_reactant_atom_count != null) parts.push(`reactant_atoms=${row.route_reactant_atom_count}`);
  if (row.route_product_atom_count != null) parts.push(`product_atoms=${row.route_product_atom_count}`);
  if (row.route_context_group_mode) parts.push(`cluster=${row.route_context_group_mode}`);
  if (row.route_context_selected_group_count != null && row.route_context_group_count != null) {
    parts.push(`groups=${row.route_context_selected_group_count}/${row.route_context_group_count}`);
  }
  if (row.route_event_atom_count != null) parts.push(`event_atoms=${row.route_event_atom_count}`);
  if (row.route_target_atom_count != null && row.core_atom_count == null) parts.push(`core_atoms=${row.route_target_atom_count}`);
  if (row.count_at_frame != null) parts.push(`count=${row.count_at_frame}`);
  const matched = String(row.matched_smiles_at_anchor || "").trim();
  box.textContent = matched ? `${parts.join(" | ")}\nmatched=${matched}` : parts.join(" | ");
  const resolvedInfo = resolveContextEventAtomIds(row);
  const resolvedAtomIds = resolvedInfo.text;
  if (resolvedAtomIdsBox instanceof HTMLTextAreaElement) {
    resolvedAtomIdsBox.value = resolvedAtomIds;
  }
  if (ovitoExprBox instanceof HTMLTextAreaElement) {
    ovitoExprBox.value = buildOvitoParticleIdentifierExpression(resolvedAtomIds);
  }
  if (!resolvedAtomIds) {
    box.textContent = `${box.textContent}\nroute 原子解析为空：请确认已生成对应 .route 文件，或该事件没有可识别的原子变化。`;
    box.textContent = `${box.textContent}\n${resolution.reason}`;
  } else if (verificationStatus) {
    box.textContent = `${box.textContent}\nexpected_delta=${String(row.expected_delta_summary || "").trim() || "-"}\nobserved_delta=${String(row.observed_delta_summary || "").trim() || "-"}\nverification=${verificationStatus}\nsampling=${String(row.trajectory_sampling_status || "").trim() || "-"}\ncontext=${String(row.context_reconstruction_mode || "").trim() || "-"}\nroute_confidence=${String(row.route_confidence ?? "-")}\nreaction_confidence=${String(row.reaction_confidence ?? "-")}`;
  } else if (String(row.from_multiset_summary || "").trim() || String(row.to_multiset_summary || "").trim()) {
    box.textContent = `${box.textContent}\nfrom_multiset=${String(row.from_multiset_summary || "").trim() || "-"}\nto_multiset=${String(row.to_multiset_summary || "").trim() || "-"}\nnet=${String(row.net_reaction_summary || "").trim() || "-"}\nquality=${String(row.event_quality || "").trim() || "-"}`;
  } else if (String(row.transition_from_samples || "").trim() || String(row.transition_to_samples || "").trim()) {
    box.textContent = `${box.textContent}\nfrom=${String(row.transition_from_samples || "").trim() || "-"}\nto=${String(row.transition_to_samples || "").trim() || "-"}`;
  } else if (!resolution.step2Visualizable) {
    box.textContent = `${box.textContent}\n${resolution.reason}`;
  } else if (resolvedInfo.source === "context") {
    box.textContent = `${box.textContent}\n当前提取器已优先采用“轨迹可视化上下文原子集”，用于在前后几帧中连续观察该分子/反应团的变化。`;
  } else if (resolvedInfo.source === "anchor_target" && !resolvedInfo.eventIds) {
    box.textContent = `${box.textContent}\n未解析到“变化原子”，已回退为 anchor 帧目标物种对应的 atom ids，可直接用于 OVITO/VMD 继续核对。`;
  }
  syncContextExtractActionState();
}

function renderContextLocateResultCard(moduleKey, cardId, metaId, tableId, exportBtnId) {
  const slot = ensureResultSlot(moduleKey);
  const card = q(cardId);
  const metaEl = q(metaId);
  const tableEl = q(tableId);
  const exportCsvBtn = q(exportBtnId);
  const candidateMetaEl = q("contextReactionCandidateMetaBox");
  const candidateTableEl = q("contextReactionCandidateTable");
  const discardedMetaEl = q("contextReactionDiscardedMetaBox");
  const discardedTableEl = q("contextReactionDiscardedTable");
  if (!card || !metaEl || !tableEl || !exportCsvBtn) return;
  const hasRows = !!(slot.rows || []).length;
  const candidateRows = Array.isArray(slot.meta?.candidate_rows) ? slot.meta.candidate_rows : [];
  const discardedRows = Array.isArray(slot.meta?.discarded_rows) ? slot.meta.discarded_rows : [];
  const status = String(slot.meta?.status || "").toLowerCase();
  const shouldShow = hasRows || candidateRows.length || discardedRows.length || (status && !["idle", "ready"].includes(status));
  card.classList.toggle("hidden", !shouldShow);
  const safeMeta = { ...(slot.meta || {}) };
  delete safeMeta.candidate_rows;
  delete safeMeta.discarded_rows;
  metaEl.textContent = JSON.stringify(safeMeta, null, 2);
  renderContextRowsToTable(tableEl, slot.rows || [], {
    actionLabel: "选为 Step 2",
    actionClass: "btn-context-load-row",
    selectedRow: state.contextExtract.selectedEventRow,
  });
  if (moduleKey === "context_reaction" && candidateMetaEl) {
    candidateMetaEl.textContent = JSON.stringify({
      status,
      candidate_rows: candidateRows.length,
    }, null, 2);
  }
  if (moduleKey === "context_reaction" && candidateTableEl) {
    renderContextRowsToTable(candidateTableEl, candidateRows, {
      actionLabel: "按候选过程查看",
      actionClass: "btn-context-load-candidate-row",
      selectedRow: state.contextExtract.selectedEventRow,
    });
  }
  if (moduleKey === "context_reaction" && discardedMetaEl) {
    discardedMetaEl.textContent = JSON.stringify({
      status,
      discarded_rows: discardedRows.length,
    }, null, 2);
  }
  if (moduleKey === "context_reaction" && discardedTableEl) {
    renderContextRowsToTable(discardedTableEl, discardedRows, {
      actionLabel: "",
      actionClass: "",
      selectedRow: null,
    });
  }
  exportCsvBtn.disabled = !hasRows;
}

function renderContextExtractResultCard() {
  const slot = ensureResultSlot("context_extract");
  const card = q("contextExtractResultCard");
  const metaEl = q("contextExtractMetaBox");
  const atomGroupsEl = q("contextExtractAtomGroupsSummary");
  const pathPanel = q("contextExtractPathPanel");
  const trajectoryPathEl = q("contextExtractTrajectoryPath");
  const vmdScriptPathEl = q("contextExtractVmdScriptPath");
  const typeMapPathEl = q("contextExtractTypeMapPath");
  const copyTrajectoryPathBtn = q("btnCopyContextTrajectoryPath");
  const copyVmdScriptPathBtn = q("btnCopyContextVmdScriptPath");
  const copyTypeMapPathBtn = q("btnCopyContextTypeMapPath");
  const openExportDirBtn = q("btnOpenContextExportDir");
  const tableEl = q("contextExtractResultTable");
  const framesBox = q("contextExtractFramesBox");
  const exportFramesBtn = q("btnExportContextFrames");
  const exportTrajBtn = q("btnExportContextTraj");
  const openBtn = q("btnOpenContextTraj");
  const openVmdBtn = q("btnOpenContextTrajVmd");
  const openOvitoBtn = q("btnOpenContextTrajOvito");
  const openPymolBtn = q("btnOpenContextTrajPymol");
  const revealBtn = q("btnRevealContextTraj");
  if (
    !card || !metaEl || !tableEl || !framesBox || !exportFramesBtn || !exportTrajBtn
    || !openBtn || !openVmdBtn || !openOvitoBtn || !openPymolBtn || !revealBtn
    || !trajectoryPathEl || !vmdScriptPathEl || !typeMapPathEl
    || !copyTrajectoryPathBtn || !copyVmdScriptPathBtn || !copyTypeMapPathBtn || !openExportDirBtn
  ) return;
  const hasRows = !!(slot.rows || []).length;
  const status = String(slot.meta?.status || "").toLowerCase();
  const shouldShow = hasRows || (status && !["idle", "ready"].includes(status));
  card.classList.toggle("hidden", !shouldShow);
  metaEl.textContent = JSON.stringify(slot.meta || {}, null, 2);
  if (atomGroupsEl instanceof HTMLElement) {
    const atomGroups = state.contextExtract.atomGroups || {};
    const truth = slot.meta?.meta?.event_truth_summary || {};
    const classLabel = String(slot.meta?.meta?.selected_event_class || slot.meta?.selected_event_class || "")
      || String(state.contextExtract.selectedEventConfig?.selected_event_class || "");
    const truthParts = [];
    if (classLabel) truthParts.push(classLabel === "verified" ? "严格事件" : "候选过程");
    if (truth.expected_delta_summary) truthParts.push(`expected=${truth.expected_delta_summary}`);
    if (truth.observed_delta_summary) truthParts.push(`observed=${truth.observed_delta_summary}`);
    if (truth.trajectory_sampling_status) truthParts.push(`sampling=${truth.trajectory_sampling_status}`);
    if (truth.context_reconstruction_mode) truthParts.push(`context=${truth.context_reconstruction_mode}`);
    const groupSummary = Object.keys(atomGroups).length
      ? `当前导出子轨迹默认采用“跨帧完整分子并集”上下文：${summarizeContextAtomGroups(atomGroups)}`
      : "";
    atomGroupsEl.textContent = [truthParts.join(" | "), groupSummary].filter(Boolean).join(" | ");
  }
  renderContextRowsToTable(tableEl, slot.rows || []);
  const frameRows = state.contextExtract.frameRows || [];
  framesBox.textContent = JSON.stringify(frameRows, null, 2);
  exportFramesBtn.disabled = !frameRows.length;
  const hasTrajText = !!state.contextExtract.trajectoryText;
  const hasTrajPath = !!state.contextExtract.trajectoryPath;
  const hasVmdScriptPath = !!state.contextExtract.vmdScriptPath;
  const hasTypeMapPath = !!state.contextExtract.typeMapPath;
  const exportDirPath = pathDirname(state.contextExtract.vmdScriptPath || state.contextExtract.trajectoryPath || state.contextExtract.typeMapPath || "");
  trajectoryPathEl.value = state.contextExtract.trajectoryPath || "";
  vmdScriptPathEl.value = state.contextExtract.vmdScriptPath || "";
  typeMapPathEl.value = state.contextExtract.typeMapPath || "";
  if (pathPanel instanceof HTMLElement) {
    pathPanel.classList.toggle("hidden", !(hasTrajPath || hasVmdScriptPath || hasTypeMapPath));
  }
  copyTrajectoryPathBtn.disabled = !hasTrajPath;
  copyVmdScriptPathBtn.disabled = !hasVmdScriptPath;
  copyTypeMapPathBtn.disabled = !hasTypeMapPath;
  openExportDirBtn.disabled = !exportDirPath;
  const query = slot.meta?.query || {};
  const manualExtraction = !!String(query.frame_ranges || "").trim() || numericOrZero(query.manual_atom_ids_count) > 0;
  const selectedResolution = resolveContextEventResolution(state.contextExtract.selectedEventRow);
  const allowExternalOpen = !!hasTrajPath && (manualExtraction || selectedResolution.step2Visualizable);
  exportTrajBtn.disabled = !hasTrajText && !hasTrajPath;
  openBtn.disabled = !allowExternalOpen;
  openVmdBtn.disabled = !allowExternalOpen;
  openOvitoBtn.disabled = !allowExternalOpen;
  openPymolBtn.disabled = !allowExternalOpen;
  revealBtn.disabled = !hasTrajPath;
  if ((!hasTrajPath || !allowExternalOpen) && slot.meta) {
    const trajectoryNote = slot.meta?.meta?.trajectory_note || slot.meta?.trajectory_note || "";
    const hint = trajectoryNote
      || (!allowExternalOpen
        ? "当前提取结果不是“严格反应事件”导出，因此外部查看按钮保持禁用。"
        : "未生成可打开的轨迹子文件，因此 OVITO/VMD/PyMOL 按钮保持禁用。");
    if (metaEl.textContent && !String(metaEl.textContent).includes("viewer_button_hint")) {
      const payload = typeof slot.meta === "object" && slot.meta !== null
        ? { ...slot.meta, viewer_button_hint: hint }
        : { viewer_button_hint: hint };
      metaEl.textContent = JSON.stringify(payload, null, 2);
    }
  }
  renderContextTrajectoryViewer();
}

function renderResultPanels() {
  renderGeneralResultCard();
  renderModuleResultCard("next", "nextMetaBox", "nextResultTable", "btnExportNext");
  renderModuleResultCard("intermediate", "intermediateMetaBox", "intermediateResultTable", "btnExportIntermediate");
  renderModuleResultCard("rxn", "rxnMetaBox", "rxnResultTable", "btnExportRxn");
  renderContextLocateResultCard("context_species", "contextSpeciesResultCard", "contextSpeciesMetaBox", "contextSpeciesResultTable", "btnExportContextSpeciesCsv");
  renderContextLocateResultCard("context_reaction", "contextReactionResultCard", "contextReactionMetaBox", "contextReactionResultTable", "btnExportContextReactionCsv");
  renderContextExtractResultCard();
  renderContextSelectedEventBox();
}

function renderActiveResultPanel() {
  renderResultPanels();
}

function setActiveResultModule(key) {
  state.results.active = RESULT_MODULES.some((item) => item.key === key) ? key : DEFAULT_RESULT_MODULE;
  ensureResultSlot(state.results.active);
  if (state.results.active === "species") {
    setGeneralQueryMode("formula");
  } else if (state.results.active === "mass") {
    setGeneralQueryMode("mass");
  }
  const queryModule = queryModuleForResult(state.results.active);
  if (queryModule) openQueryModule(queryModule);
  renderActiveResultPanel();
}

function setResultRows(moduleKey, rows) {
  const slot = ensureResultSlot(moduleKey);
  const safeRows = Array.isArray(rows) ? rows : [];
  slot.rows = safeRows;
  slot.columns = buildColumns(safeRows);
  renderResultPanels();
}

function setResultMeta(moduleKey, meta) {
  const slot = ensureResultSlot(moduleKey);
  slot.meta = meta || {};
  renderResultPanels();
}

function setResultData(moduleKey, { meta = {}, rows = [] } = {}) {
  setResultMeta(moduleKey, meta);
  setResultRows(moduleKey, rows);
  setActiveResultModule(moduleKey);
}

function patchActiveResultMeta(patch) {
  const key = state.results.active || DEFAULT_RESULT_MODULE;
  const slot = ensureResultSlot(key);
  slot.meta = { ...(slot.meta || {}), ...(patch || {}) };
  renderActiveResultPanel();
}

function patchResultMeta(moduleKey, patch) {
  const slot = ensureResultSlot(moduleKey);
  slot.meta = { ...(slot.meta || {}), ...(patch || {}) };
  renderActiveResultPanel();
}

function openQueryModule(moduleKey) {
  const workspaceKey = {
    general: "species",
    intermediate: "species",
    reaction: "reaction",
    context: "events",
  }[moduleKey];
  if (workspaceKey) setWorkspaceModule(workspaceKey, { focus: false });
  const key = moduleKey === "intermediate" || moduleKey === "reaction" || moduleKey === "context" ? moduleKey : "general";
  const details = document.querySelector(`[data-query-module-group="${key}"]`);
  if (details instanceof HTMLDetailsElement && !details.open) {
    details.open = true;
  }
}

function workspacePanel(key) {
  return q(`workspace-${key}`);
}

function workspaceLabel(key) {
  return WORKSPACE_MODULES.find((item) => item.key === key)?.label || key;
}

function setWorkspaceModule(key, { focus = true } = {}) {
  const next = WORKSPACE_MODULES.some((item) => item.key === key) ? key : "dataset";
  state.ui.workspace = next;
  document.querySelectorAll("[data-workspace-module]").forEach((button) => {
    const active = button.dataset.workspaceModule === next;
    button.classList.toggle("is-active", active);
    button.setAttribute("aria-current", active ? "page" : "false");
  });
  document.querySelectorAll(".workspace-panel").forEach((panel) => {
    panel.classList.toggle("hidden", panel.id !== `workspace-${next}`);
  });
  const title = q("workspaceTitle");
  const hint = q("workspaceHint");
  const item = WORKSPACE_MODULES.find((candidate) => candidate.key === next);
  if (title) title.textContent = item?.label || "工作区";
  if (hint) hint.textContent = item?.hint || "";
  const url = new URL(window.location.href);
  url.searchParams.set("module", next);
  window.history.replaceState({}, "", url);
  if (focus) {
    const target = workspacePanel(next);
    target?.scrollIntoView({ behavior: "smooth", block: "start" });
  }
}

function datasetRequestParams() {
  return {
    reac: globalReac(),
    species_file: globalSpeciesFile(),
    trajectory_file: globalTrajectoryFile(),
    route_file: globalRouteFile(),
    table_file: globalTableFile(),
  };
}

function renderDatasetStatus(dataset) {
  const status = q("datasetStatus");
  const name = q("datasetName");
  if (!status || !name) return;
  const safe = dataset || {};
  name.textContent = safe.label || "未选择数据集";
  const labels = { reaction: "Reaction", species: "Species", trajectory: "Trajectory", route: "Route", table: "Table" };
  status.innerHTML = Object.entries(safe.artifacts || {})
    .map(([key, item]) => `<span class="dataset-chip ${item.exists ? "is-ready" : "is-missing"}" title="${escapeHtml(item.path || "未提供")}"><strong>${labels[key] || key}</strong>${item.exists ? "可用" : "缺失"}</span>`)
    .join("");
  document.querySelectorAll("[data-requires-artifact]").forEach((element) => {
    const required = String(element.dataset.requiresArtifact || "").split(",").filter(Boolean);
    const missing = required.filter((key) => !safe.artifacts?.[key]?.exists);
    element.classList.toggle("requires-dataset", missing.length > 0);
    element.dataset.datasetHint = missing.length ? `需要 ${missing.join(", ")} 数据` : "";
  });
}

async function refreshDatasetStatus({ silent = false } = {}) {
  const status = q("datasetStatus");
  if (!silent && status) status.innerHTML = '<span class="dataset-chip">检查文件...</span>';
  const data = await fetchJson("/api/dataset_status", datasetRequestParams());
  state.ui.dataset = data.dataset || null;
  renderDatasetStatus(state.ui.dataset);
  return data.dataset;
}

function openReactionTool(toolKey) {
  const key = toolKey === "rxn" ? "rxn" : "next";
  const details = document.querySelector(`[data-reaction-tool="${key}"]`);
  if (details instanceof HTMLDetailsElement && !details.open) {
    details.open = true;
  }
}

function queryModuleForResult(moduleKey) {
  if (moduleKey === "intermediate") return "intermediate";
  if (moduleKey === "next" || moduleKey === "rxn") return "reaction";
  if (moduleKey === "context_species" || moduleKey === "context_reaction" || moduleKey === "context_extract") return "context";
  if (moduleKey === "species" || moduleKey === "mass") return "general";
  return "";
}

function setGeneralQueryMode(modeKey) {
  const next = modeKey === "mass" ? "mass" : "formula";
  state.ui.generalQueryMode = next;
  renderGeneralResultCard();
}

function resultPanelTargetId(moduleKey) {
  if (moduleKey === "species" || moduleKey === "mass") return "generalResultCard";
  if (moduleKey === "next") return "nextResultCard";
  if (moduleKey === "intermediate") return "intermediateResultCard";
  if (moduleKey === "rxn") return "rxnResultCard";
  if (moduleKey === "context_species") return "contextSpeciesResultCard";
  if (moduleKey === "context_reaction") return "contextReactionResultCard";
  if (moduleKey === "context_extract") return "contextExtractResultCard";
  if (moduleKey === "plot") return "plotMeta";
  return "generalResultCard";
}

function bringResultPanelIntoView(moduleKey) {
  if (window.innerWidth > WORKBENCH_STACK_BREAKPOINT) return;
  const targetId = resultPanelTargetId(moduleKey);
  const el = q(targetId);
  if (!el) return;
  el.scrollIntoView({ behavior: "smooth", block: "start" });
}

function flashResultPanel(moduleKey) {
  const targetId = resultPanelTargetId(moduleKey);
  const el = q(targetId);
  if (!el) return;
  el.classList.remove("result-focus-flash");
  window.setTimeout(() => {
    el.classList.add("result-focus-flash");
    window.setTimeout(() => el.classList.remove("result-focus-flash"), 900);
  }, 0);
}

function focusResultModule(moduleKey) {
  setActiveResultModule(moduleKey);
  if (moduleKey === "next" || moduleKey === "rxn") {
    openReactionTool(moduleKey);
  }
  bringResultPanelIntoView(moduleKey);
  flashResultPanel(moduleKey);
}

function setMeta(obj) {
  setResultMeta(state.results.active || DEFAULT_RESULT_MODULE, obj);
}

function initializeResultWorkbench(initialMeta = {}) {
  RESULT_MODULES.forEach((item) => {
    const slot = ensureResultSlot(item.key);
    slot.rows = [];
    slot.columns = [];
    slot.meta = {
      status: "idle",
      module: item.label,
      hint: "等待查询",
    };
  });
  const defaultSlot = ensureResultSlot(DEFAULT_RESULT_MODULE);
  defaultSlot.meta = { ...defaultSlot.meta, ...(initialMeta || {}) };
  setActiveResultModule(DEFAULT_RESULT_MODULE);
}

function setPlotMeta(obj) {
  q("plotMeta").textContent = JSON.stringify(obj, null, 2);
}

function setPlotProgress(status, progress = 0, message = "", active = false) {
  const box = q("plotProgress");
  const label = q("plotProgressLabel");
  const pct = q("plotProgressPct");
  const fill = q("plotProgressFill");
  const msg = q("plotProgressMsg");
  q("btnPlot").disabled = !!active;
  box.classList.toggle("is-idle", !active);
  label.textContent = status || "Idle";
  const clamped = Math.max(0, Math.min(100, Number(progress) || 0));
  pct.textContent = `${clamped.toFixed(1)}%`;
  fill.style.width = `${clamped}%`;
  msg.textContent = message || "等待开始";
}

function setIntermediateProgress(status, progress = 0, message = "", active = false) {
  const box = q("intermediateProgress");
  const label = q("intermediateProgressLabel");
  const pct = q("intermediateProgressPct");
  const fill = q("intermediateProgressFill");
  const msg = q("intermediateProgressMsg");
  q("btnIntermediate").disabled = !!active;
  box.classList.toggle("is-idle", !active);
  label.textContent = status || "Idle";
  const clamped = Math.max(0, Math.min(100, Number(progress) || 0));
  pct.textContent = `${clamped.toFixed(1)}%`;
  fill.style.width = `${clamped}%`;
  msg.textContent = message || "等待开始";
}

function setProgressCard(prefix, status, progress = 0, message = "", active = false) {
  const box = q(`${prefix}Progress`);
  const label = q(`${prefix}ProgressLabel`);
  const pct = q(`${prefix}ProgressPct`);
  const fill = q(`${prefix}ProgressFill`);
  const msg = q(`${prefix}ProgressMsg`);
  if (!box || !label || !pct || !fill || !msg) return;
  box.classList.toggle("is-idle", !active);
  label.textContent = status || "Idle";
  const clamped = Math.max(0, Math.min(100, Number(progress) || 0));
  pct.textContent = `${clamped.toFixed(1)}%`;
  fill.style.width = `${clamped}%`;
  msg.textContent = message || "等待开始";
}

function setContextLocateProgress(prefix, buttonId, status, progress = 0, message = "", active = false) {
  if (q(buttonId)) q(buttonId).disabled = !!active;
  document.querySelectorAll(".btn-context-load-row").forEach((btn) => {
    if (btn instanceof HTMLButtonElement) btn.disabled = !!active;
  });
  setProgressCard(prefix, status, progress, message, active);
}

function setContextSpeciesProgress(status, progress = 0, message = "", active = false) {
  setContextLocateProgress("contextSpecies", "btnContextLocateSpecies", status, progress, message, active);
}

function setContextReactionProgress(status, progress = 0, message = "", active = false) {
  setContextLocateProgress("contextReaction", "btnContextLocateReaction", status, progress, message, active);
}

function setContextExtractProgress(status, progress = 0, message = "", active = false) {
  state.contextExtract.isRunning = !!active;
  setProgressCard("contextExtract", status, progress, message, active);
  syncContextExtractActionState();
}

function setCarbonPlotMeta(obj) {
  q("carbonPlotMeta").textContent = JSON.stringify(obj, null, 2);
}

function setCarbonPlotSummary(obj) {
  q("carbonPlotSummary").textContent = JSON.stringify(obj, null, 2);
}

function syncCarbonLayoutFields() {
  const isSubplots = q("qCarbonLayout").value === "subplots";
  q("carbonRegionsField").classList.toggle("hidden", !isSubplots);
}

function syncUnifiedPlotMode() {
  const mode = q("qUnifiedPlotMode").value;
  const showSpecies = mode === "species";
  q("plotSectionSpecies").classList.toggle("hidden", !showSpecies);
  q("plotSectionCarbon").classList.toggle("hidden", showSpecies);
  if (showSpecies) {
    q("qPlotTarget").focus();
  } else {
    q("qCarbonSpeciesFile").focus();
  }
}

function buildWorkspaceShell() {
  const shell = document.querySelector("main.shell");
  const globalConfig = document.querySelector(".global-config");
  const source = q("globalDatasourceCard");
  const query = document.querySelector(".query-result-workbench");
  const plotSwitch = document.querySelector(".plot-unified-switch");
  const plotSpecies = q("plotSectionSpecies");
  const plotCarbon = q("plotSectionCarbon");
  const transition = q("transitionMatrixSection");
  const guide = document.querySelector(".usage-guide");
  if (!shell || !globalConfig || !source || !query || !plotSwitch || !plotSpecies || !plotCarbon || !transition || !guide) return;
  if (q("workspaceShell")) return;

  const workspace = document.createElement("section");
  workspace.id = "workspaceShell";
  workspace.className = "workspace-shell";
  workspace.innerHTML = `
    <aside class="workspace-nav" aria-label="分析模块">
      <div class="workspace-nav-head">
        <span class="workspace-kicker">ReacNetGenerator</span>
        <strong>分析工作台</strong>
      </div>
      <nav id="workspaceNav" class="workspace-nav-list"></nav>
      <div class="workspace-dataset-summary">
        <span class="workspace-kicker">当前数据集</span>
        <strong id="datasetName">未选择数据集</strong>
        <div id="datasetStatus" class="dataset-status"></div>
      </div>
    </aside>
    <section class="workspace-main">
      <header class="workspace-header">
        <div>
          <span class="workspace-kicker">分析模块</span>
          <h2 id="workspaceTitle">数据集</h2>
          <p id="workspaceHint">文件与可用性</p>
        </div>
        <button id="btnRefreshDataset" class="ghost workspace-refresh" type="button">检查数据集</button>
      </header>
      <div id="workspace-dataset" class="workspace-panel"></div>
      <div id="workspace-species" class="workspace-panel hidden"></div>
      <div id="workspace-reaction" class="workspace-panel hidden"></div>
      <div id="workspace-events" class="workspace-panel hidden"></div>
      <div id="workspace-evolution" class="workspace-panel hidden"></div>
      <div id="workspace-transition" class="workspace-panel hidden"></div>
    </section>
  `;
  shell.insertBefore(workspace, guide);
  const panels = {
    dataset: q("workspace-dataset"),
    species: q("workspace-species"),
    reaction: q("workspace-reaction"),
    events: q("workspace-events"),
    evolution: q("workspace-evolution"),
    transition: q("workspace-transition"),
  };
  panels.dataset.append(globalConfig, source);
  panels.dataset.append(guide);
  panels.species.append(query);
  panels.evolution.append(plotSwitch, plotSpecies, plotCarbon);
  panels.transition.append(transition);

  const queryGroups = Array.from(query.querySelectorAll(".query-module-group"));
  const generalGroup = queryGroups.find((element) => element.dataset.queryModuleGroup === "general");
  const intermediateGroup = queryGroups.find((element) => element.dataset.queryModuleGroup === "intermediate");
  const reactionGroup = queryGroups.find((element) => element.dataset.queryModuleGroup === "reaction");
  const contextGroup = queryGroups.find((element) => element.dataset.queryModuleGroup === "context");
  if (generalGroup) panels.species.append(generalGroup);
  if (intermediateGroup) panels.species.append(intermediateGroup);
  if (reactionGroup) panels.reaction.append(reactionGroup);
  if (contextGroup) panels.events.append(contextGroup);
  query.remove();

  const nav = q("workspaceNav");
  nav.innerHTML = WORKSPACE_MODULES.map((item) => `
    <button type="button" class="workspace-nav-item" data-workspace-module="${item.key}">
      <strong>${escapeHtml(item.label)}</strong>
      <span>${escapeHtml(item.hint)}</span>
    </button>
  `).join("");
}

function syncPlotSpeciesSourceMode() {
  const hasMulti = value("qPlotSpeciesFiles").length > 0;
  const singleInput = q("qPlotSpeciesFile");
  singleInput.disabled = hasMulti;
  singleInput.placeholder = hasMulti
    ? "已启用多文件模式: 此项已忽略"
    : "单文件模式: 留空使用顶部共享 Species；若共享也为空，再由 reactionabcd 推导";
}

function syncCarbonSpeciesSourceMode() {
  const hasMulti = value("qCarbonSpeciesFiles").length > 0;
  const singleInput = q("qCarbonSpeciesFile");
  singleInput.disabled = hasMulti;
  singleInput.placeholder = hasMulti
    ? "已启用多文件模式: 此项已忽略"
    : "单文件模式: 留空使用顶部共享 Species；若共享也为空，再由 reactionabcd 推导";
}

function setCarbonPlotProgress(status, progress = 0, message = "", active = false) {
  const box = q("carbonPlotProgress");
  const label = q("carbonPlotProgressLabel");
  const pct = q("carbonPlotProgressPct");
  const fill = q("carbonPlotProgressFill");
  const msg = q("carbonPlotProgressMsg");
  q("btnCarbonPlot").disabled = !!active;
  box.classList.toggle("is-idle", !active);
  label.textContent = status || "Idle";
  const clamped = Math.max(0, Math.min(100, Number(progress) || 0));
  pct.textContent = `${clamped.toFixed(1)}%`;
  fill.style.width = `${clamped}%`;
  msg.textContent = message || "等待开始";
}

function renderCarbonPlotHighlights(summary) {
  const host = q("carbonPlotHighlights");
  host.innerHTML = "";
  if (!summary) return;

  const base = summary.group_by ? summary.overall || {} : summary;
  const chips = [
    ["Parent", base.parent_carbon_number ? `C${base.parent_carbon_number}` : "n/a"],
    ["Decay onset", base.parent_decay_onset_time ?? "n/a"],
    ["Small peak", base.small_fragment_peak_time ?? "n/a"],
    ["Large peak", base.large_hydrocarbon_peak_time ?? "n/a"],
    ["Max carbon", base.max_carbon_number_observed ?? "n/a"],
  ];
  if (summary.group_by && summary.by_system) {
    chips.unshift(["Systems", Object.keys(summary.by_system).length]);
  }

  chips.forEach(([label, value]) => {
    const item = document.createElement("div");
    item.className = "stat-chip";
    item.innerHTML = `<strong>${escapeHtml(label)}:</strong> ${escapeHtml(value)}`;
    host.appendChild(item);
  });
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
  const [left, right] = rxn.split("->", 2).map((s) => s.trim());
  const splitSide = (side) => {
    if (!side) return [];
    const tokens = [];
    let depth = 0;
    let start = 0;
    for (let i = 0; i < side.length; i += 1) {
      const ch = side[i];
      if (ch === "[") depth += 1;
      else if (ch === "]" && depth > 0) depth -= 1;
      else if (ch === "+" && depth === 0) {
        const part = side.slice(start, i).trim();
        if (part) tokens.push(part);
        start = i + 1;
      }
    }
    const tail = side.slice(start).trim();
    if (tail) tokens.push(tail);
    return tokens;
  };
  const lhs = splitSide(left);
  const rhs = splitSide(right);
  lhs.forEach((s) => out.push({ side: "reactant", smiles: s }));
  rhs.forEach((s) => out.push({ side: "product", smiles: s }));
  return out;
}

function looksLikeSmiles(s) {
  const t = String(s || "");
  return t.includes("[") || t.includes("=") || t.includes("#") || /\d/.test(t);
}

let smilesHoverTooltip = null;
let smilesHoverTimer = 0;
let smilesHoverTarget = null;

function smilesSvgUrl(smiles, width = 260, height = 180, showH = true) {
  return `/api/smiles_svg?smiles=${encodeURIComponent(smiles)}&w=${width}&h=${height}&show_h=${showH ? 1 : 0}`;
}

function previewTriggerFromTarget(target) {
  if (!(target instanceof Element)) return null;
  return target.closest("[data-smiles-preview], .formula-structure-btn");
}

function previewSmilesFromElement(el) {
  if (!(el instanceof HTMLElement)) return "";
  const raw = el.dataset.smilesPreview || el.dataset.rxn || el.dataset.smiles || "";
  return safeDecodeDataValue(raw).trim();
}

function ensureSmilesHoverTooltip() {
  if (smilesHoverTooltip) return smilesHoverTooltip;
  const el = document.createElement("div");
  el.className = "smiles-hover-tooltip";
  el.setAttribute("role", "tooltip");
  document.body.appendChild(el);
  smilesHoverTooltip = el;
  return el;
}

function buildSmilesHoverTooltip(smiles) {
  const tooltip = ensureSmilesHoverTooltip();
  const text = String(smiles || "").trim();
  const terms = text.includes("->") ? parseReactionSmiles(text) : [];
  const items = terms.length ? terms.slice(0, 6) : [{ side: "SMILES", smiles: text }];
  const extraCount = terms.length > items.length ? terms.length - items.length : 0;

  tooltip.innerHTML = `
    <div class="smiles-hover-title">${terms.length ? "Reaction SMILES preview" : "SMILES preview"}</div>
    <div class="smiles-hover-grid">
      ${items
        .map(
          (item, idx) => `
            <div class="smiles-hover-card">
              <div class="smiles-hover-label">${escapeHtml(terms.length ? `${item.side} #${idx + 1}` : "structure")}</div>
              <img src="${smilesSvgUrl(item.smiles)}" alt="SMILES structure preview" loading="eager" />
              <code>${escapeHtml(item.smiles)}</code>
            </div>
          `
        )
        .join("")}
    </div>
    ${extraCount ? `<div class="smiles-hover-more">另有 ${extraCount} 个片段未显示</div>` : ""}
  `;
}

function positionSmilesHoverTooltip(event) {
  if (!smilesHoverTooltip || !smilesHoverTooltip.classList.contains("is-visible")) return;
  const pad = 14;
  const gap = 18;
  const rect = smilesHoverTooltip.getBoundingClientRect();
  let left = event.clientX + gap;
  let top = event.clientY + gap;
  if (left + rect.width + pad > window.innerWidth) {
    left = Math.max(pad, event.clientX - rect.width - gap);
  }
  if (top + rect.height + pad > window.innerHeight) {
    top = Math.max(pad, event.clientY - rect.height - gap);
  }
  smilesHoverTooltip.style.left = `${left}px`;
  smilesHoverTooltip.style.top = `${top}px`;
}

function showSmilesHoverTooltip(el, event) {
  const smiles = previewSmilesFromElement(el);
  const hasExplicitPreviewPayload = el instanceof HTMLElement && Object.prototype.hasOwnProperty.call(el.dataset, "smilesPreview");
  if (!smiles) return;
  if (!hasExplicitPreviewPayload && !looksLikeExplicitSmiles(smiles)) return;
  window.clearTimeout(smilesHoverTimer);
  smilesHoverTarget = el;
  smilesHoverTimer = window.setTimeout(() => {
    if (smilesHoverTarget !== el) return;
    buildSmilesHoverTooltip(smiles);
    ensureSmilesHoverTooltip().classList.add("is-visible");
    positionSmilesHoverTooltip(event);
  }, 180);
}

function hideSmilesHoverTooltip() {
  window.clearTimeout(smilesHoverTimer);
  smilesHoverTimer = 0;
  smilesHoverTarget = null;
  if (smilesHoverTooltip) {
    smilesHoverTooltip.classList.remove("is-visible");
  }
}

function initSmilesHoverPreview() {
  document.addEventListener("mouseover", (event) => {
    const trigger = previewTriggerFromTarget(event.target);
    if (!(trigger instanceof HTMLElement)) return;
    if (smilesHoverTarget === trigger) return;
    showSmilesHoverTooltip(trigger, event);
  });

  document.addEventListener("mousemove", (event) => {
    positionSmilesHoverTooltip(event);
  });

  document.addEventListener("mouseout", (event) => {
    const trigger = previewTriggerFromTarget(event.target);
    if (!(trigger instanceof HTMLElement)) return;
    if (event.relatedTarget instanceof Node && trigger.contains(event.relatedTarget)) return;
    hideSmilesHoverTooltip();
  });

  document.addEventListener("focusin", (event) => {
    const trigger = previewTriggerFromTarget(event.target);
    if (!(trigger instanceof HTMLElement)) return;
    showSmilesHoverTooltip(trigger, { clientX: window.innerWidth / 2, clientY: window.innerHeight / 3 });
  });

  document.addEventListener("focusout", (event) => {
    const trigger = previewTriggerFromTarget(event.target);
    if (trigger instanceof HTMLElement) hideSmilesHoverTooltip();
  });
}

function resultRowStructureLabel(row, index) {
  const rank = row?.rank == null ? `#${index + 1}` : `#${row.rank}`;
  const formula = row?.formula || row?.reaction_formulas || row?.species || row?.label || "";
  const formulaText = String(formula || "").trim();
  if (!formulaText) return rank;
  return `${rank} ${formulaText.length > 80 ? `${formulaText.slice(0, 77)}...` : formulaText}`;
}

function structureItemsFromRows(rows, limit = STRUCTURE_LIST_LIMIT) {
  const items = [];
  const seen = new Set();
  (rows || []).some((row, index) => {
    let kind = "";
    let smiles = "";
    if (row?.reaction_smiles) {
      kind = "reaction";
      smiles = String(row.reaction_smiles || "").trim();
    } else if (row?.smiles) {
      kind = "smiles";
      smiles = String(row.smiles || "").trim();
    } else if (row?.species && looksLikeExplicitSmiles(row.species)) {
      kind = "smiles";
      smiles = String(row.species || "").trim();
    } else if (row?.label && looksLikeExplicitSmiles(row.label)) {
      kind = "smiles";
      smiles = String(row.label || "").trim();
    }

    if (!smiles) return false;
    const key = `${kind}:${smiles}`;
    if (seen.has(key)) return false;
    seen.add(key);
    items.push({
      kind,
      smiles,
      title: resultRowStructureLabel(row, index),
      formula: row?.formula || row?.reaction_formulas || "",
    });
    return items.length >= limit;
  });
  return items;
}

function setStructureNote(viewerKey, text) {
  const ctx = viewerContext(viewerKey);
  const note = q(ctx.noteId);
  if (note) note.textContent = text;
}

function reactionSideHtml(label, terms, showH) {
  const items = Array.isArray(terms) ? terms : [];
  if (!items.length) {
    return `
      <div class="structure-reaction-side">
        <div class="structure-side-title">${escapeHtml(label)}</div>
        <div class="structure-empty-side">无</div>
      </div>
    `;
  }
  return `
    <div class="structure-reaction-side">
      <div class="structure-side-title">${escapeHtml(label)}</div>
      <div class="structure-side-fragments">
        ${items
          .map(
            (term, termIdx) => `
              <div class="structure-fragment-card">
                <div class="smiles-hover-label">#${termIdx + 1}</div>
                <img src="${smilesSvgUrl(term.smiles, 150, 104, showH)}" alt="reaction fragment structure" loading="lazy" />
                <code>${escapeHtml(term.smiles)}</code>
              </div>
            `
          )
          .join("")}
      </div>
    </div>
  `;
}

function structureCardHtml(item, idx, showH) {
  const title = item.title || `#${idx + 1}`;
  const terms = item.kind === "reaction" ? parseReactionSmiles(item.smiles) : [];
  if (terms.length) {
    const reactants = terms.filter((term) => term.side === "reactant");
    const products = terms.filter((term) => term.side === "product");
    const encoded = encodeURIComponent(item.smiles);
    return `
      <div class="svg-card structure-result-card structure-reaction-row">
        <div class="structure-reaction-row-head">
          <strong>${escapeHtml(title)}</strong>
          <span>${reactants.length} reactant${reactants.length === 1 ? "" : "s"} -> ${products.length} product${products.length === 1 ? "" : "s"}</span>
        </div>
        <div class="structure-reaction-flow">
          ${reactionSideHtml("Reactants", reactants, showH)}
          <div class="structure-reaction-arrow">-></div>
          ${reactionSideHtml("Products", products, showH)}
        </div>
        <details class="structure-smiles-details">
          <summary>reaction_smiles</summary>
          <code class="smiles-preview-token" data-smiles-preview="${encoded}">${escapeHtml(item.smiles)}</code>
        </details>
      </div>
    `;
  }
  return `
    <div class="svg-card structure-result-card">
      <strong>${escapeHtml(title)}</strong>
      <div class="structure-card-meta">smiles</div>
      <div><img src="${smilesSvgUrl(item.smiles, 360, 220, showH)}" alt="SMILES structure" loading="lazy" /></div>
      <div class="smiles"><code>${escapeHtml(item.smiles)}</code></div>
    </div>
  `;
}

function renderStructurePreviewItems(viewerKey, items, { note = "", scroll = false } = {}) {
  const key = normalizeViewerKey(viewerKey);
  const ctx = viewerContext(key);
  const card = q(ctx.cardId);
  const gallery = q(ctx.galleryId);
  if (!card || !gallery) return;

  const safeItems = Array.isArray(items) ? items.filter((item) => item?.smiles) : [];
  state.structurePreviews[key] = safeItems;

  if (!safeItems.length) {
    card.classList.add("hidden");
    gallery.innerHTML = "";
    setStructureNote(key, note || "当前结果没有可渲染的 SMILES");
    return;
  }

  const showH = viewerShowHEnabled(key);
  card.classList.remove("hidden");
  setStructureNote(key, note || `自动显示 ${safeItems.length} 个结构；鼠标悬停 SMILES 可快速预览`);
  gallery.classList.toggle("structure-reaction-list", safeItems.some((item) => item.kind === "reaction"));
  gallery.innerHTML = safeItems.map((item, idx) => structureCardHtml(item, idx, showH)).join("");
  if (scroll) {
    card.scrollIntoView({ behavior: "smooth", block: "start" });
  }
}

function renderStructureListFromRows(viewerKey, rows) {
  const items = structureItemsFromRows(rows || [], STRUCTURE_LIST_LIMIT);
  const totalPreviewable = structureItemsFromRows(rows || [], Number.MAX_SAFE_INTEGER).length;
  const truncated = totalPreviewable > items.length;
  const note = items.length
    ? `自动显示前 ${items.length}${truncated ? `/${totalPreviewable}` : ""} 个可渲染结构；悬停 SMILES 可快速预览`
    : "当前结果没有可渲染的 SMILES";
  renderStructurePreviewItems(viewerKey, items, { note });
}

function renderTable(rows) {
  setResultRows(state.results.active || DEFAULT_RESULT_MODULE, rows);
}

function csvEscape(v) {
  const s = String(v ?? "");
  if (s.includes(",") || s.includes("\n") || s.includes('"')) {
    return `"${s.replaceAll('"', '""')}"`;
  }
  return s;
}

function exportResultCsvByModule(moduleKey, prefix = "rng_query") {
  const slot = ensureResultSlot(moduleKey);
  const rows = slot.rows || [];
  const cols = slot.columns || [];
  if (!rows.length || !cols.length) return;
  const lines = [cols.join(",")];
  rows.forEach((row) => {
    lines.push(cols.map((c) => csvEscape(row[c])).join(","));
  });
  const blob = new Blob([lines.join("\n")], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  const stamp = new Date().toISOString().replace(/[.:]/g, "-");
  a.href = url;
  a.download = `${prefix}_${slot.key}_${stamp}.csv`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

function downloadTextBlob(text, filename, mime = "text/plain;charset=utf-8;") {
  const blob = new Blob([text], { type: mime });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

function exportContextFramesCsv() {
  const rows = state.contextExtract.frameRows || [];
  if (!rows.length) return;
  const cols = Array.from(
    rows.reduce((acc, row) => {
      Object.keys(row || {}).forEach((key) => acc.add(key));
      return acc;
    }, new Set())
  );
  const lines = [cols.join(",")];
  rows.forEach((row) => {
    lines.push(cols.map((key) => csvEscape(row[key])).join(","));
  });
  const stamp = new Date().toISOString().replace(/[.:]/g, "-");
  const name = state.contextExtract.framesFilename || `rng_context_frames_${stamp}.csv`;
  downloadTextBlob(lines.join("\n"), name, "text/csv;charset=utf-8;");
}

function exportContextTrajectory() {
  const text = state.contextExtract.trajectoryText || "";
  if (text) {
    const stamp = new Date().toISOString().replace(/[.:]/g, "-");
    const name = state.contextExtract.trajectoryFilename || `rng_context_subset_${stamp}.lammpstrj`;
    downloadTextBlob(text, name, "text/plain;charset=utf-8;");
    return;
  }
  const path = state.contextExtract.trajectoryPath || "";
  if (!path) return;
  openContextTrajectoryPath("reveal").catch((err) => {
    patchResultMeta("context_extract", { error: String(err), trajectory_path: path });
  });
}

async function openContextTrajectoryPath(mode = "default") {
  const path = mode === "vmd"
    ? (state.contextExtract.vmdScriptPath || state.contextExtract.trajectoryPath || "")
    : (state.contextExtract.trajectoryPath || "");
  if (!path) return;
  return fetchJson("/api/open_path", { path, mode });
}

async function openContextExportDirectory() {
  const dir = pathDirname(
    state.contextExtract.vmdScriptPath
    || state.contextExtract.trajectoryPath
    || state.contextExtract.typeMapPath
    || ""
  );
  if (!dir) {
    throw new Error("当前没有可打开的导出目录");
  }
  return fetchJson("/api/open_path", { path: dir, mode: "default" });
}

function resetContextTrajectoryViewer() {
  state.contextExtract.parsedFrames = [];
  state.contextExtract.frameIndex = 0;
  state.contextExtract.viewMode = "3d";
  state.contextExtract.zoom = 1;
  state.contextExtract.rotX = -0.45;
  state.contextExtract.rotY = 0.65;
  state.contextExtract.showBox = true;
  state.contextExtract.highlightMode = "route_target";
  state.contextExtract.focusEventAtoms = true;
  state.contextExtract.showTrails = true;
  state.contextExtract.trailWindow = 8;
  state.contextExtract.hoverAtom = null;
  state.contextExtract.storyboardItems = [];
  state.contextExtract.snapshotItems = [];
}

function hasManualContextAtomIds() {
  return !!String(value("qContextAtomIds") || "").trim();
}

function syncContextTrajectoryAtomScopeControl() {
  const routeTrace = q("qContextIncludeRouteTrace");
  const scope = q("qContextTrajectoryAtomScope");
  if (!(routeTrace instanceof HTMLInputElement) || !(scope instanceof HTMLSelectElement)) return;
  const routeEnabled = !!routeTrace.checked;
  const manualAtomIds = hasManualContextAtomIds();
  scope.disabled = !routeEnabled && !manualAtomIds;
  if (!routeEnabled && !manualAtomIds && scope.value === "event") {
    scope.value = "all";
  }
}

function parseLammpstrjSubset(text) {
  const raw = String(text || "").trim();
  if (!raw) return [];
  const lines = raw.split(/\r?\n/);
  const frames = [];
  let idx = 0;
  while (idx < lines.length) {
    if (String(lines[idx] || "").trim() !== "ITEM: TIMESTEP") {
      idx += 1;
      continue;
    }
    const frame = Number.parseInt(String(lines[idx + 1] || "").trim(), 10);
    idx += 2;
    let nAtoms = 0;
    let box = [];
    let atoms = [];
    let columns = [];
    while (idx < lines.length && String(lines[idx] || "").trim() !== "ITEM: TIMESTEP") {
      const line = String(lines[idx] || "").trim();
      if (!line) {
        idx += 1;
        continue;
      }
      if (line.startsWith("ITEM: NUMBER OF ATOMS")) {
        nAtoms = Number.parseInt(String(lines[idx + 1] || "0").trim(), 10) || 0;
        idx += 2;
        continue;
      }
      if (line.startsWith("ITEM: BOX BOUNDS")) {
        box = [];
        for (let axis = 0; axis < 3 && idx + 1 + axis < lines.length; axis += 1) {
          const parts = String(lines[idx + 1 + axis] || "")
            .trim()
            .split(/\s+/)
            .map((value) => Number.parseFloat(value));
          box.push({
            lo: Number.isFinite(parts[0]) ? parts[0] : 0,
            hi: Number.isFinite(parts[1]) ? parts[1] : 0,
          });
        }
        idx += 4;
        continue;
      }
      if (line.startsWith("ITEM: ATOMS")) {
        columns = line.replace(/^ITEM: ATOMS\s+/, "").trim().split(/\s+/);
        const colIndex = Object.fromEntries(columns.map((name, colIdx) => [name, colIdx]));
        const xKey = ["x", "xu", "xs"].find((name) => Object.prototype.hasOwnProperty.call(colIndex, name)) || "";
        const yKey = ["y", "yu", "ys"].find((name) => Object.prototype.hasOwnProperty.call(colIndex, name)) || "";
        const zKey = ["z", "zu", "zs"].find((name) => Object.prototype.hasOwnProperty.call(colIndex, name)) || "";
        const useScaled = xKey.endsWith("s") && yKey.endsWith("s") && zKey.endsWith("s") && box.length === 3;
        idx += 1;
        atoms = [];
        for (let atomIdx = 0; atomIdx < nAtoms && idx < lines.length; atomIdx += 1, idx += 1) {
          const parts = String(lines[idx] || "").trim().split(/\s+/);
          if (!parts.length) continue;
          const readNum = (key, fallback = NaN) => {
            const pos = colIndex[key];
            if (pos == null || pos >= parts.length) return fallback;
            const parsed = Number.parseFloat(parts[pos]);
            return Number.isFinite(parsed) ? parsed : fallback;
          };
          let x = xKey ? readNum(xKey) : NaN;
          let y = yKey ? readNum(yKey) : NaN;
          let z = zKey ? readNum(zKey) : NaN;
          if (useScaled) {
            x = box[0].lo + x * (box[0].hi - box[0].lo);
            y = box[1].lo + y * (box[1].hi - box[1].lo);
            z = box[2].lo + z * (box[2].hi - box[2].lo);
          }
          if (![x, y, z].every((value) => Number.isFinite(value))) continue;
          const atom = {
            id: colIndex.id == null ? atomIdx + 1 : Number.parseInt(parts[colIndex.id], 10) || atomIdx + 1,
            type: colIndex.type == null ? "" : String(parts[colIndex.type] || ""),
            mol: colIndex.mol == null ? "" : String(parts[colIndex.mol] || ""),
            element: colIndex.element == null ? "" : String(parts[colIndex.element] || ""),
            x,
            y,
            z,
          };
          atoms.push(atom);
        }
        continue;
      }
      idx += 1;
    }
    frames.push({
      frame: Number.isFinite(frame) ? frame : frames.length,
      nAtoms: nAtoms || atoms.length,
      box,
      atoms,
      columns,
    });
  }
  return frames;
}

function contextFrameRowLookup(frameValue) {
  const frame = Number(frameValue);
  return (state.contextExtract.frameRows || []).find((row) => Number(row.frame) === frame) || null;
}

function parseAtomIdSet(raw) {
  const text = String(raw || "").trim();
  if (!text) return new Set();
  const ids = text
    .split(/[^0-9]+/)
    .map((item) => Number.parseInt(item, 10))
    .filter((value) => Number.isFinite(value) && value > 0);
  return new Set(ids);
}

function contextEventRowsByAnchor(frameValue) {
  const frame = Number(frameValue);
  return (ensureResultSlot("context_extract").rows || []).filter((row) => Number(row?.anchor_frame) === frame);
}

function parseEventAnchorsFromRefs(refText) {
  const anchors = new Set();
  String(refText || "")
    .split(/\s*;\s*/)
    .map((item) => item.trim())
    .filter(Boolean)
    .forEach((token) => {
      const m = token.match(/@(\d+)$/);
      if (!m) return;
      const value = Number.parseInt(m[1], 10);
      if (Number.isFinite(value)) anchors.add(value);
    });
  return anchors;
}

function contextEventRowsForFrame(frameObj) {
  if (!frameObj) return [];
  const frame = Number(frameObj.frame);
  const row = contextFrameRowLookup(frame);
  const anchors = parseEventAnchorsFromRefs(row?.event_refs || "");
  const direct = contextEventRowsByAnchor(frame);
  direct.forEach((item) => anchors.add(Number(item?.anchor_frame)));
  if (!anchors.size) return [];
  const allRows = ensureResultSlot("context_extract").rows || [];
  return allRows.filter((item) => anchors.has(Number(item?.anchor_frame)));
}

function mergeAtomSetsFromRows(rows, key) {
  const merged = new Set();
  (rows || []).forEach((row) => {
    const set = parseAtomIdSet(row?.[key]);
    set.forEach((id) => merged.add(id));
  });
  return merged;
}

function contextHighlightAtomIdsForFrame(frameObj) {
  if (!frameObj) return new Set();
  const mode = String(state.contextExtract.highlightMode || "route_target");
  if (mode === "none") return new Set();
  const row = contextFrameRowLookup(frameObj.frame);
  const eventRows = contextEventRowsForFrame(frameObj);
  if (mode === "route_context") return parseAtomIdSet(row?.route_context_atom_ids);
  if (mode === "route_target") return parseAtomIdSet(row?.route_target_atom_ids);
  if (mode === "route_reactant") return parseAtomIdSet(row?.route_reactant_atom_ids);
  if (mode === "route_product") return parseAtomIdSet(row?.route_product_atom_ids);
  if (mode === "event_changed_target") return mergeAtomSetsFromRows(eventRows, "route_changed_target_atom_ids");
  if (mode === "event_reactant_to_product") return mergeAtomSetsFromRows(eventRows, "route_reactant_to_product_atom_ids");
  if (mode === "event_product_to_reactant") return mergeAtomSetsFromRows(eventRows, "route_product_to_reactant_atom_ids");
  return new Set();
}

function contextHighlightModeLabel(mode) {
  const key = String(mode || "none");
  if (key === "route_target") return "reaction core";
  if (key === "route_context") return "context shell";
  if (key === "route_reactant") return "reactant-side atoms";
  if (key === "route_product") return "product-side atoms";
  if (key === "event_changed_target") return "event changed(core)";
  if (key === "event_reactant_to_product") return "reactant→product transitions";
  if (key === "event_product_to_reactant") return "product→reactant transitions";
  return "none";
}

function contextTrailSegments(frameIndex, atomIds, frameWindow) {
  const frames = state.contextExtract.parsedFrames || [];
  if (!frames.length || !atomIds || !atomIds.size) return [];
  const windowSize = Math.max(1, Math.min(200, Number.parseInt(String(frameWindow), 10) || 8));
  const lo = Math.max(0, frameIndex - windowSize);
  const hi = Math.min(frames.length - 1, frameIndex + windowSize);
  const segments = [];
  atomIds.forEach((atomId) => {
    const points = [];
    for (let idx = lo; idx <= hi; idx += 1) {
      const frame = frames[idx];
      const atom = (frame?.atoms || []).find((item) => Number(item?.id) === Number(atomId));
      if (!atom) continue;
      points.push({ frameIndex: idx, x: atom.x, y: atom.y, z: atom.z, atom });
    }
    if (points.length >= 2) {
      segments.push({ atomId: Number(atomId), points });
    }
  });
  return segments;
}

function contextFrameColor(atom) {
  const raw = String(atom?.element || atom?.type || "").trim();
  const upper = raw.toUpperCase();
  if (upper === "H") return "#8f8f8f";
  if (upper === "C") return "#263238";
  if (upper === "O") return "#d84315";
  if (upper === "N") return "#1565c0";
  if (upper === "CL") return "#2e7d32";
  if (upper === "S") return "#f9a825";
  const typeNum = Number.parseInt(raw.replace(/^\D+/, ""), 10);
  const palette = ["#5c6bc0", "#26a69a", "#ef6c00", "#ab47bc", "#42a5f5", "#8d6e63", "#7cb342", "#ef5350"];
  return palette[Number.isFinite(typeNum) ? Math.abs(typeNum - 1) % palette.length : 0];
}

function contextFrameRadius(atom) {
  const raw = String(atom?.element || atom?.type || "").trim().toUpperCase();
  if (raw === "H") return 3;
  if (raw === "CL") return 6;
  if (raw === "C" || raw === "N" || raw === "O" || raw === "S") return 4.6;
  return 4;
}

function contextFrameAtomLabel(atom) {
  const symbol = String(atom?.element || "").trim() || (atom?.type ? `T${atom.type}` : "atom");
  const mol = atom?.mol ? ` mol=${atom.mol}` : "";
  return `${symbol} id=${atom?.id ?? "?"}${mol}`;
}

function setContextFrameInfo(frameObj, hovered = null) {
  const info = q("contextFrameAtomInfo");
  if (!info) return;
  if (!frameObj) {
    info.textContent = "没有可显示的帧。";
    return;
  }
  if (!hovered) {
    const highlightSet = contextHighlightAtomIdsForFrame(frameObj);
    const highlightText = contextHighlightModeLabel(state.contextExtract.highlightMode);
    const focusText = state.contextExtract.focusEventAtoms ? "on" : "off";
    const trailText = state.contextExtract.showTrails ? `on/${state.contextExtract.trailWindow}` : "off";
    info.textContent = `拖动旋转 3D 视图；滚轮缩放。\nframe=${frameObj.frame} atoms=${frameObj.atoms.length} highlight=${highlightSet.size}(${highlightText}) focus=${focusText} trails=${trailText}`;
    return;
  }
  const isHighlighted = !!hovered.highlighted;
  info.textContent = `${contextFrameAtomLabel(hovered)}${isHighlighted ? " [highlight]" : ""}\nx=${hovered.x.toFixed(4)} y=${hovered.y.toFixed(4)} z=${hovered.z.toFixed(4)}`;
}

function projectContextPoint(point, mode, rotX, rotY) {
  let { x, y, z } = point;
  if (mode === "xy") return { px: x, py: -y, depth: z };
  if (mode === "xz") return { px: x, py: -z, depth: y };
  if (mode === "yz") return { px: y, py: -z, depth: x };
  const cy = Math.cos(rotY);
  const sy = Math.sin(rotY);
  const x1 = x * cy + z * sy;
  const z1 = -x * sy + z * cy;
  const cx = Math.cos(rotX);
  const sx = Math.sin(rotX);
  const y2 = y * cx - z1 * sx;
  const z2 = y * sx + z1 * cx;
  return { px: x1, py: -y2, depth: z2 };
}

function contextFrameCorners(box) {
  if (!Array.isArray(box) || box.length !== 3) return [];
  const [xBox, yBox, zBox] = box;
  return [
    { x: xBox.lo, y: yBox.lo, z: zBox.lo },
    { x: xBox.hi, y: yBox.lo, z: zBox.lo },
    { x: xBox.lo, y: yBox.hi, z: zBox.lo },
    { x: xBox.hi, y: yBox.hi, z: zBox.lo },
    { x: xBox.lo, y: yBox.lo, z: zBox.hi },
    { x: xBox.hi, y: yBox.lo, z: zBox.hi },
    { x: xBox.lo, y: yBox.hi, z: zBox.hi },
    { x: xBox.hi, y: yBox.hi, z: zBox.hi },
  ];
}

function drawContextFrameCanvas() {
  const canvas = q("contextFrameCanvas");
  if (!(canvas instanceof HTMLCanvasElement)) return;
  const frames = state.contextExtract.parsedFrames || [];
  const frameObj = frames[state.contextExtract.frameIndex] || null;
  const rect = canvas.getBoundingClientRect();
  const width = Math.max(240, Math.round(rect.width || 920));
  const height = Math.max(280, Math.round(rect.height || 520));
  const dpr = window.devicePixelRatio || 1;
  canvas.width = Math.round(width * dpr);
  canvas.height = Math.round(height * dpr);
  const ctx = canvas.getContext("2d");
  if (!ctx) return;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, width, height);
  ctx.fillStyle = "#fffdf8";
  ctx.fillRect(0, 0, width, height);

  if (!frameObj || !frameObj.atoms.length) {
    ctx.fillStyle = "#625e55";
    ctx.font = "14px Menlo, monospace";
    ctx.fillText("当前没有可显示的轨迹帧。", 20, 34);
    canvas._projectedAtoms = [];
    setContextFrameInfo(frameObj, null);
    return;
  }

  const mode = state.contextExtract.viewMode || "3d";
  const zoom = Number(state.contextExtract.zoom || 1) || 1;
  const rotX = Number(state.contextExtract.rotX || 0);
  const rotY = Number(state.contextExtract.rotY || 0);
  const box = frameObj.box || [];
  const atoms = frameObj.atoms || [];
  const highlightSet = contextHighlightAtomIdsForFrame(frameObj);
  const hasHighlight = highlightSet.size > 0;
  const focusEventAtoms = !!state.contextExtract.focusEventAtoms;
  const showTrails = !!state.contextExtract.showTrails;
  const trailWindow = Math.max(1, Math.min(200, Number.parseInt(String(state.contextExtract.trailWindow || 8), 10) || 8));
  const atomsForView = focusEventAtoms
    ? atoms.filter((atom) => highlightSet.has(Number(atom?.id)))
    : atoms.slice();
  const useBoxCenter = !focusEventAtoms && box.length === 3;
  const center = useBoxCenter
    ? {
        x: (box[0].lo + box[0].hi) / 2,
        y: (box[1].lo + box[1].hi) / 2,
        z: (box[2].lo + box[2].hi) / 2,
      }
    : atomsForView.reduce(
        (acc, atom) => ({
          x: acc.x + atom.x / Math.max(atomsForView.length, 1),
          y: acc.y + atom.y / Math.max(atomsForView.length, 1),
          z: acc.z + atom.z / Math.max(atomsForView.length, 1),
        }),
        { x: 0, y: 0, z: 0 }
      );

  const rawPoints = atomsForView.map((atom) => ({
    atom,
    x: atom.x - center.x,
    y: atom.y - center.y,
    z: atom.z - center.z,
  }));
  const boxPoints = contextFrameCorners(box).map((corner) => ({
    x: corner.x - center.x,
    y: corner.y - center.y,
    z: corner.z - center.z,
  }));
  const projectedAtoms = rawPoints.map((item) => {
    const proj = projectContextPoint(item, mode, rotX, rotY);
    return { ...item, ...proj };
  });
  const projectedBox = (state.contextExtract.showBox && !focusEventAtoms)
    ? boxPoints.map((point) => projectContextPoint(point, mode, rotX, rotY))
    : [];
  const extentPoints = projectedAtoms.concat(projectedBox);
  if (!extentPoints.length) {
    ctx.fillStyle = "#625e55";
    ctx.font = "14px Menlo, monospace";
    const msg = focusEventAtoms
      ? "当前帧没有事件原子可显示（请检查 Highlight / Route Trace / Frame 选择）"
      : "当前过滤后没有可显示的原子。";
    ctx.fillText(msg, 20, 34);
    canvas._projectedAtoms = [];
    setContextFrameInfo(frameObj, null);
    return;
  }
  const xs = extentPoints.map((item) => item.px);
  const ys = extentPoints.map((item) => item.py);
  const minX = Math.min(...xs);
  const maxX = Math.max(...xs);
  const minY = Math.min(...ys);
  const maxY = Math.max(...ys);
  const spanX = Math.max(1e-6, maxX - minX);
  const spanY = Math.max(1e-6, maxY - minY);
  const pad = 32;
  const scale = Math.min((width - pad * 2) / spanX, (height - pad * 2) / spanY) * zoom;
  const toScreen = (proj) => ({
    x: width / 2 + proj.px * scale,
    y: height / 2 + proj.py * scale,
  });

  if (state.contextExtract.showBox && projectedBox.length === 8) {
    const edges = [
      [0, 1], [0, 2], [1, 3], [2, 3],
      [4, 5], [4, 6], [5, 7], [6, 7],
      [0, 4], [1, 5], [2, 6], [3, 7],
    ];
    ctx.save();
    ctx.strokeStyle = "#d6c5af";
    ctx.lineWidth = 1;
    edges.forEach(([a, b]) => {
      const p1 = toScreen(projectedBox[a]);
      const p2 = toScreen(projectedBox[b]);
      ctx.beginPath();
      ctx.moveTo(p1.x, p1.y);
      ctx.lineTo(p2.x, p2.y);
      ctx.stroke();
    });
    ctx.restore();
  }

  if (showTrails && hasHighlight) {
    const trailSegments = contextTrailSegments(state.contextExtract.frameIndex, highlightSet, trailWindow);
    trailSegments.forEach((segment) => {
      const points = segment.points
        .map((point) => {
          const proj = projectContextPoint(
            {
              x: point.x - center.x,
              y: point.y - center.y,
              z: point.z - center.z,
            },
            mode,
            rotX,
            rotY
          );
          return toScreen(proj);
        });
      if (points.length < 2) return;
      const color = contextFrameColor(segment.points[segment.points.length - 1]?.atom);
      ctx.beginPath();
      ctx.moveTo(points[0].x, points[0].y);
      for (let idx = 1; idx < points.length; idx += 1) {
        ctx.lineTo(points[idx].x, points[idx].y);
      }
      ctx.strokeStyle = color;
      ctx.globalAlpha = 0.35;
      ctx.lineWidth = 1.5;
      ctx.stroke();
      ctx.globalAlpha = 1;
    });
  }

  const paintAtoms = projectedAtoms
    .map((item) => {
      const screen = toScreen(item);
      const atomId = Number(item.atom?.id);
      const highlighted = Number.isFinite(atomId) && highlightSet.has(atomId);
      return {
        ...item.atom,
        screenX: screen.x,
        screenY: screen.y,
        depth: item.depth,
        radius: contextFrameRadius(item.atom),
        color: contextFrameColor(item.atom),
        highlighted,
      };
    })
    .sort((left, right) => left.depth - right.depth);

  paintAtoms.forEach((atom) => {
    ctx.beginPath();
    ctx.fillStyle = atom.color;
    const alpha = focusEventAtoms && hasHighlight
      ? (atom.highlighted ? 0.98 : 0.0)
      : (hasHighlight ? (atom.highlighted ? 0.96 : 0.18) : 0.92);
    ctx.globalAlpha = alpha;
    const radius = atom.highlighted ? atom.radius + 1.2 : atom.radius;
    ctx.arc(atom.screenX, atom.screenY, radius, 0, Math.PI * 2);
    ctx.fill();
    ctx.lineWidth = 0.8;
    ctx.strokeStyle = "#fff";
    ctx.stroke();
    if (atom.highlighted) {
      ctx.beginPath();
      ctx.strokeStyle = "#ff7043";
      ctx.lineWidth = 2.1;
      ctx.globalAlpha = 1;
      ctx.arc(atom.screenX, atom.screenY, radius + 3.0, 0, Math.PI * 2);
      ctx.stroke();
    }
  });
  ctx.globalAlpha = 1;

  if (state.contextExtract.hoverAtom) {
    const hovered = paintAtoms.find((item) => item.id === state.contextExtract.hoverAtom.id);
    if (hovered) {
      ctx.beginPath();
      ctx.strokeStyle = "#ff7043";
      ctx.lineWidth = 2;
      ctx.arc(hovered.screenX, hovered.screenY, hovered.radius + 3, 0, Math.PI * 2);
      ctx.stroke();
      setContextFrameInfo(frameObj, hovered);
    } else {
      setContextFrameInfo(frameObj, null);
    }
  } else {
    setContextFrameInfo(frameObj, null);
  }
  canvas._projectedAtoms = paintAtoms;
}

function renderContextFrameSummary() {
  const host = q("contextFrameSummary");
  if (!host) return;
  const frameObj = (state.contextExtract.parsedFrames || [])[state.contextExtract.frameIndex] || null;
  if (!frameObj) {
    host.innerHTML = "";
    return;
  }
  const row = contextFrameRowLookup(frameObj.frame);
  const parts = [
    `frame=${frameObj.frame}`,
    `atoms=${frameObj.atoms.length}`,
    `target_count=${row?.target_count ?? "n/a"}`,
    `event_refs=${row?.event_refs || "n/a"}`,
  ];
  const highlightSet = contextHighlightAtomIdsForFrame(frameObj);
  parts.push(`highlight=${contextHighlightModeLabel(state.contextExtract.highlightMode)}:${highlightSet.size}`);
  parts.push(`focus=${state.contextExtract.focusEventAtoms ? "event-only" : "all-atoms"}`);
  parts.push(`trails=${state.contextExtract.showTrails ? `on/${state.contextExtract.trailWindow}` : "off"}`);
  if (row && row.route_target_atom_count != null) {
    parts.push(`core_atoms=${row.route_target_atom_count}`);
  }
  if (row && row.route_reactant_atom_count != null && row.route_product_atom_count != null) {
    parts.push(`reactant_atoms=${row.route_reactant_atom_count}`);
    parts.push(`product_atoms=${row.route_product_atom_count}`);
  }
  host.innerHTML = parts.map((text) => `<span class="stat-chip">${escapeHtml(text)}</span>`).join("");
}

function clearContextStoryboard() {
  state.contextExtract.storyboardItems = [];
  const grid = q("contextStoryboardGrid");
  if (grid instanceof HTMLElement) {
    grid.innerHTML = "";
  }
}

function contextStoryboardFrameIndices(frames, anchorFrame = NaN) {
  const list = Array.isArray(frames) ? frames : [];
  if (!list.length) return [];
  const snapshotItems = Array.isArray(state.contextExtract.snapshotItems) ? state.contextExtract.snapshotItems : [];
  if (snapshotItems.length) {
    const seen = new Set();
    return snapshotItems
      .map((item) => list.findIndex((frameObj) => Number(frameObj?.frame) === Number(item?.frame)))
      .filter((index) => Number.isInteger(index) && index >= 0 && index < list.length)
      .filter((index) => {
        if (seen.has(index)) return false;
        seen.add(index);
        return true;
      });
  }
  if (list.length <= 5) return list.map((_, index) => index);
  const anchorIdx = Number.isFinite(anchorFrame)
    ? list.findIndex((frameObj) => Number(frameObj?.frame) === Number(anchorFrame))
    : -1;
  const raw = [
    0,
    Math.round((list.length - 1) * 0.25),
    anchorIdx,
    Math.round((list.length - 1) * 0.75),
    list.length - 1,
  ];
  const seen = new Set();
  return raw
    .filter((index) => Number.isInteger(index) && index >= 0 && index < list.length)
    .filter((index) => {
      if (seen.has(index)) return false;
      seen.add(index);
      return true;
    })
    .sort((a, b) => a - b);
}

function contextStoryboardItemLabel(frameObj, anchorFrame, row) {
  const frame = Number(frameObj?.frame);
  const planned = (state.contextExtract.snapshotItems || []).find((item) => Number(item?.frame) === frame);
  if (planned && planned.label) {
    return String(planned.label);
  }
  const parts = [];
  if (Number.isFinite(anchorFrame) && frame === anchorFrame) {
    parts.push("anchor");
  }
  if (row?.event_refs) {
    parts.push(String(row.event_refs));
  }
  if (!parts.length) {
    parts.push(`frame ${frame}`);
  }
  return parts.join(" | ");
}

function buildContextStoryboardItems() {
  const canvas = q("contextFrameCanvas");
  if (!(canvas instanceof HTMLCanvasElement)) return [];
  const frames = state.contextExtract.parsedFrames || [];
  if (!frames.length) return [];
  const anchorFrame = Number(
    state.contextExtract.selectedEventRow?.anchor_frame
    ?? (ensureResultSlot("context_extract").rows || [])[0]?.anchor_frame
  );
  const indices = contextStoryboardFrameIndices(frames, anchorFrame);
  const prevIndex = state.contextExtract.frameIndex;
  const prevHover = state.contextExtract.hoverAtom;
  const items = [];
  indices.forEach((index) => {
    state.contextExtract.frameIndex = index;
    state.contextExtract.hoverAtom = null;
    drawContextFrameCanvas();
    const frameObj = frames[index];
    const row = contextFrameRowLookup(frameObj?.frame);
    items.push({
      index,
      frame: frameObj?.frame,
      label: contextStoryboardItemLabel(frameObj, anchorFrame, row),
      imageUrl: canvas.toDataURL("image/png"),
    });
  });
  state.contextExtract.frameIndex = Math.max(0, Math.min(frames.length - 1, prevIndex));
  state.contextExtract.hoverAtom = prevHover;
  drawContextFrameCanvas();
  renderContextFrameSummary();
  syncContextFrameSelect();
  state.contextExtract.storyboardItems = items;
  return items;
}

function syncContextStoryboardSelection() {
  const grid = q("contextStoryboardGrid");
  if (!(grid instanceof HTMLElement)) return;
  const activeIndex = state.contextExtract.frameIndex;
  grid.querySelectorAll(".context-storyboard-item").forEach((el) => {
    if (!(el instanceof HTMLElement)) return;
    const itemIndex = Number.parseInt(String(el.dataset.frameIndex || ""), 10);
    el.classList.toggle("is-active", itemIndex === activeIndex);
  });
}

function renderContextStoryboard() {
  const grid = q("contextStoryboardGrid");
  if (!(grid instanceof HTMLElement)) return;
  const frames = state.contextExtract.parsedFrames || [];
  if (!frames.length) {
    clearContextStoryboard();
    grid.innerHTML = '<div class="context-storyboard-empty">当前没有可生成的关键帧快照。</div>';
    return;
  }
  const items = buildContextStoryboardItems();
  if (!items.length) {
    clearContextStoryboard();
    grid.innerHTML = '<div class="context-storyboard-empty">轨迹已提取，但快照生成失败。</div>';
    return;
  }
  grid.innerHTML = items
    .map((item) => `
      <button type="button" class="context-storyboard-item${item.index === state.contextExtract.frameIndex ? " is-active" : ""}" data-frame-index="${item.index}">
        <img src="${item.imageUrl}" alt="frame ${item.frame}" />
        <span class="context-storyboard-caption">
          <strong>${escapeHtml(String(item.frame))}</strong>
          <span>${escapeHtml(String(item.label || ""))}</span>
        </span>
      </button>
    `)
    .join("");
}

function syncContextFrameSelect() {
  const select = q("qContextFrameSelect");
  if (!(select instanceof HTMLSelectElement)) return;
  const frames = state.contextExtract.parsedFrames || [];
  const current = frames[state.contextExtract.frameIndex]?.frame;
  select.innerHTML = frames
    .map((frameObj, index) => {
      const row = contextFrameRowLookup(frameObj.frame);
      const suffix = row?.event_refs ? ` | ${row.event_refs}` : "";
      return `<option value="${index}" ${frameObj.frame === current ? "selected" : ""}>${escapeHtml(`${frameObj.frame}${suffix}`)}</option>`;
    })
    .join("");
  select.disabled = !frames.length;
  q("btnContextFramePrev").disabled = state.contextExtract.frameIndex <= 0;
  q("btnContextFrameNext").disabled = state.contextExtract.frameIndex >= frames.length - 1 || !frames.length;
}

function setContextFrameIndex(nextIndex) {
  const frames = state.contextExtract.parsedFrames || [];
  if (!frames.length) return;
  const clamped = Math.max(0, Math.min(frames.length - 1, Number(nextIndex) || 0));
  state.contextExtract.frameIndex = clamped;
  state.contextExtract.hoverAtom = null;
  syncContextFrameSelect();
  renderContextFrameSummary();
  drawContextFrameCanvas();
  syncContextStoryboardSelection();
}

function renderContextTrajectoryViewer() {
  const card = q("contextTrajectoryCard");
  const note = q("contextTrajectoryNote");
  if (!card || !note) return;
  const slot = ensureResultSlot("context_extract");
  const inlineViewerFlag = slot.meta?.query?.inline_viewer ?? slot.meta?.meta?.inline_viewer;
  const inlineViewerEnabled = Number(inlineViewerFlag ?? 0) > 0;
  if (!inlineViewerEnabled) {
    clearContextStoryboard();
    card.classList.add("hidden");
    return;
  }
  const status = String(slot.meta?.status || slot.meta?.meta?.status || "").toLowerCase();
  const shouldShow = !!(slot.rows || []).length || (status && !["idle", "ready"].includes(status));
  card.classList.toggle("hidden", !shouldShow);
  if (!shouldShow) return;

  const trajectoryText = state.contextExtract.trajectoryText || "";
  const trajectoryPreviewText = state.contextExtract.trajectoryPreviewText || "";
  const viewerText = trajectoryText || trajectoryPreviewText;
  const trajectoryPath = state.contextExtract.trajectoryPath || "";
  const trajectoryNote = slot.meta?.meta?.trajectory_note || slot.meta?.trajectory_note || "";
  if (!viewerText) {
    note.textContent = trajectoryPath
      ? `${trajectoryNote || "轨迹片段已写入临时文件。"} 可用默认程序、OVITO、PyMOL 或 VMD 打开。`
      : trajectoryNote || "未返回轨迹片段，当前只能导出帧列表。";
    q("qContextFrameSelect").innerHTML = "";
    q("qContextFrameSelect").disabled = true;
    q("btnContextFramePrev").disabled = true;
    q("btnContextFrameNext").disabled = true;
    renderContextFrameSummary();
    clearContextStoryboard();
    drawContextFrameCanvas();
    return;
  }
  if (!(state.contextExtract.parsedFrames || []).length) {
    state.contextExtract.parsedFrames = parseLammpstrjSubset(viewerText);
  }
  const frames = state.contextExtract.parsedFrames || [];
  if (!frames.length) {
    note.textContent = "轨迹片段已返回，但前端未能解析为可视帧。";
    q("qContextFrameSelect").innerHTML = "";
    q("qContextFrameSelect").disabled = true;
    q("btnContextFramePrev").disabled = true;
    q("btnContextFrameNext").disabled = true;
    clearContextStoryboard();
    drawContextFrameCanvas();
    return;
  }
  const isPreviewOnly = !!(slot.meta?.meta?.trajectory_preview_only || slot.meta?.trajectory_preview_only) && !trajectoryText;
  note.textContent = isPreviewOnly
    ? "当前显示为预览帧（完整子轨迹已保存到临时文件）；拖动旋转 3D，滚轮缩放。"
    : "显示当前提取结果中的 LAMMPS 轨迹子集；拖动旋转 3D，滚轮缩放。";
  if (!frames[state.contextExtract.frameIndex]) {
    const anchorFrame = Number((slot.rows || [])[0]?.anchor_frame);
    const anchorIdx = frames.findIndex((frameObj) => frameObj.frame === anchorFrame);
    state.contextExtract.frameIndex = anchorIdx >= 0 ? anchorIdx : 0;
  }
  q("qContextFrameView").value = state.contextExtract.viewMode || "3d";
  q("qContextHighlightMode").value = state.contextExtract.highlightMode || "route_target";
  q("qContextFocusEventAtoms").checked = !!state.contextExtract.focusEventAtoms;
  q("qContextShowTrails").checked = !!state.contextExtract.showTrails;
  q("qContextTrailWindow").value = String(state.contextExtract.trailWindow || 8);
  q("qContextFrameShowBox").checked = !!state.contextExtract.showBox;
  q("qContextFrameZoom").value = String(state.contextExtract.zoom || 1);
  syncContextFrameSelect();
  renderContextFrameSummary();
  drawContextFrameCanvas();
  renderContextStoryboard();
}

function viewerShowHEnabled(viewerKey = "general") {
  const ctx = viewerContext(viewerKey);
  const el = q(ctx.showHId);
  return !el || el.checked;
}

function parseTargetsForPlot(raw) {
  return String(raw || "")
    .split(/\n+/)
    .map((x) => x.trim())
    .filter(Boolean);
}

function carbonRangeText(id, fallback = "") {
  const text = value(id);
  return text || fallback;
}

function carbonTimeColumn(query) {
  return String(query?.time_col || "time");
}

function carbonTimeAxisLabel(query) {
  return String(query?.time_axis_label || carbonTimeColumn(query));
}

function resolveStructureSmiles(item) {
  const direct = String(item?.smiles || "").trim();
  if (direct) return direct;
  const species = String(item?.species || "").trim();
  if (species && looksLikeExplicitSmiles(species)) return species;
  const label = String(item?.label || "").trim();
  if (label.includes("|")) {
    const parts = label.split("|");
    const candidate = String(parts[parts.length - 1] || "").trim();
    if (candidate && looksLikeExplicitSmiles(candidate)) return candidate;
  }
  if (label && looksLikeExplicitSmiles(label)) return label;
  return "";
}

async function openStructureBySmiles(smiles, viewerKey = "general") {
  const text = String(smiles || "").trim();
  if (!text) return;
  renderStructurePreviewItems(viewerKey, [{ kind: "smiles", smiles: text, title: "Pinned structure" }], {
    note: "已固定显示点击的结构；悬停 SMILES 可快速预览",
    scroll: true,
  });
}

function buildFormulaLookup(entries) {
  const bySystem = new Map();
  let maxCarbon = 0;
  (entries || []).forEach((entry) => {
    const carbon = Number(entry?.carbon_number);
    if (!Number.isFinite(carbon)) return;
    const systemKey = entry?.system == null ? "" : String(entry.system);
    if (!bySystem.has(systemKey)) bySystem.set(systemKey, new Map());
    const systemMap = bySystem.get(systemKey);
    systemMap.set(Math.trunc(carbon), {
      carbonNumber: Math.trunc(carbon),
      formulae: Array.isArray(entry?.formulae) ? entry.formulae : [],
      nFormulae: Number(entry?.n_formulae) || 0,
      truncated: !!entry?.truncated,
    });
    maxCarbon = Math.max(maxCarbon, Math.trunc(carbon));
  });
  return { bySystem, maxCarbon };
}

function formulaItemsForSystemRange(systemValue, startCarbon, endCarbon) {
  const lookup = state.carbonPlot.formulaLookup;
  if (!lookup || !lookup.bySystem || !lookup.bySystem.size) return [];
  const systemsToTry = [];
  const primary = systemValue == null ? "" : String(systemValue);
  if (primary) systemsToTry.push(primary);
  systemsToTry.push("");
  const lo = Number.isFinite(startCarbon) ? Math.max(0, Math.trunc(startCarbon)) : 0;
  const hi = Number.isFinite(endCarbon) ? Math.trunc(endCarbon) : lookup.maxCarbon;
  const merged = new Map();

  systemsToTry.forEach((systemKey) => {
    const carbonMap = lookup.bySystem.get(systemKey);
    if (!carbonMap) return;
    for (let carbon = lo; carbon <= hi; carbon += 1) {
      const entry = carbonMap.get(carbon);
      if (!entry) continue;
      (entry.formulae || []).forEach((item) => {
        const species = String(item?.species || "").trim();
        if (!species) return;
        const count = Number(item?.total_count) || 0;
        const prev = merged.get(species) || 0;
        merged.set(species, prev + count);
      });
    }
  });
  return Array.from(merged.entries())
    .map(([species, total]) => ({ species, total_count: total }))
    .sort((a, b) => {
      if (b.total_count !== a.total_count) return b.total_count - a.total_count;
      return a.species.localeCompare(b.species);
    });
}

function formulaItemsForCurveGroup(curves, startCarbon, endCarbon, ranges = null) {
  const merged = new Map();
  const activeRanges = Array.isArray(ranges) && ranges.length ? ranges : [{ start: startCarbon, end: endCarbon }];
  (curves || []).forEach((curve) => {
    activeRanges.forEach((range) => {
      const items = formulaItemsForSystemRange(curve.systemValue || "", range.start, range.end);
      items.forEach((item) => {
        const prev = merged.get(item.species) || 0;
        merged.set(item.species, prev + (Number(item.total_count) || 0));
      });
    });
  });
  return Array.from(merged.entries())
    .map(([species, total]) => ({ species, total_count: total }))
    .sort((a, b) => {
      if (b.total_count !== a.total_count) return b.total_count - a.total_count;
      return a.species.localeCompare(b.species);
    });
}

function parseCarbonRangeToken(text) {
  const parseIntStrict = (value, origin) => {
    const parsed = Number.parseInt(value, 10);
    if (!Number.isFinite(parsed)) throw new Error(`Invalid carbon range "${origin}"`);
    return parsed;
  };
  let token = String(text || "").trim().replaceAll(" ", "");
  token = token.replaceAll("≤", "<=").replaceAll("≥", ">=");
  token = token.replace(/c(?=\d)/gi, "");
  if (!token) throw new Error("Carbon range token cannot be empty");
  if (token.endsWith("+")) return { start: parseIntStrict(token.slice(0, -1), text), end: null };
  if (token.startsWith(">=")) return { start: parseIntStrict(token.slice(2), text), end: null };
  if (token.startsWith(">")) return { start: parseIntStrict(token.slice(1), text) + 1, end: null };
  if (token.startsWith("<=")) return { start: null, end: parseIntStrict(token.slice(2), text) };
  if (token.startsWith("<")) return { start: null, end: parseIntStrict(token.slice(1), text) - 1 };
  if (token.includes("-")) {
    const [left, right] = token.split("-", 2);
    const start = parseIntStrict(left, text);
    const end = parseIntStrict(right, text);
    if (end < start) throw new Error(`Invalid carbon range "${text}"`);
    return { start, end };
  }
  const value = parseIntStrict(token, text);
  return { start: value, end: value };
}

function splitCarbonRangeExpression(text) {
  const cleaned = String(text || "").trim().replaceAll(" ", "");
  if (!cleaned) return [];
  const parts = [];
  let start = 0;
  for (let idx = 0; idx < cleaned.length; idx += 1) {
    if (cleaned[idx] === "+" && idx < cleaned.length - 1) {
      parts.push(cleaned.slice(start, idx));
      start = idx + 1;
    }
  }
  parts.push(cleaned.slice(start));
  return parts.map((item) => item.trim()).filter(Boolean);
}

function carbonRangeLabel(range) {
  const start = range?.start == null ? null : Number(range.start);
  const end = range?.end == null ? null : Number(range.end);
  if (Number.isFinite(start) && Number.isFinite(end)) {
    return start === end ? `C${start}` : `C${start}-C${end}`;
  }
  if (Number.isFinite(start)) return `C${start}+`;
  if (Number.isFinite(end)) return `<=C${end}`;
  return "all C";
}

function carbonRangeExpressionFromRanges(ranges) {
  return (ranges || []).map((range) => carbonRangeLabel(range)).join("+");
}

function carbonSpecRanges(spec) {
  const ranges = Array.isArray(spec?.ranges) ? spec.ranges : [];
  if (ranges.length) {
    return ranges.map((range) => ({
      start: range?.start == null ? null : Number(range.start),
      end: range?.end == null ? null : Number(range.end),
    }));
  }
  return [{
    start: spec?.start == null ? null : Number(spec.start),
    end: spec?.end == null ? null : Number(spec.end),
  }];
}

function carbonRangeBounds(ranges, maxCarbon = null) {
  const normalized = (ranges || []).filter(Boolean);
  if (!normalized.length) return { start: null, end: null };
  const starts = normalized.map((range) => (range.start != null && Number.isFinite(Number(range.start)) ? Number(range.start) : 0));
  const hasOpenEnd = normalized.some((range) => range.end == null || !Number.isFinite(Number(range.end)));
  const ends = normalized.map((range) => {
    if (range.end != null && Number.isFinite(Number(range.end))) return Number(range.end);
    return Number.isFinite(Number(maxCarbon)) ? Number(maxCarbon) : null;
  });
  const finiteEnds = ends.filter((item) => Number.isFinite(item));
  return {
    start: starts.length ? Math.min(...starts) : null,
    end: hasOpenEnd && !Number.isFinite(Number(maxCarbon)) ? null : (finiteEnds.length ? Math.max(...finiteEnds) : null),
  };
}

function parseCarbonRangeSpecsClient(text) {
  const raw = String(text || "").trim();
  if (!raw) return [];
  const tokens = raw
    .split(/[;\n,]+/)
    .map((item) => item.trim())
    .filter(Boolean);
  return tokens.map((token, index) => {
    let label = "";
    let rangeText = token;
    if (token.includes(":")) {
      const parts = token.split(":", 2);
      label = parts[0].trim();
      rangeText = parts[1].trim();
    }
    const ranges = splitCarbonRangeExpression(rangeText).map((item) => parseCarbonRangeToken(item));
    if (!ranges.length) throw new Error(`Invalid carbon range "${token}"`);
    const { start, end } = carbonRangeBounds(ranges);
    const autoLabel = carbonRangeExpressionFromRanges(ranges);
    return {
      label: label || autoLabel || `range_${index + 1}`,
      start: start == null ? null : Number(start),
      end: end == null ? null : Number(end),
      ranges,
    };
  });
}

function rangeOverlaps(start, end, spec) {
  const loA = Number.isFinite(start) ? Number(start) : Number.NEGATIVE_INFINITY;
  const hiA = Number.isFinite(end) ? Number(end) : Number.POSITIVE_INFINITY;
  const loB = Number.isFinite(spec.start) ? Number(spec.start) : Number.NEGATIVE_INFINITY;
  const hiB = Number.isFinite(spec.end) ? Number(spec.end) : Number.POSITIVE_INFINITY;
  return !(hiA < loB || loA > hiB);
}

function rangeOverlapsAny(start, end, spec) {
  return carbonSpecRanges(spec).some((range) => rangeOverlaps(start, end, range));
}

function representativeCarbon(start, end, fallbackStart, fallbackEnd) {
  if (Number.isFinite(start) && Number.isFinite(end)) return (Number(start) + Number(end)) / 2.0;
  if (Number.isFinite(start)) return Number(start);
  if (Number.isFinite(end)) return Number(end);
  if (Number.isFinite(fallbackStart) && Number.isFinite(fallbackEnd)) return (Number(fallbackStart) + Number(fallbackEnd)) / 2.0;
  return Number.NaN;
}

function normalizeCarbonPlotRows(rows, timeCol = "time") {
  const out = [];
  (rows || []).forEach((row) => {
    const time = Number(row?.[timeCol]);
    if (!Number.isFinite(time)) return;
    const meanCount = Number(row?.mean_count ?? row?.count ?? 0);
    const stdCount = Number(row?.std_count ?? 0);
    const startCarbon = Number(row?.series_start_carbon);
    const endCarbon = Number(row?.series_end_carbon);
    out.push({
      time,
      meanCount: Number.isFinite(meanCount) ? meanCount : 0,
      stdCount: Number.isFinite(stdCount) ? stdCount : 0,
      displayLabel: String(row?.display_label ?? row?.__series_key ?? "series"),
      plotRegion: String(row?.plot_region ?? "All carbon numbers"),
      systemValue: row?.__system_value == null || row?.__system_value === "" ? "" : String(row.__system_value),
      startCarbon: Number.isFinite(startCarbon) ? startCarbon : Number.NaN,
      endCarbon: Number.isFinite(endCarbon) ? endCarbon : Number.NaN,
      representativeCarbon: Number(row?.representative_carbon),
      displayMode: String(row?.display_mode ?? "exact"),
      displaySort: Number(row?.display_sort ?? Number.MAX_SAFE_INTEGER),
      isParentHighlight: !!row?.is_parent_highlight,
    });
  });
  return out;
}

function mergeCarbonCompareRows(rows, mergeSpecs) {
  if (!mergeSpecs || !mergeSpecs.length) return rows.map((item) => ({ ...item }));
  const maxCarbon = rows.reduce((acc, row) => {
    if (Number.isFinite(row.endCarbon)) return Math.max(acc, row.endCarbon);
    return acc;
  }, 0);
  const keyed = new Map();
  rows.forEach((row) => {
    let mergedSpec = null;
    for (const spec of mergeSpecs) {
      if (rangeOverlapsAny(row.startCarbon, row.endCarbon, spec)) {
        mergedSpec = spec;
        break;
      }
    }
    const mergeRanges = mergedSpec ? carbonSpecRanges(mergedSpec) : [{ start: row.startCarbon, end: row.endCarbon }];
    const bounds = mergedSpec ? carbonRangeBounds(mergeRanges, maxCarbon) : { start: row.startCarbon, end: row.endCarbon };
    const startCarbon = mergedSpec ? (Number.isFinite(bounds.start) ? Number(bounds.start) : 0) : row.startCarbon;
    const endCarbon = mergedSpec
      ? (Number.isFinite(bounds.end) ? Number(bounds.end) : maxCarbon)
      : row.endCarbon;
    const displayLabel = mergedSpec ? mergedSpec.label : row.displayLabel;
    const displayMode = mergedSpec ? "merged" : row.displayMode;
    const displaySort = mergedSpec ? startCarbon : row.displaySort;
    const representative = mergedSpec
      ? representativeCarbon(bounds.start, bounds.end, row.startCarbon, row.endCarbon)
      : row.representativeCarbon;
    const rangeKey = carbonRangeExpressionFromRanges(mergeRanges);
    const seriesKey = `${row.systemValue}::${row.plotRegion}::${displayLabel}::${rangeKey}`;
    const aggKey = `${seriesKey}::${row.time}`;
    const exists = keyed.get(aggKey);
    if (exists) {
      exists.meanCount += row.meanCount;
      exists.varCount += row.stdCount * row.stdCount;
      exists.isParentHighlight = exists.isParentHighlight || row.isParentHighlight;
      return;
    }
    keyed.set(aggKey, {
      time: row.time,
      meanCount: row.meanCount,
      varCount: row.stdCount * row.stdCount,
      displayLabel,
      plotRegion: row.plotRegion,
      systemValue: row.systemValue,
      startCarbon,
      endCarbon,
      representativeCarbon: representative,
      displayMode,
      displaySort,
      isParentHighlight: row.isParentHighlight,
      seriesKey,
      mergeRanges,
    });
  });
  return Array.from(keyed.values())
    .map((item) => ({
      ...item,
      stdCount: Math.sqrt(Math.max(0, item.varCount)),
    }))
    .sort((a, b) => {
      if (a.displaySort !== b.displaySort) return a.displaySort - b.displaySort;
      if (a.time !== b.time) return a.time - b.time;
      return a.displayLabel.localeCompare(b.displayLabel);
    });
}

function buildCarbonCompareData(rows, timeName = "time") {
  if (!rows.length) return { xValues: [], curves: [], xName: timeName };
  const systems = Array.from(new Set(rows.map((r) => r.systemValue).filter((v) => v)));
  const regions = Array.from(new Set(rows.map((r) => r.plotRegion).filter((v) => v)));
  const hasManySystems = systems.length > 1;
  const hasManyRegions = regions.length > 1;
  const xValues = Array.from(new Set(rows.map((r) => r.time))).sort((a, b) => a - b);

  const grouped = new Map();
  rows.forEach((row) => {
    const key = row.seriesKey || `${row.systemValue}::${row.plotRegion}::${row.displayLabel}`;
    let item = grouped.get(key);
    if (!item) {
      const parts = [];
      if (hasManySystems && row.systemValue) parts.push(row.systemValue);
      if (hasManyRegions && row.plotRegion) parts.push(row.plotRegion);
      parts.push(row.displayLabel);
      item = {
        key,
        name: parts.join(" | "),
        valuesByTime: new Map(),
        max_value: Number.NEGATIVE_INFINITY,
        displaySort: Number.isFinite(row.displaySort) ? row.displaySort : Number.MAX_SAFE_INTEGER,
        displayLabel: row.displayLabel,
        startCarbon: row.startCarbon,
        endCarbon: row.endCarbon,
        systemValue: row.systemValue || "",
        plotRegion: row.plotRegion || "",
        mergeRanges: Array.isArray(row.mergeRanges) ? row.mergeRanges : [{ start: row.startCarbon, end: row.endCarbon }],
      };
      grouped.set(key, item);
    }
    item.valuesByTime.set(row.time, row.meanCount);
    if (row.meanCount > item.max_value) item.max_value = row.meanCount;
  });

  const curves = Array.from(grouped.values())
    .map((curve) => ({
      key: curve.key,
      name: curve.name,
      displaySort: curve.displaySort,
      max_value: Number.isFinite(curve.max_value) ? curve.max_value : 0,
      displayLabel: curve.displayLabel,
      startCarbon: curve.startCarbon,
      endCarbon: curve.endCarbon,
      systemValue: curve.systemValue,
      plotRegion: curve.plotRegion,
      mergeRanges: Array.isArray(curve.mergeRanges) ? curve.mergeRanges : [{ start: curve.startCarbon, end: curve.endCarbon }],
      values: xValues.map((time) => (curve.valuesByTime.has(time) ? curve.valuesByTime.get(time) : null)),
    }))
    .sort((a, b) => {
      if (a.displaySort !== b.displaySort) return a.displaySort - b.displaySort;
      return a.name.localeCompare(b.name);
    });
  return { xValues, curves, xName: timeName };
}

function syncCarbonSeriesSelection(curves) {
  const available = new Set((curves || []).map((item) => item.key));
  const previous = new Set(state.carbonPlot.selectedSeriesKeys || []);
  const kept = [];
  previous.forEach((key) => {
    if (available.has(key)) kept.push(key);
  });
  if (!kept.length) {
    const top = [...(curves || [])]
      .sort((a, b) => b.max_value - a.max_value)
      .slice(0, Math.min(12, curves.length))
      .map((item) => item.key);
    state.carbonPlot.selectedSeriesKeys = top;
    return;
  }
  state.carbonPlot.selectedSeriesKeys = kept;
}

function getSelectedCarbonCurves() {
  const selected = new Set(state.carbonPlot.selectedSeriesKeys || []);
  return (state.carbonPlot.compareCurves || []).filter((curve) => selected.has(curve.key));
}

function normalizeCarbonMergeRanges(ranges) {
  const seen = new Set();
  return (ranges || [])
    .map((range) => ({
      start: range?.start == null ? null : Number(range.start),
      end: range?.end == null ? null : Number(range.end),
    }))
    .filter((range) => Number.isFinite(range.start) || Number.isFinite(range.end))
    .filter((range) => {
      const key = `${Number.isFinite(range.start) ? range.start : ""}-${Number.isFinite(range.end) ? range.end : ""}`;
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    })
    .sort((a, b) => {
      const sa = Number.isFinite(a.start) ? a.start : Number.NEGATIVE_INFINITY;
      const sb = Number.isFinite(b.start) ? b.start : Number.NEGATIVE_INFINITY;
      if (sa !== sb) return sa - sb;
      const ea = Number.isFinite(a.end) ? a.end : Number.POSITIVE_INFINITY;
      const eb = Number.isFinite(b.end) ? b.end : Number.POSITIVE_INFINITY;
      return ea - eb;
    });
}

function carbonMergeItemFromElement(element) {
  if (!(element instanceof HTMLElement)) return null;
  let ranges = [];
  try {
    ranges = JSON.parse(element.dataset.carbonRanges || "[]");
  } catch {
    ranges = [];
  }
  ranges = normalizeCarbonMergeRanges(ranges);
  if (!ranges.length) return null;
  return {
    key: String(element.dataset.carbonMergeKey || carbonRangeExpressionFromRanges(ranges)),
    label: String(element.dataset.carbonMergeLabel || carbonRangeExpressionFromRanges(ranges)),
    ranges,
  };
}

function carbonMergeSpecText(label, ranges) {
  const normalized = normalizeCarbonMergeRanges(ranges);
  if (!normalized.length) return "";
  const rangeText = carbonRangeExpressionFromRanges(normalized);
  return `${label || rangeText}:${rangeText}`;
}

function carbonMergeSpecOverlapsRanges(spec, ranges) {
  return normalizeCarbonMergeRanges(ranges).some((range) => rangeOverlapsAny(range.start, range.end, spec));
}

function setCarbonMergeInputWithSpec(label, ranges) {
  const normalized = normalizeCarbonMergeRanges(ranges);
  if (!normalized.length) return false;
  const nextText = carbonMergeSpecText(label, normalized);
  if (!nextText) return false;
  let existing = [];
  const raw = value("qCarbonCurveMerge");
  if (raw) {
    try {
      existing = parseCarbonRangeSpecsClient(raw).filter((spec) => !carbonMergeSpecOverlapsRanges(spec, normalized));
    } catch {
      existing = [];
    }
  }
  const texts = existing.map((spec) => carbonMergeSpecText(spec.label, carbonSpecRanges(spec))).filter(Boolean);
  texts.push(nextText);
  q("qCarbonCurveMerge").value = texts.join("; ");
  return true;
}

async function applyCarbonMergeItems(items) {
  const normalizedItems = (items || []).filter(Boolean);
  const ranges = normalizeCarbonMergeRanges(normalizedItems.flatMap((item) => item.ranges || []));
  if (ranges.length < 2) return;
  const label = carbonRangeExpressionFromRanges(ranges);
  if (!setCarbonMergeInputWithSpec(label, ranges)) return;
  state.carbonPlot.mergeBasket = [];
  updateCarbonMergeDropZone();
  await runCarbonInteractive(rebuildCarbonInteractiveCompare);
}

function updateCarbonMergeDropZone() {
  const zone = q("carbonMergeDropZone");
  if (!zone) return;
  const basket = state.carbonPlot.mergeBasket || [];
  if (!basket.length) {
    zone.textContent = "拖动 C 分组到另一项上可立即合并；也可先拖到这里，再拖第二项到这里合并。";
    return;
  }
  const label = basket.map((item) => item.label).join(" + ");
  zone.textContent = `已暂存 ${label}，再拖一个分组到这里完成合并。`;
}

function renderCarbonCurveSelector() {
  const host = q("carbonCurveList");
  const filterText = value("qCarbonCurveFilter").toLowerCase();
  const selected = new Set(state.carbonPlot.selectedSeriesKeys || []);
  const curves = (state.carbonPlot.compareCurves || []).filter((item) => {
    if (!filterText) return true;
    return item.name.toLowerCase().includes(filterText);
  });
  host.innerHTML = "";
  if (!curves.length) {
    host.innerHTML = '<div class="carbon-curve-item">没有匹配的曲线</div>';
    return;
  }

  const groups = new Map();
  curves.forEach((curve) => {
    const start = Number(curve.startCarbon);
    const end = Number(curve.endCarbon);
    const ranges = normalizeCarbonMergeRanges(
      Array.isArray(curve.mergeRanges) && curve.mergeRanges.length ? curve.mergeRanges : [{ start, end }]
    );
    const rangeKey = carbonRangeExpressionFromRanges(ranges);
    const label = String(curve.displayLabel || curve.name || "series");
    const groupKey = `${label}::${rangeKey || "all"}`;
    let group = groups.get(groupKey);
    if (!group) {
      const bounds = carbonRangeBounds(ranges);
      group = {
        key: groupKey,
        label,
        ranges,
        startCarbon: Number.isFinite(bounds.start) ? Math.trunc(bounds.start) : Number.NaN,
        endCarbon: Number.isFinite(bounds.end) ? Math.trunc(bounds.end) : Number.NaN,
        displaySort: Number.isFinite(curve.displaySort) ? curve.displaySort : Number.MAX_SAFE_INTEGER,
        maxValue: Number.NEGATIVE_INFINITY,
        curves: [],
      };
      groups.set(groupKey, group);
    }
    group.maxValue = Math.max(group.maxValue, Number(curve.max_value) || 0);
    group.displaySort = Math.min(group.displaySort, Number(curve.displaySort) || Number.MAX_SAFE_INTEGER);
    group.curves.push(curve);
  });

  let colorIndex = 0;
  Array.from(groups.values())
    .sort((a, b) => {
      if (a.displaySort !== b.displaySort) return a.displaySort - b.displaySort;
      return a.label.localeCompare(b.label);
    })
    .forEach((group) => {
      const details = document.createElement("details");
      details.className = "carbon-curve-group carbon-merge-draggable";
      details.draggable = true;
      details.dataset.carbonMergeKey = group.key;
      details.dataset.carbonMergeLabel = group.label;
      details.dataset.carbonRanges = JSON.stringify(group.ranges);
      details.title = "拖到另一个 C 分组上可合并曲线";

      const spanLabel = carbonRangeExpressionFromRanges(group.ranges) || "all C";

      const formulaItems = formulaItemsForCurveGroup(group.curves, group.startCarbon, group.endCarbon, group.ranges);
      const showFormulaItems = formulaItems.slice(0, 24);

      const summary = document.createElement("summary");
      summary.className = "carbon-group-summary";
      summary.innerHTML = `
        <span class="carbon-group-title">
          <strong>${escapeHtml(group.label)}</strong>
          <code>${escapeHtml(spanLabel)}</code>
        </span>
        <span class="carbon-group-stats">
          <span>curves=${group.curves.length}</span>
          <span>formulas=${formulaItems.length}</span>
          <span>max=${fmtTick(group.maxValue)}</span>
        </span>
      `;
      details.appendChild(summary);

      const body = document.createElement("div");
      body.className = "carbon-group-body";

      const curveWrap = document.createElement("div");
      curveWrap.className = "carbon-group-curves";
      group.curves
        .sort((a, b) => a.name.localeCompare(b.name))
        .forEach((curve) => {
          const color = PLOT_COLORS[colorIndex % PLOT_COLORS.length];
          colorIndex += 1;
          const row = document.createElement("div");
          row.className = "carbon-curve-item";
          row.innerHTML = `
            <label>
              <input class="carbon-curve-toggle" type="checkbox" data-series-key="${escapeHtml(curve.key)}" ${selected.has(curve.key) ? "checked" : ""} />
              <code title="${escapeHtml(curve.name)}">${escapeHtml(curve.name)}</code>
            </label>
            <span>
              <span class="legend-swatch" style="background:${color}"></span>
              max=${fmtTick(curve.max_value)}
            </span>
          `;
          curveWrap.appendChild(row);
        });
      body.appendChild(curveWrap);

      const formulaWrap = document.createElement("div");
      formulaWrap.className = "carbon-formula-box";
      if (!showFormulaItems.length) {
        formulaWrap.innerHTML = '<div class="carbon-formula-meta">无可用分子式索引</div>';
      } else {
        const formulaMeta = document.createElement("div");
        formulaMeta.className = "carbon-formula-meta";
        formulaMeta.textContent = `分子式列表（按总丰度排序，显示前 ${showFormulaItems.length}/${formulaItems.length}）`;
        formulaWrap.appendChild(formulaMeta);
        const tags = document.createElement("div");
        tags.className = "carbon-formula-tags";
        showFormulaItems.forEach((item) => {
          const smiles = resolveStructureSmiles(item);
          const tag = document.createElement(smiles ? "button" : "span");
          tag.className = `carbon-formula-tag${smiles ? " formula-structure-btn" : " is-static"}`;
          if (smiles) {
            tag.type = "button";
            tag.dataset.smiles = smiles;
            tag.dataset.smilesPreview = encodeURIComponent(smiles);
            tag.title = `点击查看结构: ${smiles}`;
          }
          tag.innerHTML = `<code>${escapeHtml(item.species)}</code>`;
          tags.appendChild(tag);
        });
        formulaWrap.appendChild(tags);
      }
      body.appendChild(formulaWrap);
      details.appendChild(body);
      host.appendChild(details);
    });
}

function drawCarbonCompareCanvas(xValues, curves, xName, yName = "number of molecules", logY = false) {
  const chartHost = q("carbonPlotChart");
  const canvas = q("carbonPlotCanvas");
  chartHost.style.display = "none";
  canvas.style.display = "block";

  const wrap = canvas.parentElement;
  const dpr = window.devicePixelRatio || 1;
  const width = Math.max(320, Math.floor(wrap.clientWidth - 4));
  const height = 360;
  canvas.style.width = `${width}px`;
  canvas.style.height = `${height}px`;
  canvas.width = Math.floor(width * dpr);
  canvas.height = Math.floor(height * dpr);

  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, width, height);

  if (!xValues.length || !curves.length) {
    ctx.fillStyle = "#666";
    ctx.font = "14px sans-serif";
    ctx.fillText("请在上方勾选要对比的曲线", 18, 28);
    return;
  }

  const transformY = (v) => {
    if (!Number.isFinite(v)) return null;
    if (!logY) return v;
    if (v <= 0) return null;
    return Math.log10(v);
  };

  const padL = 62;
  const padR = 22;
  const padT = 20;
  const padB = 44;
  const plotW = width - padL - padR;
  const plotH = height - padT - padB;
  const xMin = Math.min(...xValues);
  const xMax = Math.max(...xValues);
  let yMin = Number.POSITIVE_INFINITY;
  let yMax = Number.NEGATIVE_INFINITY;
  curves.forEach((curve) => {
    curve.values.forEach((v) => {
      const yv = transformY(v);
      if (!Number.isFinite(yv)) return;
      if (yv < yMin) yMin = yv;
      if (yv > yMax) yMax = yv;
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
  if (xMin === xMax) return;

  const xToPx = (x) => padL + ((x - xMin) / (xMax - xMin)) * plotW;
  const yToPx = (y) => padT + plotH - ((y - yMin) / (yMax - yMin)) * plotH;

  ctx.fillStyle = "#fff";
  ctx.fillRect(padL, padT, plotW, plotH);
  ctx.strokeStyle = "#e7decd";
  ctx.fillStyle = "#5a5349";
  ctx.font = "12px sans-serif";
  ctx.lineWidth = 1;
  const nY = 5;
  for (let i = 0; i <= nY; i += 1) {
    const ratio = i / nY;
    const y = padT + plotH * (1 - ratio);
    ctx.beginPath();
    ctx.moveTo(padL, y);
    ctx.lineTo(padL + plotW, y);
    ctx.stroke();
    const scaled = yMin + (yMax - yMin) * ratio;
    const label = logY ? fmtTick(10 ** scaled) : fmtTick(scaled);
    ctx.fillText(label, 8, y + 4);
  }
  const nX = 6;
  for (let i = 0; i <= nX; i += 1) {
    const ratio = i / nX;
    const x = padL + plotW * ratio;
    ctx.beginPath();
    ctx.moveTo(x, padT);
    ctx.lineTo(x, padT + plotH);
    ctx.stroke();
    const val = xMin + (xMax - xMin) * ratio;
    ctx.fillText(fmtTick(val), x - 18, padT + plotH + 18);
  }
  ctx.strokeStyle = "#9f9380";
  ctx.lineWidth = 1.2;
  ctx.beginPath();
  ctx.moveTo(padL, padT);
  ctx.lineTo(padL, padT + plotH);
  ctx.lineTo(padL + plotW, padT + plotH);
  ctx.stroke();
  ctx.fillStyle = "#4c463e";
  ctx.font = "13px sans-serif";
  ctx.fillText(xName || "time", padL + plotW - 45, padT + plotH + 36);
  ctx.save();
  ctx.translate(18, padT + 16);
  ctx.rotate(-Math.PI / 2);
  ctx.fillText(logY ? `${yName} (log)` : yName, 0, 0);
  ctx.restore();

  curves.forEach((curve, idx) => {
    const color = PLOT_COLORS[idx % PLOT_COLORS.length];
    ctx.strokeStyle = color;
    ctx.lineWidth = 1.9;
    ctx.beginPath();
    let started = false;
    for (let i = 0; i < xValues.length; i += 1) {
      const yv = transformY(curve.values[i]);
      if (!Number.isFinite(yv)) {
        started = false;
        continue;
      }
      const x = xToPx(xValues[i]);
      const y = yToPx(yv);
      if (!started) {
        ctx.moveTo(x, y);
        started = true;
      } else {
        ctx.lineTo(x, y);
      }
    }
    ctx.stroke();
  });
}

function drawCarbonCompareECharts(xValues, curves, xName, yName = "number of molecules", logY = false) {
  const chartHost = q("carbonPlotChart");
  const canvas = q("carbonPlotCanvas");
  chartHost.style.display = "block";
  canvas.style.display = "none";
  if (!carbonPlotChart) carbonPlotChart = window.echarts.init(chartHost);
  if (!xValues.length || !curves.length) {
    carbonPlotChart.clear();
    carbonPlotChart.setOption({
      title: {
        text: "请在上方勾选要对比的曲线",
        left: "center",
        top: "middle",
        textStyle: { fontSize: 16, color: "#7a7267" },
      },
    });
    return;
  }
  const series = curves.map((curve) => ({
    name: curve.name,
    type: "line",
    smooth: false,
    showSymbol: false,
    emphasis: { focus: "series" },
    data: xValues.map((x, idx) => {
      const y = curve.values[idx];
      if (logY && (!(Number.isFinite(y)) || y <= 0)) return [x, null];
      return [x, Number.isFinite(y) ? y : null];
    }),
  }));
  carbonPlotChart.setOption(
    {
      color: PLOT_COLORS,
      animation: false,
      grid: { left: 64, right: 20, top: 24, bottom: 62 },
      tooltip: {
        trigger: "axis",
        axisPointer: { type: "cross" },
      },
      xAxis: {
        type: "value",
        name: xName || "time",
        nameLocation: "end",
        nameGap: 16,
      },
      yAxis: {
        type: logY ? "log" : "value",
        name: logY ? `${yName} (log)` : yName,
        nameLocation: "end",
      },
      dataZoom: [
        { type: "inside", xAxisIndex: 0 },
        { type: "slider", xAxisIndex: 0, bottom: 18, height: 18 },
      ],
      series,
    },
    true
  );
}

async function drawCarbonCompare(xValues, curves, xName) {
  const logY = !!q("qCarbonCompareLogY").checked;
  const hasECharts = await ensureECharts();
  if (hasECharts) {
    drawCarbonCompareECharts(xValues, curves, xName, "number of molecules", logY);
    return;
  }
  drawCarbonCompareCanvas(xValues, curves, xName, "number of molecules", logY);
}

function resetCarbonInteractive() {
  state.carbonPlot.formulaIndex = [];
  state.carbonPlot.formulaLookup = null;
  state.carbonPlot.baseRows = [];
  state.carbonPlot.compareRows = [];
  state.carbonPlot.compareXValues = [];
  state.carbonPlot.compareCurves = [];
  state.carbonPlot.selectedSeriesKeys = [];
  state.carbonPlot.dragMergeItem = null;
  state.carbonPlot.mergeBasket = [];
  q("carbonCurveTools").classList.add("hidden");
  q("carbonCompareWrap").classList.add("hidden");
  q("carbonCurveList").innerHTML = "";
  q("qCarbonCurveFilter").value = "";
  q("qCarbonCurveMerge").value = "";
  updateCarbonMergeDropZone();
  q("qCarbonCompareLogY").checked = false;
  if (carbonPlotChart) {
    carbonPlotChart.clear();
  }
  const canvas = q("carbonPlotCanvas");
  const chart = q("carbonPlotChart");
  canvas.style.display = "none";
  chart.style.display = "none";
}

async function rebuildCarbonInteractiveCompare() {
  const baseRows = state.carbonPlot.baseRows || [];
  if (!baseRows.length) {
    resetCarbonInteractive();
    return;
  }
  let mergeSpecs = [];
  const mergeText = value("qCarbonCurveMerge");
  if (mergeText) {
    mergeSpecs = parseCarbonRangeSpecsClient(mergeText);
  }
  const mergedRows = mergeCarbonCompareRows(baseRows, mergeSpecs);
  const compare = buildCarbonCompareData(mergedRows, carbonTimeAxisLabel(state.carbonPlot.query));
  state.carbonPlot.compareRows = mergedRows;
  state.carbonPlot.compareXValues = compare.xValues;
  state.carbonPlot.compareCurves = compare.curves;
  syncCarbonSeriesSelection(compare.curves);
  renderCarbonCurveSelector();
  const selectedCurves = getSelectedCarbonCurves();
  await drawCarbonCompare(compare.xValues, selectedCurves, compare.xName || "time");
}

async function runCarbonInteractive(action) {
  try {
    await action();
  } catch (err) {
    setCarbonPlotMeta({
      query: state.carbonPlot.query || {},
      interactive_error: String(err),
    });
  }
}

async function initializeCarbonInteractive(plotRows, query) {
  const baseRows = normalizeCarbonPlotRows(plotRows, carbonTimeColumn(query));
  state.carbonPlot.baseRows = baseRows;
  state.carbonPlot.query = query || {};
  if (!baseRows.length) {
    resetCarbonInteractive();
    return;
  }
  q("carbonCurveTools").classList.remove("hidden");
  q("carbonCompareWrap").classList.remove("hidden");
  await rebuildCarbonInteractiveCompare();
}

function updateCarbonSelection(mode) {
  const curves = state.carbonPlot.compareCurves || [];
  if (!curves.length) return;
  if (mode === "all") {
    state.carbonPlot.selectedSeriesKeys = curves.map((curve) => curve.key);
  } else if (mode === "none") {
    state.carbonPlot.selectedSeriesKeys = [];
  } else if (mode === "top12") {
    state.carbonPlot.selectedSeriesKeys = [...curves]
      .sort((a, b) => b.max_value - a.max_value)
      .slice(0, Math.min(12, curves.length))
      .map((curve) => curve.key);
  }
  renderCarbonCurveSelector();
  drawCarbonCompare(state.carbonPlot.compareXValues || [], getSelectedCarbonCurves(), carbonTimeAxisLabel(state.carbonPlot.query));
}

function clearCarbonDragState() {
  document.querySelectorAll(".carbon-merge-draggable.is-dragging, .carbon-curve-group.is-drop-target").forEach((el) => {
    el.classList.remove("is-dragging", "is-drop-target");
  });
  const zone = q("carbonMergeDropZone");
  if (zone) zone.classList.remove("is-drop-target");
}

function readCarbonDragItem(event) {
  const transfer = event?.dataTransfer;
  if (!transfer) return state.carbonPlot.dragMergeItem || null;
  const raw = transfer.getData("application/x-reacnet-carbon-merge") || "";
  if (!raw) return state.carbonPlot.dragMergeItem || null;
  try {
    const parsed = JSON.parse(raw);
    return {
      key: String(parsed.key || ""),
      label: String(parsed.label || ""),
      ranges: normalizeCarbonMergeRanges(parsed.ranges || []),
    };
  } catch {
    return state.carbonPlot.dragMergeItem || null;
  }
}

function bindCarbonDragMerge() {
  const list = q("carbonCurveList");
  const zone = q("carbonMergeDropZone");
  if (!list || !zone) return;

  list.addEventListener("dragstart", (event) => {
    const target = event.target;
    if (!(target instanceof Element)) return;
    if (target.closest("input, button, select, textarea, a")) {
      event.preventDefault();
      return;
    }
    const itemEl = target.closest(".carbon-merge-draggable");
    const item = carbonMergeItemFromElement(itemEl);
    if (!item || !event.dataTransfer) return;
    state.carbonPlot.dragMergeItem = item;
    itemEl.classList.add("is-dragging");
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("application/x-reacnet-carbon-merge", JSON.stringify(item));
    event.dataTransfer.setData("text/plain", item.label);
  });

  list.addEventListener("dragover", (event) => {
    const item = state.carbonPlot.dragMergeItem;
    if (!item) return;
    const target = event.target;
    if (!(target instanceof Element)) return;
    const targetEl = target.closest(".carbon-merge-draggable");
    document.querySelectorAll(".carbon-curve-group.is-drop-target").forEach((el) => el.classList.remove("is-drop-target"));
    if (!targetEl) return;
    event.preventDefault();
    if (event.dataTransfer) event.dataTransfer.dropEffect = "move";
    targetEl.classList.add("is-drop-target");
  });

  list.addEventListener("drop", (event) => {
    const target = event.target;
    if (!(target instanceof Element)) return;
    const targetEl = target.closest(".carbon-merge-draggable");
    const sourceItem = readCarbonDragItem(event);
    const targetItem = carbonMergeItemFromElement(targetEl);
    clearCarbonDragState();
    state.carbonPlot.dragMergeItem = null;
    if (!sourceItem || !targetItem || sourceItem.key === targetItem.key) return;
    event.preventDefault();
    applyCarbonMergeItems([sourceItem, targetItem]);
  });

  list.addEventListener("dragend", () => {
    clearCarbonDragState();
    state.carbonPlot.dragMergeItem = null;
  });

  zone.addEventListener("dragover", (event) => {
    if (!state.carbonPlot.dragMergeItem) return;
    event.preventDefault();
    if (event.dataTransfer) event.dataTransfer.dropEffect = "move";
    zone.classList.add("is-drop-target");
  });

  zone.addEventListener("dragleave", () => {
    zone.classList.remove("is-drop-target");
  });

  zone.addEventListener("drop", (event) => {
    const item = readCarbonDragItem(event);
    clearCarbonDragState();
    state.carbonPlot.dragMergeItem = null;
    if (!item) return;
    event.preventDefault();
    const basket = state.carbonPlot.mergeBasket || [];
    const exists = basket.some((entry) => entry.key === item.key);
    if (!exists) basket.push(item);
    state.carbonPlot.mergeBasket = basket;
    if (basket.length >= 2) {
      applyCarbonMergeItems(basket);
      return;
    }
    updateCarbonMergeDropZone();
  });
}

function resetPlotInteractive() {
  state.plot.selectedSeriesKeys = [];
  state.plot.allCurves = [];
  state.plot.curves = [];
  state.plot.mappingRows = [];
  q("plotCurveTools").classList.add("hidden");
  q("plotCurveList").innerHTML = "";
  q("qPlotCurveFilter").value = "";
}

function syncPlotSeriesSelection(curves) {
  const available = new Set((curves || []).map((item) => item.name));
  const previous = new Set(state.plot.selectedSeriesKeys || []);
  const kept = [];
  previous.forEach((key) => {
    if (available.has(key)) kept.push(key);
  });
  if (!kept.length) {
    const top = [...(curves || [])]
      .sort((a, b) => (Number(b.max_value) || 0) - (Number(a.max_value) || 0))
      .slice(0, Math.min(12, curves.length))
      .map((item) => item.name);
    state.plot.selectedSeriesKeys = top;
    return;
  }
  state.plot.selectedSeriesKeys = kept;
}

function getSelectedPlotCurves() {
  const selected = new Set(state.plot.selectedSeriesKeys || []);
  return (state.plot.allCurves || []).filter((curve) => selected.has(curve.name));
}

function plotCurveMembers(curve) {
  const curveName = String(curve?.name || "");
  const rows = (state.plot.mappingRows || []).filter((row) => String(row?.series_name || "") === curveName);
  if (!rows.length) {
    return (curve?.members || []).map((member) => ({
      key: String(member),
      label: String(member),
      score: 0,
      smiles: String(member),
    }));
  }
  const merged = new Map();
  rows.forEach((row) => {
    const formula = String(row?.formula || "").trim();
    const smiles = String(row?.smiles || "").trim();
    const label = formula && smiles && formula !== smiles ? `${formula} | ${smiles}` : (smiles || formula || String(row?.query || ""));
    if (!label) return;
    const tp = Number(row?.tp_total) || 0;
    const key = smiles || label;
    const existing = merged.get(key);
    if (!existing) {
      merged.set(key, { label, score: tp, smiles });
      return;
    }
    existing.score += tp;
    if (!existing.smiles && smiles) existing.smiles = smiles;
  });
  return Array.from(merged.entries())
    .map(([key, item]) => ({ key, label: item.label, score: item.score, smiles: item.smiles }))
    .sort((a, b) => {
      if (b.score !== a.score) return b.score - a.score;
      return a.label.localeCompare(b.label);
    });
}

function renderPlotCurveSelector() {
  const host = q("plotCurveList");
  const filterText = value("qPlotCurveFilter").toLowerCase();
  const selected = new Set(state.plot.selectedSeriesKeys || []);
  const curves = (state.plot.allCurves || []).filter((item) => {
    if (!filterText) return true;
    return String(item?.name || "").toLowerCase().includes(filterText);
  });
  host.innerHTML = "";
  if (!curves.length) {
    host.innerHTML = '<div class="carbon-curve-item">没有匹配的曲线</div>';
    return;
  }

  curves
    .slice()
    .sort((a, b) => {
      const sa = Number(a?.max_value) || 0;
      const sb = Number(b?.max_value) || 0;
      if (sb !== sa) return sb - sa;
      return String(a?.name || "").localeCompare(String(b?.name || ""));
    })
    .forEach((curve, idx) => {
      const details = document.createElement("details");
      details.className = "carbon-curve-group";
      const members = plotCurveMembers(curve);
      const shown = members.slice(0, 24);
      const summary = document.createElement("summary");
      summary.className = "carbon-group-summary";
      summary.innerHTML = `
        <span class="carbon-group-title">
          <label>
            <input class="plot-curve-toggle" type="checkbox" data-series-key="${escapeHtml(curve.name)}" ${selected.has(curve.name) ? "checked" : ""} />
            <strong>${escapeHtml(curve.name)}</strong>
          </label>
        </span>
        <span class="carbon-group-stats">
          <span><span class="legend-swatch" style="background:${PLOT_COLORS[idx % PLOT_COLORS.length]}"></span></span>
          <span>members=${members.length}</span>
          <span>max=${fmtTick(curve.max_value)}</span>
        </span>
      `;
      details.appendChild(summary);
      const body = document.createElement("div");
      body.className = "carbon-group-body";
      const box = document.createElement("div");
      box.className = "carbon-formula-box";
      if (!shown.length) {
        box.innerHTML = '<div class="carbon-formula-meta">无成员分子信息</div>';
      } else {
        box.innerHTML = `<div class="carbon-formula-meta">成员分子式/SMILES（显示前 ${shown.length}/${members.length}）</div>`;
        const tags = document.createElement("div");
        tags.className = "carbon-formula-tags";
        shown.forEach((item) => {
          const smiles = resolveStructureSmiles(item);
          const tag = document.createElement(smiles ? "button" : "span");
          tag.className = `carbon-formula-tag${smiles ? " formula-structure-btn" : " is-static"}`;
          if (smiles) {
            tag.type = "button";
            tag.dataset.smiles = smiles;
            tag.dataset.smilesPreview = encodeURIComponent(smiles);
            tag.title = `点击查看结构: ${smiles}`;
          }
          tag.innerHTML = `<code>${escapeHtml(item.label)}</code>`;
          tags.appendChild(tag);
        });
        box.appendChild(tags);
      }
      body.appendChild(box);
      details.appendChild(body);
      host.appendChild(details);
    });
}

function updatePlotSelection(mode) {
  const curves = state.plot.allCurves || [];
  if (!curves.length) return;
  if (mode === "all") {
    state.plot.selectedSeriesKeys = curves.map((curve) => curve.name);
  } else if (mode === "none") {
    state.plot.selectedSeriesKeys = [];
  } else if (mode === "top12") {
    state.plot.selectedSeriesKeys = [...curves]
      .sort((a, b) => (Number(b.max_value) || 0) - (Number(a.max_value) || 0))
      .slice(0, Math.min(12, curves.length))
      .map((curve) => curve.name);
  }
  const selected = getSelectedPlotCurves();
  state.plot.curves = selected;
  renderPlotCurveSelector();
  drawPlot(state.plot.xValues, selected, state.plot.xName, state.plot.yName);
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
let carbonPlotChart = null;
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

function revokeCarbonPlotUrl() {
  if (state.carbonPlot.svgUrl) {
    URL.revokeObjectURL(state.carbonPlot.svgUrl);
    state.carbonPlot.svgUrl = "";
  }
}

function renderCarbonPlot(svgText) {
  const image = q("carbonPlotImage");
  revokeCarbonPlotUrl();
  state.carbonPlot.svgText = svgText || "";
  if (!svgText) {
    image.removeAttribute("src");
    image.alt = "无图像";
    q("btnCarbonPlotExportCsv").disabled = true;
    q("btnCarbonPlotExportSvg").disabled = true;
    return;
  }
  const blob = new Blob([svgText], { type: "image/svg+xml;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  state.carbonPlot.svgUrl = url;
  image.src = url;
  image.alt = "carbon-number evolution plot";
  q("btnCarbonPlotExportCsv").disabled = !(state.carbonPlot.plotData || []).length;
  q("btnCarbonPlotExportSvg").disabled = false;
}

function exportCarbonPlotCsv() {
  const rows = state.carbonPlot.plotData || [];
  if (!rows.length) return;
  const cols = Array.from(
    rows.reduce((acc, row) => {
      Object.keys(row || {}).forEach((key) => acc.add(key));
      return acc;
    }, new Set())
  );
  const lines = [cols.map(csvEscape).join(",")];
  rows.forEach((row) => {
    lines.push(cols.map((col) => csvEscape(row[col])).join(","));
  });
  const blob = new Blob([lines.join("\n")], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  const stamp = new Date().toISOString().replace(/[.:]/g, "-");
  a.href = url;
  a.download = `rng_carbon_plot_${stamp}.csv`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

function exportCarbonPlotSvg() {
  if (!state.carbonPlot.svgText) return;
  const blob = new Blob([state.carbonPlot.svgText], { type: "image/svg+xml;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  const stamp = new Date().toISOString().replace(/[.:]/g, "-");
  a.href = url;
  a.download = `rng_carbon_plot_${stamp}.svg`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

async function runSpecies() {
  openQueryModule("general");
  setGeneralQueryMode("formula");
  setResultData("species", { meta: { status: "running", module: resultModuleLabel("species") }, rows: [] });
  const data = await fetchJson("/api/species", {
    reac: globalReac(),
    min_tp: globalMinTp(),
    formula: value("qFormula"),
    top: value("qSpeciesTop") || 20,
  });
  setResultData("species", { meta: { query: data.query, meta: data.meta }, rows: data.rows || [] });
  focusResultModule("species");
}

async function runMass() {
  openQueryModule("general");
  setGeneralQueryMode("mass");
  setResultData("mass", { meta: { status: "running", module: resultModuleLabel("mass") }, rows: [] });
  const data = await fetchJson("/api/species_mass", {
    reac: globalReac(),
    min_tp: globalMinTp(),
    mass: value("qMass"),
    mode: q("qMassMode").value,
    tol: value("qMassTol"),
    top: value("qMassTop") || 50,
  });
  setResultData("mass", { meta: { query: data.query, meta: data.meta }, rows: data.rows || [] });
  focusResultModule("mass");
}

async function runNext() {
  openQueryModule("reaction");
  openReactionTool("next");
  setResultData("next", { meta: { status: "running", module: resultModuleLabel("next") }, rows: [] });
  const data = await fetchJson("/api/next", {
    reac: globalReac(),
    min_tp: globalMinTp(),
    start: value("qSmiles"),
    role: q("qRole").value,
    top: value("qNextTop") || 20,
    net_positive_only: q("qNetPositive").checked ? 1 : 0,
  });
  setResultData("next", { meta: { query: data.query, meta: data.meta }, rows: data.rows || [] });
  focusResultModule("next");
}

async function runIntermediate() {
  openQueryModule("intermediate");
  setResultData("intermediate", { meta: { status: "starting", module: resultModuleLabel("intermediate") }, rows: [] });
  const params = {
    reac: globalReac(),
    min_tp: globalMinTp(),
    species_file: effectiveSpeciesFile("qInterSpeciesFile"),
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
  };
  setIntermediateProgress("Queued", 0, "等待后台开始读取 species 文件", true);
  let taskId = "";
  try {
    const started = await fetchJson("/api/intermediate_candidates_start", params);
    taskId = started.task_id;
    state.intermediateTaskId = taskId;
    const data = await waitTaskResult(taskId, {
      pollMs: 700,
      onProgress: (task) => {
        if (state.intermediateTaskId !== taskId) return;
        setIntermediateProgress(
          task.phase || task.status || "running",
          task.progress_pct ?? (Number(task.progress || 0) * 100),
          task.message || "",
          task.status !== "completed" && task.status !== "error"
        );
      },
    });
    if (state.intermediateTaskId !== taskId) return;
    setResultData("intermediate", { meta: { query: data.query, meta: data.meta, task_id: taskId }, rows: data.rows || [] });
    focusResultModule("intermediate");
    setIntermediateProgress("Completed", 100, "筛选完成", false);
  } catch (err) {
    const task = err && err.task ? err.task : null;
    setIntermediateProgress("Error", task?.progress_pct || 0, task?.error || String(err), false);
    throw err;
  }
}

async function runRxnFormula() {
  openQueryModule("reaction");
  openReactionTool("rxn");
  setResultData("rxn", { meta: { status: "running", module: resultModuleLabel("rxn") }, rows: [] });
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
  setResultData("rxn", { meta: { query: data.query, meta: data.meta }, rows: data.rows || [] });
  focusResultModule("rxn");
}

function contextBaseParams() {
  return {
    reac: globalReac(),
    species_file: effectiveSpeciesFile("qContextSpeciesFile"),
    target: value("qContextTarget"),
    match_mode: q("qContextMatchMode").value,
  };
}

function buildContextSpeciesLocateParams(overrides = {}) {
  return {
    ...contextBaseParams(),
    event_mode: selectedEventModeValue("qContextEventMode", "qContextEventModeAdvanced"),
    before_frames: value("qContextBefore") || 3,
    after_frames: value("qContextAfter") || 3,
    max_events: value("qContextMaxEvents") || 12,
    include_trajectory: 0,
    include_route_trace: 1,
    ...overrides,
  };
}

function buildContextReactionLocateParams(overrides = {}) {
  return {
    reac: globalReac(),
    species_file: effectiveSpeciesFile("qContextSpeciesFile"),
    trajectory_file: effectiveTrajectoryFile("qContextTrajectoryFile"),
    route_file: effectiveRouteFile("qContextRouteFile"),
    reaction_smiles: value("qContextReactionLocateSmiles"),
    before_frames: value("qContextReactionBefore") || 5,
    after_frames: value("qContextReactionAfter") || 5,
    max_events: value("qContextReactionMaxEvents") || 12,
    type_element_map: value("qContextTypeElementMap"),
    ...overrides,
  };
}

function contextExtractBaseParams() {
  return {
    reac: globalReac(),
    species_file: effectiveSpeciesFile("qContextSpeciesFile"),
    target: value("qContextTarget"),
    match_mode: q("qContextMatchMode").value,
  };
}

function buildContextExtractParams(overrides = {}) {
  const scope = overrides.trajectory_atom_scope || q("qContextTrajectoryAtomScope").value || "event";
  const manualAtomIds = value("qContextAtomIds");
  let includeRouteTrace = overrides.include_route_trace == null
    ? (q("qContextIncludeRouteTrace").checked ? 1 : 0)
    : overrides.include_route_trace;
  if (String(scope) === "event" && !String(manualAtomIds || "").trim()) {
    includeRouteTrace = 1;
  }
  return {
    ...contextExtractBaseParams(),
    trajectory_file: effectiveTrajectoryFile("qContextTrajectoryFile"),
    route_file: effectiveRouteFile("qContextRouteFile"),
    reaction_smiles: value("qContextReactionSmiles"),
    frame_ranges: value("qContextFrameRanges"),
    atom_ids: manualAtomIds,
    type_element_map: value("qContextTypeElementMap"),
    include_trajectory: 1,
    include_route_trace: includeRouteTrace,
    inline_viewer: q("qContextInlineViewer").checked ? 1 : 0,
    route_atom_sample_limit: value("qContextRouteSampleLimit") || 80,
    trajectory_atom_scope: scope,
    ...overrides,
  };
}

function clearContextSelectedEvent({ render = true } = {}) {
  state.contextExtract.selectedEventRow = null;
  state.contextExtract.selectedEventConfig = null;
  if (render) renderResultPanels();
}

function loadContextSelectedEvent(row, config = {}) {
  state.contextExtract.selectedEventRow = row ? { ...row } : null;
  state.contextExtract.selectedEventConfig = row
      ? {
        source: config.source || "species",
        source_label: config.source_label || "物种事件",
        species_file: config.species_file || effectiveSpeciesFile("qContextSpeciesFile"),
        target: config.target || value("qContextTarget"),
        match_mode: config.match_mode || q("qContextMatchMode").value,
        event_mode: config.event_mode || q("qContextEventMode").value,
        before_frames: config.before_frames || value("qContextBefore") || 3,
        after_frames: config.after_frames || value("qContextAfter") || 3,
        reaction_smiles: config.reaction_smiles || "",
        reaction_formulas: config.reaction_formulas || "",
        event_id: config.event_id || row?.event_id || "",
        selected_event_class: config.selected_event_class || row?.selected_event_class || "",
      }
    : null;
  renderResultPanels();
}

function resetContextExtractPayload({ clearRows = false, clearSelection = false, status = "idle" } = {}) {
  state.contextExtract.taskId = "";
  state.contextExtract.atomGroups = null;
  state.contextExtract.frameRows = [];
  state.contextExtract.trajectoryText = "";
  state.contextExtract.trajectoryPreviewText = "";
  state.contextExtract.trajectoryFilename = "";
  state.contextExtract.framesFilename = "";
  state.contextExtract.trajectoryPath = "";
  state.contextExtract.vmdScriptPath = "";
  state.contextExtract.typeMapPath = "";
  state.contextExtract.snapshotItems = [];
  resetContextTrajectoryViewer();
  if (clearSelection) {
    clearContextSelectedEvent();
  }
  if (clearRows) {
    setResultMeta("context_extract", { status, module: resultModuleLabel("context_extract") });
    setResultRows("context_extract", []);
  } else {
    setResultMeta("context_extract", { status, module: resultModuleLabel("context_extract") });
  }
}

function resetContextLocatePayload(moduleKey, { clearRows = false } = {}) {
  if (moduleKey === "context_species") {
    state.contextSpeciesTaskId = "";
  } else if (moduleKey === "context_reaction") {
    state.contextReactionTaskId = "";
  }
  if (clearRows) {
    setResultMeta(moduleKey, { status: "starting", module: resultModuleLabel(moduleKey) });
    setResultRows(moduleKey, []);
  } else {
    setResultMeta(moduleKey, { status: "starting", module: resultModuleLabel(moduleKey) });
  }
}

async function runContextLocateTask({ moduleKey, params, setProgress, taskKey, startPath = "/api/structure_context_start" }) {
  openQueryModule("context");
  resetContextLocatePayload(moduleKey, { clearRows: true });
  setProgress("Queued", 0, "等待后台开始定位事件", true);
  let taskId = "";
  try {
    const started = await fetchJson(startPath, params);
    taskId = started.task_id;
    state[taskKey] = taskId;
    const data = await waitTaskResult(taskId, {
      pollMs: 700,
      onProgress: (task) => {
        if (state[taskKey] !== taskId) return;
        setProgress(
          task.phase || task.status || "running",
          task.progress_pct ?? (Number(task.progress || 0) * 100),
          task.message || "",
          task.status !== "completed" && task.status !== "error"
        );
      },
    });
    if (state[taskKey] !== taskId) return data;
    setResultData(moduleKey, {
      meta: {
        query: data.query,
        meta: data.meta,
        task_id: taskId,
        status: data?.meta?.status || "ok",
        candidate_rows: data.candidate_rows || [],
        discarded_rows: data.discarded_rows || [],
      },
      rows: data.rows || [],
    });
    focusResultModule(moduleKey);
    setProgress("Completed", 100, data?.meta?.message || "事件定位完成", false);
    return data;
  } catch (err) {
    const task = err && err.task ? err.task : null;
    setProgress("Error", task?.progress_pct || 0, task?.error || String(err), false);
    throw err;
  }
}

async function runContextLocateSpecies() {
  clearContextSelectedEvent();
  resetContextExtractPayload({ clearRows: true, status: "idle" });
  return runContextLocateTask({
    moduleKey: "context_species",
    params: buildContextSpeciesLocateParams(),
    setProgress: setContextSpeciesProgress,
    taskKey: "contextSpeciesTaskId",
  });
}

async function runContextLocateReaction() {
  clearContextSelectedEvent();
  resetContextExtractPayload({ clearRows: true, status: "idle" });
  return runContextLocateTask({
    moduleKey: "context_reaction",
    params: buildContextReactionLocateParams(),
    setProgress: setContextReactionProgress,
    taskKey: "contextReactionTaskId",
    startPath: "/api/reaction_event_locate_start",
  });
}

async function runContextExtract(mode = "auto") {
  const extractMode = String(mode || "auto").trim().toLowerCase() === "manual" ? "manual" : "auto";
  const manualFrameRanges = value("qContextFrameRanges");
  const selectedRow = state.contextExtract.selectedEventRow;
  const selectedConfig = state.contextExtract.selectedEventConfig || {};
  if (extractMode === "auto") {
    if (!selectedRow) {
      throw new Error("请先在 Step 1 的“反应事件候选实例”中载入一条事件，再使用自动提取。");
    }
  } else if (!manualFrameRanges) {
    throw new Error("手工提取模式需要填写 Frame Ranges。");
  }

  const overrides = {};
  const useReactionFirst = extractMode === "auto" && !!String(selectedRow?.event_id || "").trim();
  if (extractMode === "auto" && !useReactionFirst) {
    const anchorFrame = Number(selectedRow.anchor_frame ?? selectedRow.first_frame_found);
    if (!Number.isFinite(anchorFrame)) {
      throw new Error("已选事件缺少 anchor frame，无法提取");
    }
    overrides.anchor_frame = Math.trunc(anchorFrame);
    overrides.frame_ranges = "";
    overrides.max_events = 1;
    overrides.target = selectedConfig.target || value("qContextTarget");
    overrides.species_file = selectedConfig.species_file || effectiveSpeciesFile("qContextSpeciesFile");
    overrides.match_mode = selectedConfig.match_mode || q("qContextMatchMode").value;
    overrides.before_frames = selectedConfig.before_frames || value("qContextBefore") || 3;
    overrides.after_frames = selectedConfig.after_frames || value("qContextAfter") || 3;
    overrides.reaction_smiles = selectedConfig.reaction_smiles || value("qContextReactionSmiles");
    overrides.include_route_trace = 1;
    overrides.atom_ids = "";
  }

  let params = buildContextExtractParams(overrides);
  let startPath = "/api/structure_context_start";
  if (useReactionFirst) {
    startPath = "/api/reaction_event_extract_start";
    params = {
      reac: globalReac(),
      species_file: selectedConfig.species_file || effectiveSpeciesFile("qContextSpeciesFile"),
      trajectory_file: effectiveTrajectoryFile("qContextTrajectoryFile"),
      route_file: effectiveRouteFile("qContextRouteFile"),
      reaction_smiles: selectedConfig.reaction_smiles || selectedRow.reaction_smiles || value("qContextReactionLocateSmiles") || value("qContextReactionSmiles"),
      reaction_formulas: selectedConfig.reaction_formulas || selectedRow.reaction_formulas || "",
      event_id: selectedRow.event_id,
      selected_event_class: selectedConfig.selected_event_class || selectedRow.selected_event_class || "",
      before_frames: selectedConfig.before_frames || value("qContextReactionBefore") || 5,
      after_frames: selectedConfig.after_frames || value("qContextReactionAfter") || 5,
      max_events: value("qContextReactionMaxEvents") || 12,
      type_element_map: value("qContextTypeElementMap"),
      inline_viewer: q("qContextInlineViewer").checked ? 1 : 0,
    };
  }
  openQueryModule("context");
  resetContextExtractPayload({ clearRows: true });
  setContextExtractProgress("Queued", 0, "等待后台开始提取轨迹", true);
  let taskId = "";
  try {
    const started = await fetchJson(startPath, params);
    taskId = started.task_id;
    state.contextExtract.taskId = taskId;
    const data = await waitTaskResult(taskId, {
      pollMs: 700,
      onProgress: (task) => {
        if (state.contextExtract.taskId !== taskId) return;
        setContextExtractProgress(
          task.phase || task.status || "running",
          task.progress_pct ?? (Number(task.progress || 0) * 100),
          task.message || "",
          task.status !== "completed" && task.status !== "error"
        );
      },
    });
    if (state.contextExtract.taskId !== taskId) return data;
    state.contextExtract.frameRows = data.frame_rows || [];
    state.contextExtract.atomGroups = data.atom_groups || null;
    state.contextExtract.trajectoryText = data.trajectory_text || "";
    state.contextExtract.trajectoryPreviewText = data.trajectory_preview_text || data.trajectory_text || "";
    state.contextExtract.trajectoryFilename = data?.suggested_files?.trajectory || "";
    state.contextExtract.framesFilename = data?.suggested_files?.frames_csv || "";
    state.contextExtract.trajectoryPath = data.trajectory_saved_path || data?.meta?.trajectory_saved_path || "";
    state.contextExtract.vmdScriptPath = data.vmd_script_saved_path || data?.meta?.vmd_script_saved_path || "";
    state.contextExtract.typeMapPath = data.type_map_saved_path || data?.meta?.type_map_saved_path || "";
    state.contextExtract.snapshotItems = Array.isArray(data.snapshot_items) ? data.snapshot_items : [];
    const refreshedSelectedRow = extractMode === "auto" && selectedRow
      ? ((data.rows || []).find((row) => sameContextEventRow(row, selectedRow))
        || data.selected_event
        || null)
      : null;
    if (refreshedSelectedRow) {
      loadContextSelectedEvent(refreshedSelectedRow, selectedConfig);
      const frameRangesInput = q("qContextFrameRanges");
      if (frameRangesInput instanceof HTMLTextAreaElement) {
        frameRangesInput.value = contextRowDefaultFrameRangeText(refreshedSelectedRow);
      }
      const atomIdsInput = q("qContextAtomIds");
      if (atomIdsInput instanceof HTMLTextAreaElement) {
        atomIdsInput.value = resolveContextEventAtomIds(refreshedSelectedRow).text;
      }
    }
    setResultData("context_extract", {
      meta: { query: data.query, meta: data.meta, task_id: taskId },
      rows: data.rows || [],
    });
    focusResultModule("context_extract");
    setContextExtractProgress("Completed", 100, data?.meta?.message || "轨迹提取完成", false);
    return data;
  } catch (err) {
    const task = err && err.task ? err.task : null;
    setContextExtractProgress("Error", task?.progress_pct || 0, task?.error || String(err), false);
    throw err;
  }
}

async function runContextExtractAndOpen(mode = "ovito") {
  const selectedResolution = resolveContextEventResolution(state.contextExtract.selectedEventRow);
  if (state.contextExtract.selectedEventRow && !selectedResolution.step2Visualizable) {
    throw new Error(`当前已选事件属于“${selectedResolution.label}”，不能直接作为严格反应事件打开 OVITO/VMD。请改选主表中的严格事件，或先按候选过程导出后手工核查。`);
  }
  await runContextExtract("auto");
  if (!state.contextExtract.trajectoryPath) {
    throw new Error("事件子轨迹已提取，但没有生成可供外部程序打开的轨迹文件。请检查 trajectory 输出设置。");
  }
  await openContextTrajectoryPath(mode);
}

function focusContextExtractCard() {
  const card = q("card-context-extract");
  if (!card) return;
  if (window.innerWidth <= WORKBENCH_STACK_BREAKPOINT) {
    card.scrollIntoView({ behavior: "smooth", block: "start" });
  }
  card.classList.remove("result-focus-flash");
  window.setTimeout(() => {
    card.classList.add("result-focus-flash");
    window.setTimeout(() => card.classList.remove("result-focus-flash"), 900);
  }, 0);
}

function focusContextReactionLocateCard() {
  const card = q("card-context-reaction-locate");
  if (!(card instanceof HTMLElement)) return;
  if (window.innerWidth <= WORKBENCH_STACK_BREAKPOINT) {
    card.scrollIntoView({ behavior: "smooth", block: "start" });
  }
  card.classList.remove("result-focus-flash");
  window.setTimeout(() => {
    card.classList.add("result-focus-flash");
    window.setTimeout(() => card.classList.remove("result-focus-flash"), 900);
  }, 0);
}

function loadContextRowForExtraction(row, config = {}) {
  if (!row) return;
  const frameRangesInput = q("qContextFrameRanges");
  if (frameRangesInput instanceof HTMLTextAreaElement) {
    frameRangesInput.value = contextRowDefaultFrameRangeText(row);
  }
  const atomIdsInput = q("qContextAtomIds");
  if (atomIdsInput instanceof HTMLTextAreaElement) {
    atomIdsInput.value = resolveContextEventAtomIds(row).text;
  }
  const routeTraceToggle = q("qContextIncludeRouteTrace");
  if (routeTraceToggle instanceof HTMLInputElement) {
    routeTraceToggle.checked = true;
  }
  const scopeSelect = q("qContextTrajectoryAtomScope");
  if (scopeSelect instanceof HTMLSelectElement) {
    scopeSelect.value = "event";
  }
  syncContextTrajectoryAtomScopeControl();
  const reactionInput = q("qContextReactionSmiles");
  if (reactionInput instanceof HTMLInputElement) {
    const rowReactionSmiles = String(config.reaction_smiles || row?.reaction_smiles || "").trim();
    if (rowReactionSmiles) {
      reactionInput.value = rowReactionSmiles;
    }
  }
  loadContextSelectedEvent(row, config);
  focusContextExtractCard();
}

async function runPlot() {
  resetPlotInteractive();
  setResultData("plot", { meta: { status: "starting", module: resultModuleLabel("plot") }, rows: [] });
  setPlotProgress("Queued", 0, "等待后台开始读取 species 文件", true);
  setPlotMeta({ status: "starting", mode: "species" });

  const targets = parseTargetsForPlot(value("qPlotTarget"));
  const speciesFilesText = value("qPlotSpeciesFiles");
  const params = {
    reac: globalReac(),
    min_tp: globalMinTp(),
    species_file: speciesFilesText ? "" : effectiveSpeciesFile("qPlotSpeciesFile"),
    species_files: speciesFilesText,
    target: targets,
    formula_mode: q("qPlotFormulaMode").value,
    x_axis: q("qPlotXAxis").value,
    time_align: q("qPlotTimeAlign").value,
    normalize: q("qPlotNormalize").value,
    smooth_window: value("qPlotSmooth") || 1,
    downsample: value("qPlotDownsample") || 1800,
  };
  let taskId = "";
  try {
    const started = await fetchJson("/api/evolution_plot_start", {
      plot_kind: "species",
      ...params,
    });
    taskId = started.task_id;
    state.plot.taskId = taskId;
    setPlotMeta({ status: "running", mode: "species", task_id: taskId, phase: "queued" });

    const data = await waitTaskResult(taskId, {
      pollMs: 450,
      onProgress: (task) => {
        if (state.plot.taskId !== taskId) return;
        setPlotProgress(
          task.phase || task.status || "running",
          task.progress_pct ?? (Number(task.progress || 0) * 100),
          task.message || "",
          task.status !== "completed" && task.status !== "error"
        );
        setPlotMeta({
          status: task.status,
          mode: "species",
          task_id: taskId,
          phase: task.phase || task.status,
          progress_pct: task.progress_pct,
          message: task.message || "",
        });
      },
    });
    if (state.plot.taskId !== taskId) return;

    // Left table shows formula->SMILES mapping for auditing
    const mappingRows = data.mapping || [];
    setResultData("plot", {
      meta: { query: data.query, mode: data.mode || "species", mapping_rows: mappingRows.length },
      rows: mappingRows,
    });
    focusResultModule("plot");
    setPlotMeta({ query: data.query, meta: data.meta, mode: data.mode || "species", task_id: taskId });

    const allCurves = (data.curves || []).map((c) => ({
      name: c.name,
      values: c.values || [],
      max_value: c.max_value ?? 0,
      members: c.members || [],
    }));
    const yName = data.query && ["initial", "max"].includes(data.query.normalize) ? "normalized_count" : "count";
    state.plot.xName = data.x_name || "x";
    state.plot.yName = yName;
    state.plot.xValues = data.x_values || [];
    state.plot.mappingRows = mappingRows;
    state.plot.allCurves = allCurves;
    syncPlotSeriesSelection(allCurves);
    const selected = getSelectedPlotCurves();
    state.plot.curves = selected;
    q("plotCurveTools").classList.remove("hidden");
    renderPlotCurveSelector();
    await drawPlot(state.plot.xValues, selected, state.plot.xName, state.plot.yName);
    setPlotProgress("Completed", 100, "读取与绘图完成", false);
  } catch (err) {
    const task = err && err.task ? err.task : null;
    setPlotProgress("Error", task?.progress_pct || 0, task?.error || String(err), false);
    setPlotMeta({ task_id: taskId || null, error: String(err), mode: "species" });
    throw err;
  }
}

async function runCarbonPlot() {
  resetCarbonInteractive();
  renderCarbonPlot("");
  renderCarbonPlotHighlights(null);
  setCarbonPlotMeta({ status: "starting", hint: "正在创建后台任务" });
  setCarbonPlotSummary({ status: "starting" });
  q("btnCarbonPlotExportCsv").disabled = true;
  q("btnCarbonPlotExportSvg").disabled = true;

  const speciesFilesText = value("qCarbonSpeciesFiles");
  const params = {
    reac: globalReac(),
    species_file: speciesFilesText ? "" : effectiveSpeciesFile("qCarbonSpeciesFile"),
    species_files: speciesFilesText,
    data: value("qCarbonData"),
    x_axis: q("qCarbonXAxis").value,
    timestep_ps: value("qCarbonTimestepPs") || 0.0001,
    time_align: q("qCarbonTimeAlign").value,
    mode: q("qCarbonMode").value,
    top_k: value("qCarbonTopK") || 12,
    max_exact_lines: value("qCarbonMaxExact") || 24,
    parent_carbon_number: value("qCarbonParent"),
    highlight_small: carbonRangeText("qCarbonSmall", "1-4"),
    highlight_large: value("qCarbonLarge") || 30,
    carbon_bins: value("qCarbonBins"),
    display_ranges: value("qCarbonDisplayRanges"),
    merge_ranges: value("qCarbonMergeRanges"),
    layout: q("qCarbonLayout").value,
    layout_regions: q("qCarbonLayout").value === "subplots" ? value("qCarbonRegions") : "",
    system_mode: q("qCarbonSystemMode").value,
    legend_mode: q("qCarbonLegend").value,
    theme: q("qCarbonTheme").value,
    smoothing: q("qCarbonSmoothing").value,
    smooth_window: value("qCarbonSmoothWindow") || 5,
    smooth_polyorder: value("qCarbonSmoothPolyorder") || 2,
    fig_width: value("qCarbonFigWidth") || 11.5,
    fig_height: value("qCarbonFigHeight") || 8.0,
    max_formula_list: value("qCarbonMaxFormula") || 30,
    max_points: 1200,
  };
  const started = await fetchJson("/api/evolution_plot_start", {
    plot_kind: "carbon",
    ...params,
  });
  const taskId = started.task_id;
  state.carbonPlot.taskId = taskId;
  setCarbonPlotProgress("Queued", 0, "等待后台开始读取 species 文件", true);
  try {
    const data = await waitTaskResult(taskId, {
      pollMs: 800,
      onProgress: (task) => {
        if (state.carbonPlot.taskId !== taskId) return;
        setCarbonPlotProgress(
          task.phase || task.status || "running",
          task.progress_pct ?? (Number(task.progress || 0) * 100),
          task.message || "",
          task.status !== "completed" && task.status !== "error"
        );
      },
    });
    if (state.carbonPlot.taskId !== taskId) return;

    state.carbonPlot.plotData = data.plot_data || [];
    state.carbonPlot.summary = data.summary || null;
    state.carbonPlot.query = data.query || {};
    state.carbonPlot.formulaIndex = data.carbon_formula_index || [];
    state.carbonPlot.formulaLookup = buildFormulaLookup(state.carbonPlot.formulaIndex);
    renderCarbonPlot(data.svg || "");
    renderCarbonPlotHighlights(data.summary || null);
    setCarbonPlotMeta({ query: data.query, meta: data.meta, mode: data.mode || "carbon", task_id: taskId });
    setCarbonPlotSummary(data.summary || { status: "no summary" });
    await initializeCarbonInteractive(data.plot_data || [], data.query || {});
    setCarbonPlotProgress("Completed", 100, "读取与绘图完成", false);
  } catch (err) {
    const task = err && err.task ? err.task : null;
    setCarbonPlotMeta({ task_id: taskId, error: String(err) });
    setCarbonPlotSummary({ status: "error", error: String(err) });
    setCarbonPlotProgress("Error", task?.progress_pct || 0, task?.error || String(err), false);
    throw err;
  }
}

function transitionLabel(species, fallbackIndex = 0) {
  const formula = String(species?.formula || "?");
  return `${formula} · #${Number(species?.rank || fallbackIndex + 1)}`;
}

function shortText(text, limit = 42) {
  const raw = String(text || "");
  return raw.length > limit ? `${raw.slice(0, Math.max(1, limit - 3))}...` : raw;
}

function renderTransitionStats(data) {
  const meta = data?.meta || {};
  const stats = [
    ["物种", `${meta.n_species_displayed || 0}/${meta.n_species_total || 0}`],
    ["事件", Number(meta.total_events || 0).toLocaleString()],
    ["非零通道", Number(meta.nonzero_events || 0).toLocaleString()],
    ["矩阵密度", `${(Number(meta.density || 0) * 100).toFixed(2)}%`],
  ];
  q("transitionStats").innerHTML = stats
    .map(([label, value]) => `<span class="stat-chip"><strong>${escapeHtml(label)}</strong> ${escapeHtml(value)}</span>`)
    .join("");
}

function renderTransitionSelection(selection = null) {
  const box = q("transitionSelection");
  const data = state.transition.data;
  if (!data) {
    box.textContent = "点击矩阵单元或网络节点查看通量详情";
    return;
  }
  if (!selection) {
    const lead = (data.species || [])[0];
    if (!lead) {
      box.textContent = "当前筛选没有可显示的物种";
      return;
    }
    box.innerHTML = `<strong>${escapeHtml(transitionLabel(lead))}</strong><code>${escapeHtml(lead.smiles)}</code><span>入流 ${Number(lead.incoming).toLocaleString()} · 出流 ${Number(lead.outgoing).toLocaleString()}</span>`;
    return;
  }
  if (selection.kind === "edge") {
    box.innerHTML = `<strong>${escapeHtml(`${selection.source_formula} → ${selection.target_formula}`)}</strong><code>${escapeHtml(selection.source)}</code><span class="transition-arrow">→</span><code>${escapeHtml(selection.target)}</code><span>${Number(selection.count).toLocaleString()} events</span>`;
    return;
  }
  const item = selection.species || selection;
  box.innerHTML = `<strong>${escapeHtml(transitionLabel(item))}</strong><code>${escapeHtml(item.smiles)}</code><span>入流 ${Number(item.incoming).toLocaleString()} · 出流 ${Number(item.outgoing).toLocaleString()} · 总通量 ${Number(item.total).toLocaleString()}</span>`;
}

function renderTransitionEdgeTable(data) {
  const table = q("transitionEdgeTable");
  const thead = table.querySelector("thead");
  const tbody = table.querySelector("tbody");
  const edges = data?.edges || [];
  thead.innerHTML = "<tr><th>#</th><th>来源</th><th>目标</th><th>事件数</th></tr>";
  tbody.innerHTML = edges
    .map((edge, index) => `
      <tr data-transition-edge="${index}">
        <td>${index + 1}</td>
        <td><strong>${escapeHtml(edge.source_formula)}</strong><code title="${escapeHtml(edge.source)}">${escapeHtml(shortText(edge.source, 42))}</code></td>
        <td><strong>${escapeHtml(edge.target_formula)}</strong><code title="${escapeHtml(edge.target)}">${escapeHtml(shortText(edge.target, 42))}</code></td>
        <td><strong>${Number(edge.count).toLocaleString()}</strong></td>
      </tr>
    `)
    .join("");
  q("btnTransitionExport").disabled = !edges.length;
}

function transitionSpeciesByLabel(label) {
  return (state.transition.data?.species || []).find((item) => item.smiles === label) || null;
}

function renderTransitionHeatmap(data) {
  const host = q("transitionChart");
  if (!window.echarts) return false;
  const chart = window.echarts.getInstanceByDom(host) || window.echarts.init(host);
  const labels = data.labels || [];
  const matrix = data.matrix || [];
  const minCount = Math.max(0, Number(data.query?.min_count || 0));
  const points = [];
  let maxValue = 1;
  matrix.forEach((row, y) => row.forEach((count, x) => {
    const numeric = Number(count) || 0;
    if (numeric > maxValue) maxValue = numeric;
    if (numeric >= minCount && numeric > 0) points.push([x, y, numeric, Math.log10(numeric + 1)]);
  }));
  const maxLog = Math.log10(maxValue + 1);
  chart.setOption({
    animation: false,
    grid: { left: 76, right: 30, top: 26, bottom: 72 },
    tooltip: {
      position: "top",
      formatter: (params) => {
        const [x, y, _logCount, count] = params.data || [];
        const source = transitionSpeciesByLabel(labels[y]) || {};
        const target = transitionSpeciesByLabel(labels[x]) || {};
        return `<strong>${escapeHtml(source.formula || "?")} → ${escapeHtml(target.formula || "?")}</strong><br/>${Number(count).toLocaleString()} events`;
      },
    },
    xAxis: {
      type: "category",
      data: labels,
      name: "目标物种",
      axisLabel: { interval: 0, rotate: 55, formatter: (label) => transitionSpeciesByLabel(label)?.formula || "?" },
      splitArea: { show: true },
    },
    yAxis: {
      type: "category",
      data: labels,
      inverse: true,
      name: "来源物种",
      axisLabel: { formatter: (label) => transitionSpeciesByLabel(label)?.formula || "?" },
      splitArea: { show: true },
    },
    visualMap: {
      min: 0,
      max: maxLog,
      calculable: true,
      orient: "horizontal",
      left: "center",
      bottom: 6,
      text: ["高", "低"],
      formatter: (value) => Math.max(1, Math.round((10 ** value) - 1)).toLocaleString(),
      inRange: { color: ["#f0eee8", "#4ca587", "#d86f2c"] },
    },
    series: [{
      type: "heatmap",
      data: points.map((item) => [item[0], item[1], item[3], item[2]]),
      encode: { x: 0, y: 1, value: 2 },
      label: { show: labels.length <= 24, formatter: (params) => Number(params.data?.[3] || 0) || "" },
      emphasis: { itemStyle: { shadowBlur: 10, shadowColor: "rgba(0,0,0,0.25)" } },
    }],
  }, true);
  chart.off("click");
  chart.on("click", (params) => {
    const [x, y, _logCount, count] = params.data || [];
    const edge = {
      kind: "edge",
      source: labels[y],
      target: labels[x],
      source_formula: transitionSpeciesByLabel(labels[y])?.formula || "?",
      target_formula: transitionSpeciesByLabel(labels[x])?.formula || "?",
      count: Number(count) || 0,
    };
    state.transition.selected = edge;
    renderTransitionSelection(edge);
  });
  return true;
}

function renderTransitionNetwork(data) {
  const host = q("transitionChart");
  if (!window.echarts) return false;
  const chart = window.echarts.getInstanceByDom(host) || window.echarts.init(host);
  const edges = data.edges || [];
  const connected = new Set(edges.flatMap((edge) => [edge.source, edge.target]));
  const nodes = (data.species || [])
    .filter((item) => connected.has(item.smiles))
    .map((item) => ({
      id: item.smiles,
      name: item.smiles,
      value: item.total,
      symbolSize: 14 + Math.min(34, Math.sqrt(Math.max(0, item.total)) * 0.55),
      label: { formatter: item.formula },
      species: item,
    }));
  const maxEdge = Math.max(1, ...edges.map((edge) => Number(edge.count) || 0));
  chart.setOption({
    animationDurationUpdate: 350,
    tooltip: {
      formatter: (params) => {
        if (params.dataType === "edge") return `${escapeHtml(params.data.source_formula)} → ${escapeHtml(params.data.target_formula)}<br/>${Number(params.data.count).toLocaleString()} events`;
        const item = params.data?.species || {};
        return `<strong>${escapeHtml(item.formula || "?")}</strong><br/>总通量 ${Number(item.total || 0).toLocaleString()}`;
      },
    },
    series: [{
      type: "graph",
      layout: "force",
      roam: true,
      draggable: true,
      force: { repulsion: 230, edgeLength: [55, 190], gravity: 0.08 },
      data: nodes,
      links: edges.map((edge) => ({
        ...edge,
        lineStyle: { width: 0.8 + 5 * Math.sqrt(Number(edge.count) / maxEdge), curveness: 0.08, opacity: 0.55 },
      })),
      edgeSymbol: ["none", "arrow"],
      edgeSymbolSize: [0, 7],
      label: { show: true, position: "right" },
      lineStyle: { color: "source" },
      emphasis: { focus: "adjacency", lineStyle: { opacity: 0.95 } },
    }],
  }, true);
  chart.off("click");
  chart.on("click", (params) => {
    if (params.dataType === "edge") {
      state.transition.selected = { kind: "edge", ...params.data };
    } else {
      state.transition.selected = { kind: "species", species: params.data?.species || {} };
    }
    renderTransitionSelection(state.transition.selected);
  });
  return true;
}

async function renderTransitionChart() {
  const data = state.transition.data;
  if (!data) return;
  const loaded = await ensureECharts();
  if (!loaded) {
    q("transitionChart").innerHTML = '<div class="context-storyboard-empty">ECharts 加载失败，仍可使用下方通道排行。</div>';
    return;
  }
  if (state.transition.mode === "network") renderTransitionNetwork(data);
  else renderTransitionHeatmap(data);
}

function setTransitionMode(mode) {
  state.transition.mode = mode === "network" ? "network" : "heatmap";
  const heatmap = state.transition.mode === "heatmap";
  q("btnTransitionHeatmap").classList.toggle("is-selected", heatmap);
  q("btnTransitionHeatmap").setAttribute("aria-pressed", heatmap ? "true" : "false");
  q("btnTransitionNetwork").classList.toggle("is-selected", !heatmap);
  q("btnTransitionNetwork").setAttribute("aria-pressed", heatmap ? "false" : "true");
  renderTransitionChart();
}

async function runTransitionTable() {
  q("btnTransitionLoad").disabled = true;
  q("transitionSourceLabel").textContent = "正在解析转移矩阵";
  q("transitionResultPanel").classList.remove("hidden");
  try {
    const data = await fetchJson("/api/transition_table", {
      table: value("qTransitionTable") || globalTableFile() || state.ui.dataset?.artifacts?.table?.path || "",
      max_species: value("qTransitionMaxSpecies") || 40,
      min_count: value("qTransitionMinCount") || 1,
      top_edges: value("qTransitionTopEdges") || 40,
    });
    state.transition.data = data;
    state.transition.selected = null;
    q("transitionSourceLabel").textContent = data.query?.table || "transition table";
    renderTransitionStats(data);
    renderTransitionSelection();
    renderTransitionEdgeTable(data);
    await renderTransitionChart();
  } finally {
    q("btnTransitionLoad").disabled = false;
  }
}

function exportTransitionEdges() {
  const edges = state.transition.data?.edges || [];
  if (!edges.length) return;
  const cols = ["source_formula", "source_smiles", "target_formula", "target_smiles", "count"];
  const lines = [cols.join(",")];
  edges.forEach((edge) => lines.push([
    edge.source_formula,
    edge.source,
    edge.target_formula,
    edge.target,
    edge.count,
  ].map(csvEscape).join(",")));
  const blob = new Blob([lines.join("\n")], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "rng_transition_edges.csv";
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

async function runWithToast(fn, moduleKey = null) {
  try {
    document.body.style.cursor = "progress";
    await fn();
  } catch (err) {
    if (moduleKey) {
      setResultData(moduleKey, {
        meta: { error: String(err), module: resultModuleLabel(moduleKey) },
        rows: [],
      });
    } else {
      patchActiveResultMeta({ error: String(err) });
    }
  } finally {
    document.body.style.cursor = "default";
  }
}

function bindEvents() {
  initSmilesHoverPreview();
  q("btnSpecies").addEventListener("click", () => runWithToast(runSpecies, "species"));
  q("btnMass").addEventListener("click", () => runWithToast(runMass, "mass"));
  q("btnNext").addEventListener("click", () => runWithToast(runNext, "next"));
  q("btnIntermediate").addEventListener("click", () => runWithToast(runIntermediate, "intermediate"));
  q("btnRxn").addEventListener("click", () => runWithToast(runRxnFormula, "rxn"));
  q("btnContextLocateSpecies").addEventListener("click", () => runWithToast(runContextLocateSpecies, "context_species"));
  q("btnContextLocateReaction").addEventListener("click", () => runWithToast(runContextLocateReaction, "context_reaction"));
  q("btnContextExtractAuto").addEventListener("click", () => runWithToast(() => runContextExtract("auto"), "context_extract"));
  q("btnContextExtractOpenOvito").addEventListener("click", () => runWithToast(() => runContextExtractAndOpen("ovito"), "context_extract"));
  q("btnContextExtractOpenVmd").addEventListener("click", () => runWithToast(() => runContextExtractAndOpen("vmd"), "context_extract"));
  q("btnContextExtractManual").addEventListener("click", () => runWithToast(() => runContextExtract("manual"), "context_extract"));
  q("btnPlot").addEventListener("click", () => runWithToast(runPlot, "plot"));
  q("btnCarbonPlot").addEventListener("click", () => runWithToast(runCarbonPlot));
  q("btnTransitionLoad").addEventListener("click", () => runWithToast(runTransitionTable));
  q("btnTransitionHeatmap").addEventListener("click", () => setTransitionMode("heatmap"));
  q("btnTransitionNetwork").addEventListener("click", () => setTransitionMode("network"));
  q("btnTransitionExport").addEventListener("click", exportTransitionEdges);
  q("btnRefreshDataset").addEventListener("click", () => runWithToast(() => refreshDatasetStatus()));
  q("workspaceNav").addEventListener("click", (event) => {
    const button = event.target.closest("[data-workspace-module]");
    if (!(button instanceof HTMLButtonElement)) return;
    setWorkspaceModule(button.dataset.workspaceModule || "dataset");
  });
  ["reacFile", "sharedSpeciesFile", "sharedTrajectoryFile", "sharedRouteFile", "sharedTableFile"].forEach((id) => {
    q(id).addEventListener("change", () => refreshDatasetStatus({ silent: true }).catch(() => {}));
  });
  q("transitionEdgeTable").addEventListener("click", (event) => {
    const row = event.target.closest("tr[data-transition-edge]");
    if (!row) return;
    const index = Number.parseInt(row.dataset.transitionEdge || "", 10);
    const edge = state.transition.data?.edges?.[index];
    if (!edge) return;
    state.transition.selected = { kind: "edge", ...edge };
    renderTransitionSelection(state.transition.selected);
  });

  document.querySelectorAll(".viewer-show-h").forEach((input) => {
    input.addEventListener("change", () => {
      const viewerKey = String(input.dataset.viewerKey || "general");
      renderStructurePreviewItems(viewerKey, state.structurePreviews[normalizeViewerKey(viewerKey)] || []);
    });
  });

  q("btnExportGeneral").addEventListener("click", () =>
    exportResultCsvByModule(activeGeneralResultKey(), "rng_general")
  );
  q("btnExportNext").addEventListener("click", () => exportResultCsvByModule("next", "rng_next"));
  q("btnExportIntermediate").addEventListener("click", () =>
    exportResultCsvByModule("intermediate", "rng_intermediate")
  );
  q("btnExportRxn").addEventListener("click", () => exportResultCsvByModule("rxn", "rng_rxn"));
  q("btnCopyContextResolvedAtomIds").addEventListener("click", async (event) => {
    try {
      await copyTextToClipboard(value("qContextResolvedAtomIds"));
      flashButtonLabel(event.currentTarget, "已复制 Atom IDs");
    } catch (err) {
      patchResultMeta("context_extract", { error: String(err) });
    }
  });
  q("btnCopyContextOvitoExpr").addEventListener("click", async (event) => {
    try {
      await copyTextToClipboard(value("qContextOvitoSelectionExpr"));
      flashButtonLabel(event.currentTarget, "已复制表达式");
    } catch (err) {
      patchResultMeta("context_extract", { error: String(err) });
    }
  });
  q("btnCopyContextTrajectoryPath").addEventListener("click", async (event) => {
    try {
      await copyTextToClipboard(state.contextExtract.trajectoryPath || "");
      flashButtonLabel(event.currentTarget, "已复制轨迹路径");
    } catch (err) {
      patchResultMeta("context_extract", { error: String(err), trajectory_path: state.contextExtract.trajectoryPath || "" });
    }
  });
  q("btnCopyContextVmdScriptPath").addEventListener("click", async (event) => {
    try {
      await copyTextToClipboard(state.contextExtract.vmdScriptPath || "");
      flashButtonLabel(event.currentTarget, "已复制 VMD 路径");
    } catch (err) {
      patchResultMeta("context_extract", { error: String(err), vmd_script_path: state.contextExtract.vmdScriptPath || "" });
    }
  });
  q("btnCopyContextTypeMapPath").addEventListener("click", async (event) => {
    try {
      await copyTextToClipboard(state.contextExtract.typeMapPath || "");
      flashButtonLabel(event.currentTarget, "已复制 Type Map 路径");
    } catch (err) {
      patchResultMeta("context_extract", { error: String(err), type_map_path: state.contextExtract.typeMapPath || "" });
    }
  });
  q("btnExportContextSpeciesCsv").addEventListener("click", () => exportResultCsvByModule("context_species", "rng_context_species_events"));
  q("btnExportContextReactionCsv").addEventListener("click", () => exportResultCsvByModule("context_reaction", "rng_context_reaction_events"));
  q("btnExportContextFrames").addEventListener("click", exportContextFramesCsv);
  q("btnExportContextTraj").addEventListener("click", exportContextTrajectory);
  q("btnContextClearSelection").addEventListener("click", () => {
    clearContextSelectedEvent();
  });
  q("btnOpenContextTraj").addEventListener("click", () => {
    openContextTrajectoryPath("default").catch((err) => {
      patchResultMeta("context_extract", { error: String(err), trajectory_path: state.contextExtract.trajectoryPath || "" });
    });
  });
  q("btnOpenContextTrajVmd").addEventListener("click", () => {
    openContextTrajectoryPath("vmd").catch((err) => {
      patchResultMeta("context_extract", { error: String(err), trajectory_path: state.contextExtract.trajectoryPath || "" });
    });
  });
  q("btnOpenContextTrajOvito").addEventListener("click", () => {
    openContextTrajectoryPath("ovito").catch((err) => {
      patchResultMeta("context_extract", { error: String(err), trajectory_path: state.contextExtract.trajectoryPath || "" });
    });
  });
  q("btnOpenContextTrajPymol").addEventListener("click", () => {
    openContextTrajectoryPath("pymol").catch((err) => {
      patchResultMeta("context_extract", { error: String(err), trajectory_path: state.contextExtract.trajectoryPath || "" });
    });
  });
  q("btnRevealContextTraj").addEventListener("click", () => {
    openContextTrajectoryPath("reveal").catch((err) => {
      patchResultMeta("context_extract", { error: String(err), trajectory_path: state.contextExtract.trajectoryPath || "" });
    });
  });
  q("btnOpenContextExportDir").addEventListener("click", () => {
    openContextExportDirectory().catch((err) => {
      patchResultMeta("context_extract", {
        error: String(err),
        trajectory_path: state.contextExtract.trajectoryPath || "",
        vmd_script_path: state.contextExtract.vmdScriptPath || "",
        type_map_path: state.contextExtract.typeMapPath || "",
      });
    });
  });
  const bindContextLocateTable = (tableId, getRows, getConfig, buttonClass = "btn-context-load-row") => {
    const table = q(tableId);
    if (!table) return;
    table.addEventListener("click", (event) => {
      const target = event.target;
      if (!(target instanceof Element)) return;
      const loadBtn = target.closest(`.${buttonClass}`);
      const rowEl = target.closest("tbody tr");
      if (!(rowEl instanceof HTMLTableRowElement) || !(rowEl.parentElement instanceof HTMLTableSectionElement)) return;
      const rowIndex = Array.from(rowEl.parentElement.rows).indexOf(rowEl);
      if (rowIndex < 0) return;
      const sourceRows = typeof getRows === "function" ? getRows() : [];
      const row = (sourceRows || [])[rowIndex];
      const resolution = resolveContextEventResolution(row);
      if (!resolution.step2Extractable) {
        return;
      }
      if (row && (loadBtn instanceof HTMLButtonElement || rowEl instanceof HTMLTableRowElement)) {
        loadContextRowForExtraction(row, typeof getConfig === "function" ? getConfig() : {});
      }
    });
  };
  bindContextLocateTable("contextSpeciesResultTable", () => ensureResultSlot("context_species").rows || [], () => ({
    source: "species",
    source_label: "物种事件",
    species_file: effectiveSpeciesFile("qContextSpeciesFile"),
    target: value("qContextTarget"),
    match_mode: q("qContextMatchMode").value,
    event_mode: selectedEventModeValue("qContextEventMode", "qContextEventModeAdvanced"),
    before_frames: value("qContextBefore") || 3,
    after_frames: value("qContextAfter") || 3,
    reaction_smiles: "",
  }));
  bindContextLocateTable("contextReactionResultTable", () => ensureResultSlot("context_reaction").rows || [], () => ({
    source: "reaction_first",
    source_label: "Reaction-First 严格反应事件",
    species_file: effectiveSpeciesFile("qContextSpeciesFile"),
    before_frames: value("qContextReactionBefore") || 5,
    after_frames: value("qContextReactionAfter") || 5,
    reaction_smiles: value("qContextReactionLocateSmiles"),
    event_id: "",
    selected_event_class: "verified",
  }));
  bindContextLocateTable("contextReactionCandidateTable", () => ensureResultSlot("context_reaction").meta?.candidate_rows || [], () => ({
    source: "reaction_first_candidate",
    source_label: "Reaction-First 候选过程",
    species_file: effectiveSpeciesFile("qContextSpeciesFile"),
    before_frames: value("qContextReactionBefore") || 5,
    after_frames: value("qContextReactionAfter") || 5,
    reaction_smiles: value("qContextReactionLocateSmiles"),
    event_id: "",
    selected_event_class: "candidate",
  }), "btn-context-load-candidate-row");
  q("contextExtractResultTable").addEventListener("click", (event) => {
    const target = event.target;
    if (!(target instanceof Element)) return;
    const rowEl = target.closest("tbody tr");
    if (!(rowEl instanceof HTMLTableRowElement) || !(rowEl.parentElement instanceof HTMLTableSectionElement)) return;
    const rowIndex = Array.from(rowEl.parentElement.rows).indexOf(rowEl);
    if (rowIndex < 0) return;
    const row = (ensureResultSlot("context_extract").rows || [])[rowIndex];
    const anchorFrame = Number(row?.anchor_frame ?? row?.first_frame_found ?? row?.requested_start);
    if (!Number.isFinite(anchorFrame)) return;
    const frameIdx = (state.contextExtract.parsedFrames || []).findIndex((frameObj) => Number(frameObj.frame) === anchorFrame);
    if (frameIdx >= 0) {
      setContextFrameIndex(frameIdx);
    }
  });
  q("contextStoryboardGrid").addEventListener("click", (event) => {
    const target = event.target;
    if (!(target instanceof Element)) return;
    const card = target.closest(".context-storyboard-item");
    if (!(card instanceof HTMLElement)) return;
    const frameIndex = Number.parseInt(String(card.dataset.frameIndex || ""), 10);
    if (!Number.isInteger(frameIndex)) return;
    setContextFrameIndex(frameIndex);
  });
  q("rxnResultTable").addEventListener("click", (event) => {
    const target = event.target;
    if (!(target instanceof Element)) return;
    const rowEl = target.closest("tbody tr");
    if (!(rowEl instanceof HTMLTableRowElement) || !(rowEl.parentElement instanceof HTMLTableSectionElement)) return;
    const rowIndex = Array.from(rowEl.parentElement.rows).indexOf(rowEl);
    if (rowIndex < 0) return;
    const row = (ensureResultSlot("rxn").rows || [])[rowIndex] || {};
    const reactionSmiles = String(row?.reaction_smiles || "").trim();
    if (!reactionSmiles) return;
    const locateInput = q("qContextReactionLocateSmiles");
    const helperInput = q("qContextReactionSmiles");
    if (locateInput instanceof HTMLInputElement) {
      locateInput.value = reactionSmiles;
    }
    if (helperInput instanceof HTMLInputElement) {
      helperInput.value = reactionSmiles;
    }
    clearContextSelectedEvent({ render: false });
    const frameRangesInput = q("qContextFrameRanges");
    if (frameRangesInput instanceof HTMLTextAreaElement) {
      frameRangesInput.value = "";
    }
    const atomIdsInput = q("qContextAtomIds");
    if (atomIdsInput instanceof HTMLTextAreaElement) {
      atomIdsInput.value = "";
    }
    openQueryModule("context");
    focusContextReactionLocateCard();
  });
  q("qContextFrameSelect").addEventListener("change", (event) => {
    const target = event.target;
    if (!(target instanceof HTMLSelectElement)) return;
    setContextFrameIndex(Number.parseInt(target.value, 10) || 0);
  });
  q("btnContextFramePrev").addEventListener("click", () => setContextFrameIndex(state.contextExtract.frameIndex - 1));
  q("btnContextFrameNext").addEventListener("click", () => setContextFrameIndex(state.contextExtract.frameIndex + 1));
  q("qContextFrameView").addEventListener("change", (event) => {
    const target = event.target;
    if (!(target instanceof HTMLSelectElement)) return;
    state.contextExtract.viewMode = target.value || "3d";
    state.contextExtract.hoverAtom = null;
    drawContextFrameCanvas();
    renderContextStoryboard();
  });
  q("qContextHighlightMode").addEventListener("change", (event) => {
    const target = event.target;
    if (!(target instanceof HTMLSelectElement)) return;
    state.contextExtract.highlightMode = target.value || "route_target";
    state.contextExtract.hoverAtom = null;
    renderContextFrameSummary();
    drawContextFrameCanvas();
    renderContextStoryboard();
  });
  q("qContextFocusEventAtoms").addEventListener("change", (event) => {
    const target = event.target;
    if (!(target instanceof HTMLInputElement)) return;
    state.contextExtract.focusEventAtoms = !!target.checked;
    state.contextExtract.hoverAtom = null;
    renderContextFrameSummary();
    drawContextFrameCanvas();
    renderContextStoryboard();
  });
  q("qContextShowTrails").addEventListener("change", (event) => {
    const target = event.target;
    if (!(target instanceof HTMLInputElement)) return;
    state.contextExtract.showTrails = !!target.checked;
    state.contextExtract.hoverAtom = null;
    renderContextFrameSummary();
    drawContextFrameCanvas();
    renderContextStoryboard();
  });
  q("qContextTrailWindow").addEventListener("input", (event) => {
    const target = event.target;
    if (!(target instanceof HTMLInputElement)) return;
    const value = Math.max(1, Math.min(200, Number.parseInt(target.value, 10) || 8));
    state.contextExtract.trailWindow = value;
    target.value = String(value);
    state.contextExtract.hoverAtom = null;
    renderContextFrameSummary();
    drawContextFrameCanvas();
  });
  q("qContextFrameShowBox").addEventListener("change", (event) => {
    const target = event.target;
    if (!(target instanceof HTMLInputElement)) return;
    state.contextExtract.showBox = !!target.checked;
    drawContextFrameCanvas();
    renderContextStoryboard();
  });
  q("qContextFrameZoom").addEventListener("input", (event) => {
    const target = event.target;
    if (!(target instanceof HTMLInputElement)) return;
    state.contextExtract.zoom = Number.parseFloat(target.value) || 1;
    drawContextFrameCanvas();
  });
  q("qContextIncludeRouteTrace").addEventListener("change", () => {
    syncContextTrajectoryAtomScopeControl();
  });
  q("qContextFrameRanges").addEventListener("input", () => {
    syncContextExtractActionState();
  });
  q("qContextAtomIds").addEventListener("input", () => {
    syncContextTrajectoryAtomScopeControl();
    syncContextExtractActionState();
  });
  const contextCanvas = q("contextFrameCanvas");
  let contextDragging = false;
  let contextDragX = 0;
  let contextDragY = 0;
  const updateContextHoverAtom = (event) => {
    if (!(contextCanvas instanceof HTMLCanvasElement)) return;
    const rect = contextCanvas.getBoundingClientRect();
    const x = event.clientX - rect.left;
    const y = event.clientY - rect.top;
    const projected = Array.isArray(contextCanvas._projectedAtoms) ? contextCanvas._projectedAtoms : [];
    let nearest = null;
    let nearestDist = 16;
    projected.forEach((atom) => {
      const dist = Math.hypot(atom.screenX - x, atom.screenY - y);
      if (dist <= nearestDist) {
        nearest = atom;
        nearestDist = dist;
      }
    });
    state.contextExtract.hoverAtom = nearest;
    drawContextFrameCanvas();
  };
  if (contextCanvas instanceof HTMLCanvasElement) {
    contextCanvas.addEventListener("mousedown", (event) => {
      contextDragging = true;
      contextDragX = event.clientX;
      contextDragY = event.clientY;
    });
    contextCanvas.addEventListener("mousemove", (event) => {
      if (contextDragging && (state.contextExtract.viewMode || "3d") === "3d") {
        const dx = event.clientX - contextDragX;
        const dy = event.clientY - contextDragY;
        contextDragX = event.clientX;
        contextDragY = event.clientY;
        state.contextExtract.rotY += dx * 0.01;
        state.contextExtract.rotX += dy * 0.01;
        state.contextExtract.hoverAtom = null;
        drawContextFrameCanvas();
        return;
      }
      updateContextHoverAtom(event);
    });
    contextCanvas.addEventListener("mouseleave", () => {
      contextDragging = false;
      state.contextExtract.hoverAtom = null;
      drawContextFrameCanvas();
    });
    contextCanvas.addEventListener("mouseup", () => {
      contextDragging = false;
    });
    contextCanvas.addEventListener("wheel", (event) => {
      event.preventDefault();
      const nextZoom = (Number(state.contextExtract.zoom || 1) || 1) * (event.deltaY < 0 ? 1.08 : 0.92);
      state.contextExtract.zoom = Math.max(0.4, Math.min(2.4, nextZoom));
      q("qContextFrameZoom").value = String(state.contextExtract.zoom);
      drawContextFrameCanvas();
    }, { passive: false });
  }
  document.querySelectorAll(".btn-jump-result").forEach((btn) => {
    btn.addEventListener("click", () => {
      const key = String(btn.dataset.resultModule || "").trim();
      if (!key) return;
      focusResultModule(key);
    });
  });
  q("btnPlotExportCsv").addEventListener("click", exportPlotCsv);
  q("btnPlotExportPng").addEventListener("click", exportPlotPng);
  q("qPlotCurveFilter").addEventListener("input", () => renderPlotCurveSelector());
  q("plotCurveList").addEventListener("click", (event) => {
    const target = event.target;
    if (!(target instanceof Element)) return;
    const button = target.closest(".formula-structure-btn");
    if (!(button instanceof HTMLElement)) return;
    const smiles = String(button.dataset.smiles || "").trim();
    if (!smiles) return;
    openStructureBySmiles(smiles, "plot").catch((err) => {
      patchActiveResultMeta({ error: String(err), smiles });
    });
  });
  q("plotCurveList").addEventListener("change", (event) => {
    const target = event.target;
    if (!(target instanceof HTMLInputElement)) return;
    if (!target.classList.contains("plot-curve-toggle")) return;
    const key = String(target.dataset.seriesKey || "");
    const selected = new Set(state.plot.selectedSeriesKeys || []);
    if (target.checked) selected.add(key);
    else selected.delete(key);
    state.plot.selectedSeriesKeys = Array.from(selected);
    const visible = getSelectedPlotCurves();
    state.plot.curves = visible;
    drawPlot(state.plot.xValues, visible, state.plot.xName, state.plot.yName);
  });
  q("btnPlotCurveSelectAll").addEventListener("click", () => updatePlotSelection("all"));
  q("btnPlotCurveTop12").addEventListener("click", () => updatePlotSelection("top12"));
  q("btnPlotCurveClear").addEventListener("click", () => updatePlotSelection("none"));
  q("btnCarbonPlotExportCsv").addEventListener("click", exportCarbonPlotCsv);
  q("btnCarbonPlotExportSvg").addEventListener("click", exportCarbonPlotSvg);
  q("qCarbonCurveFilter").addEventListener("input", () => renderCarbonCurveSelector());
  q("carbonCurveList").addEventListener("click", (event) => {
    const target = event.target;
    if (!(target instanceof Element)) return;
    const button = target.closest(".formula-structure-btn");
    if (!(button instanceof HTMLElement)) return;
    const smiles = String(button.dataset.smiles || "").trim();
    if (!smiles) return;
    openStructureBySmiles(smiles, "carbon").catch((err) => {
      patchActiveResultMeta({ error: String(err), smiles });
    });
  });
  q("qCarbonCompareLogY").addEventListener("change", () => {
    drawCarbonCompare(state.carbonPlot.compareXValues || [], getSelectedCarbonCurves(), carbonTimeAxisLabel(state.carbonPlot.query));
  });
  q("carbonCurveList").addEventListener("change", (event) => {
    const target = event.target;
    if (!(target instanceof HTMLInputElement)) return;
    if (!target.classList.contains("carbon-curve-toggle")) return;
    const key = String(target.dataset.seriesKey || "");
    const selected = new Set(state.carbonPlot.selectedSeriesKeys || []);
    if (target.checked) selected.add(key);
    else selected.delete(key);
    state.carbonPlot.selectedSeriesKeys = Array.from(selected);
    drawCarbonCompare(state.carbonPlot.compareXValues || [], getSelectedCarbonCurves(), carbonTimeAxisLabel(state.carbonPlot.query));
  });
  q("btnCarbonCurveSelectAll").addEventListener("click", () => updateCarbonSelection("all"));
  q("btnCarbonCurveTop12").addEventListener("click", () => updateCarbonSelection("top12"));
  q("btnCarbonCurveClear").addEventListener("click", () => updateCarbonSelection("none"));
  q("btnCarbonCurveApplyMerge").addEventListener("click", () => runCarbonInteractive(rebuildCarbonInteractiveCompare));
  q("btnCarbonCurveResetMerge").addEventListener("click", () => {
    q("qCarbonCurveMerge").value = "";
    state.carbonPlot.mergeBasket = [];
    updateCarbonMergeDropZone();
    runCarbonInteractive(rebuildCarbonInteractiveCompare);
  });
  bindCarbonDragMerge();

  q("qMassMode").addEventListener("change", () => {
    q("qMassTol").value = q("qMassMode").value === "exact" ? "0.5" : "0";
  });
  q("qPlotSpeciesFiles").addEventListener("input", syncPlotSpeciesSourceMode);
  q("qCarbonSpeciesFiles").addEventListener("input", syncCarbonSpeciesSourceMode);
  q("qCarbonLayout").addEventListener("change", syncCarbonLayoutFields);
  q("qUnifiedPlotMode").addEventListener("change", syncUnifiedPlotMode);

  window.addEventListener("resize", () => {
    if (plotChart && q("plotChart").style.display !== "none") {
      plotChart.resize();
    } else if (state.plot.xValues.length && state.plot.curves.length) {
      drawPlotCanvas(state.plot.xValues, state.plot.curves, state.plot.xName, state.plot.yName);
    }
    if (carbonPlotChart && q("carbonPlotChart").style.display !== "none") {
      carbonPlotChart.resize();
    } else if ((state.carbonPlot.compareXValues || []).length) {
      drawCarbonCompareCanvas(
        state.carbonPlot.compareXValues || [],
        getSelectedCarbonCurves(),
        carbonTimeAxisLabel(state.carbonPlot.query),
        "number of molecules",
        !!q("qCarbonCompareLogY").checked
      );
    }
    const transitionHost = q("transitionChart");
    if (transitionHost && window.echarts) {
      const transitionChart = window.echarts.getInstanceByDom(transitionHost);
      if (transitionChart) transitionChart.resize();
    }
    drawContextFrameCanvas();
  });
  syncContextTrajectoryAtomScopeControl();
  syncContextExtractActionState();
}

async function init() {
  buildWorkspaceShell();
  bindEvents();
  let health = null;
  try {
    health = await fetchJson("/api/health", {});
  } catch (_) {
    health = null;
  }
  const rdkitAvailable = Boolean(health?.rdkit?.available);
  initializeResultWorkbench({
    status: "ready",
    hint: "绘图可直接用 species 数据源；网络检索再填写 reactionabcd",
    rdkit_available: rdkitAvailable,
    viewer_note: rdkitAvailable ? "SMILES 结构渲染可用" : "RDKit 不可用，结构渲染将显示错误占位图",
  });
  const initialModule = new URLSearchParams(window.location.search).get("module");
  setWorkspaceModule(initialModule || "dataset", { focus: false });
  setPlotMeta({ status: "ready", hint: "使用 Plot 面板查询后在此显示曲线" });
  setPlotProgress("Idle", 0, "等待开始", false);
  setIntermediateProgress("Idle", 0, "等待开始", false);
  setContextExtractProgress("Idle", 0, "等待开始", false);
  resetContextTrajectoryViewer();
  setCarbonPlotMeta({ status: "ready", hint: "使用 Carbon Plot 面板后在此显示绘图参数" });
  setCarbonPlotSummary({ status: "ready", hint: "绘图后在此显示 summary JSON" });
  renderCarbonPlotHighlights(null);
  setCarbonPlotProgress("Idle", 0, "等待开始", false);
  drawPlotCanvas([], [], "x", "count");
  resetPlotInteractive();
  renderCarbonPlot("");
  drawContextFrameCanvas();
  resetCarbonInteractive();
  setGeneralQueryMode(DEFAULT_GENERAL_QUERY_MODE);
  syncCarbonLayoutFields();
  syncUnifiedPlotMode();
  syncPlotSpeciesSourceMode();
  syncCarbonSpeciesSourceMode();
  refreshDatasetStatus({ silent: true }).catch(() => {});
}

init();
