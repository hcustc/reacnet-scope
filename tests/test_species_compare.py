from __future__ import annotations

import builtins
import csv
import io
import json
import zipfile
from pathlib import Path

import pytest

from reacnet_scope import services as svc
from reacnet_scope.composition import SPECIES_COMPOSITION_STORE
from reacnet_scope.trajectory import save_timestep_ps
from scripts.webapp_dash.app import create_app


def _source(tmp_path: Path, name: str, lines: list[str]) -> str:
    path = tmp_path / f"{name}.species"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    SPECIES_COMPOSITION_STORE.build(str(path))
    return str(path)


def _entry(path: str, label: str, target: str) -> dict[str, str]:
    return {"species_file": path, "label": label, "target_smiles": target}


def test_manual_exact_targets_keep_independent_timelines_and_export(tmp_path, monkeypatch):
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "workspace"))
    first = _source(tmp_path, "first", ["Timestep 0: [H][O][H] 1", "Timestep 10: [H][O][H] 4"])
    second = _source(tmp_path, "second", ["Timestep 5: [H][O]([H]) 2", "Timestep 20: [H][O]([H]) 3", "Timestep 30: [H][O]([H]) 1"])
    sources = [_entry(first, "甲", "[H][O][H]"), _entry(second, "乙", "[H][O]([H])")]

    original_open = builtins.open
    def guarded_open(file, *args, **kwargs):
        if str(file) in {first, second}:
            raise AssertionError("online comparison opened raw Species source")
        return original_open(file, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(builtins, "open", guarded_open)
        payload = svc.compare_species_sources(sources)

    assert [row["status"] for row in payload["summary"]] == ["ready", "ready"]
    assert [(row["initial"], row["final"], row["peak"], row["peak_time"]) for row in payload["summary"]] == [(1, 4, 4, 10), (2, 1, 3, 20)]
    assert [curve["x_values"] for curve in payload["curves"]] == [[0, 10], [5, 20, 30]]
    assert payload["query"]["sources"] == sources
    with zipfile.ZipFile(io.BytesIO(svc.species_comparison_zip(payload))) as archive:
        query = json.loads(archive.read("query.json"))
        curve_reader = csv.DictReader(io.StringIO(archive.read("curves.csv").decode()))
        curves = list(curve_reader)
        summary = list(csv.DictReader(io.StringIO(archive.read("summary.csv").decode())))
    assert query == payload["query"]
    assert curve_reader.fieldnames == ["species_file", "label", "target_smiles", "analyzed_frame", "source_timestep", "abundance"]
    assert [(row["label"], row["target_smiles"], row["source_timestep"], row["abundance"]) for row in curves] == [
        ("甲", "[H][O][H]", "0", "1"), ("甲", "[H][O][H]", "10", "4"),
        ("乙", "[H][O]([H])", "5", "2"), ("乙", "[H][O]([H])", "20", "3"),
        ("乙", "[H][O]([H])", "30", "1")]
    assert [(row["label"], row["peak"], row["peak_time"]) for row in summary] == [("甲", "4", "10"), ("乙", "3", "20")]


def test_missing_target_zero_and_missing_index_are_distinct(tmp_path, monkeypatch):
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "workspace"))
    zero = _source(tmp_path, "zero", ["Timestep 0: C 0", "Timestep 10: C 0"])
    missing = _source(tmp_path, "missing", ["Timestep 0: O 2"])
    unprepared = tmp_path / "unprepared.species"
    unprepared.write_text("Timestep 0: C 7\n", encoding="utf-8")
    result = svc.compare_species_sources([_entry(zero, "zero", "C"), _entry(missing, "absent", "C"), _entry(str(unprepared), "no index", "C")])
    assert [row["status"] for row in result["summary"]] == ["zero", "target_not_found", "missing_index"]
    assert len(result["curves"]) == 1
    assert svc.species_compare_catalog(str(unprepared))["status"] == "missing_index"


