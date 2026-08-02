(function () {
  "use strict";

  window.dash_clientside = Object.assign({}, window.dash_clientside);
  const namespace = window.dash_clientside.reacnetScope = Object.assign(
    {},
    window.dash_clientside.reacnetScope
  );

  const state = {
    container: null,
    viewer: null,
    lastCameraKey: "",
    resizeObserver: null,
    eventKey: "",
    hoverLabel: null,
    selectedAtomId: null,
    selectedShape: null,
  };

  function numeric(value, fallback) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : fallback;
  }

  function atomCoordinate(atom, axis) {
    const displayValue = atom[`display_${axis}`];
    return numeric(displayValue, numeric(atom[axis], 0));
  }

  function parseBond(value) {
    const match = String(value || "").match(/^(\d+)-(\d+)(?:-([0-9.]+))?$/);
    if (!match) {
      return null;
    }
    return {
      left: Number(match[1]),
      right: Number(match[2]),
      order: Math.max(1, Math.min(3, Math.round(numeric(match[3], 1)))),
    };
  }

  function selectedIds(viewerData, scope) {
    const groups = viewerData.atom_groups || {};
    if (scope === "core") {
      return new Set((groups.core || []).map(Number));
    }
    if (scope === "participants") {
      const values = groups.participants || groups.reactant || groups.product || [];
      return new Set(values.map(Number));
    }
    return new Set((groups.context || []).map(Number));
  }

  function ensureViewer(container) {
    if (state.viewer && state.container === container) {
      return state.viewer;
    }
    if (state.resizeObserver) {
      state.resizeObserver.disconnect();
    }
    container.replaceChildren();
    state.container = container;
    state.viewer = window.$3Dmol.createViewer(container, {
      backgroundColor: "#fbfcfe",
      antialias: true,
    });
    if (window.ResizeObserver) {
      state.resizeObserver = new ResizeObserver(function () {
        if (state.viewer && typeof state.viewer.resize === "function") {
          state.viewer.resize();
          state.viewer.render();
        }
      });
      state.resizeObserver.observe(container);
    }
    state.lastCameraKey = "";
    return state.viewer;
  }

  function addChangedBond(viewer, atomsById, bondValue, color, dashed) {
    const bond = parseBond(bondValue);
    if (!bond || !atomsById.has(bond.left) || !atomsById.has(bond.right)) {
      return;
    }
    const left = atomsById.get(bond.left);
    const right = atomsById.get(bond.right);
    viewer.addLine({
      start: {x: left.x, y: left.y, z: left.z},
      end: {x: right.x, y: right.y, z: right.z},
      color: color,
      dashed: dashed,
      dashLength: 0.28,
      gapLength: 0.16,
      linewidth: 3,
    });
  }

  function atomRole(atom) {
    const group = String((atom.properties || {}).group || "environment");
    if (group === "core") {
      return "反应核";
    }
    if (group === "participant") {
      return "参与原子";
    }
    return "环境原子";
  }

  function createTextElement(tagName, className, textValue) {
    const element = document.createElement(tagName);
    if (className) {
      element.className = className;
    }
    element.textContent = String(textValue);
    return element;
  }

  function changedBonds(atomId, values) {
    return (values || []).map(parseBond).filter(function (bond) {
      return bond && (bond.left === atomId || bond.right === atomId);
    }).map(function (bond) {
      return `#${bond.left}–#${bond.right}`;
    });
  }

  function renderAtomInspector(atom, frame, evidence, mode, onClear) {
    const body = document.getElementById("event-atom-inspector-body");
    if (!body) {
      return;
    }
    body.replaceChildren();
    if (!atom) {
      body.className = "rs-atom-inspector-body rs-atom-inspector-empty";
      body.textContent = "将鼠标移到原子上查看信息，点击后可固定详情。";
      return;
    }

    body.className = "rs-atom-inspector-body";
    const heading = document.createElement("div");
    heading.className = "rs-atom-detail-heading";
    heading.appendChild(
      createTextElement("strong", "rs-atom-detail-name", `${atom.atom} · #${atom.serial}`)
    );
    heading.appendChild(
      createTextElement(
        "span",
        `rs-atom-detail-mode rs-atom-detail-mode-${mode}`,
        mode === "pinned" ? "已固定" : "悬停"
      )
    );
    body.appendChild(heading);

    const details = document.createElement("dl");
    details.className = "rs-atom-detail-grid";
    const addDetail = function (label, value) {
      details.appendChild(createTextElement("dt", "", label));
      details.appendChild(createTextElement("dd", "", value));
    };
    const properties = atom.properties || {};
    const element = String(atom.elem || "").trim();
    const atomType = String(properties.type || "").trim();
    addDetail("元素", element && element !== "X" ? element : "未映射");
    addDetail("Atom ID", atom.serial);
    addDetail("Type", atomType || "—");
    addDetail("角色", atomRole(atom));
    addDetail("Frame", frame && frame.frame !== undefined ? frame.frame : "—");
    addDetail(
      "显示坐标",
      `(${numeric(atom.x, 0).toFixed(3)}, ${numeric(atom.y, 0).toFixed(3)}, ${numeric(atom.z, 0).toFixed(3)}) Å`
    );
    const formed = changedBonds(Number(atom.serial), evidence.formed);
    const broken = changedBonds(Number(atom.serial), evidence.broken);
    addDetail("形成键", formed.length ? formed.join("、") : "—");
    addDetail("断裂键", broken.length ? broken.join("、") : "—");
    body.appendChild(details);

    if (mode === "pinned") {
      const clearButton = createTextElement("button", "rs-atom-detail-clear", "取消固定");
      clearButton.type = "button";
      clearButton.addEventListener("click", onClear);
      body.appendChild(clearButton);
    }
  }

  function renderCoreAtomList(coreAtoms, onSelect) {
    const list = document.getElementById("event-core-atom-list");
    if (!list) {
      return;
    }
    list.replaceChildren();
    if (!coreAtoms.length) {
      list.appendChild(createTextElement("span", "rs-core-atom-empty", "当前帧无反应核原子"));
      return;
    }
    coreAtoms.forEach(function (atom) {
      const button = createTextElement(
        "button",
        "rs-core-atom-chip",
        `${atom.atom} #${atom.serial}`
      );
      button.type = "button";
      button.title = `定位 ${atom.atom} · #${atom.serial}`;
      button.setAttribute("aria-pressed", String(Number(state.selectedAtomId) === Number(atom.serial)));
      if (Number(state.selectedAtomId) === Number(atom.serial)) {
        button.classList.add("is-selected");
      }
      button.addEventListener("click", function () {
        onSelect(atom);
      });
      list.appendChild(button);
    });
  }

  function addAtomLabel(viewer, atom) {
    return viewer.addLabel(`${atom.atom} · #${atom.serial}`, {
      position: {x: atom.x, y: atom.y, z: atom.z},
      fontSize: 11,
      fontColor: "#0f172a",
      backgroundColor: "#ffffff",
      backgroundOpacity: 0.88,
      borderColor: "#1d4ed8",
      borderThickness: 1,
      inFront: true,
    });
  }

  function addCoreHalo(viewer, atom, selected) {
    const radius = atom.elem === "H" ? 0.56 : 0.70;
    return viewer.addSphere({
      center: {x: atom.x, y: atom.y, z: atom.z},
      radius: selected ? radius + 0.10 : radius,
      color: selected ? "#f59e0b" : "#2563eb",
      opacity: selected ? 0.26 : 0.13,
    });
  }

  function clearHoverLabel(viewer) {
    if (state.hoverLabel && viewer && typeof viewer.removeLabel === "function") {
      viewer.removeLabel(state.hoverLabel);
    }
    state.hoverLabel = null;
  }

  function resetInspector() {
    state.selectedAtomId = null;
    const list = document.getElementById("event-core-atom-list");
    if (list) {
      list.replaceChildren();
    }
    renderAtomInspector(null, null, {}, "hover", function () {});
  }

  namespace.renderEventTrajectory = function (frameIndex, scope, viewerData, labelOptions) {
    const container = document.getElementById("event-trajectory-3dmol");
    if (!container) {
      return "";
    }
    if (!window.$3Dmol || typeof window.$3Dmol.createViewer !== "function") {
      container.textContent = "3Dmol.js 未加载；请展开下方 Plotly 兼容视图。";
      return "3Dmol.js 资源不可用";
    }

    const frames = viewerData && Array.isArray(viewerData.frames)
      ? viewerData.frames
      : [];
    if (!frames.length) {
      resetInspector();
      if (state.viewer) {
        state.viewer.removeAllModels();
        state.viewer.removeAllShapes();
        state.viewer.removeAllLabels();
        state.viewer.render();
      } else {
        container.replaceChildren();
      }
      return "";
    }

    try {
      const index = Math.max(
        0,
        Math.min(Math.trunc(numeric(frameIndex, 0)), frames.length - 1)
      );
      const frame = frames[index] || {};
      const eventKey = String(viewerData.event_id || "event");
      if (state.eventKey !== eventKey) {
        state.eventKey = eventKey;
        state.selectedAtomId = null;
      }
      const allowedIds = selectedIds(viewerData, scope || "context");
      let sourceAtoms = Array.isArray(frame.atoms) ? frame.atoms : [];
      if (allowedIds.size) {
        sourceAtoms = sourceAtoms.filter(function (atom) {
          return allowedIds.has(Number(atom.id));
        });
      }
      if (!sourceAtoms.length) {
        return "当前显示范围没有可渲染原子";
      }

      const viewer = ensureViewer(container);
      const previousView = state.lastCameraKey && typeof viewer.getView === "function"
        ? viewer.getView()
        : null;
      viewer.removeAllModels();
      viewer.removeAllShapes();
      viewer.removeAllLabels();
      state.hoverLabel = null;
      state.selectedShape = null;

      const localIndexById = new Map();
      const atomsById = new Map();
      const modelAtoms = sourceAtoms.map(function (atom, localIndex) {
        const atomId = Number(atom.id);
        const element = String(atom.element || "").trim();
        const label = String(atom.label || element || `T${atom.type || "?"}`);
        const group = String(atom.group || "environment");
        const chain = group === "core" ? "C" : (group === "participant" ? "P" : "E");
        const rendered = {
          serial: atomId,
          atom: label,
          elem: element || "X",
          chain: chain,
          x: atomCoordinate(atom, "x"),
          y: atomCoordinate(atom, "y"),
          z: atomCoordinate(atom, "z"),
          bonds: [],
          bondOrder: [],
          properties: {
            originalId: atomId,
            group: group,
            label: label,
            type: String(atom.type || ""),
          },
        };
        localIndexById.set(atomId, localIndex);
        atomsById.set(atomId, rendered);
        return rendered;
      });

      const seenBonds = new Set();
      (frame.bonds || []).forEach(function (bondValue) {
        const bond = parseBond(bondValue);
        if (!bond || !localIndexById.has(bond.left) || !localIndexById.has(bond.right)) {
          return;
        }
        const key = bond.left < bond.right
          ? `${bond.left}-${bond.right}`
          : `${bond.right}-${bond.left}`;
        if (seenBonds.has(key)) {
          return;
        }
        seenBonds.add(key);
        const leftIndex = localIndexById.get(bond.left);
        const rightIndex = localIndexById.get(bond.right);
        modelAtoms[leftIndex].bonds.push(rightIndex);
        modelAtoms[leftIndex].bondOrder.push(bond.order);
        modelAtoms[rightIndex].bonds.push(leftIndex);
        modelAtoms[rightIndex].bondOrder.push(bond.order);
      });

      const model = viewer.addModel();
      model.addAtoms(modelAtoms);
      model.setStyle({}, {
        stick: {radius: 0.14, colorscheme: "Jmol"},
        sphere: {scale: 0.28, colorscheme: "Jmol"},
      });
      model.setStyle({chain: "E"}, {
        stick: {radius: 0.09, opacity: 0.42, colorscheme: "Jmol"},
        sphere: {scale: 0.20, opacity: 0.48, colorscheme: "Jmol"},
      });
      model.setStyle({chain: "P"}, {
        stick: {radius: 0.15, colorscheme: "Jmol"},
        sphere: {scale: 0.30, colorscheme: "Jmol"},
      });
      model.setStyle({chain: "C"}, {
        stick: {radius: 0.19, colorscheme: "Jmol"},
        sphere: {scale: 0.38, colorscheme: "Jmol"},
      });

      const coreAtoms = modelAtoms.filter(function (atom) {
        return atom.chain === "C";
      });
      coreAtoms.forEach(function (atom) {
        addCoreHalo(viewer, atom, false);
      });

      const evidence = viewerData.bond_evidence || {};
      const bondState = String(frame.bond_state || "intermediate");
      (evidence.broken || []).forEach(function (bondValue) {
        addChangedBond(viewer, atomsById, bondValue, "#dc2626", bondState !== "before");
      });
      (evidence.formed || []).forEach(function (bondValue) {
        addChangedBond(viewer, atomsById, bondValue, "#16a34a", bondState !== "after");
      });

      const showCoreLabels = Array.isArray(labelOptions)
        ? labelOptions.includes("core_labels")
        : labelOptions === true || labelOptions === "core_labels";
      if (showCoreLabels) {
        coreAtoms.forEach(function (atom) {
          addAtomLabel(viewer, atom);
        });
      }

      const clearSelection = function () {
        state.selectedAtomId = null;
        if (state.selectedShape && typeof viewer.removeShape === "function") {
          viewer.removeShape(state.selectedShape);
        }
        state.selectedShape = null;
        renderCoreAtomList(coreAtoms, selectAtom);
        renderAtomInspector(null, frame, evidence, "hover", clearSelection);
        viewer.render();
      };
      const selectAtom = function (atom) {
        state.selectedAtomId = Number(atom.serial);
        if (state.selectedShape && typeof viewer.removeShape === "function") {
          viewer.removeShape(state.selectedShape);
        }
        state.selectedShape = addCoreHalo(viewer, atom, true);
        renderCoreAtomList(coreAtoms, selectAtom);
        renderAtomInspector(atom, frame, evidence, "pinned", clearSelection);
        viewer.render();
      };

      const selectedAtom = atomsById.get(Number(state.selectedAtomId));
      if (selectedAtom) {
        state.selectedShape = addCoreHalo(viewer, selectedAtom, true);
        renderAtomInspector(selectedAtom, frame, evidence, "pinned", clearSelection);
      } else {
        state.selectedAtomId = null;
        renderAtomInspector(null, frame, evidence, "hover", clearSelection);
      }
      renderCoreAtomList(coreAtoms, selectAtom);

      if (typeof model.setHoverable === "function") {
        model.setHoverable(
          {},
          true,
          function (atom) {
            clearHoverLabel(viewer);
            if (!(showCoreLabels && atom.chain === "C")) {
              state.hoverLabel = addAtomLabel(viewer, atom);
            }
            renderAtomInspector(atom, frame, evidence, "hover", clearSelection);
            viewer.render();
          },
          function () {
            clearHoverLabel(viewer);
            const pinned = atomsById.get(Number(state.selectedAtomId));
            if (pinned) {
              renderAtomInspector(pinned, frame, evidence, "pinned", clearSelection);
            } else {
              renderAtomInspector(null, frame, evidence, "hover", clearSelection);
            }
            viewer.render();
          }
        );
      }
      if (typeof model.setClickable === "function") {
        model.setClickable({}, true, function (atom) {
          selectAtom(atom);
        });
      }

      const cameraKey = `${viewerData.event_id || "event"}:${scope || "context"}`;
      if (previousView && state.lastCameraKey === cameraKey) {
        viewer.setView(previousView);
      } else {
        viewer.zoomTo();
      }
      state.lastCameraKey = cameraKey;
      viewer.render();
      return `3Dmol.js · ${sourceAtoms.length} atoms · PBC-centered · 悬停查看，点击固定`;
    } catch (error) {
      console.error("ReacNet Scope 3Dmol render failed", error);
      return `3Dmol.js 渲染失败：${error && error.message ? error.message : error}`;
    }
  };
}());
