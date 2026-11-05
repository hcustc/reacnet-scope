"""Prepared catalogue selection and source-bound abundance windows."""

import builtins
import csv
import io
import subprocess
import sys
from types import SimpleNamespace
from pathlib import Path

import pytest

from reacnet_scope import services as svc
from reacnet_scope.composition import SPECIES_COMPOSITION_STORE


ORTHO = "[H]OC1=C(Cl)C([H])=C([H])C([H])=C1[H]"
PARA = "[H]OC1=C([H])C([H])=C(Cl)C([H])=C1[H]"


@pytest.fixture
def abundance(tmp_path, monkeypatch):
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "workspace"))
    source = tmp_path / "demo.species"
    source.write_text("".join(
        f"Timestep {step}: {ORTHO} {count} {PARA} 3 [O]=[O] 20\n"
        for step, count in [(0, 10), (10, 4), (20, 2), (30, 8)]
    ))
    SPECIES_COMPOSITION_STORE.build(str(source))
    return {"species": str(source)}


def test_catalog_filters_keep_isomers_and_use_only_the_prepared_index(abundance, monkeypatch):
    real_open = builtins.open

    def deny_raw(path, *args, **kwargs):
        if isinstance(path, (str, Path)) and str(path) == abundance["species"]:
            raise AssertionError("online search read the raw species file")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", deny_raw)
    monkeypatch.setattr(SPECIES_COMPOSITION_STORE, "build", lambda *_a, **_k: pytest.fail("online rebuild"))
    filters = {"Cl": {"mode": "present"}, "N": {"mode": "absent"},
               "C": {"mode": "range", "min": 6, "max": 6}}
    result = svc.search_abundance_species(abundance, "128.002893", kind="mass",
                                          mass_tolerance=0.00001, element_filters=filters)
    assert {row["smiles"] for row in result["rows"]} == {ORTHO, PARA}
    assert all(row["formula"] == "C6H5ClO" for row in result["rows"])
    assert result["source_mode"] == "prepared_index"
    first = svc.search_abundance_species(abundance, "C6H5ClO", limit=1)
    second = svc.search_abundance_species(abundance, "C6H5ClO", limit=1, offset=1)
    assert first["has_more"] and not second["has_more"]
    assert first["rows"][0]["smiles"] != second["rows"][0]["smiles"]
    assert svc.search_abundance_species(abundance, ORTHO, kind="smiles")["rows"][0]["smiles"] == ORTHO
    assert not svc.search_abundance_species(abundance, "130.002893", kind="mass", mass_tolerance=0.01)["rows"]


@pytest.mark.parametrize("query,tolerance", [("nan", 0.1), ("128", -1), ("128", float("inf"))])
def test_invalid_mass_filter_is_not_an_empty_result(abundance, query, tolerance):
    with pytest.raises(svc.ServiceError):
        svc.search_abundance_species(abundance, query, kind="mass", mass_tolerance=tolerance)


def test_time_window_precedes_processing_and_preserves_original_csv_coordinates(abundance, monkeypatch):
    monkeypatch.setattr(SPECIES_COMPOSITION_STORE, "species_totals", lambda *_a: pytest.fail("loaded unrelated catalogue"))
    payload = svc.build_species_evolution(abundance, [ORTHO], start_timestep=10,
                                          end_timestep=20, normalize="initial", downsample=0)
    curve = payload["curves"][0]
    assert curve["raw_values"] == [4.0, 2.0]
    assert curve["values"] == [1.0, 0.5]
    assert curve["analyzed_frames"] == [1, 2]
    assert curve["source_timesteps"] == [10, 20]
    assert payload["query"]["start_timestep"] == 10
    rows = list(csv.reader(io.StringIO(svc.evolution_to_csv(payload))))
    assert rows[1][:2] == ["1", "10"] and rows[2][:2] == ["2", "20"]
    assert [float(row[2]) for row in rows[1:]] == [4, 2]
    smoothed = svc.build_species_evolution(abundance, [ORTHO], start_timestep=10,
                                           end_timestep=30, smooth_window=3, downsample=0)
    assert smoothed["curves"][0]["values"] == [3, 14 / 3, 5]
    relative = svc.build_species_evolution(abundance, [ORTHO], start_timestep=10,
                                           end_timestep=20, time_align="relative", downsample=0)
    assert relative["x_values"] == [0, 10]