def test_time_conversion_is_per_source_and_query_replacement_has_no_old_curve(tmp_path, monkeypatch):
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "workspace"))
    first = _source(tmp_path, "first", ["Timestep 0: C 1 O 4", "Timestep 10: C 3 O 2"])
    second = _source(tmp_path, "second", ["Timestep 5: C 2", "Timestep 15: C 4"])
    save_timestep_ps(first, 0.2)
    sources = [_entry(first, "first", "C"), _entry(second, "second", "C")]
    partial = svc.compare_species_sources(sources, x_axis="ps")
    assert [row["status"] for row in partial["summary"]] == ["ready", "time_conversion_missing"]
    assert partial["curves"] == []
    assert partial["comparability"]["status"] == "not_comparable"
    save_timestep_ps(second, 0.5)
    physical = svc.compare_species_sources(sources, x_axis="ps")
    assert [curve["x_values"] for curve in physical["curves"]] == [[0.0, 2.0], [2.5, 7.5]]
    replaced = svc.compare_species_sources([_entry(first, "first", "O"), _entry(second, "second", "C")])
    assert replaced["curves"][0]["values"] == [4, 2]
    assert all(curve["target_smiles"] != "O" for curve in replaced["curves"][1:])


def test_source_records_keep_explicit_metadata_unknowns_and_evidence_levels(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "workspace"))
    first = _source(tmp_path, "first", ["Timestep 0: C 1"])
    second = _source(tmp_path, "second", ["Timestep 0: C 2"])
    (tmp_path / "rng_run.json").write_text(
        json.dumps({"parameters": {"miso": 2, "runHMM": True}}),
        encoding="utf-8",
    )
    result = svc.compare_species_sources(
        [
            {
                **_entry(first, "first", "C"),
                "model_iteration": "iter32",
                "simulation_condition": "2500 K",
                "replicate": "seed-1",
            },
            _entry(second, "second", "C"),
        ]
    )

    assert result["schema_version"] == 2
    assert result["identity_basis"] == "exact_rng_species_per_source"
    first_record, second_record = result["source_records"]
    assert first_record["dataset_id"]
    assert first_record["source_revision"]["fingerprint"]
    assert first_record["metadata"]["model_iteration"] == {
        "value": "iter32",
        "status": "confirmed",
        "source": "comparison_request",
    }
    assert second_record["metadata"]["model_iteration"]["status"] == "unknown"
    assert first_record["processing"]["fields"]["miso"]["value"] == 2
    assert first_record["evidence"]["species_abundance"] is True
    assert first_record["evidence"]["timed_molecular_evidence"] is False

    with zipfile.ZipFile(io.BytesIO(svc.species_comparison_zip(result))) as archive:
        assert "sources.json" in archive.namelist()
        assert "comparability.json" in archive.namelist()


def _post_callback(
    client, output_contains, changed, values, states=None, expected_status=200
):
    dependency = next(item for item in client.get("/_dash-dependencies").get_json()
                      if output_contains in item["output"])
    output = dependency["output"]
    if output.startswith(".."):
        outputs = [{"id": token.rsplit(".", 1)[0], "property": token.rsplit(".", 1)[1].split("@")[0]}
                   for token in output.strip(".").split("...")]
    else:
        outputs = {"id": output.rsplit(".", 1)[0], "property": output.rsplit(".", 1)[1].split("@")[0]}
    def data(items, source):
        return [{"id": item["id"], "property": item["property"],
                 "value": source.get(f"{item['id']}.{item['property']}", source.get(item["id"]))}
                for item in items]
    response = client.post("/_dash-update-component", json={
        "output": output, "outputs": outputs, "changedPropIds": [changed],
        "inputs": data(dependency["inputs"], values),
        "state": data(dependency["state"], states or {}),
    })
    assert response.status_code == expected_status, response.get_data(as_text=True)
    if expected_status == 204:
        return None
    return response.get_json()["response"]


def _find_pattern_component(node, component_type):
    if isinstance(node, dict):
        props = node.get("props") or {}
        component_id = props.get("id")
        if isinstance(component_id, dict) and component_id.get("type") == component_type:
            return node
        for value in node.values():
            found = _find_pattern_component(value, component_type)
            if found is not None:
                return found
    elif isinstance(node, list):
        for value in node:
            found = _find_pattern_component(value, component_type)
            if found is not None:
                return found
    return None


