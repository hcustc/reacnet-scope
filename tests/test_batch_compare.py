from __future__ import annotations

import csv
import io
from pathlib import Path
from typing import Any

import pytest

from reacnet_scope import dir_browser
from reacnet_scope.batch_compare import BatchComparator, ConditionGroup, SimulationCondition
from reacnet_scope.network import Reaction, ReactionNetwork
from reacnet_scope import services as svc
from scripts.webapp_dash.app import create_app


def _network(*reactions: Reaction) -> ReactionNetwork:
    return ReactionNetwork(list(reactions))


def _write_reactions(folder: Path, name: str, lines: list[str]) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    reaction_file = folder / f"{name}.reactionabcd"
    reaction_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return reaction_file


def _allow_test_root(monkeypatch: pytest.MonkeyPatch, root: Path) -> None:
    roots = [root.resolve()]
    monkeypatch.setattr(dir_browser, "ALLOWED_ROOTS", roots)
    monkeypatch.setattr(svc, "ALLOWED_ROOTS", roots)


def _callback_payload(
    client: Any,
    *,
    input_ids: list[str],
    changed: str,
    input_values: dict[str, Any],
    state_values: dict[str, Any] | None = None,
    output_id: str = "",
) -> dict[str, Any]:
    dependency = next(
        item
        for item in client.get("/_dash-dependencies").get_json()
        if [value["id"] for value in item["inputs"]] == input_ids
        and (not output_id or f"{output_id}." in str(item.get("output") or ""))
    )
    output_spec = dependency["output"]
    if output_spec.startswith(".."):
        outputs: Any = [
            {
                "id": token.split(".")[0],
                "property": token.split(".")[1].split("@")[0],
            }
            for token in output_spec.strip(".").split("...")
        ]
    else:
        outputs = {
            "id": output_spec.split(".")[0],
            "property": output_spec.split(".")[1].split("@")[0],
        }
    states = state_values or {}
    return {
        "output": output_spec,
        "outputs": outputs,
        "changedPropIds": [changed],
        "inputs": [
            {
                "id": item["id"],
                "property": item["property"],
                "value": input_values.get(
                    f"{item['id']}.{item['property']}",
                    input_values.get(item["id"]),
                ),
            }
            for item in dependency["inputs"]
        ],
        "state": [
            {
                "id": item["id"],
                "property": item["property"],
                "value": states.get(
                    f"{item['id']}.{item['property']}",
                    states.get(item["id"]),
                ),
            }
            for item in dependency["state"]
        ],
    }


def _sample_grouped_payload() -> dict[str, Any]:
    columns = [
        {"field": "index", "headerName": "#", "type": "numericColumn"},
        {"field": "reaction_smiles", "headerName": "反应式 (SMILES)"},
        {"field": "group_1_mean_tp", "headerName": "300 K · 平均 TP", "type": "numericColumn"},
    ]
    row = {
        "id": "reaction_1",
        "index": 1,
        "reaction_smiles": "[C] -> [O]",
        "reaction_formulas": "C -> O",
        "detection_rate": 1.0,
        "total_tp": 30,
        "total_net_tp": 24,
        "group_1_mean_tp": 15.0,
    }
    detail = {
        "id": "reaction_1",
        "reaction_smiles": "[C] -> [O]",
        "reaction_formulas": "C -> O",
        "detection_rate": 1.0,
        "total_tp": 30,
        "total_net_tp": 24,
        "groups": [
            {
                "id": "group_1",
                "group_name": "300 K",
                "n_replicates": 2,
                "detected_count": 2,
                "detection_rate": 1.0,
                "mean_tp": 15.0,
                "std_tp": 7.07,
                "mean_net_tp": 12.0,
                "ci_95_lower": -48.53,
                "ci_95_upper": 78.53,
                "replicates": [
                    {"name": "rep1", "tp": 10.0},
                    {"name": "rep2", "tp": 20.0},
                ],
            }
        ],
    }
    return {
        "ok": True,
        "rows": [row],
        "columns": columns,
        "groups": [{"id": "group_1", "name": "300 K", "n_replicates": 2}],
        "details": {"reaction_1": detail},
        "meta": {
            "status": "ok",
            "message": "对比完成：1 个反应，1 个条件组，2 个重复实验",
            "n_reactions": 1,
            "n_groups": 1,
            "n_conditions": 2,
        },
    }