def test_window_valid_empty_and_invalid_bounds_are_distinct(abundance):
    assert not svc.build_species_evolution(abundance, [ORTHO], start_timestep=11, end_timestep=19)["curves"]
    for start, end in [(20, 10), (0.5, 20), (float("nan"), 20)]:
        with pytest.raises(svc.ServiceError):
            svc.build_species_evolution(abundance, [ORTHO], start_timestep=start, end_timestep=end)
    assert svc.build_species_evolution(abundance, [ORTHO], start_timestep=0, end_timestep=0)["curves"][0]["raw_values"] == [10]


def test_filter_results_can_be_added_as_exact_curves_and_stale_results_are_rejected(abundance, monkeypatch):
    from dash.exceptions import PreventUpdate
    from scripts.webapp_dash import evolution_workbench as workbench
    from scripts.webapp_dash.app import create_app
    from scripts.webapp_dash.candidate_workbench import context_key
    app = create_app()
    callbacks = {item["callback"].__name__: item["callback"].__wrapped__ for item in app.callback_map.values()
                 if item.get("callback") and item["callback"].__module__.endswith("evolution_workbench")}
    dataset = {"dataset_id": "run", "source_revision": "one", "artifacts": abundance}
    monkeypatch.setattr(workbench, "ctx", SimpleNamespace(triggered_id="evolution-filter-search"))
    result, rows, _, message, style, prev, next_page = callbacks["find"](
        1, None, None, None, dataset, "C6H5ClO", "formula", 0.5, ["Cl"], [], "C", 6, 6, None)
    assert len(rows) == 2 and not style and prev and next_page
    assert result["context"] == context_key(dataset)
    monkeypatch.setattr(workbench, "ctx", SimpleNamespace(triggered_id="evolution-filter-replace"))
    targets, _entry, _editor, clicks, *_ = _invoke_editor(callbacks["edit_targets"],
        dataset=dataset, existing="old", clicks=2, selected=rows, result=result, _replace=1)
    assert set(targets.splitlines()) == {f"smiles:{ORTHO}", f"smiles:{PARA}"}
    assert clicks == 3
    payload = svc.build_species_evolution(abundance, targets.splitlines(), start_timestep=10, end_timestep=20)
    assert len(payload["curves"]) == 2
    with pytest.raises(PreventUpdate):
        _invoke_editor(callbacks["edit_targets"], dataset={**dataset, "source_revision": "two"},
                       existing="old", clicks=2, selected=rows, result=result, _replace=1)


def test_abundance_ranking_is_only_loaded_after_explicit_expansion(monkeypatch):
    from dash.exceptions import PreventUpdate
    from scripts.webapp_dash.app import create_app
    app = create_app()
    load = next(item["callback"].__wrapped__ for item in app.callback_map.values()
                if item.get("callback") and item["callback"].__name__ == "_load_abundance_ranking")
    monkeypatch.setattr(svc, "ranked_species_abundance", lambda *_a, **_k: pytest.fail("implicit ranking query"))
    with pytest.raises(PreventUpdate):
        load({"page": "evolution", "version": 2, "entry": "species"}, {"dataset_id": "run"}, False, None)


def test_only_current_nonempty_rank_selection_can_request_a_curve():
    from dash.exceptions import PreventUpdate
    from scripts.webapp_dash.app import create_app
    from scripts.webapp_dash.candidate_workbench import context_key
    app = create_app()
    request = next(item["callback"].__wrapped__ for item in app.callback_map.values()
                   if item.get("callback") and item["callback"].__name__ == "request_ranked_curve")
    dataset = {"dataset_id": "run", "source_revision": "one"}
    rows = [{"smiles": ORTHO, "formula": "C6H5ClO"}]
    ranking = {"context": context_key(dataset), "rows": rows}
    assert request(rows, ranking, dataset) == {"selection": rows, "context": context_key(dataset)}
    for selection in (None, [], {"ids": []}, [{"smiles": PARA}]):
        with pytest.raises(PreventUpdate):
            request(selection, ranking, dataset)
    with pytest.raises(PreventUpdate):
        request(rows, ranking, {**dataset, "source_revision": "two"})


