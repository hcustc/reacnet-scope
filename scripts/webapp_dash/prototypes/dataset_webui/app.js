/*
 * PROTOTYPE — throwaway implementation.
 *
 * Three variants of the redesigned dataset experience, switchable via
 * ?variant=, on an isolated prototype route next to the Dash WebUI.
 * All datasets, tasks, failures, and storage state are simulated in memory.
 */

const VARIANTS = {
  A: "证据台账",
  B: "能力检查器",
  C: "因果流水线",
};

const CAPABILITY_ORDER = ["reaction", "species", "events", "trajectory", "element"];

const CAPABILITY_META = {
  reaction: {
    mark: "Rx",
    title: "反应网络",
    purpose: "物种 / 反应式检索与聚合候选路径",
    sourceKind: "聚合反应证据",
    sourceSuffix: ".reactionabcd",
    indexName: "无需额外派生索引",
    analysis: "物种检索 · 反应式检索 · 候选路径",
    cli: "reacnet-scope query-reaction --dataset <prefix>",
  },
  species: {
    mark: "Sp",
    title: "物种时间序列",
    purpose: "时间演化与中间体筛选",
    sourceKind: "物种时间序列",
    sourceSuffix: ".species",
    indexName: "无需额外派生索引",
    analysis: "时间演化 · 中间体筛选",
    cli: "reacnet-scope query-species --dataset <prefix>",
  },
  events: {
    mark: "Et",
    title: "反应事件证据",
    purpose: "事件检索、事件路径与事件跳转",
    sourceKind: "逐帧反应与分子证据",
    sourceSuffix: ".timeline.h5 + .molecules.csv",
    indexName: "events.sqlite3 · schema 3",
    analysis: "反应事件 · 事件路径 · 轨迹跳转",
    cli: "reacnet-scope-prepare events --dataset <prefix>",
  },
  trajectory: {
    mark: "Tr",
    title: "局部轨迹证据",
    purpose: "时间定位、局部轨迹提取与导出",
    sourceKind: "原子坐标轨迹",
    sourceSuffix: ".lammpstrj",
    indexName: "trajectory.sqlite3 · schema 2",
    analysis: "轨迹查看 · 局部提取 · 事件包导出",
    cli: "reacnet-scope-prepare trajectory --dataset <prefix>",
  },
  element: {
    mark: "El",
    title: "元素分布演化",
    purpose: "按用户所选元素的原子数分组物种",
    sourceKind: "物种组成证据",
    sourceSuffix: ".species + 结构映射",
    indexName: "element-composition.sqlite3 · schema 2",
    analysis: "元素分布演化（元素可选）",
    cli: "reacnet-scope-prepare element-composition --dataset <prefix>",
  },
};

const NAV_GROUPS = [
  ["检索与趋势", [["Sp", "物种检索"], ["Rx", "反应式检索"], ["Ev", "时间演化"]]],
  ["事件证据", [["Et", "反应事件"], ["Tr", "轨迹查看"]]],
  ["自动分析", [["In", "中间体筛选"], ["Pw", "反应路径"], ["Cx", "组成演化"]]],
];

function initialDatasets() {
  return {
    "2cp": {
      id: "2cp",
      name: "2CP_O2_1ER",
      displayName: "2% O₂ · 1 ER",
      path: "/data/runs/combustion/2CP_O2_1ER",
      prefix: "/data/runs/combustion/2CP_O2_1ER/2CP_O2_1ER",
      identity: "ds_7f20a9d1",
      available: true,
      revision: "source-r48 · 2026-08-03 09:42",
      workspace: "本地 sidecar",
      workspacePath: "/data/runs/combustion/2CP_O2_1ER/.reacnet-scope/",
      workspaceSize: "7.6 GB",
      capabilities: {
        reaction: { status: "ready", source: "2CP_O2_1ER.reactionabcd", detail: "已直接识别聚合反应记录" },
        species: { status: "ready", source: "2CP_O2_1ER.species", detail: "已直接识别 48,001 个分析帧" },
        events: { status: "unprepared", source: "2CP_O2_1ER.timeline.h5", detail: "源证据可用，尚未准备事件证据索引" },
        trajectory: { status: "ready", source: "2CP_O2_1ER.lammpstrj", detail: "索引与当前源修订一致" },
        element: { status: "stale", source: "2CP_O2_1ER.species", detail: "旧 C/O/Cl 索引与通用元素模型不兼容" },
      },
    },
    rp3: {
      id: "rp3",
      name: "rp3",
      displayName: "",
      path: "/data/runs/rp3-study",
      prefix: "/data/runs/rp3-study/rp3",
      identity: "ds_21c8b54e",
      available: true,
      revision: "source-r12 · 2026-08-02 18:16",
      workspace: "中央回退工作区",
      workspacePath: "~/.local/share/reacnet-scope/workspaces/ds_21c8b54e/",
      workspaceSize: "1.9 GB",
      capabilities: {
        reaction: { status: "ready", source: "rp3.reactionabcd", detail: "已直接识别聚合反应记录" },
        species: { status: "ready", source: "rp3.species", detail: "已直接识别 12,001 个分析帧" },
        events: { status: "unprepared", source: "rp3.reactionevent.csv", detail: "源证据可用，尚未准备事件证据索引" },
        trajectory: { status: "missing", source: "未找到 .lammpstrj", detail: "源轨迹未位于已知位置，可在技术修复中手动关联" },
        element: { status: "unprepared", source: "rp3.species", detail: "源证据可用，尚未准备通用元素组成索引" },
      },
    },
    external: {
      id: "external",
      name: "External-drive run",
      displayName: "外接盘 · 高温组",
      path: "/media/research/EXT-RUN-09",
      prefix: "/media/research/EXT-RUN-09/run09",
      identity: "ds_a8e2210c",
      available: false,
      revision: "位置不可用",
      workspace: "本地 sidecar（离线）",
      workspacePath: "/media/research/EXT-RUN-09/.reacnet-scope/",
      workspaceSize: "3.2 GB",
      capabilities: {},
    },
  };
}

function initialWorkspaces() {
  return [
    { datasetId: "2cp", name: "2% O₂ · 1 ER", path: "/data/runs/combustion/2CP_O2_1ER/.reacnet-scope/", type: "sidecar", typeLabel: "本地 sidecar", size: "7.6 GB", reclaim: "7.4 GB", lastUsed: "刚刚", caps: "事件、轨迹、元素分布", hidden: false },
    { datasetId: "rp3", name: "rp3", path: "~/.local/share/reacnet-scope/workspaces/ds_21c8b54e/", type: "central", typeLabel: "中央回退", size: "1.9 GB", reclaim: "1.8 GB", lastUsed: "昨天", caps: "事件、元素分布", hidden: false },
    { datasetId: "legacy", name: "T1800_O2-0.5_rep2", path: "~/.reacnet-scope-cache/datasets/legacy-18/", type: "legacy", typeLabel: "旧中央工作区", size: "640 MB", reclaim: "612 MB", lastUsed: "12 天前", caps: "事件、轨迹", hidden: false },
    { datasetId: "lazy", name: "cold-start-control", path: "尚未创建（读取数据集不会创建工作区）", type: "lazy", typeLabel: "尚未创建", size: "0 B", reclaim: "0 B", lastUsed: "18 天前", caps: "无派生数据", hidden: false },
    { datasetId: "unavailable", name: "External-drive run", path: "/media/research/EXT-RUN-09/.reacnet-scope/", type: "unavailable", typeLabel: "存储不可用", size: "3.2 GB", reclaim: "待位置恢复", lastUsed: "31 天前", caps: "事件、轨迹、元素分布", hidden: false },
  ];
}

