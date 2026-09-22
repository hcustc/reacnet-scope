"""Recorded bond views must remain navigable and depict topology faithfully."""
import math

from scripts.webapp_dash.candidate_workbench import bond_graph


def test_graph_has_visible_restore_and_bounded_home_view():
    view = bond_graph([{'atom_ids': [1, 2, 3]}], ['1-2-2'])
    button = view.children[0].children[0]
    graph = view.children[1]
    assert button.children == '还原视图'
    assert button.id['index'] == graph.id['index']
    assert graph.config['displayModeBar'] is True
    assert graph.config['doubleClick'] == 'reset'
    assert graph.config['scrollZoom'] is False
    assert graph.figure.layout.dragmode == 'pan'
    markers = graph.figure.data[-1]
    for values, bounds in zip((markers.x, markers.y), graph.figure.layout.meta['home_ranges']):
        assert all(bounds[0] < v < bounds[1] for v in values)
    assert 'RNG 键级 2' in graph.figure.data[0].hovertemplate
    # Separate views, including identical states, have independent reset controls.
    assert bond_graph([{'atom_ids': [1, 2, 3]}], ['1-2-2']).children[1].id != graph.id


def test_scrambled_atom_ids_do_not_turn_ring_into_crossed_polygon():
    graph = bond_graph([{'atom_ids': [1, 2, 3, 4]}],
                       ['1-3-1', '3-2-2', '2-4-1', '4-1-2']).children[1]
    nodes = graph.figure.data[-1]
    positions = dict(zip(map(int, nodes.text), zip(nodes.x, nodes.y)))
    lengths = [math.dist(positions[a], positions[b])
               for a, b in [(1, 3), (3, 2), (2, 4), (4, 1)]]
    assert max(lengths) / min(lengths) < 1.05
    assert len(graph.figure.data) == 5  # Four recorded edges and all nodes.


def test_empty_side_is_explicit_and_single_atom_can_be_restored():
    assert '没有可显示的原子' in bond_graph([], []).children
    graph = bond_graph([{'atom_ids': [7]}], []).children[1]
    assert tuple(graph.figure.data[-1].text) == ('7',)
    assert all(lo < hi for lo, hi in graph.figure.layout.meta['home_ranges'])