def test_dash_compare_discards_result_when_target_or_sources_change(tmp_path, monkeypatch):
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "workspace"))
    first = _source(tmp_path, "first", ["Timestep 0: C 1 O 5", "Timestep 10: C 3 O 2"])
    second = _source(tmp_path, "second", ["Timestep 5: C 4", "Timestep 20: C 2"])
    sources = [_entry(first, "first", "C"), _entry(second, "second", "C")]
    client = create_app().server.test_client()
    target_pattern = '{"path":["ALL"],"type":"species-compare-target"}'
    label_pattern = '{"path":["ALL"],"type":"species-compare-label"}'
    values = {
        "species-compare-run": 1, "species-compare-sources-store": sources,
        target_pattern + ".value": ["C", "C"],
        target_pattern + ".id": [{"path": first, "type": "species-compare-target"}, {"path": second, "type": "species-compare-target"}],
        label_pattern + ".value": ["first", "second"],
        label_pattern + ".id": [{"path": first, "type": "species-compare-label"}, {"path": second, "type": "species-compare-label"}],
        "species-compare-axis": "step",
    }
    result = _post_callback(client, "species-compare-result-store.data", "species-compare-run.n_clicks", values)
    assert len(result["species-compare-graph"]["figure"]["data"]) == 2
    assert result["species-compare-export"]["disabled"] is False
    values[target_pattern + ".value"] = ["O", "C"]
    cleared = _post_callback(client, "species-compare-result-store.data", target_pattern + ".value", values)
    assert cleared["species-compare-result-store"]["data"] is None
    assert cleared["species-compare-graph"]["figure"]["data"] == []
    assert cleared["species-compare-export"]["disabled"] is True
    removed = _post_callback(client, "species-compare-sources-store.data",
                             json.dumps({"path": second, "type": "species-compare-remove"}, sort_keys=True, separators=(",", ":")) + ".n_clicks",
                             {"species-compare-managed": [], "species-compare-add-path": None,
                              '{"path":["ALL"],"type":"species-compare-remove"}': [None, 1]},
                             {"species-compare-sources-store": sources,
                              target_pattern + ".value": ["O", "C"], target_pattern + ".id": values[target_pattern + ".id"],
                              label_pattern + ".value": ["first", "second"], label_pattern + ".id": values[label_pattern + ".id"]})
    assert len(removed["species-compare-sources-store"]["data"]) == 1
    assert removed["species-compare-sources-store"]["data"][0]["target_smiles"] == "O"


def test_dash_adds_sources_and_offers_each_exact_catalog(tmp_path, monkeypatch):
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "workspace"))
    first = _source(tmp_path, "first", ["Timestep 0: [H][O][H] 1"])
    second = _source(tmp_path, "second", ["Timestep 0: [H][O]([H]) 2"])
    client = create_app().server.test_client()
    empty_pattern = '{"path":["ALL"],"type":"species-compare-remove"}'
    target_pattern = '{"path":["ALL"],"type":"species-compare-target"}'
    label_pattern = '{"path":["ALL"],"type":"species-compare-label"}'
    first_result = _post_callback(client, "species-compare-sources-store.data", "species-compare-add-path.n_clicks",
        {"species-compare-managed": [], "species-compare-add-path": 1, empty_pattern: []},
        {"species-compare-managed": [], "batch-managed-store": {"datasets": []}, "species-compare-path": first,
         "species-compare-new-label": "water A", "species-compare-sources-store": [],
         target_pattern + ".value": [], target_pattern + ".id": [],
         label_pattern + ".value": [], label_pattern + ".id": []})
    sources = first_result["species-compare-sources-store"]["data"]
    second_result = _post_callback(client, "species-compare-sources-store.data", "species-compare-add-path.n_clicks",
        {"species-compare-managed": [], "species-compare-add-path": 2, empty_pattern: [None]},
        {"species-compare-managed": [], "batch-managed-store": {"datasets": []}, "species-compare-path": second,
         "species-compare-new-label": "water B", "species-compare-sources-store": sources,
         target_pattern + ".value": ["[H][O][H]"],
         target_pattern + ".id": [{"path": first, "type": "species-compare-target"}],
         label_pattern + ".value": ["water A"],
         label_pattern + ".id": [{"path": first, "type": "species-compare-label"}]})
    sources = second_result["species-compare-sources-store"]["data"]
    assert [item["label"] for item in sources] == ["water A", "water B"]
    assert sources[0]["target_smiles"] == "[H][O][H]"
    rendered = _post_callback(client, "species-compare-sources.children", "species-compare-sources-store.data",
                              {"species-compare-sources-store": sources})
    children = rendered["species-compare-sources"]["children"]
    options = [
        _find_pattern_component(row, "species-compare-target")["props"]["options"]
        for row in children
    ]
    assert [[option["value"] for option in group] for group in options] == [["[H][O][H]"], ["[H][O]([H])"]]


