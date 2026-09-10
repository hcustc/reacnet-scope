"""Use validated Current Dataset contexts, including Workspace-linked evidence."""

from pathlib import Path

import pytest

from reacnet_scope import dataset_context, dir_browser, services as svc
from reacnet_scope.trajectory import save_linked_trajectory
from reacnet_scope.reaction_readiness import evaluate_reaction_readiness
from tests.test_reaction_readiness import _case, _request


def linked_dataset_context(tmp_path, monkeypatch):
    monkeypatch.setattr(svc, "ALLOWED_ROOTS", [tmp_path])
    monkeypatch.setattr(dir_browser, "ALLOWED_ROOTS", [tmp_path])
    external = tmp_path / "external"
    external.mkdir()
    artifacts, event = _case(external, monkeypatch)
    rng = tmp_path / "rng"
    rng.mkdir()
    base = rng / "run.lammpstrj"
    reaction = Path(f"{base}.reactionabcd")
    reaction.write_text("1 [C]+[O]->[C][O]\n", encoding="utf-8")
    Path(f"{base}.species").write_text(
        "Timestep 0: [C] 1 [O] 1\nTimestep 10: [C][O] 1\n", encoding="utf-8",
    )
    save_linked_trajectory(str(reaction), artifacts["trajectory"])
    validation = dataset_context.validate_dataset_candidate(str(rng), str(base))
    return dataset_context.current_dataset_from_validation(validation), event


def test_validated_dataset_with_linked_trajectory_is_not_falsely_stale(tmp_path, monkeypatch):
    context, event = linked_dataset_context(tmp_path, monkeypatch)
    assert context["source_revision"]["fingerprint"]
    assert Path(context["artifacts"]["trajectory"]).parent != Path(context["folder"])
    result = svc.evaluate_reaction_readiness(
        context["artifacts"], event, _request(), dataset_id=context["dataset_id"],
        source_revision=context["source_revision"], replicate=context["label"],
    )
    check = next(check for check in result.report["qc_handoff"]["checks"]
                 if check["id"] == "source_revision_current")
    assert check["status"] == "pass", check
    assert result.report["qc_handoff"]["status"] == "ready"
    assert result.bundle is not None
    assert result.report["subject"]["source_revision_fingerprint"] == context["source_revision"]["fingerprint"]
    assert set(result.report["data_version"]["artifact_paths"]) == {"reaction", "species"}
    assert set(result.report["evidence_version"]["artifact_paths"]) == {"reaction", "species", "trajectory"}
    assert result.report["evidence_version"]["artifact_paths"]["trajectory"] == context["artifacts"]["trajectory"]
    assert result.report["data_version"]["fingerprint"] != result.report["evidence_version"]["fingerprint"]


def _evaluate(context, event):
    return svc.evaluate_reaction_readiness(
        context["artifacts"], event, _request(), dataset_id=context["dataset_id"],
        source_revision=context["source_revision"], replicate=context["label"],
    )


@pytest.mark.parametrize("kind", ["reaction", "species", "trajectory"])
@pytest.mark.parametrize("change", ["modify", "delete"])
def test_dataset_and_external_evidence_changes_still_block_handoff(tmp_path, monkeypatch, kind, change):
    context, event = linked_dataset_context(tmp_path, monkeypatch)
    assert _evaluate(context, event).report["qc_handoff"]["status"] == "ready"
    source = Path(context["artifacts"][kind])
    if change == "delete":
        source.unlink()
    else:
        with source.open("a") as stream:
            stream.write("\n")
    result = _evaluate(context, event)
    assert result.report["qc_handoff"]["status"] == "blocked"
    assert result.bundle is None
    source_check = next(check for check in result.report["qc_handoff"]["checks"]
                        if check["id"] == "source_revision_current")
    # The linked trajectory is geometry evidence, not a directory source file.
    assert source_check["status"] == ("pass" if kind == "trajectory" else "blocked")