def test_element_conditions_reject_conflicts_and_fractional_counts():
    from scripts.webapp_dash.evolution_workbench import element_filter_query
    with pytest.raises(ValueError):
        element_filter_query(["Cl"], ["Cl"], None, None, None)
    with pytest.raises(ValueError):
        element_filter_query([], [], "C", 1.5, 6)
    assert element_filter_query(["C"], [], "C", None, 6) == {"C": {"mode": "range", "min": 1, "max": 6}}


def test_installed_cli_exports_only_the_requested_window(abundance, tmp_path):
    output = tmp_path / "window.csv"
    result = subprocess.run([
        str(Path(sys.executable).parent / "reacnet-scope"), "species-evolution",
        "--species-file", abundance["species"], "--target", f"smiles:{ORTHO}",
        "--start-timestep", "10", "--end-timestep", "20", "--normalize", "max", "--out-csv", str(output),
    ], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr + result.stdout
    rows = list(csv.reader(output.read_text().splitlines()))
    assert [row[:2] for row in rows[1:]] == [["1", "10"], ["2", "20"]]
    assert [float(row[2]) for row in rows[1:]] == [4, 2]


@pytest.fixture
def element_abundance(tmp_path, monkeypatch):
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "workspace"))
    source = tmp_path / "elements.species"
    carbon = "[C]" * 6
    oxygenated = carbon + "[O][O]"
    source.write_text("".join(
        f"Timestep {step}: {ORTHO} {count} {PARA} 3 {carbon} 2 {oxygenated} 4 [C][C][C][C][C] 7 [O]=[O] 20\n"
        for step, count in [(0, 10), (10, 4), (20, 2), (30, 8)]
    ))
    SPECIES_COMPOSITION_STORE.build(str(source))
    return {"species": str(source)}


def test_element_condition_sums_all_matching_formulas_and_keeps_exact_escape(element_abundance, monkeypatch):
    # Online analysis consumes only the published index, including for aggregation.
    original_open = builtins.open
    def deny_raw(path, *args, **kwargs):
        if isinstance(path, (str, Path)) and str(path) == element_abundance["species"]:
            pytest.fail("online aggregate read raw evidence")
        return original_open(path, *args, **kwargs)
    monkeypatch.setattr(builtins, "open", deny_raw)
    monkeypatch.setattr(SPECIES_COMPOSITION_STORE, "build", lambda *_a, **_k: pytest.fail("online rebuild"))
    # Reject full-catalogue fallback and the old members × frames allocation path.
    monkeypatch.setattr(SPECIES_COMPOSITION_STORE, "species_totals", lambda *_a: pytest.fail("full catalogue"))
    monkeypatch.setattr(SPECIES_COMPOSITION_STORE, "species_count_matrix", lambda *_a: pytest.fail("per-member matrix"))
    payload = svc.build_species_evolution(element_abundance, ["C6", "formula:C6", f"smiles:{ORTHO}"], downsample=2)
    aggregate, exact_formula, exact_structure = payload["curves"]
    assert payload["schema_version"] == 2
    assert aggregate["query_type"] == "element_sum"
    assert aggregate["element_condition"] == {"element": "C", "count": 6}
    assert aggregate["raw_values"] == [19, 13, 11, 17]
    assert aggregate["n_members"] == 4
    assert aggregate["formula"] is None and aggregate["formula_exact_mass"] is None
    assert exact_formula["raw_values"] == [2, 2, 2, 2]
    assert exact_formula["name"] != aggregate["name"]
    assert exact_structure["raw_values"] == [10, 4, 2, 8]
    assert sum(aggregate["values"]) > sum(exact_formula["values"])
    rows = [row for row in payload["mapping"] if row["query_type"] == "element_sum"]
    assert {row["member_formula"] for row in rows} == {"C6", "C6H5ClO", "C6O2"}
    assert all(row["formula_exact_mass"] is None and row["exact_mass"] is not None for row in rows)
    csv_rows = list(csv.reader(io.StringIO(svc.evolution_to_csv(payload))))
    assert [float(row[2]) for row in csv_rows[1:]] == [19, 13, 11, 17]


