/* Refit the candidate graph when its visible container changes size.
 * Use Dash props instead of reaching into the Cytoscape component instance.
 */
(function () {
  "use strict";
  let container;
  let observer;
  let pending;
  let previous = "";

  // Native <details> toggles change the DOM without updating Dash's prop.
  // Keep user toggles in sync so a later graph pick can reopen evidence,
  // and another completed search can close the query form again.
  document.addEventListener("toggle", function (event) {
    const section = event.target;
    if (section.id !== "cp-evidence" && section.id !== "cp-search-settings") return;
    if (window.dash_clientside && window.dash_clientside.set_props) {
      window.dash_clientside.set_props(section.id, {open: section.open});
    }
  }, true);

  function attach() {
    const next = document.getElementById("cp-graph");
    if (next === container) return;
    if (observer) observer.disconnect();
    container = next;
    previous = "";
    if (!container) return;
    observer = new ResizeObserver(function (entries) {
      const box = entries[0].contentRect;
      const size = {width: Math.round(box.width), height: Math.round(box.height)};
      const key = JSON.stringify(size);
      if (!size.width || !size.height) {
        previous = "";
        clearTimeout(pending);
        return;
      }
      if (key === previous) return;
      previous = key;
      clearTimeout(pending);
      pending = setTimeout(function () {
        if (window.dash_clientside && window.dash_clientside.set_props) {
          window.dash_clientside.set_props("cp-graph-size", {data: size});
        }
      }, 150);
    });
    observer.observe(container);
  }

  new MutationObserver(attach).observe(document.documentElement, {childList: true, subtree: true});
  attach();
}());
