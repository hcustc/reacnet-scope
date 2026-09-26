/* Keyboard behavior for the species detail view. */
document.addEventListener('keydown', function (event) {
    if (event.key !== 'Escape' || event.defaultPrevented) return;
    // Let open Bootstrap overlays handle Escape first.
    if (document.querySelector('.modal.show, .offcanvas.show')) return;
    const datasetMenu = document.getElementById('current-dataset-menu');
    if (datasetMenu?.open) {
        // The dataset picker owns the first Escape while its options are open.
        if (datasetMenu.querySelector('[aria-expanded="true"]')) return;
        datasetMenu.open = false;
        datasetMenu.querySelector('summary')?.focus({preventScroll: true});
        event.preventDefault();
        return;
    }
    const eventDetail = document.getElementById('event-detail-panel');
    const closeEvent = document.getElementById('event-detail-close-btn');
    if (eventDetail && eventDetail.getClientRects().length && closeEvent) {
        event.preventDefault();
        closeEvent.click();
        return;
    }
    const detail = document.getElementById('species-detail-stage');
    const back = document.getElementById('species-stage-back-btn');
    if (detail && detail.getClientRects().length && back && back.getClientRects().length) {
        event.preventDefault();
        back.click();
    }
});

function isDatasetMenuInteraction(menu, event) {
    // Selecting a dropdown option can unmount it before this document handler
    // runs. The original event path still records that it belonged to the menu.
    return event.composedPath().includes(menu);
}

document.addEventListener('click', function (event) {
    const menu = document.getElementById('current-dataset-menu');
    if (menu?.open && !isDatasetMenuInteraction(menu, event)) menu.open = false;
});

document.addEventListener('focusin', function (event) {
    const menu = document.getElementById('current-dataset-menu');
    if (menu?.open && !isDatasetMenuInteraction(menu, event)) menu.open = false;
});

// Task tabs choose the visible tool and commit its page and task to Dash.
document.addEventListener('click', function (event) {
    const task = event.target instanceof Element ? event.target.closest('.rs-task-tab') : null;
    if (!task) return;
    let taskId;
    try {
        taskId = JSON.parse(task.id);
    } catch (_) {
        return;
    }
    if (taskId.type !== 'workspace-open-page') return;
    const pageId = taskId.page;
    const destination = pageId === 'reaction-candidates' || pageId === 'reaction-related'
        ? 'reactions' : pageId;
    if (!window.reacnetScopeNavigation?.activatePage(destination)) return;
    const reactionTask = {
        'reactions': 'overview',
        'reaction-candidates': 'candidates',
        'reaction-related': 'direct',
    }[pageId];
    if (reactionTask) {
        window.dash_clientside.set_props('reaction-task-tabs', {value: reactionTask});
    }
    window.dash_clientside.set_props('page-store', {data: {page: destination}});
}, true);

// Species detail shortcuts commit the destination to the session store.
// Show the already mounted page immediately while Dash updates its task state.
document.addEventListener('click', function (event) {
    const clicked = event.target instanceof Element ? event.target.closest('button') : null;
    if (!clicked) return;
    const destination = {
        'species-direct-route-btn': 'reactions',
        'cp-from-species': 'reactions',
        'species-to-channels-btn': 'reactions',
        'species-to-evolution-btn': 'evolution',
    }[clicked.id];
    if (!destination || clicked.disabled) return;
    window.reacnetScopeNavigation?.activatePage(destination, 'species');
    if (['species-direct-route-btn', 'cp-from-species'].includes(clicked.id)) {
        window.dash_clientside.set_props('reaction-task-tabs', {value: 'candidates'});
    }
    setTimeout(() => {
        window.dash_clientside.set_props('page-store', {data: {page: destination, entry: 'species', version: 2}});
    }, 0);
});
