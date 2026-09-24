/* Refresh imported index tasks while their library panel is visible. */
(function () {
  "use strict";

  let tick = 0;

  function refresh() {
    const page = document.getElementById("page-data-management");
    const panel = document.getElementById("library-management-panel");
    if (!page?.classList.contains("active") || !panel?.getClientRects().length) return;
    if (!window.dash_clientside?.set_props) return;
    window.dash_clientside.set_props("library-index-refresh", {data: ++tick});
  }

  document.addEventListener("click", function (event) {
    const target = event.target instanceof Element ? event.target : null;
    if (!target?.closest('[id*="library-build-index"], [id*="library-cancel-index"]')) return;
    setTimeout(refresh, 250);
    setTimeout(refresh, 1250);
  }, true);

  setInterval(refresh, 5000);
}());