@pytest.mark.parametrize("query,expected", [
    ("C=6", [19, 13, 11, 17]), ("element:Cl1", [13, 7, 5, 11]),
    ("C0", [20, 20, 20, 20]), ("O2", [24, 24, 24, 24]),
    ("formula:O2", [20, 20, 20, 20]),
])
def test_element_query_grammar_and_zero_count(element_abundance, query, expected):
    payload = svc.build_species_evolution(element_abundance, [query])
    assert payload["curves"][0]["raw_values"] == expected
    results = svc.search_abundance_species(element_abundance, query)
    assert set(payload["curves"][0]["members"]) == {row["smiles"] for row in results["rows"]}


def test_element_aggregate_is_not_limited_by_catalogue_or_split_limit(tmp_path, monkeypatch):
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "many-workspace"))
    source = tmp_path / "many.species"
    # 60 distinct six-carbon formulas plus an unrelated five-carbon species.
    members = ["[C]" * 6 + "[O]" * count for count in range(60)]
    source.write_text("Timestep 0: " + " ".join(f"{smi} 1" for smi in members) + " [C][C][C][C][C] 100\n")
    SPECIES_COMPOSITION_STORE.build(str(source))
    payload = svc.build_species_evolution({"species": str(source)}, ["C6"], formula_mode="both", max_smiles_per_formula=1)
    assert len(payload["curves"]) == 2
    assert payload["curves"][0]["raw_values"] == [60]
    assert set(payload["curves"][0]["members"]) == set(members)
    assert payload["curves"][1]["raw_values"] == [1]
    assert any("59" in warning for warning in payload["meta"]["warnings"])


def test_element_condition_errors_and_interpretation(element_abundance):
    assert svc.describe_abundance_targets("C6\nformula:O2") == [
        "C6 → C 原子数 = 6，其他元素不限", "formula:O2 → 完整分子式 O2"]
    details = svc.describe_abundance_targets("六碳::C6\nformula:O2\nsmiles:[O]=[O]", structured=True)
    assert [item["kind"] for item in details] == ["element", "formula", "smiles"]
    assert (details[0]["label"], details[0]["element"], details[0]["count"]) == ("六碳", "C", 6)
    assert details[2]["query"] == "[O]=[O]"
    for query in ["Xx0", "element:C-1", "element:C1.5"]:
        with pytest.raises(svc.ServiceError):
            svc.build_species_evolution(element_abundance, [query])
        with pytest.raises(svc.ServiceError):
            svc.search_abundance_species(element_abundance, query)


def test_element_aggregation_window_and_multisource_export(element_abundance, abundance):
    payload = svc.build_species_evolution(element_abundance, ["C6"],
        species_files=f"first::{element_abundance['species']}\nsecond::{abundance['species']}",
        start_timestep=10, end_timestep=20, normalize="initial", downsample=0)
    assert [curve["n_members"] for curve in payload["curves"]] == [4, 2]
    assert set(payload["curves"][1]["members"]) == {ORTHO, PARA}
    assert [curve["raw_values"] for curve in payload["curves"]] == [[13, 11], [7, 5]]
    assert [curve["values"] for curve in payload["curves"]] == [[1, 11 / 13], [1, 5 / 7]]
    assert all(curve["analyzed_frames"] == [1, 2] for curve in payload["curves"])
    assert all(curve["source_timesteps"] == [10, 20] for curve in payload["curves"])
    assert all(curve["element_condition"] == {"element": "C", "count": 6} for curve in payload["curves"])


