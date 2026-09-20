from __future__ import annotations

import io
import json
from pathlib import Path
from zipfile import ZipFile

import pytest

from reacnet_scope.dft_geometry import DftGeometryRequest
from reacnet_scope.indexes import TRAJECTORY_INDEX_STORE
from reacnet_scope.reaction_readiness import (
    REACTION_READINESS_SCHEMA_VERSION,
    ReactionReadinessRequest,
    evaluate_reaction_readiness,
)


def _frame(timestep: int) -> str:
    return (
        "ITEM: TIMESTEP\n"
        f"{timestep}\n"
        "ITEM: NUMBER OF ATOMS\n2\n"
        "ITEM: BOX BOUNDS pp pp pp\n0 10\n0 10\n0 10\n"
        "ITEM: ATOMS id type x y z\n"
        "1 1 1.0 1.0 1.0\n"
        "2 2 3.0 1.0 1.0\n"
    )


def _case(tmp_path: Path, monkeypatch) -> tuple[dict[str, str], dict]:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "cache"))
    trajectory = tmp_path / "run.lammpstrj"
    trajectory.write_text(_frame(0) + _frame(10), encoding="utf-8")
    TRAJECTORY_INDEX_STORE.build(str(trajectory))
    return {"trajectory": str(trajectory)}, {
        "event_id": "rngevt-readiness",
        "reaction_key": "[C]+[O]->[C][O]",
        "association_status": "matched",
        "before_timestep": 0,
        "after_timestep": 10,
        "atom_id_list": [1, 2],
        "reactant_bonds": "",
        "product_bonds": "1-2-1",
        "reactant_participants": [
            {"species": "[C]", "atom_ids": [1]},
            {"species": "[O]", "atom_ids": [2]},
        ],
        "product_participants": [
            {"species": "[C][O]", "atom_ids": [1, 2]},
        ],
    }


def _request(**updates) -> ReactionReadinessRequest:
    geometry_values = {
        "source_length_unit": "angstrom",
        "atom_type_map": {"1": "C", "2": "O"},
        "electronic_states": {"reactants": (0, 1), "products": (0, 1)},
        **updates,
    }
    return ReactionReadinessRequest(
        geometry=DftGeometryRequest(**geometry_values),
        isolated_cluster_confirmed=True,
    )


def test_ready_is_a_deterministic_qc_handoff_claim_and_zip_is_auditable(
    tmp_path: Path,
    monkeypatch,
) -> None:
    artifacts, event = _case(tmp_path, monkeypatch)

    first = evaluate_reaction_readiness(
        artifacts,
        event,
        _request(),
        dataset_id="dataset-01",
        replicate="replicate-02",
    )
    second = evaluate_reaction_readiness(
        artifacts,
        event,
        _request(),
        dataset_id="dataset-01",
        replicate="replicate-02",
    )

    assert first.report == second.report
    assert first.report["schema_version"] == REACTION_READINESS_SCHEMA_VERSION
    assert first.report["qc_handoff"]["status"] == "ready"
    assert first.report["kinetics_applicability"]["status"] == "insufficient_evidence"
    assert "score" not in json.dumps(first.report)
    assert "rate can be calculated" in first.report["qc_handoff"]["claim_limit"]
    assert first.bundle is not None
    assert first.bundle.to_zip() == second.bundle.to_zip()
    with ZipFile(io.BytesIO(first.bundle.to_zip())) as archive:
        assert "reaction_readiness.json" in archive.namelist()
        assert "occurrence.json" in archive.namelist()
        report = json.loads(archive.read("reaction_readiness.json"))
        occurrence = json.loads(archive.read("occurrence.json"))
        assert report["subject"]["atom_ids"] == {
            "product": [1, 2],
            "reactant": [1, 2],
        }
        assert report["subject"]["replicate"] == "replicate-02"
        assert report["subject"]["source_revision_fingerprint"]
        assert occurrence["event_id"] == event["event_id"]


@pytest.mark.parametrize(
    ("event_update", "readiness_request", "expected"),
    [
        (
            {"association_status": "unresolved_hmm_timeline"},
            _request(),
            "blocked",
        ),
        (
            {"product_participants": [{"species": "[C][O]", "atom_ids": [1]}]},
            _request(),
            "blocked",
        ),
        (
            {},
            ReactionReadinessRequest(
                geometry=DftGeometryRequest(
                    source_length_unit="angstrom",
                    atom_type_map={"1": "C", "2": "O"},
                ),
                isolated_cluster_confirmed=True,
            ),
            "needs_input",
        ),
        (
            {},
            ReactionReadinessRequest(geometry=_request().geometry),
            "needs_input",
        ),
        (
            {},
            _request(
                electronic_states={"reactants": (0, 1), "products": (0, 17)}
            ),
            "blocked",
        ),
    ],
)
def test_readiness_statuses_fail_closed(
    tmp_path: Path,
    monkeypatch,
    event_update: dict,
    readiness_request: ReactionReadinessRequest,
    expected: str,
) -> None:
    artifacts, event = _case(tmp_path, monkeypatch)
    event.update(event_update)

    result = evaluate_reaction_readiness(
        artifacts,
        event,
        readiness_request,
        dataset_id="dataset-01",
        replicate="replicate-01",
    )

    assert result.report["qc_handoff"]["status"] == expected
    assert result.bundle is None


