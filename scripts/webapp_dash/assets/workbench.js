/* Keyboard behavior for the responsive, non-modal inspection panel. */
document.addEventListener('keydown', function (event) {
    if (event.key !== 'Escape' || event.defaultPrevented) return;
    // Bootstrap dialogs own Escape while they are open.
    if (document.querySelector('.modal.show, .offcanvas.show')) return;
    const close = document.getElementById('species-detail-close');
    if (close && close.getClientRects().length) {
        event.preventDefault();
        close.click();
    }
});