const state = {
  variant: readVariant(),
  datasets: initialDatasets(),
  workspaces: initialWorkspaces(),
  currentDatasetId: null,
  candidateId: "2cp",
  view: "select",
  browsePath: "/data/runs/combustion",
  tasks: [],
  queuePaused: false,
  drawerOpen: false,
  overflowOpen: false,
  expandedCaps: new Set(["events"]),
  focusedCap: "events",
  modal: null,
  toastTimer: null,
  recentIds: ["2cp", "rp3", "external"],
};

function readVariant() {
  const key = new URLSearchParams(window.location.search).get("variant")?.toUpperCase();
  return VARIANTS[key] ? key : "A";
}

function e(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function datasetLabel(dataset) {
  return dataset?.displayName || dataset?.name || "未选择";
}

function clone(value) {
  return JSON.parse(JSON.stringify(value));
}

function resetSimulation() {
  state.datasets = initialDatasets();
  state.workspaces = initialWorkspaces();
  state.tasks = [];
  state.queuePaused = false;
  state.drawerOpen = false;
  state.overflowOpen = false;
  state.modal = null;
  state.expandedCaps = new Set(["events"]);
  state.focusedCap = "events";
  state.recentIds = ["2cp", "rp3", "external"];
}

function taskFor(datasetId, capabilityId) {
  return [...state.tasks].reverse().find(
    (task) => task.datasetId === datasetId && task.capabilityId === capabilityId && task.status !== "completed",
  );
}

function currentDataset() {
  return state.currentDatasetId ? state.datasets[state.currentDatasetId] : null;
}

function statusInfo(status) {
  const statuses = {
    ready: ["可用", "ready", "✓"],
    unprepared: ["需要准备", "warning", "○"],
    running: ["准备中", "running", "↻"],
    queued: ["已排队", "queued", "…"],
    cancelled: ["已取消 · 有检查点", "cancelled", "↩"],
    interrupted: ["已中断 · 等待继续", "interrupted", "↩"],
    failed: ["失败 · 可重试", "error", "!"],
    stale: ["源已变化 / 索引不兼容", "stale", "!"],
    missing: ["缺少源证据", "missing", "×"],
    completed: ["已完成", "ready", "✓"],
  };
  return statuses[status] || statuses.unprepared;
}

function effectiveCap(dataset, capabilityId) {
  const base = dataset.capabilities[capabilityId];
  if (!base) return null;
  const task = taskFor(dataset.id, capabilityId);
  const taskStatus = task?.status === "failed" && task.sourceChanged ? "stale" : task?.status;
  return { ...base, status: task ? taskStatus : base.status, task };
}

function actionFor(cap) {
  if (!cap) return null;
  if (cap.status === "unprepared") return ["准备", "prepare-cap", "primary-button"];
  if (cap.status === "queued") return ["取消排队", "cancel-cap-task", "secondary-button"];
  if (cap.status === "running") return ["查看任务", "open-task-drawer", "secondary-button"];
  if (cap.status === "cancelled" || cap.status === "interrupted") return ["继续", "continue-cap-task", "primary-button"];
  if (cap.status === "failed") return ["重试", "retry-cap-task", "primary-button"];
  if (cap.status === "stale") return ["重新准备", "prepare-cap", "primary-button"];
  return null;
}

function capStatusCopy(cap) {
  if (cap.status === "running") {
    return `${cap.task.phase} · ${cap.task.progress}% · ${cap.task.processed}`;
  }
  if (cap.status === "queued") {
    const queued = state.tasks.filter((task) => task.status === "queued");
    return `队列第 ${Math.max(1, queued.findIndex((task) => task.id === cap.task.id) + 1)} 项 · 属于 ${datasetLabel(state.datasets[cap.task.datasetId])}`;
  }
  if (cap.status === "cancelled") return `检查点 ${cap.task?.checkpoint || "已保留"} · 可继续`;
  if (cap.status === "interrupted") return "应用关闭时中断；不会自动恢复昂贵工作";
  if (cap.status === "failed") return cap.task?.error || "构建失败，已保留有效检查点";
  return cap.detail;
}

function statusGlyph(status) {
  const [, tone, glyph] = statusInfo(status);
  const toneClass = tone === "ready" ? "" : tone === "error" || tone === "stale" || tone === "missing" ? "error" : tone === "neutral" ? "neutral" : "warning";
  return `<span class="status-glyph ${toneClass}" aria-hidden="true">${glyph}</span>`;
}

function stateLabel(status) {
  const [label, tone] = statusInfo(status);
  return `<span class="state-label ${tone}">${e(label)}</span>`;
}

function progressMarkup(task) {
  if (!task) return "";
  const indeterminate = task.measurable === false;
  return `
    <div class="progress-wrap">
      <div class="progress-label"><span>${e(task.phase)}</span><span>${indeterminate ? "计量中" : `${task.progress}%`}</span></div>
      <div class="progress-track ${indeterminate ? "indeterminate" : ""}"><div class="progress-fill" style="width:${indeterminate ? 35 : task.progress}%"></div></div>
    </div>`;
}

function navMarkup() {
  return NAV_GROUPS.map(([label, items]) => `
    <div class="nav-group">
      <div class="nav-label">${label}</div>
      ${items.map(([icon, title]) => `<button class="nav-item" type="button"><span class="nav-icon">${icon}</span>${title}</button>`).join("")}
    </div>`).join("");
}

function topbarMarkup() {
  const dataset = currentDataset();
  const active = state.tasks.filter((task) => ["running", "queued"].includes(task.status));
  const attention = state.tasks.filter((task) => ["failed", "interrupted", "cancelled"].includes(task.status));
  const badgeCount = active.length || attention.length;
  const hasFailure = state.tasks.some((task) => task.status === "failed");
  return `
    <header class="topbar">
      <div class="page-context">数据集</div>
      <div class="topbar-divider"></div>
      <button class="dataset-link" data-action="navigate-view" data-view="select" type="button" title="前往选择数据集">
        <span class="dataset-dot ${dataset ? "ready" : ""}"></span>
        <span class="dataset-copy"><small>当前数据集</small><strong>${e(datasetLabel(dataset))}</strong></span>
      </button>
      <div class="top-actions">
        <button class="task-badge ${hasFailure ? "has-failure" : ""}" data-action="open-task-drawer" type="button">
          <span aria-hidden="true">↻</span><span class="task-label">后台任务</span><span class="count">${badgeCount}</span>
        </button>
        <button class="icon-button" data-action="toggle-overflow" type="button" aria-label="更多操作">•••</button>
        ${state.overflowOpen ? `
          <div class="overflow-menu">
            <button data-action="recheck-status" type="button">重新检查状态</button>
            <button data-action="simulate-restart" type="button">模拟关闭并重启</button>
            <button data-action="reset-all" type="button">重置原型状态</button>
          </div>` : ""}
      </div>
    </header>`;
}

function shellMarkup() {
  return `
    <div class="shell variant-${state.variant.toLowerCase()}">
      <aside class="sidebar">
        <div class="brand"><span class="brand-mark">RS</span><span><strong>ReacNet Scope</strong><small>反应分析工作台</small></span></div>
        <div class="nav-scroll">${navMarkup()}</div>
        <div class="nav-bottom">
          <div class="nav-label">数据工作区</div>
          <button class="nav-item active" data-action="navigate-view" data-view="current" type="button"><span class="nav-icon">Ds</span>数据集</button>
          <button class="nav-item" type="button"><span class="nav-icon">Cp</span>批量对比</button>
          <div class="nav-footnote">所有计算均在当前服务器执行</div>
        </div>
      </aside>
      ${topbarMarkup()}
      <main class="main">${mainMarkup()}</main>
      ${prototypeSwitcherMarkup()}
    </div>`;
}

function pageHeadingMarkup() {
  const dataset = currentDataset();
  const descriptions = {
    current: "独立理解每项分析能力；准备任务始终属于数据集与源修订。",
    select: dataset ? "预览候选不会改变当前分析上下文，确认后才切换。" : "先选择并预览一个 Dataset Candidate，再明确设为当前数据集。",
    storage: "管理可恢复的派生状态；ReacNetGenerator 源数据始终保持不变。",
  };
  return `
    <div class="page-heading">
      <div class="page-heading-main"><div><h1>数据集</h1><p>${descriptions[state.view]}</p></div><span class="prototype-tag">THROWAWAY PROTOTYPE</span></div>
    </div>`;
}

function subnavMarkup() {
  if (!currentDataset()) return "";
  return `
    <div class="subnav-row">
      <nav class="subnav" aria-label="数据集视图">
        <button class="${state.view === "current" ? "active" : ""}" data-action="navigate-view" data-view="current" type="button">当前数据集</button>
        <button class="${state.view === "select" ? "active" : ""}" data-action="navigate-view" data-view="select" type="button">选择数据集</button>
        <button class="${state.view === "storage" ? "active" : ""}" data-action="navigate-view" data-view="storage" type="button">存储管理</button>
      </nav>
      ${scenarioToolsMarkup()}
    </div>`;
}

function scenarioToolsMarkup() {
  return `
    <div class="scenario-tools">
      <label for="scenario-picker">原型场景</label>
      <select id="scenario-picker" data-action="load-scenario" aria-label="选择原型场景">
        <option value="">选择场景…</option>
        <option value="first">1 · 首次使用</option>
        <option value="normal">2 · 常规状态</option>
        <option value="queue">3 · 任务队列</option>
        <option value="switch">4 · 工作中切换</option>
        <option value="recovery">5–6 · 故障与中断恢复</option>
        <option value="repair">7 · 技术修复</option>
        <option value="storage">8 · 存储管理</option>
      </select>
    </div>`;
}

function mainMarkup() {
  if (!currentDataset() && state.view !== "select") state.view = "select";
  const content = state.view === "select" ? selectionMarkup() : state.view === "storage" ? storageMarkup() : currentDatasetMarkup();
  return `${pageHeadingMarkup()}${subnavMarkup()}${!currentDataset() ? `<div class="subnav-row">${scenarioToolsMarkup()}</div>` : ""}${content}`;
}

function selectionMarkup() {
  const candidate = state.datasets[state.candidateId];
  const dataset = currentDataset();
  const candidateIds = ["2cp", "rp3"];
  return `
    ${!dataset ? `<div class="empty-banner">${statusGlyph("unprepared")}<div><strong>选择一个数据集开始</strong><span>浏览和预览不会创建工作区，也不会启动任何准备任务。</span></div></div>` : ""}
    <section class="selector panel" aria-label="选择数据集">
      <div class="selector-browser">
        <div class="browser-head"><div class="section-eyebrow">服务器位置</div><h2 class="section-title">选择数据集</h2><p class="section-copy">输入目录或完整数据集前缀；候选至少支持一项 ReacNet Scope 分析能力。</p></div>
        <div class="location-bar">
          <button class="icon-button" data-action="browse-parent" type="button" title="返回上一级">←</button>
          <input class="location-input" id="location-input" value="${e(state.browsePath)}" aria-label="位置或数据集前缀" />
          <button class="secondary-button" data-action="browse-location" type="button">前往</button>
        </div>
        <div class="crumbs"><button data-action="set-path" data-path="/data" type="button">data</button><span>/</span><button data-action="set-path" data-path="/data/runs" type="button">runs</button><span>/</span><span>${e(state.browsePath.split("/").filter(Boolean).at(-1) || "data")}</span></div>
        <div class="browser-block recent-block">
          <div class="block-heading"><strong>最近使用</strong><span>保存在此浏览器</span></div>
          <div class="recent-list">${state.recentIds.map(recentItemMarkup).join("") || `<span class="section-copy">没有最近使用记录</span>`}</div>
        </div>
        <div class="browser-block discovery-block">
          <div class="block-heading"><strong>此位置发现的候选</strong><span>${candidateIds.length} 个</span></div>
          <div class="folder-strip"><button class="folder-button" data-action="set-path" data-path="/data/runs/combustion" type="button">combustion</button><button class="folder-button" data-action="set-path" data-path="/data/runs/rp3-study" type="button">rp3-study</button></div>
          <div class="candidate-list" style="margin-top:8px">${candidateIds.map(candidateItemMarkup).join("")}</div>
        </div>
      </div>
      <div class="selector-preview">${candidatePreviewMarkup(candidate)}</div>
    </section>`;
}

function recentItemMarkup(id) {
  const dataset = state.datasets[id];
  if (!dataset) return "";
  return `
    <div class="recent-item ${state.candidateId === id ? "selected" : ""} ${dataset.available ? "" : "unavailable"}">
      <button class="text-button" data-action="select-candidate" data-dataset="${id}" type="button" style="text-align:left;color:inherit;min-width:0">
        <strong>${e(datasetLabel(dataset))}</strong><small>${e(dataset.path)}</small>
      </button>
      ${dataset.available ? `<span class="availability">可预览</span>` : `<button class="remove-recent" data-action="remove-recent" data-dataset="${id}" type="button">移除</button>`}
    </div>`;
}

function candidateItemMarkup(id) {
  const dataset = state.datasets[id];
  return `
    <button class="candidate-item ${state.candidateId === id ? "selected" : ""}" data-action="select-candidate" data-dataset="${id}" type="button">
      <span><strong>${e(datasetLabel(dataset))}</strong><small>${e(dataset.prefix)}</small></span>
      <span class="availability">已识别能力</span>
    </button>`;
}

function candidatePreviewMarkup(dataset) {
  if (!dataset) return `<div class="section-copy">选择左侧候选后查看预览。</div>`;
  if (!dataset.available) {
    return `
      <div class="preview-card">
        <div class="section-eyebrow">Dataset Candidate</div>
        <h2 class="candidate-name">${e(datasetLabel(dataset))}</h2>
        <div class="candidate-path">${e(dataset.path)}</div>
        <div class="notice" style="margin-top:16px">原位置当前不可用。请恢复外接盘或在位置栏输入新的挂载位置。此记录可从“最近使用”移除。</div>
        <div class="selector-actions"><span class="selection-note">当前数据集${currentDataset() ? `仍为 ${e(datasetLabel(currentDataset()))}` : "仍未选择"}。</span><button class="primary-button" disabled type="button">设为当前数据集</button></div>
      </div>`;
  }
  return `
    <div class="preview-card">
      <div class="section-eyebrow">Dataset Candidate · 只读预览</div>
      <h2 class="candidate-name">${e(dataset.name)}</h2>
      <div class="candidate-path">${e(dataset.prefix)}</div>
      <div class="preview-meta"><span class="chip">至少一项能力可用</span><span class="chip">检查于刚刚</span><span class="chip">尚未创建新工作区</span></div>
      <div class="capability-preview">${CAPABILITY_ORDER.map((capId) => previewCapRowMarkup(dataset, capId)).join("")}</div>
      <div class="display-name-field"><label for="candidate-display-name">本地显示名称（可选）</label><input id="candidate-display-name" class="field" value="${e(dataset.displayName)}" placeholder="例如：2% O₂ · 1 ER" /></div>
      <details class="preview-technical"><summary>技术细节：身份、源位置与工作区</summary><div class="technical-grid"><span>数据集身份</span><code>${e(dataset.identity)}</code><span>源前缀</span><code>${e(dataset.prefix)}</code><span>源修订</span><code>${e(dataset.revision)}</code><span>工作区策略</span><code>${e(dataset.workspace)}（选择时不创建）</code></div></details>
      <div class="selector-actions">
        <span class="selection-note">选择候选不会改变分析上下文。仅“设为当前数据集”会切换。</span>
        ${currentDataset() ? `<button class="secondary-button" data-action="cancel-selection" type="button">取消</button>` : ""}
        <button class="primary-button" data-action="set-current" data-dataset="${dataset.id}" type="button">设为当前数据集</button>
      </div>
    </div>`;
}

function previewCapRowMarkup(dataset, capId) {
  const meta = CAPABILITY_META[capId];
  const cap = effectiveCap(dataset, capId);
  const [statusText] = statusInfo(cap.status);
  return `<div class="preview-cap-row">${statusGlyph(cap.status)}<span><strong>${e(meta.title)}</strong><small>${e(meta.purpose)}</small></span><span>${e(statusText)}</span></div>`;
}

function currentDatasetMarkup() {
  const dataset = currentDataset();
  const interrupted = state.tasks.some((task) => task.datasetId === dataset.id && task.status === "interrupted");
  return `
    ${datasetSummaryMarkup(dataset)}
    ${interrupted ? `<div class="notice"><strong>上次关闭时有准备任务被中断。</strong> 检查点已保留；请手动继续，系统不会自动恢复昂贵工作。</div>` : ""}
    ${state.variant === "A" ? variantAMarkup(dataset) : state.variant === "B" ? variantBMarkup(dataset) : variantCMarkup(dataset)}`;
}

function datasetSummaryMarkup(dataset) {
  return `
    <section class="dataset-summary panel">
      <div><div class="section-eyebrow">当前数据集</div><h2>${e(datasetLabel(dataset))}</h2><p>${e(dataset.prefix)} · ${e(dataset.revision)}</p></div>
      <div class="summary-chips">
        <div class="summary-chip"><small>工作区</small><strong>${e(dataset.workspace)}</strong></div>
        <div class="summary-chip"><small>派生状态</small><strong>${e(dataset.workspaceSize)}</strong></div>
        <button class="secondary-button" data-action="navigate-view" data-view="storage" type="button">管理存储</button>
      </div>
    </section>`;
}

function capActionMarkup(dataset, capId, cap) {
  const action = actionFor(cap);
  if (!action) return "";
  const [label, actionName, className] = action;
  return `<button class="${className}" data-action="${actionName}" data-dataset="${dataset.id}" data-capability="${capId}" type="button">${label}</button>`;
}

function variantAMarkup(dataset) {
  return `
    <section class="cap-list panel">
      <div class="cap-heading-row"><span>分析能力</span><span>可用分析</span><span>状态</span><span>操作</span><span></span></div>
      ${CAPABILITY_ORDER.map((capId) => ledgerRowMarkup(dataset, capId)).join("")}
    </section>`;
}

function ledgerRowMarkup(dataset, capId) {
  const meta = CAPABILITY_META[capId];
  const cap = effectiveCap(dataset, capId);
  const expanded = state.expandedCaps.has(capId);
  return `
    <div class="cap-record">
      <div class="cap-main-row">
        <div class="cap-title"><strong>${e(meta.title)}</strong><small>${e(meta.purpose)}</small></div>
        <div class="cap-title"><small>${e(meta.analysis)}</small></div>
        <div><div>${stateLabel(cap.status)}</div><div class="cap-status-copy">${e(capStatusCopy(cap))}</div>${cap.status === "running" ? progressMarkup(cap.task) : ""}</div>
        <div class="row-actions">${capActionMarkup(dataset, capId, cap)}</div>
        <button class="cap-toggle" data-action="toggle-cap" data-capability="${capId}" type="button" aria-expanded="${expanded}">${expanded ? "−" : "+"}</button>
      </div>
      ${expanded ? capDetailsMarkup(dataset, capId, cap) : ""}
    </div>`;
}

function variantBMarkup(dataset) {
  const capId = state.focusedCap;
  const cap = effectiveCap(dataset, capId);
  const meta = CAPABILITY_META[capId];
  return `
    <div class="inspector-layout">
      <section class="inspector-nav panel">
        <div class="inspector-intro"><div class="section-eyebrow">独立能力</div><h3>选择一项进行检查</h3><p class="section-copy">每项能力都有自己的证据、派生状态和准备动作。</p></div>
        ${CAPABILITY_ORDER.map((id) => inspectorItemMarkup(dataset, id)).join("")}
      </section>
      <section class="inspector-detail panel">
        <div class="inspector-hero"><div>${stateLabel(cap.status)}<h3>${e(meta.title)}</h3><p class="inspector-explanation">${e(meta.purpose)}。${e(capStatusCopy(cap))}</p></div><div>${capActionMarkup(dataset, capId, cap)}</div></div>
        ${cap.status === "running" ? progressMarkup(cap.task) : ""}
        ${capDetailsMarkup(dataset, capId, cap, true)}
        <div class="inspector-neighbors">左侧始终保留五项独立能力，不把某一项索引状态合并成单一的数据集完成度。</div>
      </section>
    </div>`;
}

function inspectorItemMarkup(dataset, capId) {
  const meta = CAPABILITY_META[capId];
  const cap = effectiveCap(dataset, capId);
  return `
    <button class="inspector-item ${state.focusedCap === capId ? "active" : ""}" data-action="focus-cap" data-capability="${capId}" type="button" aria-expanded="${state.focusedCap === capId}">
      ${statusGlyph(cap.status)}<span><strong>${e(meta.title)}</strong><small>${e(capStatusCopy(cap))}</small></span><span aria-hidden="true">›</span>
    </button>`;
}

function variantCMarkup(dataset) {
  return `
    <section class="pipeline-panel panel">
      <div class="pipeline-header"><span>分析能力</span><span>源证据</span><span></span><span>派生索引</span><span></span><span>可用分析</span><span>操作</span><span></span></div>
      ${CAPABILITY_ORDER.map((capId) => pipelineRowMarkup(dataset, capId)).join("")}
    </section>`;
}

function pipelineRowMarkup(dataset, capId) {
  const meta = CAPABILITY_META[capId];
  const cap = effectiveCap(dataset, capId);
  const expanded = state.expandedCaps.has(capId);
  const sourceTone = cap.status === "missing" ? "error" : "ready";
  const indexTone = cap.status === "ready" ? "ready" : ["failed", "stale", "missing"].includes(cap.status) ? "error" : "warning";
  const analysisTone = cap.status === "ready" ? "ready" : ["failed", "stale", "missing"].includes(cap.status) ? "error" : "warning";
  return `
    <div class="pipeline-record">
      <div class="pipeline-row">
        <div class="cap-title"><strong>${e(meta.title)}</strong><small>${e(capStatusCopy(cap))}</small></div>
        <div class="pipeline-cell ${sourceTone}"><small>源证据</small><strong>${e(cap.source)}</strong></div><div class="pipeline-arrow">→</div>
        <div class="pipeline-cell ${indexTone}"><small>派生索引</small><strong>${e(meta.indexName)}</strong></div><div class="pipeline-arrow">→</div>
        <div class="pipeline-cell ${analysisTone}"><small>可用分析</small><strong>${e(meta.analysis)}</strong></div>
        <div>${capActionMarkup(dataset, capId, cap)}</div>
        <button class="cap-toggle" data-action="toggle-cap" data-capability="${capId}" type="button" aria-expanded="${expanded}">${expanded ? "−" : "+"}</button>
      </div>
      ${expanded ? `<div class="pipeline-details">${capDetailsMarkup(dataset, capId, cap, true)}</div>` : ""}
    </div>`;
}

function capDetailsMarkup(dataset, capId, cap, embedded = false) {
  const meta = CAPABILITY_META[capId];
  const task = cap.task;
  const special = capId === "trajectory" && cap.status === "missing"
    ? `<button class="secondary-button" data-action="associate-source" data-dataset="${dataset.id}" data-capability="${capId}" type="button">关联 /mnt/restore/${e(dataset.name)}.lammpstrj</button>`
    : capId === "trajectory" && cap.status === "ready"
      ? `<button class="text-button" data-action="simulate-cap-stale" data-dataset="${dataset.id}" data-capability="${capId}" type="button">原型：模拟轨迹源更新</button>`
      : "";
  const body = `
    <div class="causal-chain">
      <div class="causal-node"><small>源证据</small><strong>${e(meta.sourceKind)}</strong><code>${e(dataset.path)}/${e(cap.source)}</code></div>
      <div class="causal-arrow">→</div>
      <div class="causal-node"><small>派生索引</small><strong>${e(meta.indexName)}</strong><code>${e(dataset.workspacePath)}indexes/</code></div>
      <div class="causal-arrow">→</div>
      <div class="causal-node"><small>可用分析</small><strong>${e(meta.analysis)}</strong><code>${cap.status === "ready" ? "当前可用" : "等待证据链恢复"}</code></div>
    </div>
    <div class="details-footer">
      <div class="details-meta"><span>源修订</span><code>${e(dataset.revision)}</code><span>等效 CLI</span><code>${e(meta.cli.replace("<prefix>", dataset.prefix))}</code><span>最近检查点</span><code>${e(task?.checkpoint || "无")}</code><span>日志</span><code>${e(task?.log || "尚无准备任务日志")}</code></div>
      <div class="row-actions">${special}</div>
    </div>`;
  return embedded ? body : `<div class="cap-details">${body}</div>`;
}

function storageMarkup() {
  const rows = state.workspaces.filter((workspace) => !workspace.hidden);
  return `
    <div class="storage-shell">
      <section class="storage-main panel">
        <div class="storage-header"><div><div class="section-eyebrow">Dataset Workspaces</div><h2>存储管理</h2><p>派生数据是可恢复状态，默认永不自动删除。</p></div><button class="secondary-button" data-action="recheck-status" type="button">检查存储位置</button></div>
        <div class="workspace-list">${rows.map(workspaceRowMarkup).join("")}</div>
      </section>
      <aside class="storage-aside panel">
        <div class="section-eyebrow">本机派生状态</div><h3>约 10.1 GB</h3><p>包含索引、检查点与少量数据集设置。数据源不计入此处。</p>
        <div class="storage-stat"><small>默认清理策略</small><strong>不自动删除</strong></div>
        <div class="storage-stat"><small>工作区优先级</small><strong>sidecar → 中央回退</strong></div>
        <div class="safety-note"><strong>源数据安全</strong><br />所有清理操作只处理 ReacNet Scope 工作区，绝不删除 ReacNetGenerator 输出。</div>
      </aside>
    </div>`;
}

function workspaceRowMarkup(workspace) {
  return `
    <div class="workspace-row">
      <div><strong>${e(workspace.name)}</strong><small title="${e(workspace.path)}">${e(workspace.path)}</small></div>
      <span class="storage-badge ${workspace.type}">${e(workspace.typeLabel)}</span>
      <span class="workspace-size">${e(workspace.size)}</span>
      <span class="workspace-last-used">${e(workspace.lastUsed)}</span>
      <div class="workspace-actions">
        ${workspace.size !== "0 B" && workspace.type !== "unavailable" ? `<button class="secondary-button" data-action="open-cleanup" data-cleanup="clear" data-dataset="${workspace.datasetId}" type="button">清除派生数据</button>` : ""}
        ${workspace.type !== "unavailable" ? `<button class="text-button" data-action="open-cleanup" data-cleanup="forget" data-dataset="${workspace.datasetId}" type="button">忘记此数据集</button>` : `<button class="text-button" data-action="select-candidate" data-dataset="external" type="button">恢复位置</button>`}
      </div>
    </div>`;
}

function prototypeSwitcherMarkup() {
  const allowed = ["localhost", "127.0.0.1", ""].includes(window.location.hostname);
  if (!allowed) return "";
  return `
    <div class="prototype-switcher" aria-label="原型方案切换器">
      <button data-action="cycle-variant" data-direction="-1" type="button" aria-label="上一个方案">←</button>
      <div class="switcher-label"><span class="variant-name">${state.variant} — </span><small>?variant=${state.variant}</small></div>
      <button data-action="cycle-variant" data-direction="1" type="button" aria-label="下一个方案">→</button>
    </div>`;
}

function renderTaskDrawer() {
  const root = document.querySelector("#drawer-root");
  if (!state.drawerOpen) {
    root.innerHTML = "";
    return;
  }
  const groups = [
    ["running", "进行中", ["running"]],
    ["queued", "排队中", ["queued"]],
    ["attention", "需要处理", ["failed", "interrupted", "cancelled"]],
    ["completed", "最近完成", ["completed"]],
  ];
  root.innerHTML = `
    <button class="scrim" data-action="close-task-drawer" type="button" aria-label="关闭任务抽屉"></button>
    <aside class="task-drawer" aria-label="后台准备任务">
      <div class="drawer-header"><div><div class="section-eyebrow">Preparation Tasks</div><h2>后台任务</h2><p>任务属于数据集 + 分析能力 + 源修订，不属于当前页面。</p></div><button class="icon-button" data-action="close-task-drawer" type="button" aria-label="关闭">×</button></div>
      <div class="queue-controls"><span><strong>${state.queuePaused ? "队列已暂停" : "队列运行中"}</strong> · 重型任务并发数 1；暂停不会取消正在运行的任务。</span><button class="secondary-button" data-action="${state.queuePaused ? "resume-queue" : "pause-queue"}" type="button">${state.queuePaused ? "继续队列" : "暂停队列"}</button></div>
      <div class="drawer-body">
        ${state.tasks.length ? groups.map(([key, title, statuses]) => taskGroupMarkup(key, title, statuses)).join("") : `<div class="empty-tasks">还没有准备任务。可从当前数据集的能力行开始。</div>`}
      </div>
    </aside>`;
}

function taskGroupMarkup(key, title, statuses) {
  const tasks = state.tasks.filter((task) => statuses.includes(task.status));
  if (!tasks.length) return "";
  return `<section class="task-group"><div class="task-group-heading"><span>${title}</span><span>${tasks.length}</span></div>${tasks.map(taskCardMarkup).join("")}</section>`;
}

function taskCardMarkup(task) {
  const dataset = state.datasets[task.datasetId];
  const meta = CAPABILITY_META[task.capabilityId];
  const [statusText, tone] = statusInfo(task.status);
  const actions = [];
  if (task.status === "running") {
    actions.push(`<button class="secondary-button" data-action="advance-task" data-task="${task.id}" type="button">推进 17%</button>`);
    actions.push(`<button class="secondary-button" data-action="simulate-task-failure" data-task="${task.id}" type="button">模拟失败</button>`);
    actions.push(`<button class="secondary-button" data-action="simulate-source-change" data-task="${task.id}" type="button">模拟源变化</button>`);
    actions.push(`<button class="text-button" data-action="cancel-task" data-task="${task.id}" type="button">取消</button>`);
  } else if (task.status === "queued") {
    actions.push(`<button class="text-button" data-action="cancel-task" data-task="${task.id}" type="button">取消排队</button>`);
  } else if (["cancelled", "interrupted"].includes(task.status)) {
    actions.push(`<button class="primary-button" data-action="continue-task" data-task="${task.id}" type="button">继续</button>`);
  } else if (task.status === "failed") {
    actions.push(`<button class="primary-button" data-action="retry-task" data-task="${task.id}" type="button">重试</button>`);
  }
  actions.push(`<button class="text-button" data-action="preview-task-dataset" data-dataset="${task.datasetId}" type="button">预览数据集</button>`);
  return `
    <article class="task-card">
      <div class="task-card-head"><div><strong>${e(datasetLabel(dataset))} · ${e(meta.title)}</strong><small>${e(task.sourceRevision)}</small></div><span class="state-label ${tone}">${e(statusText)}</span></div>
      ${task.status === "running" ? progressMarkup(task) : ""}
      <div class="task-detail-line">${e(task.phase)}${task.processed ? ` · ${e(task.processed)}` : ""}<br />已用 ${e(task.elapsed)} · 检查点 ${e(task.checkpoint || "无")}${task.error ? `<br /><span style="color:var(--red)">${e(task.error)}</span>` : ""}</div>
      <div class="task-actions">${actions.join("")}</div>
    </article>`;
}

function renderModal() {
  const root = document.querySelector("#modal-root");
  if (!state.modal) {
    root.innerHTML = "";
    return;
  }
  const workspace = state.workspaces.find((item) => item.datasetId === state.modal.datasetId);
  const active = activeTasksForDataset(state.modal.datasetId);
  const forget = state.modal.type === "forget";
  root.innerHTML = `
    <button class="scrim" style="z-index:70" data-action="close-modal" type="button" aria-label="关闭确认"></button>
    <section class="modal-card" role="dialog" aria-modal="true" aria-labelledby="modal-title">
      <div class="modal-content">
        <h2 id="modal-title">${forget ? "忘记此数据集" : "清除派生数据"}</h2>
        ${active.length ? `<div class="notice" style="margin-top:12px"><strong>此数据集有 ${active.length} 个活动任务。</strong> 必须先取消运行或排队任务，才能清理工作区。</div>` : ""}
        <p>${forget ? "将移除这个数据集的全部 ReacNet Scope 工作区状态，包括索引、检查点、任务历史、显示名称、手动关联、设置和最近使用记录。" : "将删除派生索引和检查点，保留重新打开数据集所需的设置、关联与历史。"}</p>
        <div class="modal-facts"><span><strong>数据集：</strong>${e(workspace?.name || state.modal.datasetId)}</span><span><strong>可释放：</strong>${e(workspace?.reclaim || "待计算")}</span><span><strong>受影响能力：</strong>${e(workspace?.caps || "无")}</span></div>
        <div class="source-safe">✓ ReacNetGenerator 源数据保持不变，不会删除任何源文件。</div>
      </div>
      <div class="modal-actions">
        <button class="secondary-button" data-action="close-modal" type="button">取消</button>
        ${active.length ? `<button class="danger-button" data-action="cancel-related-tasks" data-dataset="${state.modal.datasetId}" type="button">取消相关任务</button>` : `<button class="${forget ? "danger-button" : "primary-button"}" data-action="confirm-cleanup" data-cleanup="${state.modal.type}" data-dataset="${state.modal.datasetId}" type="button">${forget ? "忘记此数据集" : `清除并释放 ${e(workspace?.reclaim || "空间")}`}</button>`}
      </div>
    </section>`;
}

function activeTasksForDataset(datasetId) {
  return state.tasks.filter((task) => task.datasetId === datasetId && ["running", "queued"].includes(task.status));
}

function render() {
  document.querySelector("#app").innerHTML = shellMarkup();
  renderTaskDrawer();
  renderModal();
  syncUrl();
}

function syncUrl() {
  const url = new URL(window.location.href);
  url.searchParams.set("variant", state.variant);
  url.searchParams.set("view", state.view);
  if (state.view === "select") url.searchParams.set("candidate", state.candidateId);
  else url.searchParams.delete("candidate");
  if (state.scenario) url.searchParams.set("scenario", state.scenario);
  else url.searchParams.delete("scenario");
  history.replaceState(null, "", url);
}

function showToast(message) {
  const root = document.querySelector("#toast-root");
  clearTimeout(state.toastTimer);
  root.innerHTML = `<div class="toast">${e(message)}</div>`;
  state.toastTimer = setTimeout(() => { root.innerHTML = ""; }, 3200);
}

function createTask(datasetId, capabilityId, overrides = {}) {
  const dataset = state.datasets[datasetId];
  const id = `task-${Date.now()}-${Math.random().toString(16).slice(2, 7)}`;
  const task = {
    id,
    datasetId,
    capabilityId,
    sourceRevision: dataset.revision,
    status: "queued",
    phase: "等待执行槽位",
    progress: 0,
    measurable: true,
    processed: "0 B / 0 B",
    elapsed: "0:00",
    checkpoint: "尚未创建",
    log: `task://${id}/build.log`,
    error: "",
    ...overrides,
  };
  state.tasks.push(task);
  dataset.capabilities[capabilityId].status = task.status;
  dispatchNext();
  return task;
}

function dispatchNext() {
  if (state.queuePaused || state.tasks.some((task) => task.status === "running")) return;
  const next = state.tasks.find((task) => task.status === "queued");
  if (!next) return;
  next.status = "running";
  next.phase = next.progress ? "从检查点继续扫描" : "扫描源证据";
  next.processed = next.progress ? `${next.progress * 11} MB / 1.1 GB` : "0 B / 1.1 GB";
  state.datasets[next.datasetId].capabilities[next.capabilityId].status = "running";
}

function cancelTask(task) {
  const wasRunning = task.status === "running";
  task.status = "cancelled";
  task.phase = "用户取消";
  task.checkpoint = task.progress ? `chunk-${Math.max(1, Math.floor(task.progress / 10))} · ${task.progress}%` : "任务元数据已保留";
  state.datasets[task.datasetId].capabilities[task.capabilityId].status = "cancelled";
  if (wasRunning) dispatchNext();
}

function continueTask(task) {
  task.status = "queued";
  task.error = "";
  task.phase = "等待从检查点继续";
  state.datasets[task.datasetId].capabilities[task.capabilityId].status = "queued";
  dispatchNext();
}

function failTask(task, sourceChanged = false) {
  task.status = "failed";
  task.phase = sourceChanged ? "源修订变化，拒绝发布" : "写入派生索引时失败";
  task.error = sourceChanged ? "SourceRevisionChanged：构建期间检测到源数据变化，未发布不完整输出" : "IndexWriteError：模拟磁盘写入失败";
  task.sourceChanged = sourceChanged;
  task.checkpoint = `chunk-${Math.max(1, Math.floor(task.progress / 10))} · ${task.progress}%`;
  state.datasets[task.datasetId].capabilities[task.capabilityId].status = sourceChanged ? "stale" : "failed";
  if (sourceChanged) state.datasets[task.datasetId].capabilities[task.capabilityId].detail = "源修订在准备期间发生变化；旧索引不可用，且未发布不完整输出";
  dispatchNext();
}

function advanceTask(task) {
  task.progress = Math.min(100, task.progress + 17);
  task.elapsed = `0:${String(Math.max(8, Math.round(task.progress * 0.9))).padStart(2, "0")}`;
  task.processed = `${Math.round(task.progress * 11)} MB / 1.1 GB`;
  task.checkpoint = `chunk-${Math.max(1, Math.floor(task.progress / 10))} · ${task.progress}%`;
  task.phase = task.progress < 34 ? "扫描源证据" : task.progress < 68 ? "构建记录映射" : task.progress < 100 ? "校验并写入索引" : "发布完成";
  if (task.progress >= 100) {
    task.status = "completed";
    task.elapsed = "1:31";
    state.datasets[task.datasetId].capabilities[task.capabilityId].status = "ready";
    state.datasets[task.datasetId].capabilities[task.capabilityId].detail = "索引与当前源修订一致";
    dispatchNext();
  }
}

function loadScenario(name) {
  resetSimulation();
  state.scenario = name;
  if (name === "first") {
    state.currentDatasetId = null;
    state.candidateId = "2cp";
    state.view = "select";
  } else if (name === "normal") {
    state.currentDatasetId = "2cp";
    state.view = "current";
  } else if (name === "queue") {
    state.currentDatasetId = "2cp";
    state.view = "current";
    createTask("2cp", "events", { progress: 37, status: "running", phase: "构建事件身份", processed: "407 MB / 1.1 GB", elapsed: "0:34", checkpoint: "chunk-3 · 30%" });
    createTask("2cp", "trajectory", { status: "queued", phase: "等待执行槽位" });
    createTask("2cp", "element", { status: "queued", phase: "等待执行槽位" });
    state.drawerOpen = true;
  } else if (name === "switch") {
    state.currentDatasetId = "rp3";
    state.view = "current";
    createTask("2cp", "events", { progress: 54, status: "running", phase: "建立反应发生身份", processed: "594 MB / 1.1 GB", elapsed: "0:51", checkpoint: "chunk-5 · 50%" });
    createTask("2cp", "element", { status: "queued", phase: "等待执行槽位" });
    state.drawerOpen = true;
  } else if (name === "recovery") {
    state.currentDatasetId = "2cp";
    state.view = "current";
    state.tasks = [
      { id: "task-interrupted", datasetId: "2cp", capabilityId: "events", sourceRevision: state.datasets["2cp"].revision, status: "interrupted", phase: "应用关闭时中断", progress: 44, measurable: true, processed: "484 MB / 1.1 GB", elapsed: "0:42", checkpoint: "chunk-4 · 40%", log: "task://task-interrupted/build.log", error: "" },
      { id: "task-cancelled", datasetId: "2cp", capabilityId: "trajectory", sourceRevision: state.datasets["2cp"].revision, status: "cancelled", phase: "用户取消", progress: 31, measurable: true, processed: "8,410 / 27,200 帧", elapsed: "0:28", checkpoint: "frames-8000 · 29%", log: "task://task-cancelled/build.log", error: "" },
      { id: "task-failed", datasetId: "2cp", capabilityId: "element", sourceRevision: state.datasets["2cp"].revision, status: "failed", phase: "写入派生索引时失败", progress: 26, measurable: true, processed: "12,480 / 48,001 帧", elapsed: "0:19", checkpoint: "frame-12000 · 25%", log: "task://task-failed/build.log", error: "IndexWriteError：模拟磁盘写入失败" },
    ];
    for (const task of state.tasks) state.datasets[task.datasetId].capabilities[task.capabilityId].status = task.status;
    state.drawerOpen = true;
  } else if (name === "repair") {
    state.currentDatasetId = "rp3";
    state.view = "current";
    state.focusedCap = "trajectory";
    state.expandedCaps = new Set(["trajectory"]);
  } else if (name === "storage") {
    state.currentDatasetId = "2cp";
    state.view = "storage";
  }
  render();
}

function cycleVariant(direction) {
  const keys = Object.keys(VARIANTS);
  const next = (keys.indexOf(state.variant) + direction + keys.length) % keys.length;
  state.variant = keys[next];
  render();
}

function handleAction(target) {
  const action = target.dataset.action;
  if (!action) return;
  const datasetId = target.dataset.dataset;
  const capabilityId = target.dataset.capability;

  if (action === "navigate-view") {
    state.view = target.dataset.view;
    if (state.view === "current" && !currentDataset()) state.view = "select";
  } else if (action === "select-candidate") {
    if (!state.datasets[datasetId]) return;
    state.candidateId = datasetId;
    state.view = "select";
  } else if (action === "remove-recent") {
    state.recentIds = state.recentIds.filter((id) => id !== datasetId);
    if (state.candidateId === datasetId) state.candidateId = currentDataset()?.id || "2cp";
    showToast("已从最近使用移除；没有删除任何数据或工作区。 ");
  } else if (action === "set-current") {
    const displayNameInput = document.querySelector("#candidate-display-name");
    if (displayNameInput) state.datasets[datasetId].displayName = displayNameInput.value.trim();
    state.currentDatasetId = datasetId;
    state.candidateId = datasetId;
    state.view = "current";
    state.recentIds = [datasetId, ...state.recentIds.filter((id) => id !== datasetId)].slice(0, 10);
    showToast(`已切换至 ${datasetLabel(state.datasets[datasetId])}`);
  } else if (action === "cancel-selection") {
    state.candidateId = state.currentDatasetId;
    state.view = "current";
  } else if (action === "browse-parent") {
    const parts = state.browsePath.split("/").filter(Boolean);
    parts.pop();
    state.browsePath = `/${parts.join("/")}` || "/";
  } else if (action === "browse-location") {
    const value = document.querySelector("#location-input")?.value.trim();
    if (value) {
      state.browsePath = value.endsWith("/rp3") ? "/data/runs/rp3-study" : value;
      if (value.toLowerCase().includes("rp3")) state.candidateId = "rp3";
      else if (value.toLowerCase().includes("external") || value.includes("EXT-RUN")) state.candidateId = "external";
      else state.candidateId = "2cp";
      showToast("已完成轻量候选检查；没有读取大型源文件。 ");
    }
  } else if (action === "set-path") {
    state.browsePath = target.dataset.path;
    if (state.browsePath.includes("rp3")) state.candidateId = "rp3";
  } else if (action === "toggle-cap") {
    state.expandedCaps.has(capabilityId) ? state.expandedCaps.delete(capabilityId) : state.expandedCaps.add(capabilityId);
  } else if (action === "focus-cap") {
    state.focusedCap = capabilityId;
  } else if (action === "prepare-cap") {
    const existing = taskFor(datasetId, capabilityId);
    if (existing && ["cancelled", "interrupted", "failed"].includes(existing.status)) continueTask(existing);
    else if (!existing) createTask(datasetId, capabilityId);
    state.drawerOpen = true;
  } else if (["cancel-cap-task", "continue-cap-task", "retry-cap-task"].includes(action)) {
    const task = taskFor(datasetId, capabilityId);
    if (task) {
      if (action === "cancel-cap-task") cancelTask(task);
      else continueTask(task);
    }
    state.drawerOpen = true;
  } else if (action === "open-task-drawer") {
    state.drawerOpen = true;
  } else if (action === "close-task-drawer") {
    state.drawerOpen = false;
  } else if (action === "toggle-overflow") {
    state.overflowOpen = !state.overflowOpen;
  } else if (action === "recheck-status") {
    state.overflowOpen = false;
    showToast("状态已重新检查：轻量刷新完成。 ");
  } else if (action === "simulate-restart") {
    for (const task of state.tasks) {
      if (["running", "queued"].includes(task.status)) {
        task.status = "interrupted";
        task.phase = "应用关闭时中断";
        task.checkpoint = task.progress ? `chunk-${Math.max(1, Math.floor(task.progress / 10))} · ${task.progress}%` : "任务定义已保留";
        state.datasets[task.datasetId].capabilities[task.capabilityId].status = "interrupted";
      }
    }
    state.overflowOpen = false;
    state.drawerOpen = true;
    showToast("模拟重启完成：任务未自动恢复，检查点已保留。 ");
  } else if (action === "reset-all") {
    loadScenario("first");
    showToast("原型已重置为首次使用状态。 ");
    return;
  } else if (action === "pause-queue") {
    state.queuePaused = true;
  } else if (action === "resume-queue") {
    state.queuePaused = false;
    dispatchNext();
  } else if (["advance-task", "simulate-task-failure", "simulate-source-change", "cancel-task", "continue-task", "retry-task"].includes(action)) {
    const task = state.tasks.find((item) => item.id === target.dataset.task);
    if (!task) return;
    if (action === "advance-task") advanceTask(task);
    else if (action === "simulate-task-failure") failTask(task, false);
    else if (action === "simulate-source-change") failTask(task, true);
    else if (action === "cancel-task") cancelTask(task);
    else continueTask(task);
  } else if (action === "preview-task-dataset") {
    state.candidateId = datasetId;
    state.view = "select";
    state.drawerOpen = false;
  } else if (action === "associate-source") {
    const cap = state.datasets[datasetId].capabilities[capabilityId];
    cap.source = `${state.datasets[datasetId].name}.lammpstrj（手动关联）`;
    cap.status = "unprepared";
    cap.detail = "手动关联已重新验证；现在可以准备轨迹帧索引";
    showToast("源证据关联已保存到 Dataset Workspace，并通过轻量验证。 ");
  } else if (action === "simulate-cap-stale") {
    const cap = state.datasets[datasetId].capabilities[capabilityId];
    cap.status = "stale";
    cap.detail = "源文件修订已变化；旧索引保留但不可用于分析";
    showToast("已模拟源数据变化；旧派生索引未删除，但能力已标记不可用。 ");
  } else if (action === "open-cleanup") {
    state.modal = { type: target.dataset.cleanup, datasetId };
  } else if (action === "close-modal") {
    state.modal = null;
  } else if (action === "cancel-related-tasks") {
    for (const task of activeTasksForDataset(datasetId)) cancelTask(task);
    showToast("相关任务已取消，所有有效检查点仍保留。 ");
  } else if (action === "confirm-cleanup") {
    confirmCleanup(target.dataset.cleanup, datasetId);
  } else if (action === "cycle-variant") {
    cycleVariant(Number(target.dataset.direction));
    return;
  }
  render();
}

function confirmCleanup(type, datasetId) {
  const workspace = state.workspaces.find((item) => item.datasetId === datasetId);
  if (!workspace || activeTasksForDataset(datasetId).length) return;
  if (type === "clear") {
    workspace.size = "12 KB";
    workspace.reclaim = "0 B";
    if (state.datasets[datasetId]) {
      for (const capId of ["events", "trajectory", "element"]) {
        const cap = state.datasets[datasetId].capabilities[capId];
        if (cap.status !== "missing") {
          cap.status = "unprepared";
          cap.detail = "派生数据已清除；源证据与数据集设置仍保留";
        }
      }
      state.datasets[datasetId].workspaceSize = "12 KB";
    }
    state.modal = null;
    showToast(`已清除 ${workspace.name} 的派生数据；源文件保持不变。`);
  } else {
    workspace.hidden = true;
    state.tasks = state.tasks.filter((task) => task.datasetId !== datasetId);
    state.recentIds = state.recentIds.filter((id) => id !== datasetId);
    if (state.datasets[datasetId]) state.datasets[datasetId].displayName = "";
    if (state.currentDatasetId === datasetId) {
      state.currentDatasetId = null;
      state.candidateId = state.datasets[datasetId] ? datasetId : "2cp";
      state.view = "select";
    }
    state.modal = null;
    showToast(`已忘记 ${workspace.name}；只移除 ReacNet Scope 工作区状态，源数据保持不变。`);
  }
}

document.addEventListener("click", (event) => {
  const target = event.target.closest("[data-action]");
  if (!target) return;
  if (target.dataset.action === "remove-recent") event.stopPropagation();
  handleAction(target);
});

document.addEventListener("change", (event) => {
  if (event.target.matches('[data-action="load-scenario"]') && event.target.value) {
    loadScenario(event.target.value);
  }
});

document.addEventListener("keydown", (event) => {
  const active = document.activeElement;
  const editing = active && (active.matches("input, textarea, select, [contenteditable]") || active.isContentEditable);
  if (event.key === "Enter" && active?.id === "location-input") {
    const button = document.querySelector('[data-action="browse-location"]');
    if (button) handleAction(button);
    return;
  }
  if (editing) return;
  if (event.key === "ArrowLeft") cycleVariant(-1);
  if (event.key === "ArrowRight") cycleVariant(1);
  if (event.key === "Escape") {
    state.drawerOpen = false;
    state.modal = null;
    state.overflowOpen = false;
    render();
  }
});

window.addEventListener("popstate", () => {
  state.variant = readVariant();
  render();
});

const initialScenario = new URLSearchParams(window.location.search).get("scenario");
if (["first", "normal", "queue", "switch", "recovery", "repair", "storage"].includes(initialScenario)) {
  loadScenario(initialScenario);
} else {
  render();
}