def test_geometry_warnings_require_review_but_keep_a_preview_bundle(
    tmp_path: Path,
    monkeypatch,
) -> None:
    artifacts, event = _case(tmp_path, monkeypatch)

    result = evaluate_reaction_readiness(
        artifacts,
        event,
        _request(warning_atom_count=1),
        dataset_id="dataset-01",
        replicate="replicate-01",
    )

    assert result.report["qc_handoff"]["status"] == "review_required"
    assert result.bundle is not None
    warning_check = next(
        item
        for item in result.report["qc_handoff"]["checks"]
        if item["id"] == "geometry_warnings"
    )
    assert warning_check["evidence"]["warnings"][0]["code"] == "large_system"


def test_omitting_only_unchanged_spectator_requires_review(
    tmp_path: Path,
    monkeypatch,
) -> None:
    artifacts, event = _case(tmp_path, monkeypatch)
    event["atom_id_list"] = [1, 2, 3]
    event["reactant_participants"].append(
        {"species": "[H]", "atom_ids": [3]}
    )
    event["product_participants"].append(
        {"species": "[H]", "atom_ids": [3]}
    )

    result = evaluate_reaction_readiness(
        artifacts,
        event,
        _request(reactant_indices=(0, 1), product_indices=(0,)),
        dataset_id="dataset-01",
        replicate="replicate-01",
    )

    assert result.report["qc_handoff"]["status"] == "review_required"
    check = next(
        item
        for item in result.report["qc_handoff"]["checks"]
        if item["id"] == "unchanged_participants_omitted"
    )
    assert check["evidence"]["omitted_atom_ids"] == [3]
    assert result.bundle is not None


def test_omitting_reaction_core_is_blocked_even_when_sides_balance(
    tmp_path: Path,
    monkeypatch,
) -> None:
    artifacts, event = _case(tmp_path, monkeypatch)
    event["atom_id_list"] = [1, 2, 3]
    event["reactant_participants"].append(
        {"species": "[H]", "atom_ids": [3]}
    )
    event["product_participants"].append(
        {"species": "[H]", "atom_ids": [3]}
    )

    result = evaluate_reaction_readiness(
        artifacts,
        event,
        _request(reactant_indices=(2,), product_indices=(1,)),
        dataset_id="dataset-01",
        replicate="replicate-01",
    )

    assert result.report["qc_handoff"]["status"] == "blocked"
    check = next(
        item
        for item in result.report["qc_handoff"]["checks"]
        if item["id"] == "reaction_core_complete"
    )
    assert check["status"] == "blocked"
    assert result.bundle is None


def test_possible_spin_crossing_requires_review_without_guessing_state(
    tmp_path: Path,
    monkeypatch,
) -> None:
    artifacts, event = _case(tmp_path, monkeypatch)

    result = evaluate_reaction_readiness(
        artifacts,
        event,
        _request(
            electronic_states={"reactants": (0, 1), "products": (0, 3)}
        ),
        dataset_id="dataset-01",
        replicate="replicate-01",
    )

    assert result.report["qc_handoff"]["status"] == "review_required"
    check = next(
        item
        for item in result.report["qc_handoff"]["checks"]
        if item["id"] == "spin_crossing_review"
    )
    assert check["evidence"]["multiplicities"] == {
        "product": 3,
        "reactant": 1,
    }
    assert result.bundle is not None


def test_changed_source_revision_blocks_handoff(
    tmp_path: Path,
    monkeypatch,
) -> None:
    artifacts, event = _case(tmp_path, monkeypatch)

    result = evaluate_reaction_readiness(
        artifacts,
        event,
        _request(),
        dataset_id="dataset-01",
        source_revision={"fingerprint": "stale", "artifacts": []},
        replicate="replicate-01",
    )

    assert result.report["qc_handoff"]["status"] == "blocked"
    assert result.bundle is None
    check = next(
        item
        for item in result.report["qc_handoff"]["checks"]
        if item["id"] == "source_revision_current"
    )
    assert check["status"] == "blocked"
