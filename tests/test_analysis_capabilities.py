from __future__ import annotations

import json
from pathlib import Path

import pytest

from reacnet_scope import dir_browser, prepare
from reacnet_scope import services as svc
from reacnet_scope.capabilities import analysis_capability_evidence


def test_capabilities_are_independent_and_always_explain_their_state() -> None:
    evidence = analysis_capability_evidence(
        {
            "reaction": "/data/run.reactionabcd",
            "species": "/data/run.species",
        },
        index_statuses={
            "event": "missing",
            "trajectory": "missing",
            "composition": "missing",
        },
    )

    assert {key: item["state"] for key, item in evidence.items()} == {
        "reaction_search": "ready",
        "species_abundance": "ready",
        "event_search": "missing-source",
        "trajectory_evidence": "missing-source",
        "element_distribution": "needs-preparation",
    }
    assert all(item["reason"] for item in evidence.values())


@pytest.mark.parametrize(
    ("store_state", "expected"),
    [
        ("ready", "ready"),
        ("missing", "needs-preparation"),
        ("building", "preparing"),
        ("stale", "stale"),
        ("invalid", "invalid"),
    ],
)
def test_all_prepared_capability_states_are_exposed(
    store_state: str,
    expected: str,
) -> None:
    evidence = analysis_capability_evidence(
        {"trajectory": "/data/run.lammpstrj"},
        index_statuses={"trajectory": {"state": store_state}},
    )

    assert evidence["trajectory_evidence"]["state"] == expected


def test_active_task_sets_only_its_capability_to_preparing() -> None:
    evidence = analysis_capability_evidence(
        {
            "reaction": "/data/run.reactionabcd",
            "trajectory": "/data/run.lammpstrj",
        },
        index_statuses={"trajectory": "missing"},
        tasks={"trajectory": {"state": "running", "phase": "scan-frames"}},
    )

    assert evidence["reaction_search"]["state"] == "ready"
    assert evidence["trajectory_evidence"] == {
        "key": "trajectory_evidence",
        "label": "轨迹证据",
        "state": "preparing",
        "reason": "Preparation Task 正在运行：scan-frames。",
        "source_kinds": ["trajectory"],
        "preparation_kind": "trajectory",
    }


def _trajectory_dataset(root: Path) -> Path:
    base = root / "run.lammpstrj"
    base.write_text("ITEM: TIMESTEP\n0\n", encoding="utf-8")
    Path(f"{base}.reactionabcd").write_text("1 [H]->[H]\n", encoding="utf-8")
    return base


def test_persisted_task_is_bound_to_dataset_revision_and_capability(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("REACNET_SCOPE_CACHE_DIR", raising=False)
    base = _trajectory_dataset(tmp_path)
    dataset = prepare.discover_dataset(str(tmp_path), base.name)

    prepare._run_preparation_task(
        dataset,
        capability="trajectory",
        source_file=str(base),
        action="build",
        operation=lambda report: report(
            {"progress": 0.5, "phase": "scan-frames", "message": "Scanning"}
        ),
        report=lambda _update: None,
    )

    task = prepare.preparation_task_status(
        str(tmp_path), base=base.name, capability="trajectory"
    )
    assert task["dataset_id"]
    assert task["dataset_label"] == base.name
    assert task["base"] == str(base)
    assert task["capability"] == "trajectory"
    assert task["source_revision"]["fingerprint"]
    assert task["phase"] == "completed"
    assert task["progress"] == 1.0
    assert task["progress_trusted"] is True


def test_task_cannot_complete_after_its_source_revision_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("REACNET_SCOPE_CACHE_DIR", raising=False)
    base = _trajectory_dataset(tmp_path)
    dataset = prepare.discover_dataset(str(tmp_path), base.name)

    def change_source(report) -> None:
        base.write_text("ITEM: TIMESTEP\n10\nchanged\n", encoding="utf-8")
        report({"phase": "scan-frames", "message": "Scanning"})

    with pytest.raises(prepare.PreparationSourceRevisionChangedError):
        prepare._run_preparation_task(
            dataset,
            capability="trajectory",
            source_file=str(base),
            action="build",
            operation=change_source,
            report=lambda _update: None,
        )

    task_path = prepare._preparation_task_path(dataset, "trajectory")
    task = json.loads(task_path.read_text(encoding="utf-8"))
    assert task["state"] == "superseded"
    assert "source revision changed" in task["message"]


def test_task_catalog_observes_workspace_fact_for_a_non_current_dataset(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("REACNET_SCOPE_CACHE_DIR", raising=False)
    monkeypatch.setattr(svc, "ALLOWED_ROOTS", [tmp_path])
    monkeypatch.setattr(dir_browser, "ALLOWED_ROOTS", [tmp_path])
    base = _trajectory_dataset(tmp_path)
    dataset = prepare.discover_dataset(str(tmp_path), base.name)
    task_path = prepare._preparation_task_path(dataset, "trajectory")
    task_path.parent.mkdir(parents=True, exist_ok=True)
    task_path.write_text(
        json.dumps(
            {
                "dataset_id": "dataset-old",
                "capability": "trajectory",
                "state": "completed",
                "phase": "completed",
                "source_revision": {"fingerprint": "revision-old"},
                "updated_at_epoch": 10,
            }
        ),
        encoding="utf-8",
    )

    tasks = svc.list_preparation_tasks(
        [{"folder": str(tmp_path), "base": str(base), "label": "old run"}]
    )

    assert len(tasks) == 1
    assert tasks[0]["dataset_id"] == "dataset-old"
    assert tasks[0]["dataset_label"] == "old run"
    assert tasks[0]["base"] == str(base)
