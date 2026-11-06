/* Preserve separate targets when pasting lines into the compact entry field. */
(() => {
    document.addEventListener('paste', event => {
        const input = event.target;
        if (!input || input.id !== 'evolution-target-entry') return;
        const text = event.clipboardData && event.clipboardData.getData('text/plain');
        if (!text || !/[\r\n]/.test(text)) return;
        event.preventDefault();
        const start = input.selectionStart ?? input.value.length;
        const end = input.selectionEnd ?? start;
        const inserted = text.replace(/\r?\n|\r/g, '; ');
        const value = input.value.slice(0, start) + inserted + input.value.slice(end);
        window.dash_clientside.set_props('evolution-target-entry', {value});
        requestAnimationFrame(() => input.setSelectionRange(start + inserted.length, start + inserted.length));
    });
})();
