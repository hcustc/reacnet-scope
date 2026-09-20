/*
 * Keep primary workspace navigation usable while a long Dash callback is in
 * flight.  Dash still owns the canonical page-store and will reconcile these
 * classes when its navigation callback returns; this capture-phase handler is
 * only the immediate, browser-local transition.
 */
(function () {
    "use strict";

    function activatePage(pageId) {
        const targetPage = document.getElementById(`page-${pageId}`);
        const targetNav = document.getElementById(`nav-${pageId}`);
        if (!targetPage || !targetNav) {
            return false;
        }

        document.querySelectorAll(".rs-page[id^=\"page-\"]").forEach((page) => {
            page.classList.remove("active");
        });
        targetPage.classList.add("active");

        document.querySelectorAll(".rs-top-nav-item[id^=\"nav-\"]").forEach((nav) => {
            nav.classList.remove("active");
            nav.setAttribute("aria-current", "false");
        });
        targetNav.classList.add("active");
        targetNav.setAttribute("aria-current", "page");
        return true;
    }

    document.addEventListener("click", function (event) {
        const clicked = event.target instanceof Element
            ? event.target.closest("[id^=\"nav-\"]")
            : null;
        if (!clicked || !clicked.classList.contains("rs-top-nav-item")) {
            return;
        }
        activatePage(clicked.id.slice("nav-".length));
    }, true);

    window.reacnetScopeNavigation = Object.freeze({activatePage});
}());
