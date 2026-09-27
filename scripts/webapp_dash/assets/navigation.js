/*
 * Keep primary workspace navigation usable while a long Dash callback is in
 * flight. Dash owns the canonical page-store; this capture-phase handler only
 * commits the already-mounted visible chrome as one browser-local transition.
 */
(function () {
    "use strict";

    // Normalize old session values before Dash mounts its session Store.
    try {
        sessionStorage.removeItem("research-species");
        const old = JSON.parse(sessionStorage.getItem("page-store") || "null");
        if (old && typeof old === "object" && old.version !== 2 &&
                old.page !== "trajectory") {
            const next = {...old, version: 2};
            if (["reactions", "candidate-paths", "pathway"].includes(next.page)) {
                next.page = "species";
            } else if (next.page === "species-fate") {
                next.page = "events";
            } else if (next.page === "batch-compare") {
                next.page = "evolution";
                next.compare_sources = true;
            }
            sessionStorage.setItem("page-store", JSON.stringify(next));
            if (next.page === "evolution" && next.compare_sources) {
                document.documentElement.classList.add("rs-restored-compare");
            }
        } else if (old?.page === "evolution" && old.compare_sources) {
            document.documentElement.classList.add("rs-restored-compare");
        }
    } catch (_) {
        // Storage can be disabled by the browser; Dash then uses its default.
    }

    let observedMain = null;
    let pageObserver = null;
    let currentPage = null;
    let datasetContext = null;
    let restoringScroll = false;
    let restoreFrame = null;
    const scrollPositions = new Map();

    function readScrollPosition() {
        return {main: observedMain?.scrollTop || 0,
                document: document.scrollingElement?.scrollTop || 0};
    }

    function applyScrollPosition(position = {}) {
        if (observedMain) observedMain.scrollTop = position.main || 0;
        if (document.scrollingElement) document.scrollingElement.scrollTop = position.document || 0;
    }

    function restorePageScroll(pageId) {
        if (!observedMain || !pageId || pageId === currentPage) return;
        currentPage = pageId;
        restoringScroll = true;
        cancelAnimationFrame(restoreFrame);
        const position = scrollPositions.get(pageId);
        applyScrollPosition(position);
        restoreFrame = requestAnimationFrame(() => {
            applyScrollPosition(position);
            restoringScroll = false;
        });
    }

    function watchPageScroll() {
        const main = document.querySelector(".rs-main");
        if (!main) return false;
        if (main === observedMain) return true;
        pageObserver?.disconnect();
        observedMain = main;
        currentPage = main.querySelector(".rs-page.active")?.id || null;
        const savePosition = () => {
            const activePage = main.querySelector(".rs-page.active")?.id;
            if (!restoringScroll && activePage && activePage === currentPage) {
                scrollPositions.set(activePage, readScrollPosition());
            }
        };
        main.addEventListener("scroll", savePosition, {passive: true});
        // Narrow layouts scroll the document; desktop layouts scroll main.
        window.addEventListener("scroll", savePosition, {passive: true});
        pageObserver = new MutationObserver(() => {
            const activePage = main.querySelector(".rs-page.active")?.id || null;
            restorePageScroll(activePage);
        });
        // Only page visibility matters. Grid cells, graph nodes and loading
        // indicators can change thousands of classes without a navigation.
        main.querySelectorAll('.rs-page[id^="page-"]').forEach(page => {
            pageObserver.observe(page, {attributes: true, attributeFilter: ["class"]});
        });
        return true;
    }

    function setDatasetContext(context) {
        const stable = value => value && typeof value === 'object'
            ? (Array.isArray(value) ? value.map(stable) : Object.fromEntries(
                Object.keys(value).sort().map(key => [key, stable(value[key])])) ) : value;
        const next = JSON.stringify(stable(context));
        if (next === datasetContext) return;
        datasetContext = next;
        scrollPositions.clear();
        cancelAnimationFrame(restoreFrame);
        restoringScroll = false;
        applyScrollPosition();
    }

    function activatePage(pageId, entry) {
        watchPageScroll();
        const targetPage = document.getElementById(`page-${pageId}`);
        const workspace = entry || {
            'element-distribution': 'evolution',
            'reaction-compare': 'reactions',
            'trajectory': 'events',
        }[pageId] || pageId;
        const targetNav = document.getElementById(`nav-${workspace}`);
        if (!targetPage || !targetNav) {
            return false;
        }

        if (observedMain && currentPage && !restoringScroll) {
            scrollPositions.set(currentPage, readScrollPosition());
        }

        document.querySelectorAll(".rs-page[id^=\"page-\"]").forEach((page) => {
            page.classList.remove("active");
        });
        targetPage.classList.add("active");
        restorePageScroll(targetPage.id);

        document.querySelectorAll(".rs-top-nav-item[id^=\"nav-\"]").forEach((nav) => {
            nav.classList.remove("active");
            nav.setAttribute("aria-current", "false");
        });
        targetNav.classList.add("active");
        targetNav.setAttribute("aria-current", "page");

        const label = targetNav.getAttribute("aria-label") || targetNav.textContent.trim();
        ["page-title", "topbar-page-context"].forEach((id) => {
            const element = document.getElementById(id);
            if (element) element.textContent = label;
        });
        const description = document.getElementById("page-description");
        if (description) description.textContent = targetNav.dataset.pageDescription || "";
        const section = document.getElementById("page-eyebrow-section");
        if (section) section.textContent = targetNav.dataset.pageSection || "";
        return true;
    }

    document.addEventListener("click", function (event) {
        const clicked = event.target instanceof Element
            ? event.target.closest("[id^=\"nav-\"]")
            : null;
        if (!clicked || !clicked.classList.contains("rs-top-nav-item")) {
            return;
        }
        document.documentElement.classList.remove("rs-restored-compare");
        activatePage(clicked.id.slice("nav-".length));
    }, true);

    document.addEventListener("click", function (event) {
        const clicked = event.target instanceof Element ? event.target.closest("button") : null;
        if (["evolution-close-compare-btn", "species-compare-switch-btn"].includes(clicked?.id)) {
            document.documentElement.classList.remove("rs-restored-compare");
        }
    }, true);

    // A session Store can hydrate before Dash delivers a data Input update.
    // Reconcile the mounted chrome with that persisted page on every load.
    function restoreSessionPage() {
        let state;
        try {
            state = JSON.parse(sessionStorage.getItem("page-store") || "null");
        } catch (_) {
            return true;
        }
        if (!state || typeof state !== "object") return true;
        const restored = {...state};
        if (restored.version !== 2) {
            if (["reactions", "candidate-paths", "pathway"].includes(restored.page)) {
                restored.page = "species";
            } else if (restored.page === "species-fate") {
                restored.page = "events";
            } else if (restored.page === "batch-compare") {
                restored.page = "evolution";
                restored.compare_sources = true;
            } else if (restored.page === "trajectory") {
                // The server must validate the event bookmark before restoring
                // a full-width trajectory. Show the safe event entry meanwhile.
                return activatePage("events");
            }
            restored.version = 2;
        }
        if (!document.getElementById(`page-${restored.page}`)) return false;
        const activated = activatePage(restored.page, restored.entry);
        if (activated && document.documentElement.classList.contains("rs-restored-compare")) {
            const label = document.getElementById("evolution-current-label");
            if (label) label.textContent = "对比来源";
        }
        return activated;
    }

    function restoreWhenMounted(attempt = 0) {
        const restored = restoreSessionPage();
        const watched = watchPageScroll();
        if ((!restored || !watched) && attempt < 40) {
            setTimeout(() => restoreWhenMounted(attempt + 1), 50);
        }
    }
    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", () => restoreWhenMounted(), {once: true});
    } else {
        restoreWhenMounted();
    }

    window.reacnetScopeNavigation = Object.freeze({activatePage, setDatasetContext});
}());
