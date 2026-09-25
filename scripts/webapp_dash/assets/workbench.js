/* Keyboard behavior for the species detail view. */
document.addEventListener('keydown', function (event) {
    if (event.key !== 'Escape' || event.defaultPrevented) return;
    // Let open Bootstrap overlays handle Escape first.
    if (document.querySelector('.modal.show, .offcanvas.show, #species-candidate-menu .dropdown-menu.show')) return;
    const detail = document.getElementById('species-detail-stage');
    const back = document.getElementById('species-stage-back-btn');
    if (detail && detail.getClientRects().length && back && back.getClientRects().length) {
        event.preventDefault();
        back.click();
    }
});
