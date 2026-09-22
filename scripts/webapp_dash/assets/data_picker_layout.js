/* Collapse supplementary panes when the workspace becomes narrow, once per breakpoint. */
(() => {
  const attach = () => {
    const picker = document.getElementById('data-browser-view');
    if (!picker || picker.dataset.observed) return;
    picker.dataset.observed = 'true';
    let compact;
    const fit = () => {
      const rect = picker.getBoundingClientRect();
      if (!rect.width || !rect.height) return;
      const height = Math.max(160, window.innerHeight - Math.max(0, rect.top) - 12) + 'px';
      if (picker.style.height !== height) picker.style.height = height;
    };
    window.addEventListener('resize', fit);
    new MutationObserver(() => requestAnimationFrame(fit)).observe(picker, {attributes:true, attributeFilter:['class']});
    new ResizeObserver(([entry]) => {
      if (!entry.contentRect.width) return;
      const next = entry.contentRect.width <= 780;
      if (next === compact) return;
      compact = next;
      fit();
      picker.querySelectorAll('.rs-picker-sidebar, .rs-picker-selection').forEach(pane => { pane.open = !compact; });
    }).observe(picker);
    requestAnimationFrame(fit);
  };
  new MutationObserver(attach).observe(document.documentElement, {childList:true, subtree:true});
  attach();
})();