def test_exact_comparison_does_not_emit_formula_phantoms_or_merge_isomers() -> None:
    first = Reaction(("[H][C][O]",), ("[H]", "[C][O]"), 10)
    second = Reaction(("[H][O][C]",), ("[H]", "[C][O]"), 20)
    comparator = BatchComparator()
    comparator.add_condition("a", _network(first))
    comparator.add_condition("b", _network(second))

    comparisons = comparator.compare_all_common(top_n=50)
    rows, _ = comparator.build_comparison_matrix(comparisons)

    assert len(rows) == 2
    assert {row["reaction_smiles"] for row in rows} == {first.key, second.key}
    assert all(row["detection_rate"] == 0.5 for row in rows)
    first_row = next(row for row in rows if row["reaction_smiles"] == first.key)
    assert first_row["tp_a"] == 10
    assert first_row["tp_b"] == 0


def test_exact_comparison_preserves_multiplicity_and_directional_net_flux() -> None:
    forward = Reaction(("[H]", "[H]"), ("[H][H]",), 10)
    reverse = Reaction(("[H][H]",), ("[H]", "[H]"), 4)
    different_multiplicity = Reaction(("[H]",), ("[H][H]",), 99)
    comparator = BatchComparator()
    comparator.add_condition("run", _network(forward, reverse, different_multiplicity))

    comparison = comparator.compare_reaction("[H] + [H] -> [H][H]")

    assert comparison.reaction_smiles == forward.key
    assert comparison.forward_tp_by_condition["run"] == 10
    assert comparison.reverse_tp_by_condition["run"] == 4
    assert comparison.net_tp_by_condition["run"] == 6
    assert comparison.detection_rate == 1.0


def test_recursive_scanner_groups_generic_replicate_names(tmp_path: Path) -> None:
    first = _write_reactions(
        tmp_path / "series" / "case_rep1",
        "run",
        ["10 [C]->[O]"],
    )
    second = _write_reactions(
        tmp_path / "series" / "case_rep2",
        "run",
        ["12 [C]->[O]"],
    )
    comparator = BatchComparator()

    conditions = comparator.scan_directory_tree(str(tmp_path))
    groups = comparator.auto_group_conditions(conditions)

    assert [condition.name for condition in conditions] == [
        "series/case_rep1",
        "series/case_rep2",
    ]
    assert [condition.replicate for condition in conditions] == [1, 2]
    assert [condition.artifacts["reaction"] for condition in conditions] == [
        str(first),
        str(second),
    ]
    assert len(groups) == 1
    assert groups[0].group_name == "series/case"
    assert groups[0].n_replicates == 2


def test_scanner_accepts_a_dataset_directory_as_the_root(tmp_path: Path) -> None:
    reaction_file = _write_reactions(tmp_path, "run", ["10 [C]->[O]"])
    comparator = BatchComparator()

    conditions = comparator.scan_directory_tree(str(tmp_path))

    assert len(conditions) == 1
    assert conditions[0].folder == str(tmp_path)
    assert conditions[0].artifacts["reaction"] == str(reaction_file)


def test_scanner_extracts_physical_conditions_independent_of_token_order(
    tmp_path: Path,
) -> None:
    _write_reactions(
        tmp_path / "T500K_P-1atm_O2-0.5_rep1",
        "run",
        ["10 [C]->[O]"],
    )
    _write_reactions(
        tmp_path / "T500K_P-1atm_O2-0.5_rep2",
        "run",
        ["12 [C]->[O]"],
    )
    comparator = BatchComparator()

    conditions = comparator.scan_directory_tree(str(tmp_path))
    groups = comparator.auto_group_conditions(conditions)

    assert [(item.temperature, item.pressure, item.o2_ratio) for item in conditions] == [
        (500, 1, 0.5),
        (500, 1, 0.5),
    ]
    assert len(groups) == 1
    assert groups[0].group_name == "T500K_O2=0.5_P=1"
    assert groups[0].n_replicates == 2