def test_managed_selection_immediately_reuses_imported_sources_and_deselects(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "workspace"))
    first = _source(tmp_path, "first", ["Timestep 0: C 1"])
    second = _source(tmp_path, "second", ["Timestep 0: O 2"])
    bases = [str(Path(path).with_suffix("")) for path in (first, second)]
    managed = {
        "datasets": [
            {"id": base, "base": base, "label": f"已导入 {index}"}
            for index, base in enumerate(bases, 1)
        ]
    }
    client = create_app().server.test_client()
    remove_pattern = '{"path":["ALL"],"type":"species-compare-remove"}'
    target_pattern = '{"path":["ALL"],"type":"species-compare-target"}'

    selected = _post_callback(
        client,
        "species-compare-sources-store.data",
        "species-compare-managed.value",
        {"species-compare-managed": bases, remove_pattern: []},
        {"batch-managed-store": managed, "species-compare-sources-store": []},
    )
    sources = selected["species-compare-sources-store"]["data"]
    assert [item["species_file"] for item in sources] == [first, second]
    assert [item["source_origin"] for item in sources] == ["managed", "managed"]

    compared = _post_callback(
        client,
        "species-compare-result-store.data",
        "species-compare-run.n_clicks",
        {
            "species-compare-run": 1,
            "species-compare-sources-store": sources,
            target_pattern + ".value": ["C", "O"],
            target_pattern + ".id": [
                {"type": "species-compare-target", "path": path}
                for path in (first, second)
            ],
            "species-compare-axis": "step",
        },
    )
    payload = compared["species-compare-result-store"]["data"]
    assert len(payload["curves"]) == 2
    assert all("source_origin" not in row for row in payload["summary"])

    restored = _post_callback(
        client,
        "species-compare-managed.options",
        "batch-managed-store.data",
        {"batch-managed-store": managed},
        {"species-compare-sources-store": sources},
    )
    assert restored["species-compare-managed"]["value"] == bases

    manual = {**_entry(str(tmp_path / "manual.species"), "手工来源", "N"), "source_origin": "manual"}
    reduced = _post_callback(
        client,
        "species-compare-sources-store.data",
        "species-compare-managed.value",
        {
            "species-compare-managed": [bases[0]],
            remove_pattern: [None],
        },
        {
            "batch-managed-store": managed,
            "species-compare-sources-store": [*sources, manual],
            target_pattern + ".value": ["C", "O", "N"],
            target_pattern + ".id": [
                {"type": "species-compare-target", "path": path}
                for path in (first, second, manual["species_file"])
            ],
        },
    )
    remaining = reduced["species-compare-sources-store"]["data"]
    assert [item["species_file"] for item in remaining] == [first, manual["species_file"]]
    assert remaining[0]["target_smiles"] == "C"


