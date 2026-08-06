(() => {
  "use strict";

  const variantOrder = ["A", "B", "C"];
  const variants = {
    A: {
      name: "双栏事务工作台",
      note: "浏览与候选证据并列；提交影响保持在右侧。",
    },
    B: {
      name: "线性决策流程",
      note: "按位置、候选、核对与提交顺序纵向推进。",
    },
    C: {
      name: "高密度三域控制台",
      note: "目录、候选矩阵和事务摘要同时可见。",
    },
  };

  const statusMeta = {
    ready: { label: "可直接使用", tone: "ready" },
    "needs-preparation": { label: "需准备索引", tone: "needs-preparation" },
    "missing-source": { label: "缺少源文件", tone: "missing-source" },
    preparing: { label: "正在准备", tone: "needs-preparation" },
    stale: { label: "需要重新验证", tone: "stale" },
    invalid: { label: "无法使用", tone: "invalid" },
  };

  const datasets = {
    baseline: {
      id: "ds-rp3-baseline-03",
      name: "基线条件 · replicate-03",
      path: "甲烷氧化 / 1000 K / replicate-03",
      revision: "源修订 8f22c1a",
      capabilities: [
        { name: "Species 检索", state: "ready", reason: "Species Abundance Evidence 与索引均可用。" },
        { name: "Reaction Type 检索", state: "ready", reason: "聚合 Reaction Evidence 可直接查询。" },
        { name: "反应事件", state: "ready", reason: "Timed Evidence Source 与事件索引可用。" },
        { name: "局部轨迹", state: "needs-preparation", reason: "轨迹源存在；需要准备轨迹帧索引。" },
        { name: "元素分布演化", state: "missing-source", reason: "此修订没有 Species Abundance Evidence。" },
      ],
    },
    heated: {
      id: "ds-rp3-heated-02",
      name: "升温条件 · replicate-02",
      path: "甲烷氧化 / 1200 K / replicate-02",
      revision: "源修订 c4309be",
      capabilities: [
        { name: "Species 检索", state: "ready", reason: "Species Abundance Evidence 与索引均可用。" },
        { name: "Reaction Type 检索", state: "ready", reason: "聚合 Reaction Evidence 可直接查询。" },
        { name: "反应事件", state: "needs-preparation", reason: "Timed Evidence Source 可用；事件索引尚未准备。" },
        { name: "局部轨迹", state: "missing-source", reason: "没有与此数据集匹配的 LAMMPS 轨迹。" },
        { name: "元素分布演化", state: "needs-preparation", reason: "源证据可用；元素分布索引尚未准备。" },
      ],
    },
    pressure: {
      id: "ds-rp3-pressure-01",
      name: "高压条件 · replicate-01",
      path: "甲烷氧化 / 2.0 GPa / replicate-01",
      revision: "源修订 f19ab06",
      capabilities: [
        { name: "Species 检索", state: "ready", reason: "Species Abundance Evidence 与索引均可用。" },
        { name: "Reaction Type 检索", state: "ready", reason: "聚合 Reaction Evidence 可直接查询。" },
        { name: "反应事件", state: "invalid", reason: "原生 Timed Evidence Source schema 不兼容。" },
        { name: "局部轨迹", state: "needs-preparation", reason: "轨迹源存在；需要准备轨迹帧索引。" },
        { name: "元素分布演化", state: "ready", reason: "元素分布索引可用。" },
      ],
    },
  };

  const fixtureStates = {
    "no-current": {
      label: "无 Current Dataset",
      selectionState: "browsing",
      currentState: "none",
      currentId: null,
      operationState: "none",
      candidateIds: [],
      selectedId: null,
      locationOpen: false,
      feedback: null,
      capabilityAxis: "not-inspected",
    },
    "single-selected": {
      label: "单候选已选，尚未提交",
      selectionState: "candidate-selected",
      currentState: "active",
      currentId: "baseline",
      operationState: "none",
      candidateIds: ["heated"],
      selectedId: "heated",
      locationOpen: true,
      feedback: null,
      capabilityAxis: "orthogonal",
    },
    "multi-none": {
      label: "多候选，默认不选中",
      selectionState: "browsing",
      currentState: "active",
      currentId: "baseline",
      operationState: "none",
      candidateIds: ["baseline", "heated", "pressure"],
      selectedId: null,
      locationOpen: true,
      feedback: null,
      capabilityAxis: "not-inspected",
    },
    "validating-old": {
      label: "正在验证，旧 Current Dataset 仍有效",
      selectionState: "validating",
      currentState: "active",
      currentId: "baseline",
      operationState: "running",
      candidateIds: ["baseline", "heated", "pressure"],
      selectedId: "heated",
      locationOpen: true,
      capabilityAxis: "orthogonal",
      feedback: {
        tone: "info",
        title: "正在验证“升温条件 · replicate-02”",
        text: "正在核对 Dataset Identity、允许根目录和源修订。验证完成前，旧 Current Dataset“基线条件 · replicate-03”继续有效。",
      },
    },
    "failure-old": {
      label: "验证失败，旧 Current Dataset 仍有效",
      selectionState: "failed",
      currentState: "active",
      currentId: "baseline",
      operationState: "failed",
      candidateIds: ["baseline", "heated", "pressure"],
      selectedId: "heated",
      locationOpen: true,
      capabilityAxis: "orthogonal",
      feedback: {
        tone: "error",
        title: "候选的源修订已变化，未切换数据集",
        text: "旧 Current Dataset“基线条件 · replicate-03”和已保留的查询条件没有改变。刷新候选后可以重试。请求标识：VAL-2048。",
      },
    },
    "partial-success": {
      label: "切换成功，Analysis Capability 部分可用",
      selectionState: "candidate-selected",
      currentState: "active",
      currentId: "heated",
      operationState: "succeeded",
      candidateIds: ["heated"],
      selectedId: "heated",
      locationOpen: true,
      capabilityAxis: "partial-ready",
      feedback: {
        tone: "success",
        title: "当前数据集已切换为“升温条件 · replicate-02”",
        text: "旧数据集结果与选择已清空；查询条件已保留，但尚未在当前数据集运行。未就绪能力可以稍后在“管理数据”中准备。",
      },
    },
    "revision-changed": {
      label: "Current Dataset 源修订已变化",
      selectionState: "browsing",
      currentState: "revision-changed",
      currentId: "baseline",
      operationState: "none",
      candidateIds: ["baseline", "heated", "pressure"],
      selectedId: null,
      locationOpen: true,
      capabilityAxis: "stale",
      feedback: {
        tone: "warning",
        title: "Current Dataset 的源工件已变化",
        text: "数据集身份保持不变；旧修订结果不再作为当前证据。请更新当前数据集状态，或选择另一个 Dataset Candidate。",
      },
    },
  };

  const svg = {
    left: `<svg class="svg-icon" viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M15 18l-6-6 6-6" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>`,
    right: `<svg class="svg-icon" viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M9 6l6 6-6 6" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>`,
    swap: `<svg class="svg-icon" viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M7 7h11m0 0-3-3m3 3-3 3M17 17H6m0 0 3 3m-3-3 3-3" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>`,
    task: `<svg class="task-icon" viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M7 4h10v16H7zM9.5 8h5M9.5 12h5M9.5 16h3" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/></svg>`,
    folder: `<svg class="svg-icon" viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M3.5 6.5h6l2 2H20.5v9H3.5z" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round"/></svg>`,
  };

  const params = new URLSearchParams(window.location.search);
  let currentVariant = variants[params.get("variant")] ? params.get("variant") : "A";
  let currentStateKey = fixtureStates[params.get("state")] ? params.get("state") : "single-selected";
  let selectedOverride;

  function stateView() {
    const fixture = fixtureStates[currentStateKey];
    const selectedId = selectedOverride === undefined ? fixture.selectedId : selectedOverride;
    return {
      ...fixture,
      selectedId,
      selectionState:
        selectedOverride !== undefined && selectedId && fixture.selectionState === "browsing"
          ? "candidate-selected"
          : fixture.selectionState,
    };
  }

  function currentDataset(state) {
    return state.currentId ? datasets[state.currentId] : null;
  }

  function selectedDataset(state) {
    return state.selectedId ? datasets[state.selectedId] : null;
  }

  function updateUrl() {
    const next = new URL(window.location.href);
    next.searchParams.set("variant", currentVariant);
    next.searchParams.set("state", currentStateKey);
    window.history.replaceState({}, "", next);
  }

  function currentStatus(state) {
    if (state.currentState === "none") return { label: "尚未选择", tone: "danger" };
    if (state.currentState === "revision-changed") return { label: "修订已变化", tone: "warning" };
    return { label: "当前有效", tone: "ready" };
  }

  function primaryConfig(state) {
    const selected = selectedDataset(state);
    const current = currentDataset(state);

    if (state.currentState === "revision-changed" && !selected) {
      return {
        label: "更新当前数据集状态",
        disabled: false,
        reason: "重新验证相同 Dataset Identity 并采用新的源修订；这不是切换数据集。",
        action: "refresh-revision",
      };
    }
    if (state.operationState === "running") {
      return {
        label: "正在验证…",
        disabled: true,
        reason: "只锁定候选选择和重复提交；旧 Current Dataset 仍可供其他页面使用。",
        action: null,
      };
    }
    if (state.operationState === "failed" && selected) {
      return {
        label: "重试验证",
        disabled: false,
        reason: "将重新核对所选候选；失败不会改变 Current Dataset。",
        action: "validate",
      };
    }
    if (selected && current && selected.id === current.id && selected.revision === current.revision) {
      return {
        label: "当前正在使用",
        disabled: true,
        reason: "候选与 Current Dataset 的身份和源修订相同，无需再次提交。",
        action: null,
      };
    }
    if (selected) {
      return {
        label: "使用此数据集",
        disabled: false,
        reason: "提交前将再次验证 Dataset Identity 和源修订。",
        action: "validate",
      };
    }
    return {
      label: "使用此数据集",
      disabled: true,
      reason: state.locationOpen ? "请先选择一个 Dataset Candidate。" : "请先打开允许根目录并选择 Dataset Candidate。",
      action: null,
    };
  }

  function capabilityListForDetail(state, dataset) {
    if (!dataset) return [];
    if (state.currentState !== "revision-changed" || state.currentId !== datasetKey(dataset)) {
      return dataset.capabilities;
    }
    return dataset.capabilities.map((capability, index) => ({
      ...capability,
      state: index === 3 ? "invalid" : "stale",
      reason:
        index === 3
          ? "轨迹路径无法在新修订中重新验证；相关结果已停用。"
          : "源修订已变化；更新当前数据集状态后重新判定。",
    }));
  }

  function datasetKey(dataset) {
    return Object.keys(datasets).find((key) => datasets[key] === dataset);
  }

  function globalHeader(state) {
    const current = currentDataset(state);
    const status = currentStatus(state);
    return `
      <header class="global-context" aria-label="全局数据集上下文">
        <div class="brand-lockup">
          <span class="brand-mark" aria-hidden="true">RS</span>
          <span>
            <span class="brand-name">ReacNet Scope</span>
            <span class="brand-subtitle">反应证据工作台</span>
          </span>
        </div>
        <div class="global-dataset">
          <span class="status-mark ${status.tone}">${status.label}</span>
          <span class="global-dataset-copy">
            <span class="meta-label">Current Dataset · 当前数据集</span>
            <span class="global-dataset-name">${current ? current.name : "没有 Current Dataset"}</span>
          </span>
        </div>
        <div class="task-summary" aria-label="后台任务摘要">
          ${svg.task}
          <span>后台任务 1 · 事件索引 62%</span>
        </div>
      </header>`;
  }

  function navigation() {
    const items = ["物种检索", "反应式检索", "反应事件", "管理数据"];
    const navItems = items
      .map((item) => `<button class="nav-item ${item === "管理数据" ? "active" : ""}" type="button">${item}</button>`)
      .join("");
    return `
      <aside class="side-nav" aria-label="分析功能">
        <div class="nav-group-label">检索与证据</div>
        ${navItems}
        <div class="nav-group-label">数据工作区</div>
        <button class="nav-item active" type="button">选择数据集</button>
        <button class="nav-item" type="button">批量对比</button>
      </aside>
      <nav class="mobile-nav" aria-label="紧凑导航">
        ${navItems}
      </nav>`;
  }

  function stateControl() {
    const options = Object.entries(fixtureStates)
      .map(([key, fixture]) => `<option value="${key}" ${key === currentStateKey ? "selected" : ""}>${fixture.label}</option>`)
      .join("");
    return `
      <div class="prototype-state-control" role="region" aria-label="原型状态选择器">
        <span class="prototype-tag">PROTOTYPE 状态</span>
        <label for="prototype-state-select">直接切换规范状态</label>
        <select id="prototype-state-select">${options}</select>
        <span class="field-note">静态夹具；不会读取文件或改变真实会话。</span>
      </div>`;
  }

  function stateLedger(state) {
    const axes = [
      ["Dataset Selection", state.selectionState],
      ["Current Dataset Context", state.currentState],
      ["Analysis Capability", state.capabilityAxis],
      ["Operation", state.operationState],
    ];
    return `<aside class="state-ledger" aria-label="当前原型的正交状态">${axes
      .map(
        ([label, value]) => `
          <div class="state-axis">
            <span class="state-axis-label">${label}</span>
            <span class="state-axis-value">${value}</span>
          </div>`,
      )
      .join("")}</aside>`;
  }

  function feedback(state) {
    if (!state.feedback) return "";
    const role = state.feedback.tone === "error" ? "alert" : "status";
    const tabindex = state.feedback.tone === "error" ? ` tabindex="-1"` : "";
    return `
      <div class="feedback ${state.feedback.tone}" role="${role}"${tabindex}>
        <strong>${state.feedback.title}</strong>
        <span>${state.feedback.text}</span>
      </div>`;
  }

  function locationControls(state, compact = false) {
    if (!state.locationOpen) {
      return `
        <div class="location-stack">
          <div>
            <span class="panel-kicker">最近数据集</span>
            <div class="recent-list">
              <button class="text-button" type="button" data-open-location>基线条件 · replicate-03</button>
              <button class="text-button" type="button" data-open-location>升温条件 · replicate-02</button>
            </div>
          </div>
          <div>
            <span class="panel-kicker">允许根目录</span>
            <button class="button-secondary" type="button" data-open-location>打开“项目数据”</button>
          </div>
        </div>`;
    }
    return `
      <div class="location-stack">
        <div>
          <span class="panel-kicker">允许根目录与相对位置</span>
          <nav class="crumbs" aria-label="数据集位置面包屑">
            <button type="button" class="crumb-button">项目数据</button>
            <span class="crumb-separator" aria-hidden="true">/</span>
            <button type="button" class="crumb-button">甲烷氧化</button>
            <span class="crumb-separator" aria-hidden="true">/</span>
            <button type="button" class="crumb-button">候选数据集</button>
          </nav>
        </div>
        ${
          compact
            ? ""
            : `<details class="expert-path">
                <summary>输入服务器路径（专家入口）</summary>
                <div class="path-entry">
                  <input type="text" aria-label="服务器目录或公共前缀" value="/allowed/projects/methane/candidates" />
                  <button class="button-secondary" type="button">前往</button>
                </div>
                <div class="field-note">仅按 Enter 或“前往”导航；失焦不改变浏览位置。</div>
              </details>`
        }
      </div>`;
  }

  function summaryBadges(dataset) {
    const focus = dataset.capabilities.filter((capability) =>
      ["Species 检索", "反应事件"].includes(capability.name),
    );
    return focus
      .map((capability) => {
        const meta = statusMeta[capability.state];
        return `<span class="mini-badge">${capability.name.replace("检索", "")}：${meta.label}</span>`;
      })
      .join("");
  }

  function candidateOptions(state, mode = "cards") {
    if (!state.candidateIds.length) {
      return `<div class="candidate-empty"><span>尚未打开目录。先从最近数据集或允许根目录开始。</span></div>`;
    }
    const disabled = state.operationState === "running" ? "disabled" : "";
    if (mode === "table") {
      return `
        <div class="table-header" aria-hidden="true">
          <span></span><span>Dataset Candidate</span><span>Species</span><span>事件证据</span><span>轨迹</span>
        </div>
        ${state.candidateIds
          .map((id) => {
            const dataset = datasets[id];
            const caps = dataset.capabilities;
            const species = statusMeta[caps[0].state];
            const event = statusMeta[caps[2].state];
            const trajectory = statusMeta[caps[3].state];
            return `
              <label class="table-row ${state.selectedId === id ? "selected" : ""}">
                <input type="radio" name="dataset-candidate" value="${id}" ${state.selectedId === id ? "checked" : ""} ${disabled} />
                <span class="table-name">${dataset.name}<span class="table-path">${dataset.path}</span></span>
                <span class="table-state ${species.tone}">${species.label}</span>
                <span class="table-state ${event.tone}">${event.label}</span>
                <span class="table-state ${trajectory.tone}">${trajectory.label}</span>
              </label>`;
          })
          .join("")}`;
    }
    return state.candidateIds
      .map((id) => {
        const dataset = datasets[id];
        return `
          <label class="candidate-option ${state.selectedId === id ? "selected" : ""}">
            <input type="radio" name="dataset-candidate" value="${id}" ${state.selectedId === id ? "checked" : ""} ${disabled} />
            <span>
              <span class="candidate-name">${dataset.name}</span>
              <span class="candidate-path">${dataset.path}</span>
            </span>
            <span class="candidate-summary-badges">${summaryBadges(dataset)}</span>
          </label>`;
      })
      .join("");
  }

  function candidateFieldset(state, mode = "cards") {
    const countText = state.candidateIds.length
      ? `找到 ${state.candidateIds.length} 个，按名称排序`
      : "尚未浏览";
    return `
      <fieldset class="candidate-fieldset">
        <legend>Dataset Candidate · 数据集候选 <span class="candidate-count">${countText}</span></legend>
        <div class="${mode === "table" ? "candidate-table" : "candidate-list"}">
          ${candidateOptions(state, mode)}
        </div>
      </fieldset>`;
  }

  function detailDataset(state) {
    const selected = selectedDataset(state);
    if (selected) return { dataset: selected, role: "Dataset Candidate · 候选能力" };
    if (state.currentState === "revision-changed") {
      return { dataset: currentDataset(state), role: "Current Dataset · 受影响能力" };
    }
    return { dataset: null, role: "Dataset Candidate · 候选能力" };
  }

  function capabilityRows(state, dataset, compact = false) {
    const capabilities = capabilityListForDetail(state, dataset);
    if (!capabilities.length) return "";
    if (compact) {
      return capabilities
        .map((capability) => {
          const meta = statusMeta[capability.state];
          return `<div class="rail-cap-row"><span>${capability.name}</span><span class="table-state ${meta.tone}">${meta.label}</span></div>`;
        })
        .join("");
    }
    return capabilities
      .map((capability) => {
        const meta = statusMeta[capability.state];
        return `
          <div class="capability-line">
            <span class="capability-copy">
              <span class="capability-name">${capability.name}</span>
              <span class="capability-reason">${capability.reason}</span>
            </span>
            <span class="cap-badge ${meta.tone}">${meta.label}</span>
          </div>`;
      })
      .join("");
  }

  function capabilityPanel(state) {
    const detail = detailDataset(state);
    if (!detail.dataset) {
      return `
        <section class="panel" id="evidence-anchor" aria-labelledby="candidate-evidence-title">
          <div class="panel-head">
            <p class="panel-kicker">候选证据</p>
            <h2 id="candidate-evidence-title">逐项查看 Analysis Capability</h2>
          </div>
          <div class="panel-body">
            <div class="candidate-empty">选择一个 Dataset Candidate 后，在这里逐项显示能力、原因和恢复方向；不会显示总完整度。</div>
          </div>
        </section>`;
    }
    return `
      <section class="panel" id="evidence-anchor" aria-labelledby="candidate-evidence-title">
        <div class="panel-head">
          <p class="panel-kicker">${detail.role}</p>
          <div class="candidate-heading">
            <span>
              <h2 id="candidate-evidence-title">${detail.dataset.name}</h2>
              <span class="candidate-path">${detail.dataset.path} · ${detail.dataset.revision}</span>
            </span>
          </div>
          <p class="panel-subtitle">能力互相独立；缺少一项不会把整个数据集概括成“不完整”。</p>
        </div>
        <div class="panel-body">
          <div class="capability-list">${capabilityRows(state, detail.dataset)}</div>
          <details class="expert-path">
            <summary>查看源工件与绝对路径</summary>
            <p class="field-note">原型占位：普通界面默认只显示允许根名称和相对路径。</p>
          </details>
        </div>
      </section>`;
  }

  function contextBox(label, dataset, candidate = false, empty = "尚无") {
    return `
      <div class="context-box ${candidate ? "candidate" : ""}">
        <span class="meta-label">${label}</span>
        <span class="context-name">${dataset ? dataset.name : empty}</span>
        ${dataset ? `<span class="candidate-path">${dataset.revision}</span>` : ""}
      </div>`;
  }

  function transactionContent(state) {
    const current = currentDataset(state);
    const selected = selectedDataset(state);
    const primary = primaryConfig(state);
    return `
      ${state.operationState === "running" ? `<div class="progress-line" aria-hidden="true"></div>` : ""}
      <div class="context-compare">
        ${contextBox("Current Dataset · 当前仍有效", current, false, "没有 Current Dataset")}
        <span class="compare-arrow" aria-hidden="true">${svg.right}</span>
        ${contextBox("Dataset Candidate · 将要使用", selected, true, "尚未选择候选")}
      </div>
      <ul class="impact-list">
        <li>旧数据集绑定的结果与选择会清空。</li>
        <li>具有相同领域语义的查询条件会保留，但不会自动重新查询。</li>
        <li>“使用此数据集”不会自动启动 Preparation Task。</li>
      </ul>
      <div class="button-row">
        ${state.operationState === "running" ? `<button class="button-text" type="button" data-cancel-validation>放弃本次结果</button>` : `<button class="button-text" type="button">取消并返回</button>`}
        <button class="button-primary" type="button" data-primary-action="${primary.action || ""}" ${primary.disabled ? "disabled" : ""}>${primary.label}</button>
      </div>
      <p class="button-reason">${primary.reason}</p>`;
  }

  function transactionPanel(state) {
    return `
      <section class="panel transaction-card" id="transaction-anchor" aria-labelledby="transaction-title">
        <div class="panel-head">
          <p class="panel-kicker">提交与后续影响</p>
          <h2 id="transaction-title">核对上下文切换</h2>
        </div>
        <div class="panel-body">${transactionContent(state)}</div>
      </section>`;
  }

  function variantA(state) {
    return `
      <div class="variant-a-grid" data-variant="A">
        <section class="panel" id="workspace-anchor" aria-labelledby="browse-title">
          <div class="panel-head">
            <p class="panel-kicker">浏览与选择</p>
            <h2 id="browse-title">从允许位置选择 Dataset Candidate</h2>
            <p class="panel-subtitle">最近数据集 → 允许根 → 相对面包屑 → 候选；路径输入是专家入口。</p>
          </div>
          <div class="panel-body">
            ${locationControls(state)}
            <div style="height: 16px" aria-hidden="true"></div>
            ${candidateFieldset(state)}
          </div>
        </section>
        <div class="variant-a-detail">
          ${capabilityPanel(state)}
          ${transactionPanel(state)}
        </div>
      </div>`;
  }

  function stepCapabilityGrid(state) {
    const detail = detailDataset(state);
    if (!detail.dataset) {
      return `<div class="candidate-empty">完成候选选择后才显示能力核对；不会产生一个总完整度。</div>`;
    }
    return `<div class="step-capability-grid">${capabilityListForDetail(state, detail.dataset)
      .map((capability) => {
        const meta = statusMeta[capability.state];
        return `
          <div class="step-capability">
            <strong>${capability.name}</strong>
            <div class="capability-reason">${capability.reason}</div>
            <span class="cap-badge ${meta.tone}">${meta.label}</span>
          </div>`;
      })
      .join("")}</div>`;
  }

  function variantB(state) {
    const hasLocation = state.locationOpen;
    const hasCandidate = Boolean(selectedDataset(state));
    return `
      <div class="variant-b-flow" data-variant="B">
        <section class="step-card ${!hasLocation ? "current" : ""}" aria-labelledby="step-location-title">
          <div class="step-number" aria-hidden="true">1</div>
          <div class="step-content">
            <div class="step-heading">
              <span><h2 id="step-location-title">确定浏览位置</h2><p class="step-copy">只显示允许根名称和相对位置。</p></span>
              <span class="status-mark ${hasLocation ? "ready" : ""}">${hasLocation ? "已打开" : "当前步骤"}</span>
            </div>
            ${locationControls(state, true)}
          </div>
        </section>
        <section class="step-card ${hasLocation && !hasCandidate ? "current" : ""}" aria-labelledby="step-candidate-title">
          <div class="step-number" aria-hidden="true">2</div>
          <div class="step-content">
            <div class="step-heading">
              <span><h2 id="step-candidate-title">选择 Dataset Candidate</h2><p class="step-copy">单候选自动选中；多候选保持未选中，直到用户明确选择。</p></span>
              <span class="status-mark ${hasCandidate ? "ready" : ""}">${hasCandidate ? "已选择" : "待选择"}</span>
            </div>
            ${candidateFieldset(state)}
          </div>
        </section>
        <section class="step-card ${hasCandidate ? "current" : ""}" aria-labelledby="step-review-title">
          <div class="step-number" aria-hidden="true">3</div>
          <div class="step-content">
            <div class="step-heading">
              <span><h2 id="step-review-title">核对能力与切换影响</h2><p class="step-copy">先看每项能力，再执行唯一主行动。</p></span>
              <span class="status-mark ${hasCandidate ? "warning" : ""}">${hasCandidate ? "等待提交" : "尚未开始"}</span>
            </div>
            ${stepCapabilityGrid(state)}
            <div class="step-transaction">
              <div>${transactionContent(state)}</div>
            </div>
          </div>
        </section>
      </div>`;
  }

  function treeZone(state) {
    return `
      <section class="console-zone" aria-labelledby="console-location-title">
        <div class="console-zone-head"><h2 id="console-location-title">浏览位置</h2><span class="tree-meta">允许根与相对目录</span></div>
        <div class="tree-list">
          <button class="tree-row active" type="button" data-open-location>${svg.folder}<span>项目数据</span></button>
          <button class="tree-row tree-indent" type="button" data-open-location>${svg.folder}<span>甲烷氧化</span></button>
          <button class="tree-row tree-indent active" type="button" data-open-location>${svg.folder}<span>候选数据集</span></button>
          <button class="tree-row tree-indent" type="button">${svg.folder}<span>归档</span></button>
          <button class="tree-row tree-indent" type="button">${svg.folder}<span>批量对比</span></button>
          <details class="expert-path">
            <summary>输入服务器路径</summary>
            <div class="path-entry"><input type="text" aria-label="服务器目录或公共前缀" value="/allowed/projects/methane/candidates" /><button type="button">前往</button></div>
          </details>
        </div>
      </section>`;
  }

  function consoleRail(state) {
    const current = currentDataset(state);
    const selected = selectedDataset(state);
    const detail = detailDataset(state);
    const primary = primaryConfig(state);
    return `
      <section class="console-zone" aria-labelledby="console-transaction-title">
        <div class="console-zone-head"><h2 id="console-transaction-title">上下文事务</h2><span class="tree-meta">能力与影响同时核对</span></div>
        <div class="console-rail">
          <div class="rail-section">
            <h3>身份对照</h3>
            <div class="rail-context"><span class="meta-label">Current Dataset</span><span class="context-name">${current ? current.name : "没有 Current Dataset"}</span></div>
            <div class="rail-context candidate"><span class="meta-label">Dataset Candidate</span><span class="context-name">${selected ? selected.name : "尚未选择候选"}</span></div>
          </div>
          <div class="rail-section">
            <h3>${detail.role}</h3>
            ${detail.dataset ? `<div class="rail-cap-list">${capabilityRows(state, detail.dataset, true)}</div>` : `<p class="muted">选择候选后逐项显示能力。</p>`}
          </div>
          <div class="rail-section">
            <h3>切换影响</h3>
            <p class="capability-reason">清除旧结果与选择；保留查询条件但不自动运行；不启动 Preparation Task。</p>
            ${state.operationState === "running" ? `<div class="progress-line" aria-hidden="true"></div>` : ""}
            <div class="button-row">
              <button class="button-primary" type="button" data-primary-action="${primary.action || ""}" ${primary.disabled ? "disabled" : ""}>${primary.label}</button>
              ${state.operationState === "running" ? `<button class="button-secondary" type="button" data-cancel-validation>放弃本次结果</button>` : `<button class="button-text" type="button">取消并返回</button>`}
            </div>
            <p class="button-reason">${primary.reason}</p>
          </div>
        </div>
      </section>`;
  }

  function variantC(state) {
    return `
      <div class="variant-c-console" data-variant="C">
        ${treeZone(state)}
        <section class="console-zone" aria-labelledby="console-candidates-title">
          <div class="console-zone-head"><h2 id="console-candidates-title">Dataset Candidate 矩阵</h2><span class="tree-meta">${state.candidateIds.length ? `显示 ${state.candidateIds.length} / ${state.candidateIds.length}` : "尚未浏览"} · 名称稳定排序</span></div>
          ${candidateFieldset(state, "table")}
        </section>
        ${consoleRail(state)}
      </div>`;
  }

  function prototypeSwitcher() {
    const variant = variants[currentVariant];
    const localHost = ["127.0.0.1", "localhost", ""].includes(window.location.hostname);
    return `
      <div class="prototype-switcher" role="region" aria-label="布局变体切换器" ${localHost ? "" : "hidden"}>
        <button class="switcher-button" type="button" data-variant-direction="-1" aria-label="上一个布局变体">${svg.left}</button>
        <div class="switcher-label" aria-live="polite">
          <strong>${currentVariant} — ${variant.name}</strong>
          <span>${variant.note}</span>
        </div>
        <button class="switcher-button" type="button" data-variant-direction="1" aria-label="下一个布局变体">${svg.right}</button>
      </div>`;
  }

  function render() {
    const state = stateView();
    const variantRenderer = { A: variantA, B: variantB, C: variantC }[currentVariant];
    document.getElementById("prototype-root").innerHTML = `
      <div class="app-shell">
        ${globalHeader(state)}
        <div class="workspace-shell">
          ${navigation()}
          <main class="page-main" id="main-content">
            <div class="page-frame">
              <div class="page-heading-row">
                <div>
                  <p class="eyebrow">数据工作区 / 选择数据集</p>
                  <h1 id="page-title" tabindex="-1">选择 Dataset Candidate</h1>
                  <p class="page-description">先检查候选与独立 Analysis Capability；只有显式提交且最新源修订验证成功后，才替换 Current Dataset。</p>
                </div>
                <a class="return-link" href="#">返回物种检索</a>
              </div>
              ${stateControl()}
              ${stateLedger(state)}
              ${feedback(state)}
              ${variantRenderer(state)}
            </div>
          </main>
        </div>
        ${prototypeSwitcher()}
      </div>`;
    bindInteractions();
    const captureView = new URLSearchParams(window.location.search).get("view");
    if (["workspace", "evidence", "transaction"].includes(captureView)) {
      document.getElementById(`${captureView}-anchor`)?.scrollIntoView({ block: "start" });
    }
  }

  function setState(key, preserveFocus = false) {
    if (!fixtureStates[key]) return;
    currentStateKey = key;
    selectedOverride = undefined;
    updateUrl();
    render();
    document.getElementById("prototype-announcer").textContent = `原型状态：${fixtureStates[key].label}`;
    if (preserveFocus) document.getElementById("prototype-state-select")?.focus();
  }

  function cycleVariant(direction, preserveFocus = false) {
    const index = variantOrder.indexOf(currentVariant);
    currentVariant = variantOrder[(index + direction + variantOrder.length) % variantOrder.length];
    updateUrl();
    render();
    document.getElementById("prototype-announcer").textContent = `布局变体 ${currentVariant}：${variants[currentVariant].name}`;
    if (preserveFocus) document.querySelector(`[data-variant-direction="${direction}"]`)?.focus();
  }

  function bindInteractions() {
    document.getElementById("prototype-state-select")?.addEventListener("change", (event) => {
      setState(event.target.value, true);
    });

    document.querySelectorAll("[data-variant-direction]").forEach((button) => {
      button.addEventListener("click", () => cycleVariant(Number(button.dataset.variantDirection), true));
    });

    document.querySelectorAll('input[name="dataset-candidate"]').forEach((radio) => {
      radio.addEventListener("change", () => {
        selectedOverride = radio.value;
        render();
        document.getElementById("prototype-announcer").textContent = `已选择 Dataset Candidate：${datasets[radio.value].name}。Current Dataset 未改变。`;
        document.querySelector(`input[name="dataset-candidate"][value="${radio.value}"]`)?.focus();
      });
    });

    document.querySelectorAll("[data-open-location]").forEach((button) => {
      button.addEventListener("click", () => {
        currentStateKey = "multi-none";
        selectedOverride = undefined;
        updateUrl();
        render();
        document.getElementById("prototype-announcer").textContent = "已打开项目数据 / 甲烷氧化 / 候选数据集。找到三个 Dataset Candidate。";
      });
    });

    document.querySelectorAll("[data-primary-action]").forEach((button) => {
      button.addEventListener("click", () => {
        if (button.dataset.primaryAction === "validate") {
          setState("validating-old");
        } else if (button.dataset.primaryAction === "refresh-revision") {
          document.getElementById("prototype-announcer").textContent = "原型未连接验证服务：将重新验证相同 Dataset Identity 和新源修订。";
        }
      });
    });

    document.querySelectorAll("[data-cancel-validation]").forEach((button) => {
      button.addEventListener("click", () => setState("single-selected"));
    });
  }

  document.addEventListener("keydown", (event) => {
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
    const target = event.target;
    if (
      target instanceof Element &&
      target.matches("input, textarea, select, button, [contenteditable='true']")
    ) {
      return;
    }
    event.preventDefault();
    cycleVariant(event.key === "ArrowRight" ? 1 : -1);
  });

  updateUrl();
  render();
})();
