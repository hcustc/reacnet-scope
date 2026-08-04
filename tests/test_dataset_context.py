from __future__ import annotations

from pathlib import Path

import pytest

from reacnet_scope import dataset_context
from reacnet_scope import dir_browser
from reacnet_scope import services as svc


def _candidate(root: Path, name: str = "run") -> dict[str, str]:
    (root / f"{name}.reactionabcd").write_text(
        "[H] -> [H] 1\n",
        encoding="utf-8",
    )
    (root / f"{name}.species").write_text(
        "Timestep 0: [H] 1\n",
        encoding="utf-8",
    )
    return {
        "folder": str(root),
        "base": str(root / name),
        "label": name,
    }


def test_validation_captures_one_stable_source_revision_without_preparing(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(svc, "ALLOWED_ROOTS", [tmp_path])
    monkeypatch.setattr(dir_browser, "ALLOWED_ROOTS", [tmp_path])
    candidate = _candidate(tmp_path)

    validated = dataset_context.validate_dataset_candidate(
        candidate["folder"],
        candidate["base"],
    )

    assert validated["dataset_id"]
    assert validated["source_revision"]["fingerprint"]
    assert [
        item["kind"] for item in validated["source_revision"]["artifacts"]
    ] == ["reaction", "species"]
    assert validated["base"] == candidate["base"]
    assert not (tmp_path / ".reacnet-scope").exists()


def test_switch_result_only_succeeds_for_the_active_request_before_deadline() -> None:
    candidate = {"folder": "/data", "base": "/data/run", "label": "run"}
    transaction = dataset_context.begin_dataset_switch(
        candidate,
        origin={"page": "species", "trigger": "open-data-modal"},
        request_id="request-1",
        started_ns=1_000,
        timeout_seconds=5,
    )
    validated = {
        **candidate,
        "dataset_id": "dataset-1",
        "source_revision": {"fingerprint": "revision-1", "artifacts": []},
        "artifacts": {},
        "capabilities": {},
        "readiness": {},
        "ready_count": 0,
    }

    ignored = dataset_context.resolve_dataset_switch(
        transaction,
        {"request_id": "request-old", "ok": True, "validation": validated},
        completed_ns=2_000,
    )
    succeeded = dataset_context.resolve_dataset_switch(
        transaction,
        {"request_id": "request-1", "ok": True, "validation": validated},
        completed_ns=2_000,
    )
    timed_out = dataset_context.resolve_dataset_switch(
        transaction,
        {"request_id": "request-1", "ok": True, "validation": validated},
        completed_ns=6_000_001_001,
    )

    assert ignored == transaction
    assert succeeded["state"] == "succeeded"
    assert succeeded["validation"] == validated
    assert timed_out["state"] == "failed"
    assert timed_out["reason"] == "validation_timeout"


def test_cancelled_or_replaced_request_cannot_commit_a_late_result() -> None:
    first = dataset_context.begin_dataset_switch(
        {"folder": "/data", "base": "/data/first", "label": "first"},
        request_id="request-1",
        started_ns=1,
    )
    cancelled = dataset_context.supersede_dataset_switch(first, reason="cancelled")
    late = dataset_context.resolve_dataset_switch(
        cancelled,
        {
            "request_id": "request-1",
            "ok": True,
            "validation": {"dataset_id": "first"},
        },
        completed_ns=2,
    )
    replacement = dataset_context.begin_dataset_switch(
        {"folder": "/data", "base": "/data/second", "label": "second"},
        request_id="request-2",
        started_ns=3,
    )
    replaced_late = dataset_context.resolve_dataset_switch(
        replacement,
        {
            "request_id": "request-1",
            "ok": True,
            "validation": {"dataset_id": "first"},
        },
        completed_ns=4,
    )

    assert cancelled["state"] == "superseded"
    assert late == cancelled
    assert replaced_late == replacement


def test_failed_validation_keeps_candidate_and_explains_preserved_context() -> None:
    transaction = dataset_context.begin_dataset_switch(
        {"folder": "/data", "base": "/data/run", "label": "run"},
        request_id="request-1",
        started_ns=1,
    )

    failed = dataset_context.resolve_dataset_switch(
        transaction,
        {
            "request_id": "request-1",
            "ok": False,
            "reason": "candidate_missing",
            "message": "候选已不存在",
        },
        completed_ns=2,
    )

    assert failed["state"] == "failed"
    assert failed["candidate"] == transaction["candidate"]
    assert failed["message"] == "候选已不存在"


def test_same_dataset_identity_and_revision_is_an_explicit_noop() -> None:
    current = {
        "dataset_id": "dataset-1",
        "source_revision": {"fingerprint": "revision-1"},
    }

    assert dataset_context.is_same_dataset_revision(
        current,
        {
            "dataset_id": "dataset-1",
            "source_revision": {"fingerprint": "revision-1"},
        },
    )
    assert not dataset_context.is_same_dataset_revision(
        current,
        {
            "dataset_id": "dataset-1",
            "source_revision": {"fingerprint": "revision-2"},
        },
    )


def test_session_revalidation_distinguishes_active_revision_changed_and_unavailable(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(svc, "ALLOWED_ROOTS", [tmp_path])
    monkeypatch.setattr(dir_browser, "ALLOWED_ROOTS", [tmp_path])
    candidate = _candidate(tmp_path)
    validated = dataset_context.validate_dataset_candidate(
        candidate["folder"],
        candidate["base"],
    )
    current = dataset_context.current_dataset_from_validation(validated)

    active = dataset_context.revalidate_current_dataset(current)
    (tmp_path / "run.species").write_text(
        "Timestep 0: [H] 2\nTimestep 1: [H] 3\n",
        encoding="utf-8",
    )
    changed = dataset_context.revalidate_current_dataset(current)
    (tmp_path / "run.reactionabcd").unlink()
    (tmp_path / "run.species").unlink()
    unavailable = dataset_context.revalidate_current_dataset(current)

    assert active["state"] == "active"
    assert changed["state"] == "revision-changed"
    assert changed["context"]["dataset_id"] == current["dataset_id"]
    assert "species" not in changed["context"]["artifacts"]
    assert changed["context"]["artifacts"]["reaction"].endswith(
        "run.reactionabcd"
    )
    assert changed["context"]["invalidated_artifacts"] == ["species"]
    assert changed["context"]["capabilities"]["reaction"] is True
    assert changed["context"]["capabilities"]["events"] == current[
        "capabilities"
    ]["events"]
    assert changed["context"]["capabilities"]["intermediate"] is False
    assert changed["context"]["capabilities"]["evolution"] is False
    assert changed["context"]["readiness"]["basic_analysis"] == {
        "ready": False,
        "state": "stale",
    }
    assert changed["context"]["readiness"]["event_search"] == current[
        "readiness"
    ]["event_search"]
    assert changed["context"]["inputs_pending"] is True
    assert unavailable["state"] == "unavailable"
    assert unavailable["context"] is None


def test_commit_replaces_bound_selection_but_marks_preserved_inputs_pending() -> None:
    validation = {
        "folder": "/data",
        "base": "/data/new",
        "label": "new",
        "dataset_id": "dataset-new",
        "source_revision": {"fingerprint": "revision-new", "artifacts": []},
        "artifacts": {"reaction": "/data/new.reactionabcd"},
        "capabilities": {"reaction": True},
        "readiness": {},
        "ready_count": 1,
    }

    current = dataset_context.current_dataset_from_validation(validation)

    assert current["selected_smiles"] == ""
    assert current["selected_formula"] == ""
    assert current["inputs_pending"] is True
    assert current["context_state"] == "active"


def test_candidate_that_disappears_during_validation_cannot_commit(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(svc, "ALLOWED_ROOTS", [tmp_path])
    monkeypatch.setattr(dir_browser, "ALLOWED_ROOTS", [tmp_path])
    candidate = _candidate(tmp_path)

    def disappearing_scan(_folder: str, *, base: str = "") -> dict[str, object]:
        (tmp_path / "run.reactionabcd").unlink()
        (tmp_path / "run.species").unlink()
        return {"dataset": {"selected_base": base}}

    monkeypatch.setattr(dataset_context, "scan_dataset", disappearing_scan)

    with pytest.raises(svc.ServiceError) as exc_info:
        dataset_context.validate_dataset_candidate(
            candidate["folder"],
            candidate["base"],
        )

    assert exc_info.value.reason == "candidate_missing"


def test_out_of_root_candidate_is_rejected_before_dataset_scan(
    tmp_path,
    monkeypatch,
) -> None:
    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside"
    allowed.mkdir()
    outside.mkdir()
    candidate = _candidate(outside)
    scans: list[str] = []
    monkeypatch.setattr(svc, "ALLOWED_ROOTS", [allowed])
    monkeypatch.setattr(dir_browser, "ALLOWED_ROOTS", [allowed])
    monkeypatch.setattr(
        dataset_context,
        "scan_dataset",
        lambda folder, **_kwargs: scans.append(folder),
    )

    with pytest.raises(svc.ServiceError):
        dataset_context.validate_dataset_candidate(
            candidate["folder"],
            candidate["base"],
        )

    assert scans == []