def test_replicate_statistics_include_net_flux_and_student_t_interval() -> None:
    forward = Reaction(("[C]",), ("[O]",), 10)
    reverse = Reaction(("[O]",), ("[C]",), 2)
    comparator = BatchComparator()
    comparator.add_condition("r1", _network(forward, reverse))
    comparator.add_condition(
        "r2",
        _network(
            Reaction(("[C]",), ("[O]",), 20),
            Reaction(("[O]",), ("[C]",), 4),
        ),
    )
    comparison = comparator.compare_reaction(forward.key)
    group = ConditionGroup(
        group_name="condition",
        conditions=[
            SimulationCondition("r1", "/r1", replicate=1),
            SimulationCondition("r2", "/r2", replicate=2),
        ],
    )

    statistics = comparator.statistical_summary(comparison, group)

    assert statistics["mean_tp"] == 15.0
    assert statistics["std_tp"] == 7.07
    assert statistics["mean_net_tp"] == 12.0
    assert statistics["std_net_tp"] == 5.66
    assert statistics["ci_95_lower"] == -48.53
    assert statistics["ci_95_upper"] == 78.53
    assert [row["tp"] for row in statistics["replicates"]] == [10.0, 20.0]


def test_grouped_service_returns_group_columns_details_and_friendly_csv(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _allow_test_root(monkeypatch, tmp_path)
    first = _write_reactions(
        tmp_path / "T300K_O2-0.1_rep1",
        "run",
        ["10 [C]->[O]", "2 [O]->[C]"],
    )
    second = _write_reactions(
        tmp_path / "T300K_O2-0.1_rep2",
        "run",
        ["20 [C]->[O]", "4 [O]->[C]"],
    )
    third = _write_reactions(
        tmp_path / "T400K_O2-0.1_rep1",
        "run",
        ["5 [C]->[O]", "1 [O]->[C]"],
    )
    requests = [
        {
            "group_name": "300 K",
            "conditions": [
                {"name": "300-r1", "folder": str(first.parent), "reaction_file": str(first), "replicate": 1},
                {"name": "300-r2", "folder": str(second.parent), "reaction_file": str(second), "replicate": 2},
            ],
        },
        {
            "group_name": "400 K",
            "conditions": [
                {"name": "400-r1", "folder": str(third.parent), "reaction_file": str(third), "replicate": 1},
            ],
        },
    ]

    payload = svc.run_grouped_batch_comparison(requests, top_n=1)

    assert payload["meta"] == {
        "status": "ok",
        "message": "对比完成：1 个反应，2 个条件组，3 个重复实验",
        "n_reactions": 1,
        "n_groups": 2,
        "n_conditions": 3,
    }
    row = payload["rows"][0]
    assert row["reaction_smiles"] == "[C] -> [O]"
    assert row["total_tp"] == 35
    assert row["total_net_tp"] == 28
    assert row["group_1_mean_tp"] == 15.0
    assert row["group_1_std_tp"] == 7.07
    assert row["group_2_mean_tp"] == 5.0
    detail = payload["details"][row["id"]]
    assert [group["n_replicates"] for group in detail["groups"]] == [2, 1]
    assert [item["name"] for item in detail["groups"][0]["replicates"]] == [
        "300-r1",
        "300-r2",
    ]

    csv_text = svc.batch_comparison_to_csv(payload)
    csv_rows = list(csv.reader(io.StringIO(csv_text.lstrip("\ufeff"))))
    assert "300 K · 平均 TP" in csv_rows[0]
    assert "id" not in csv_rows[0]
    assert csv_rows[1][1] == "[C] -> [O]"


def test_grouped_service_fails_as_a_unit_when_one_selected_source_is_bad(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _allow_test_root(monkeypatch, tmp_path)
    good = _write_reactions(tmp_path / "good", "run", ["10 [C]->[O]"])
    requests = [
        {
            "group_name": "mixed",
            "conditions": [
                {"name": "good", "folder": str(good.parent), "reaction_file": str(good)},
                {"name": "missing", "folder": str(tmp_path / "missing")},
            ],
        }
    ]

    with pytest.raises(svc.ServiceError) as caught:
        svc.run_grouped_batch_comparison(requests)

    assert caught.value.reason == "condition_load_failed"
    assert "missing" in caught.value.message


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"min_detection_rate": 1.1}, "bad_detection_rate"),
        ({"top_n": 1.5}, "bad_top_n"),
        ({"top_n": 501}, "bad_top_n"),
    ],
)
def test_grouped_service_validates_comparison_limits(
    kwargs: dict[str, Any],
    reason: str,
) -> None:
    with pytest.raises(svc.ServiceError) as caught:
        svc.run_grouped_batch_comparison(
            [{"group_name": "unused", "conditions": [{"folder": "/unused"}]}],
            **kwargs,
        )

    assert caught.value.reason == reason