def test_installed_cli_accepts_element_condition(element_abundance, tmp_path):
    output = tmp_path / "carbon.csv"
    result = subprocess.run([
        str(Path(sys.executable).parent / "reacnet-scope"), "species-evolution",
        "--species-file", element_abundance["species"], "--target", "C6", "--out-csv", str(output),
    ], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr + result.stdout
    rows = list(csv.reader(output.read_text().splitlines()))
    assert [float(row[2]) for row in rows[1:]] == [19, 13, 11, 17]


def _invoke_editor(callback, **values):
    from inspect import signature
    args = {name: None for name in signature(callback).parameters}
    return callback(**{**args, **values})


def test_target_editor_commits_pending_text_once_and_keeps_cancelled_edits(monkeypatch):
    from dash import no_update
    from dash.exceptions import PreventUpdate
    from scripts.webapp_dash import evolution_workbench as workbench
    from scripts.webapp_dash.app import create_app
    from scripts.webapp_dash.candidate_workbench import context_key
    app = create_app()
    callback = next(item["callback"].__wrapped__ for item in app.callback_map.values()
                    if item.get("callback") and item["callback"].__name__ == "edit_targets")
    dataset = {"dataset_id": "run", "source_revision": "one"}
    context = context_key(dataset)
    def call(trigger, **values):
        monkeypatch.setattr(workbench, "ctx", SimpleNamespace(triggered_id=trigger))
        return _invoke_editor(callback, dataset=dataset, existing="C6\nC5", **values)
    result = call("evolution-query-run", pending="C4; C3", clicks=2, _run=1)
    assert result[0] == "C6\nC5\nC4\nC3" and result[1] == "" and result[3] == 3
    result = call("evolution-query-run", pending="C4", editor={"context": context, "editing": "C6"}, clicks=3, _run=1)
    assert result[0] == "C4\nC5" and result[3] == 4
    result = call("evolution-target-add", pending="C5", _add=1)
    assert result[0] == "C6\nC5" and result[3] is no_update
    result = call("evolution-batch-open", pending="C4", _open=1)
    assert result[5] is True and result[6] == "C6\nC5\nC4" and result[0] is no_update
    result = call("evolution-batch-cancel", _batch_cancel=1, batch="wrong")
    assert result[5] is False and result[0] is no_update
    result = call("evolution-batch-save", _save=1, batch="C3\nC2")
    assert result[0] == "C3\nC2" and result[3] is no_update
    result = call("evolution-query-run", _run=1, pending="Xx0")
    assert result[0] is no_update and result[3] is no_update and "未知元素" in result[4]
    result = call({"type": "evolution-target-remove", "target": "C6", "context": context}, removals=[1, 0])
    assert result[0] == "C5" and result[3] is no_update
    with pytest.raises(PreventUpdate):
        call({"type": "evolution-target-remove", "target": "C6", "context": "old"}, removals=[1, 0])


def test_csv_download_selects_drawn_curves_and_preserves_all_raw_points(abundance):
    from scripts.webapp_dash.app import create_app
    payload = svc.build_species_evolution(abundance, ['六碳::C6', '氧气::formula:O2'],
        start_timestep=10, end_timestep=30, normalize='max', smooth_window=3, downsample=1)
    assert len(payload['x_values']) == 2
    assert payload['curves'][0]['values'] != payload['curves'][0]['raw_values']
    selected = svc.evolution_to_csv(payload, curve_names=['六碳'])
    rows = list(csv.reader(io.StringIO(selected)))
    assert rows[0] == ['analyzed_frame', 'source_timestep', '六碳']
    assert [row[:2] for row in rows[1:]] == [['1', '10'], ['2', '20'], ['3', '30']]
    assert [float(row[2]) for row in rows[1:]] == [7, 5, 11]
    assert '氧气' in next(csv.reader(io.StringIO(svc.evolution_to_csv(payload))))
    assert svc.evolution_to_csv(payload, curve_names=[]) == ''
    payload['visible_curve_names'] = ['六碳']
    client = create_app().server.test_client()
    request = {'output': 'evolution-csv-download.data',
               'outputs': {'id': 'evolution-csv-download', 'property': 'data'},
               'inputs': [{'id': 'evolution-csv-btn', 'property': 'n_clicks', 'value': 1}],
               'state': [{'id': 'evolution-payload-store', 'property': 'data', 'value': payload}],
               'changedPropIds': ['evolution-csv-btn.n_clicks']}
    response = client.post('/_dash-update-component', json=request)
    assert response.status_code == 200
    download = response.get_json()['response']['evolution-csv-download']['data']
    assert download['filename'] == 'abundance_raw.csv'
    assert download['content'] == '\ufeff' + selected
    assert download['type'] == 'text/csv;charset=utf-8'
    payload['visible_curve_names'] = []
    assert client.post('/_dash-update-component', json=request).status_code == 204