def test_captured_dataset_paths_disambiguate_overridden_same_kind_evidence(tmp_path, monkeypatch):
    context, event = linked_dataset_context(tmp_path, monkeypatch)
    local_trajectory = Path(context["base"])
    local_trajectory.write_text("different unselected trajectory\n", encoding="utf-8")
    validation = dataset_context.validate_dataset_candidate(context["folder"], context["base"])
    context = dataset_context.current_dataset_from_validation(validation)
    assert context["source_revision"]["artifact_paths"]["trajectory"] == str(local_trajectory)
    assert context["artifacts"]["trajectory"] != str(local_trajectory)
    result = _evaluate(context, event)
    assert result.report["qc_handoff"]["status"] == "ready"
    local_trajectory.write_text("changed Dataset source\n", encoding="utf-8")
    assert _evaluate(context, event).report["qc_handoff"]["status"] == "blocked"


def test_legacy_revision_kinds_remain_scoped_without_captured_paths(tmp_path, monkeypatch):
    context, event = linked_dataset_context(tmp_path, monkeypatch)
    context["source_revision"].pop("artifact_paths", None)
    result = _evaluate(context, event)
    assert result.report["qc_handoff"]["status"] == "ready"
    assert result.report["data_version"]["fingerprint"] == context["source_revision"]["fingerprint"]


@pytest.mark.parametrize("kind", ["reaction", "trajectory"])
def test_source_changes_during_preflight_fail_closed(tmp_path, monkeypatch, kind):
    context, event = linked_dataset_context(tmp_path, monkeypatch)

    def changing_builder(artifacts, event, request):
        bundle = svc.build_dft_geometry_bundle(artifacts, event, request)
        with Path(artifacts[kind]).open("a") as stream:
            stream.write("\n")
        return bundle

    result = evaluate_reaction_readiness(
        context["artifacts"], event, _request(), dataset_id=context["dataset_id"],
        source_revision=context["source_revision"], replicate=context["label"],
        geometry_builder=changing_builder,
    )
    assert result.report["qc_handoff"]["status"] == "blocked"
    assert result.bundle is None
    check = next(check for check in result.report["qc_handoff"]["checks"]
                 if check["id"] == "source_revision_unchanged_during_preflight")
    assert check["status"] == "blocked"


def qc_http_preview(client, context, event):
    """Exercise the actual Dash preflight with a validated revision in app-store."""
    from reacnet_scope.trajectory import save_type_element_map
    from tests.test_dash_smoke import _callback_payload

    save_type_element_map(context["artifacts"]["trajectory"], {"1": "C", "2": "O"})
    charge_pattern = '{"stem":["ALL"],"type":"event-dft-charge"}'
    multiplicity_pattern = '{"stem":["ALL"],"type":"event-dft-multiplicity"}'
    states = {
        "event-dft-reactants": [0, 1], "event-dft-products": [0],
        "event-dft-layout": "combined",
        "event-dft-unit-confirmation": ["angstrom"],
        "event-dft-isolated-cluster-confirmation": ["confirmed"],
        f"{charge_pattern}.value": [0, 0],
        f"{charge_pattern}.id": [{"type": "event-dft-charge", "stem": stem} for stem in ("reactants", "products")],
        f"{multiplicity_pattern}.value": [1, 1],
        f"{multiplicity_pattern}.id": [{"type": "event-dft-multiplicity", "stem": stem} for stem in ("reactants", "products")],
        "event-selected-store": {"row": event},
        "app-store": context,
    }
    response = client.post("/_dash-update-component", json=_callback_payload(
        client, input_ids=["event-dft-preview-btn"], changed="event-dft-preview-btn.n_clicks",
        input_values={"event-dft-preview-btn": 1}, state_values=states,
        output_id="event-dft-store",
    ))
    assert response.status_code == 200
    payload = response.get_json()["response"]["event-dft-store"]["data"]
    assert payload["readiness_report"]["qc_handoff"]["status"] == "ready"
    return payload, states


def test_dash_http_preview_accepts_validated_external_evidence_context(tmp_path, monkeypatch):
    from scripts.webapp_dash.app import create_app

    context, event = linked_dataset_context(tmp_path, monkeypatch)
    client = create_app().server.test_client()
    payload, _states = qc_http_preview(client, context, event)
    report = payload["readiness_report"]
    assert report["data_version"] == context["source_revision"]
    assert report["subject"]["dataset_id"] == context["dataset_id"]
    assert report["evidence_version"]["artifact_paths"]["trajectory"] == context["artifacts"]["trajectory"]