def test_scan_service_reports_recursive_groups_and_reaction_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _allow_test_root(monkeypatch, tmp_path)
    _write_reactions(tmp_path / "nested" / "case_rep1", "run", ["10 [C]->[O]"])
    _write_reactions(tmp_path / "nested" / "case_rep2", "run", ["12 [C]->[O]"])

    payload = svc.scan_batch_conditions(str(tmp_path))

    assert payload["total_conditions"] == 2
    assert payload["total_groups"] == 1
    assert payload["groups"][0]["n_replicates"] == 2
    assert all(item["reaction_file"].endswith(".reactionabcd") for item in payload["conditions"])


def test_batch_ui_catalog_includes_current_and_recent_datasets() -> None:
    app = create_app()
    client = app.server.test_client()
    current = {
        "folder": "/data/current",
        "base": "/data/current/run",
        "label": "current-run",
        "artifacts": {"reaction": "/data/current/run.reactionabcd"},
    }
    recent = [
        {
            "folder": "/data/recent",
            "base": "/data/recent/other",
            "label": "other-run",
            "loaded_at": 10,
        }
    ]

    response = client.post(
        "/_dash-update-component",
        json=_callback_payload(
            client,
            input_ids=["app-store", "recent-datasets"],
            changed="app-store.data",
            input_values={"app-store": current, "recent-datasets": recent},
            output_id="batch-managed-selector",
        ),
    )

    assert response.status_code == 200
    body = response.get_json()["response"]
    options = body["batch-managed-selector"]["options"]
    assert [item["value"] for item in options] == [current["base"], recent[0]["base"]]
    assert options[0]["label"] == "当前 · current-run"
    assert options[1]["label"] == "最近 · other-run"
    assert len(body["batch-managed-store"]["data"]["datasets"]) == 2
    assert "可选择 2 个" in body["batch-managed-status"]["children"]