def test_dynamic_remove_controls_do_not_drop_sources_without_click(tmp_path):
    first = str(tmp_path / "first.species")
    second = str(tmp_path / "second.species")
    sources = [_entry(first, "first", "C"), _entry(second, "second", "O")]
    client = create_app().server.test_client()
    remove_pattern = '{"path":["ALL"],"type":"species-compare-remove"}'
    changed = (
        json.dumps(
            {"path": first, "type": "species-compare-remove"},
            sort_keys=True,
            separators=(",", ":"),
        )
        + ".n_clicks"
    )

    result = _post_callback(
        client,
        "species-compare-sources-store.data",
        changed,
        {
            "species-compare-managed": [],
            "species-compare-add-path": None,
            "evolution-open-compare-btn": None,
            remove_pattern: [None, None],
        },
        {"species-compare-sources-store": sources},
        expected_status=204,
    )

    assert result is None


def test_dash_compare_preflight_requires_two_ready_exact_targets(tmp_path, monkeypatch):
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "workspace"))
    first = _source(tmp_path, "first", ["Timestep 0: C 1"])
    second = _source(tmp_path, "second", ["Timestep 0: O 2"])
    sources = [_entry(first, "first", "C"), _entry(second, "second", "O")]
    client = create_app().server.test_client()
    target_pattern = '{"path":["ALL"],"type":"species-compare-target"}'
    target_ids = [
        {"path": first, "type": "species-compare-target"},
        {"path": second, "type": "species-compare-target"},
    ]
    values = {
        "species-compare-sources-store": sources,
        target_pattern + ".value": ["C", "O"],
        target_pattern + ".id": target_ids,
        "species-compare-catalog-store": {
            first: svc.species_compare_catalog(first),
            second: svc.species_compare_catalog(second),
        },
        "species-compare-axis": "step",
    }

    ready = _post_callback(
        client,
        "species-compare-readiness.children",
        target_pattern + ".value",
        values,
    )
    assert ready["species-compare-run"]["disabled"] is False
    assert "2/2 个来源已就绪" in str(ready["species-compare-readiness"]["children"])

    values[target_pattern + ".value"] = ["C", "N"]
    blocked = _post_callback(
        client,
        "species-compare-readiness.children",
        target_pattern + ".value",
        values,
    )
    assert blocked["species-compare-run"]["disabled"] is True
    assert "索引中没有该精确 Species" in str(
        blocked["species-compare-readiness"]["children"]
    )


def test_evolution_handoff_adds_current_source_and_exact_species(tmp_path):
    species_file = tmp_path / "current.species"
    species_file.write_text("Timestep 0: C 1\n", encoding="utf-8")
    client = create_app().server.test_client()
    remove_pattern = '{"path":["ALL"],"type":"species-compare-remove"}'

    result = _post_callback(
        client,
        "species-compare-sources-store.data",
        "evolution-open-compare-btn.n_clicks",
        {
            "species-compare-managed": [],
            "species-compare-add-path": None,
            "evolution-open-compare-btn": 1,
            remove_pattern: [],
        },
        {
            "species-compare-sources-store": [],
            "app-store": {
                "label": "current run",
                "dataset_id": "dataset-1",
                "source_revision": {"fingerprint": "revision-1"},
                "selected_smiles": "C",
                "artifacts": {"species": str(species_file)},
            },
        },
    )

    source = result["species-compare-sources-store"]["data"][0]
    assert source["species_file"] == str(species_file.resolve())
    assert source["target_smiles"] == "C"
    assert source["dataset_id"] == "dataset-1"
    assert "已加入当前RNG 数据" in str(result["species-compare-entry-note"]["children"])


def test_comparison_pages_belong_to_their_analysis_workspaces():
    from scripts.webapp_dash.navigation import PAGE_WORKSPACES
    client = create_app().server.test_client()
    assert PAGE_WORKSPACES['batch-compare'] == 'species'
    assert PAGE_WORKSPACES['reaction-compare'] == 'reactions'
    for page, nav in [('batch-compare', 'species'), ('reaction-compare', 'reactions')]:
        response = _post_callback(client, 'page-title.children@', 'page-store.data',
                                  {'page-store': {'page': page}})
        assert response[f'page-{page}']['className'].endswith(' active')
        assert response[f'nav-{nav}']['aria-current'] == 'page'
