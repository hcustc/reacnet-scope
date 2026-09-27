"""Exercise actual Cytoscape updates between route reading and the overview."""
from __future__ import annotations

import os
from threading import Thread

import pytest

if os.environ.get('REACNET_SCOPE_BROWSER_TESTS') != '1':
    pytest.skip('Browser acceptance is opt-in', allow_module_level=True)

from dash import Dash, Input, Output, dcc, html
import dash_cytoscape as cyto
from playwright.sync_api import expect, sync_playwright
from werkzeug.serving import make_server

from scripts.webapp_dash import candidate_graph as graph


def test_context_participant_stays_connected_when_switching_views():
    report = dict(context='dataset', query_request_id='query', paths=[dict(
        signature_id='route', species=['[C]', '[C][O]'], steps=[dict(
            reaction_key='[C]+[O]->[C][O]', reactants=['[C]', '[O]'], products=['[C][O]'],
            carried_from='[C]', carried_to='[C][O]', event_count=1)])])
    app = Dash(__name__)
    app.layout = html.Div([
        dcc.RadioItems(id='scope', options=['reading', 'all'], value='reading'),
        html.Div(id='rendered-scope'),
        cyto.Cytoscape(id='graph', elements=[], stylesheet=graph.STYLESHEET,
                       layout={'name': 'preset'}, style={'width': '900px', 'height': '480px'}),
    ])

    @app.callback(Output('graph', 'elements'), Output('graph', 'layout'),
                  Output('rendered-scope', 'children'), Input('scope', 'value'))
    def switch(scope):
        elements = graph.elements(report)
        if scope == 'reading':
            elements = graph.local_participants(elements, {'route'})
        return elements, graph.fitted_layout(elements), scope

    server = make_server('127.0.0.1', 0, app.server, threaded=True)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            try:
                page.goto(f'http://127.0.0.1:{server.server_port}')
                for mode in ['reading', 'all', 'reading', 'all']:
                    page.locator('#scope').get_by_text(mode, exact=True).click()
                    expect(page.locator('#rendered-scope')).to_have_text(mode)
                    page.evaluate('() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))')
                    snapshot = page.locator('#graph').evaluate('''element => {
                        const cy = [...element.querySelectorAll('*'), element].find(e => e._cyreg)._cyreg.cy;
                        return {edges: cy.edges().map(e => ({id:e.id(), source:e.source().id(), target:e.target().id()})),
                            species: cy.nodes().filter(n => n.data('kind') === 'species').map(n => n.data('species')).sort(),
                            orphans: cy.nodes().filter(n => n.degree() === 0).map(n => n.data())};
                    }''')
                    assert snapshot['species'] == ['[C]', '[C][O]', '[O]']
                    assert snapshot['orphans'] == [], (mode, snapshot)
                    assert len(snapshot['edges']) == 3, (mode, snapshot)
            finally:
                browser.close()
    finally:
        server.shutdown()
        thread.join(timeout=5)