def test_batch_ui_scan_selects_all_discovered_groups(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {
        "conditions": [
            {
                "name": "case_rep1",
                "folder": "/data/case_rep1",
                "reaction_file": "/data/case_rep1/run.reactionabcd",
                "replicate": 1,
            }
        ],
        "groups": [
            {
                "group_name": "case",
                "n_replicates": 1,
                "conditions": ["case_rep1"],
            }
        ],
        "total_conditions": 1,
        "total_groups": 1,
        "warnings": [],
    }
    monkeypatch.setattr(svc, "scan_batch_conditions", lambda _root: payload)
    app = create_app()
    client = app.server.test_client()

    response = client.post(
        "/_dash-update-component",
        json=_callback_payload(
            client,
            input_ids=["batch-scan-btn"],
            changed="batch-scan-btn.n_clicks",
            input_values={"batch-scan-btn": 1},
            state_values={"batch-root-dir": "/data"},
            output_id="batch-condition-selector",
        ),
    )

    assert response.status_code == 200
    body = response.get_json()["response"]
    assert body["batch-condition-selector"]["value"] == ["case"]
    assert body["batch-condition-selector"]["options"][0]["label"] == "case (1 个重复)"
    assert body["batch-conditions-store"]["data"] == payload
    assert "已默认选择全部条件组" in str(body["batch-conditions-status"]["children"])


def test_batch_ui_suggests_the_current_dataset_parent_as_scan_root() -> None:
    app = create_app()
    client = app.server.test_client()

    response = client.post(
        "/_dash-update-component",
        json=_callback_payload(
            client,
            input_ids=["page-store", "batch-use-current-parent-btn"],
            changed="page-store.data",
            input_values={
                "page-store": {"page": "batch-compare"},
                "batch-use-current-parent-btn": 0,
            },
            state_values={
                "app-store": {"folder": "/data/series/run1"},
                "batch-root-dir": "",
            },
            output_id="batch-root-dir",
        ),
    )

    assert response.status_code == 200
    assert response.get_json()["response"]["batch-root-dir"]["value"] == "/data/series"


def test_batch_ui_compare_renders_typed_columns_and_invalidates_stale_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    grouped_payload = _sample_grouped_payload()
    captured: dict[str, Any] = {}

    def fake_compare(requests, **kwargs):
        captured["requests"] = requests
        captured["kwargs"] = kwargs
        return grouped_payload

    monkeypatch.setattr(svc, "run_grouped_batch_comparison", fake_compare)
    app = create_app()
    client = app.server.test_client()
    managed_store = {
        "datasets": [
            {
                "id": "/data/run",
                "folder": "/data",
                "base": "/data/run",
                "label": "managed-run",
            }
        ]
    }
    input_values = {
        "batch-compare-btn": 1,
        "batch-managed-selector": ["/data/run"],
        "batch-condition-selector": [],
        "batch-min-detection": 0.25,
        "batch-top-n": 25,
        "batch-managed-store": managed_store,
        "batch-conditions-store": None,
    }
    input_ids = [
        "batch-compare-btn",
        "batch-managed-selector",
        "batch-condition-selector",
        "batch-min-detection",
        "batch-top-n",
        "batch-managed-store",
        "batch-conditions-store",
    ]

    response = client.post(
        "/_dash-update-component",
        json=_callback_payload(
            client,
            input_ids=input_ids,
            changed="batch-compare-btn.n_clicks",
            input_values=input_values,
            output_id="batch-matrix-grid",
        ),
    )

    assert response.status_code == 200
    body = response.get_json()["response"]
    assert captured["requests"][0]["group_name"] == "managed-run"
    assert captured["kwargs"] == {"min_detection_rate": 0.25, "top_n": 25}
    assert body["batch-matrix-grid"]["data"][0]["id"] == "reaction_1"
    assert body["batch-matrix-grid"]["columns"] == [
        {"id": "index", "name": "#", "type": "numeric"},
        {"id": "reaction_smiles", "name": "反应式 (SMILES)", "type": "text"},
        {"id": "group_1_mean_tp", "name": "300 K · 平均 TP", "type": "numeric"},
    ]
    assert body["batch-grid-container"]["style"] == {}
    assert body["batch-csv-btn"]["disabled"] is False
    assert body["batch-detail-card"]["style"] == {"display": "none"}

    stale_response = client.post(
        "/_dash-update-component",
        json=_callback_payload(
            client,
            input_ids=input_ids,
            changed="batch-min-detection.value",
            input_values={**input_values, "batch-min-detection": 0.5},
            output_id="batch-matrix-grid",
        ),
    )
    stale_body = stale_response.get_json()["response"]
    assert stale_body["batch-matrix-grid"]["data"] == []
    assert stale_body["batch-grid-container"]["style"] == {"display": "none"}
    assert stale_body["batch-csv-btn"]["disabled"] is True
    assert "对比条件已变化" in str(stale_body["batch-alert"]["children"])


def test_batch_ui_detail_uses_row_ids_and_displays_replicate_statistics() -> None:
    app = create_app()
    client = app.server.test_client()
    store = _sample_grouped_payload()

    response = client.post(
        "/_dash-update-component",
        json=_callback_payload(
            client,
            input_ids=["batch-matrix-grid"],
            changed="batch-matrix-grid.selected_row_ids",
            input_values={"batch-matrix-grid": ["reaction_1"]},
            state_values={"batch-matrix-grid-store": store},
            output_id="batch-reaction-chart",
        ),
    )

    assert response.status_code == 200
    body = response.get_json()["response"]
    figure = body["batch-reaction-chart"]["figure"]
    assert [trace["name"] for trace in figure["data"]] == ["组平均 TP", "单次重复"]
    assert figure["data"][0]["error_y"]["array"] == [7.07]
    assert body["batch-detail-card"]["style"] == {"display": "block"}
    stats_text = str(body["batch-reaction-stats"]["children"])
    assert "平均 TP 15.00 ± 7.07" in stats_text
    assert "95% CI [-48.53, 78.53]" in stats_text


def test_batch_ui_csv_export_uses_display_headers() -> None:
    app = create_app()
    client = app.server.test_client()
    store = _sample_grouped_payload()

    response = client.post(
        "/_dash-update-component",
        json=_callback_payload(
            client,
            input_ids=["batch-csv-btn"],
            changed="batch-csv-btn.n_clicks",
            input_values={"batch-csv-btn": 1},
            state_values={"batch-matrix-grid-store": store},
            output_id="batch-csv-download",
        ),
    )

    assert response.status_code == 200
    download = response.get_json()["response"]["batch-csv-download"]["data"]
    assert download["filename"] == "batch_comparison.csv"
    assert download["content"].startswith("\ufeff#,反应式 (SMILES),300 K · 平均 TP")
