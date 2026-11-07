from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from rdkit import Chem

from reacnet_scope import services as svc
from reacnet_scope.lewis import LewisOptions, annotate_lewis
from reacnet_scope.lewis_store import (annotation_path, annotation_status, prepare_lewis_annotations,
                                      get_lewis_annotation, search_lewis_annotations, export_lewis_annotations)
from reacnet_scope.indexes import IndexNotReadyError, IndexStaleError
from reacnet_scope.prepare import discover_dataset, run_preparation, preparation_task_status


@pytest.fixture
def sources(tmp_path, monkeypatch):
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "workspace"))
    base = tmp_path / "demo"
    base.with_suffix(".reactionabcd").write_text("2 [C][O]->[C]=[O]\n1 [CH3]->[NH4+]\n")
    base.with_suffix(".species").write_text("Timestep 0: [C][O] 2 [C]=[O] 3 [CH3] 1\n")
    (tmp_path / "rng_run.json").write_text(json.dumps({"parameters": {"miso": 0}}))
    return discover_dataset(str(tmp_path))


def neutral(**kwargs):
    return LewisOptions(assume_neutral=True, **kwargs)


@pytest.mark.parametrize("smiles,expected", [
    ("[H][C]([H])[O][O]", "C=[O+][O-]"),
    ("[H][C]([H])[N]([H])[O]", "C=[NH+][O-]"),
    ("[N][N][O]", "N#[N+][O-]"),
])
def test_charge_location_prefers_electronegative_negative_site(smiles, expected):
    result = annotate_lewis(smiles, options=neutral())
    assert result["status"] == "generated"
    assert Chem.MolToSmiles(Chem.MolFromSmiles(result["candidate_smiles"])) == expected
    assert result["original_species"] == smiles


@pytest.mark.parametrize("smiles", [
    "[H][C]1([H])[C][C]1", "[C]1[C][O]1", "[C]1[C][C][C]1", "[C]1[C][C]1",
])
def test_small_ring_candidates_avoid_triples_and_cumulated_doubles(smiles):
    from reacnet_scope.lewis import lewis_model_molecule
    result = annotate_lewis(smiles, options=neutral())
    assert result["status"] == "generated"
    model = lewis_model_molecule(result)
    Chem.GetSymmSSSR(model)
    rings = [r for r in model.GetRingInfo().BondRings() if len(r) in (3, 4)]
    assert rings
    for ring in rings:
        bonds = [model.GetBondWithIdx(i) for i in ring]
        assert all(b.GetBondTypeAsDouble() <= 2 for b in bonds)
        for atom in model.GetAtoms():
            assert sum(b.GetBondTypeAsDouble() == 2 for b in bonds
                       if atom.GetIdx() in (b.GetBeginAtomIdx(), b.GetEndAtomIdx())) <= 1
    assert result["original_species"] == smiles


@pytest.mark.parametrize("smiles", ["C1CC1", "C1=CC1", "C1=CC=C1", "C=C1CC1", "C=C=C", "C#C"])
def test_ring_preference_preserves_isolated_double_bonds_and_acyclic_unsaturation(smiles):
    result = annotate_lewis(smiles, options=neutral())
    assert result["status"] == "generated"
    assert Chem.MolToSmiles(Chem.MolFromSmiles(result["candidate_smiles"])) == smiles
    assert result["model_objectives"]["small_ring_unsaturation"] == 0


@pytest.mark.parametrize("smiles,expected", [
    ("[C][C]", "[C]=[C]"), ("[C][C][C]", "[C]=C=[C]"),
    ("[C][C][O]", "[C]=C=O"), ("[C][C][C][O]", "[C]=C=C=O"),
])
def test_carbon_fragment_naming_does_not_trade_radicals_for_charge_separation(smiles, expected):
    result = annotate_lewis(smiles, options=neutral())
    assert result["status"] == "generated"
    candidate = Chem.MolFromSmiles(result["candidate_smiles"])
    assert Chem.MolToSmiles(candidate) == Chem.MolToSmiles(Chem.MolFromSmiles(expected))
    assert all(a.GetFormalCharge() == 0 for a in candidate.GetAtoms())
    assert result["unique_electronic_state"] == "not_established"


@pytest.mark.parametrize("smiles,expected", [
    ("[C][F]", "[C]F"), ("[C][Cl]", "[C]Cl"),
    ("[H][C][Cl]", "[CH]Cl"), ("[F][C][F]", "F[C]F"),
    ("[C][Br]", "[C]Br"), ("[C][I]", "[C]I"),
])
def test_halocarbon_candidates_do_not_charge_separate_to_quench_radicals(smiles, expected):
    result = annotate_lewis(smiles, options=neutral())
    assert result["status"] == "generated"
    candidate = Chem.MolFromSmiles(result["candidate_smiles"])
    assert Chem.MolToSmiles(candidate) == Chem.MolToSmiles(Chem.MolFromSmiles(expected))
    assert all(atom.GetFormalCharge() == 0 for atom in candidate.GetAtoms())
    assert all(bond.GetBondType() == Chem.BondType.SINGLE for bond in candidate.GetBonds())
    assert result["unique_electronic_state"] == "not_established"


def test_halogen_restriction_preserves_co_charge_pair_and_exposes_model_rules():
    result = annotate_lewis("[C][O]", options=neutral())
    assert result["candidate_smiles"] == "[C-]#[O+]"
    assert result["model_objectives"]["formal_charge_count"] == 2
    assert result["model_objectives"]["charged_shell_deficit"] == 0
    assert result["method"]["charge_policy"]["positive_halogen"] == "forbidden"
    assert result["method"]["radical_electrons_basis"] == "RDKit_representation_not_electronic_state"
    assert result["method"]["method"] == "neutral-main-group-milp/v4"


def test_nitro_candidate_retains_one_adjacent_opposite_charge_pair():
    result = annotate_lewis("C[N+](=O)[O-]", options=neutral())
    mol = Chem.MolFromSmiles(result["candidate_smiles"])
    charged = [a for a in mol.GetAtoms() if a.GetFormalCharge()]
    assert sorted((a.GetSymbol(), a.GetFormalCharge()) for a in charged) == [("N", 1), ("O", -1)]
    assert mol.GetBondBetweenAtoms(charged[0].GetIdx(), charged[1].GetIdx()) is not None


@pytest.mark.parametrize("limit", ["time", "nodes"])
def test_lexicographic_passes_share_budget_and_do_not_publish_partial_optima(monkeypatch, limit):
    from reacnet_scope import lewis
    solve = lewis.milp
    calls = []
    def budgeted(*args, **kwargs):
        calls.append(kwargs["options"])
        if len(calls) == 1:
            result = solve(*args, **kwargs)
            if limit == "nodes":
                result.mip_node_count = 10
            return result
        return SimpleNamespace(status=1, message="Time limit reached", x=None)
    monkeypatch.setattr(lewis, "milp", budgeted)
    result = annotate_lewis("[C][O]", options=neutral(max_nodes=10))
    assert result["status"] == ("timeout" if limit == "time" else "truncated")
    assert result["candidate_smiles"] is None
    assert result["optimization_stages_completed"] == 1
    assert result["optimization_complete"] is False
    assert result["model_objectives"] == {"charged_shell_deficit": 0}
    assert len(calls) == (2 if limit == "time" else 1)
    if limit == "time":
        assert calls[1]["time_limit"] < calls[0]["time_limit"]


@pytest.mark.parametrize("miso", [0, 1, 2, None])
def test_candidate_keeps_identity_and_marks_representative_scope(miso):
    result = annotate_lewis("[C][O]", options=neutral(), miso=miso)
    assert result["status"] == "generated"
    assert result["candidate_smiles"] == "[C-]#[O+]"
    assert result["original_species"] == "[C][O]"
    assert result["optimization_complete"] is True
    assert result["candidate_search_complete"] is False
    assert result["chemical_validity"] == "not_established"
    assert result["coverage"] == ({2: "selected_representative_only", None: "representative_scope_unknown"}
                                    .get(miso, "representative_connectivity"))


def test_connectivity_cache_reuses_different_bond_orders_and_atom_orders(monkeypatch):
    from reacnet_scope import lewis
    original = lewis._solve
    calls = []
    def solve(*args):
        calls.append(1)
        return original(*args)
    monkeypatch.setattr(lewis, "_solve", solve)
    cache = {}
    results = [annotate_lewis(s, options=neutral(), miso=1, _cache=cache)
               for s in ("[C][O]", "[C]=[O]", "[O][C]")]
    assert len(calls) == 1
    assert len({r["input_graph_id"] for r in results}) == 1
    assert {r["candidate_smiles"] for r in results} == {"[C-]#[O+]"}
    assert len({r["original_species"] for r in results}) == 3


@pytest.mark.parametrize("smiles,radicals,atoms", [("[CH3]", 1, 4), ("[OH]", 1, 2), ("CO", 0, 6)])
def test_preserves_complete_hydrogens_and_allows_radicals(smiles, radicals, atoms):
    result = annotate_lewis(smiles, options=neutral())
    params = Chem.SmilesParserParams()
    params.removeHs = False
    molecule = Chem.MolFromSmiles(result["candidate_smiles"], params)
    assert molecule.GetNumAtoms() == atoms
    assert result["radical_electrons"] == radicals
    assert result["formal_charge"] == 0


@pytest.mark.parametrize("smiles,options,status,reason", [
    ("[C][O]", LewisOptions(), "needs_input", "neutral_assumption_required"),
    ("[NH4+]", neutral(), "unsupported", "nonzero_fragment_charge"),
    ("[P-](F)(F)(F)(F)(F)F", neutral(), "unsupported", "nonzero_fragment_charge"),
    ("[NH4]", neutral(net_charge=1), "unsupported", "nonzero_fragment_charge"),
    ("[CH3]", neutral(multiplicity=2), "unsupported", "spin_constraint"),
    ("[Na][Cl]", neutral(), "unsupported", "unsupported_elements:Na"),
    ("O=[Se]=O", neutral(), "unsupported", "unsupported_elements:Se"),
    ("C(C)(C)(C)(C)C", neutral(), "input_incomplete", "hydrogen_count_unresolved"),
    ("C1CC", neutral(), "input_incomplete", "smiles_parse_failed"),
    ("_unknownSMILES1", neutral(), "input_incomplete", "missing_or_unknown_structure"),
    ("CC", neutral(max_atoms=1), "unsupported", "atom_budget"),
])
def test_rejections_keep_original_name(smiles, options, status, reason):
    result = annotate_lewis(smiles, options=options)
    assert (result["status"], result["reason"]) == (status, reason)
    assert result["original_species"] == smiles
    assert result["candidate_smiles"] is None


@pytest.mark.parametrize("message,status", [("Time limit reached", "timeout"), ("Iteration limit reached", "truncated")])
def test_solver_timeout_never_publishes_incumbent_as_a_complete_candidate(monkeypatch, message, status):
    from reacnet_scope import lewis
    monkeypatch.setattr(lewis, "milp", lambda *a, **k: SimpleNamespace(status=1, message=message, x=[1]))
    result = annotate_lewis("[C][O]", options=neutral())
    assert result["status"] == status
    assert result["truncated"] and not result["optimization_complete"]
    assert result["candidate_smiles"] is None


def test_rejected_candidate_does_not_claim_model_infeasibility(monkeypatch):
    monkeypatch.setattr(Chem, "SanitizeMol", lambda *a, **k: Chem.SanitizeFlags.SANITIZE_PROPERTIES)
    result = annotate_lewis("[C][O]", options=neutral())
    assert result["status"] == "candidate_rejected"
    assert result["optimization_complete"] and not result["candidate_search_complete"]
    assert result["candidate_smiles"] is None


def test_alias_lookup_preserves_distinct_rng_species_and_counts(sources, monkeypatch):
    from reacnet_scope.composition import SPECIES_COMPOSITION_STORE
    from reacnet_scope import lewis_store
    SPECIES_COMPOSITION_STORE.build(sources["species"])
    before = SPECIES_COMPOSITION_STORE.species_totals(sources["species"])
    raw = {role: Path(sources[role]).read_bytes() for role in ("species", "reaction")}
    prepare_lewis_annotations(sources, options=neutral())
    monkeypatch.setattr(lewis_store, "_representatives", lambda *a: pytest.fail("online raw scan"))
    monkeypatch.setattr(lewis_store, "annotate_lewis", lambda *a, **k: pytest.fail("online solve"))
    first = search_lewis_annotations(sources, "[C-]#[O+]", limit=1)
    second = search_lewis_annotations(sources, "[O+]#[C-]", limit=1, offset=1)
    assert first["has_more"] and not second["has_more"]
    assert {first["rows"][0]["original_species"], second["rows"][0]["original_species"]} == {"[C][O]", "[C]=[O]"}
    assert get_lewis_annotation(sources, "[NH4+]")["status"] == "unsupported"
    assert get_lewis_annotation(sources, "missing")["status"] == "not_evaluated"
    assert SPECIES_COMPOSITION_STORE.species_totals(sources["species"]) == before
    assert before["[C][O]"] == 2 and before["[C]=[O]"] == 3
    assert all(Path(sources[role]).read_bytes() == data for role, data in raw.items())
    exported = json.loads(export_lewis_annotations(sources, alias="[C-]#[O+]"))
    assert len(exported["rows"]) == 2
    assert all(r["method"]["method"] and r["source_revision"] for r in exported["rows"])
    csv_text = export_lewis_annotations(sources, format="csv")
    assert "annotation_json" in csv_text and "[NH4+]" in csv_text


def test_revision_and_assumption_changes_invalidate_results(sources):
    assert annotation_status(sources)["state"] == "missing"
    with pytest.raises(IndexNotReadyError):
        search_lewis_annotations(sources)
    prepare_lewis_annotations(sources, options=neutral())
    assert annotation_status(sources, options=neutral(net_charge=0))["state"] == "stale"
    metadata = Path(sources["base"]).parent / "rng_run.json"
    metadata.write_text(json.dumps({"parameters": {"miso": 2}}))
    assert annotation_status(sources)["state"] == "stale"
    with pytest.raises(IndexStaleError):
        get_lewis_annotation(sources, "[C][O]")
    prepare_lewis_annotations(sources, options=neutral())
    assert get_lewis_annotation(sources, "[C][O]")["coverage"] == "selected_representative_only"


@pytest.mark.parametrize("old_method", ["v1", "v2", "v3"])
def test_old_publication_is_rejected_and_rebuilt_with_v4_rules(sources, old_method):
    import sqlite3
    prepare_lewis_annotations(sources, options=neutral())
    with sqlite3.connect(annotation_path(sources)) as db:
        method = json.loads(db.execute("SELECT value FROM meta WHERE key='method'").fetchone()[0])
        method["method"] = "neutral-main-group-milp/" + old_method
        db.execute("UPDATE meta SET value=? WHERE key='method'", (json.dumps(method),))
    assert annotation_status(sources)["state"] == "stale"
    with pytest.raises(IndexStaleError):
        get_lewis_annotation(sources, "[C][O]")
    prepare_lewis_annotations(sources, options=neutral())
    assert get_lewis_annotation(sources, "[C][O]")["method"]["method"] == "neutral-main-group-milp/v4"


def test_carbene_model_graph_retains_pairing_lost_by_smiles_roundtrip():
    from reacnet_scope.lewis import lewis_model_molecule
    result = annotate_lewis("[F][C][F]", options=neutral())
    assert result["model_unpaired_electrons"] == 0
    assert result["radical_electrons"] == 2  # RDKit SMILES representation, not the model assignment.
    model = lewis_model_molecule(result)
    carbon = next(a for a in model.GetAtoms() if a.GetSymbol() == "C")
    assert carbon.GetNumRadicalElectrons() == 0
    assert carbon.GetIntProp("model_lone_pairs") == 1
    assert result["model_graph"]["index_basis"] == "local_zero_based_not_RNG_atom_ids"
    assert result["schema_version"] == "reacnet-scope/lewis-annotation/v2"
    assert result["unique_electronic_state"] == "not_established"
    rendered = svc.render_lewis_svg(result)
    assert rendered["ok"] and "<svg" in rendered["svg"]


def test_legacy_result_schema_requires_repreparation(sources):
    import sqlite3
    from reacnet_scope.indexes import IndexInvalidError
    prepare_lewis_annotations(sources, options=neutral())
    with sqlite3.connect(annotation_path(sources)) as db:
        db.execute("UPDATE meta SET value=? WHERE key='schema'", (json.dumps("reacnet-scope/lewis-annotation/v1"),))
    with pytest.raises(IndexInvalidError):
        get_lewis_annotation(sources, "[C][O]")
    prepare_lewis_annotations(sources, options=neutral())
    assert get_lewis_annotation(sources, "[C][O]")["schema_version"] == "reacnet-scope/lewis-annotation/v2"


def test_queued_request_rejects_changed_source_before_reusing_publication(sources):
    from reacnet_scope.lewis_store import annotation_revision
    prepare_lewis_annotations(sources, options=neutral())
    submitted = annotation_revision(sources)["fingerprint"]
    Path(sources["species"]).write_text("Timestep 0: [C][O] 99\n")
    with pytest.raises(IndexStaleError, match="提交后"):
        prepare_lewis_annotations(sources, options=neutral(), expected_revision=submitted)


def test_corrupt_prepared_record_is_not_an_empty_or_unchecked_result(sources):
    import sqlite3
    from reacnet_scope.indexes import IndexInvalidError
    prepare_lewis_annotations(sources, options=neutral())
    with sqlite3.connect(annotation_path(sources)) as db:
        db.execute("UPDATE annotations SET payload='{}' WHERE original_species='[C][O]'")
    with pytest.raises(IndexInvalidError):
        search_lewis_annotations(sources, "[C-]#[O+]")


def test_explicit_collection_uses_same_revision_for_build_and_online_queries(sources, tmp_path, monkeypatch):
    from reacnet_scope import dir_browser
    monkeypatch.setattr(dir_browser, "ALLOWED_ROOTS", [tmp_path])
    candidate = svc.collection_candidate({role: sources[role] for role in ("reaction", "species")}, "Lewis 示例")
    validation = svc.validate_file_collection_candidate(candidate)
    svc.commit_file_collection(validation)
    current = svc.current_dataset_from_validation(validation)
    result = svc.prepare_dataset_workspace(candidate["folder"], base=candidate["base"], kind="lewis",
                                           lewis_options=neutral(), expected_dataset_id=current["dataset_id"])
    assert result["status"]["state"] == "ready"
    assert len(svc.lewis_search(current["artifacts"], "[C-]#[O+]")["rows"]) == 2


def test_miso_two_isomers_remain_separate_connectivity_annotations(sources):
    Path(sources["species"]).write_text("Timestep 0: CCO 1 COC 1\n")
    (Path(sources["base"]).parent / "rng_run.json").write_text('{"parameters":{"miso":2}}')
    prepare_lewis_annotations(sources, options=neutral())
    first, second = (get_lewis_annotation(sources, s) for s in ("CCO", "COC"))
    assert first["input_graph_id"] != second["input_graph_id"]
    assert first["coverage"] == second["coverage"] == "selected_representative_only"


@pytest.mark.parametrize("failure", ["cancel", "budget", "source"])
def test_failed_rebuild_never_replaces_published_annotations(sources, failure):
    prepare_lewis_annotations(sources, options=neutral())
    target = annotation_path(sources)
    before = hashlib.sha256(target.read_bytes()).hexdigest()
    def progress(update):
        if failure == "cancel":
            raise KeyboardInterrupt
        if failure == "source":
            Path(sources["species"]).write_text("Timestep 0: C 1\n")
    expected = KeyboardInterrupt if failure == "cancel" else IndexStaleError if failure == "source" else ValueError
    with pytest.raises(expected):
        prepare_lewis_annotations(sources, options=neutral(), rebuild=True,
                                  max_species=1 if failure == "budget" else 100,
                                  progress_callback=progress)
    assert hashlib.sha256(target.read_bytes()).hexdigest() == before
    assert not list(target.parent.glob(".lewis.*.building.sqlite3"))


def test_whole_task_timeout_does_not_publish(sources):
    with pytest.raises(TimeoutError, match="整体任务"):
        prepare_lewis_annotations(sources, options=neutral(), max_task_seconds=1e-12)
    assert annotation_status(sources)["state"] == "missing"


def test_partial_rebuild_is_never_visible_to_readers(sources):
    prepare_lewis_annotations(sources, options=neutral())
    phases = []
    def progress(update):
        phases.append(update["phase"])
        assert get_lewis_annotation(sources, "[C][O]")["status"] == "generated"
    prepare_lewis_annotations(sources, options=neutral(multiplicity=2), progress_callback=progress)
    assert "annotating" in phases and "publishing" in phases
    assert get_lewis_annotation(sources, "[C][O]")["reason"] == "spin_constraint"


def test_republication_between_metadata_and_row_read_requires_retry(sources, monkeypatch):
    from reacnet_scope import lewis_store
    prepare_lewis_annotations(sources, options=neutral())
    required = lewis_store._required
    def replace_publication(*args, **kwargs):
        metadata = required(*args, **kwargs)
        prepare_lewis_annotations(sources, options=neutral(multiplicity=2), rebuild=True)
        return metadata
    monkeypatch.setattr(lewis_store, "_required", replace_publication)
    with pytest.raises(IndexStaleError, match="重新发布"):
        search_lewis_annotations(sources)


def test_preparation_cli_query_and_export(sources, tmp_path, capsys):
    from scripts.rng_query_cli import main
    assert main(["prepare", "build", "lewis", str(tmp_path)]) == 2
    assert "中性" in capsys.readouterr().err
    assert annotation_status(sources)["state"] == "missing"
    assert main(["prepare", "build", "lewis", str(tmp_path), "--assume-neutral"]) == 0
    task = preparation_task_status(str(tmp_path), capability="lewis")
    assert task["state"] == "completed"
    capsys.readouterr()
    assert main(["lewis", str(tmp_path), "--alias", "[C-]#[O+]", "--limit", "1"]) == 0
    assert json.loads(capsys.readouterr().out)["has_more"]
    assert main(["lewis", str(tmp_path), "--species", "[NH4+]"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "unsupported"
    destination = tmp_path / "annotations.json"
    assert main(["lewis", str(tmp_path), "--out", str(destination)]) == 0
    assert len(json.loads(destination.read_text())["rows"]) == 4


def test_dash_service_builds_and_detail_uses_same_contract(sources, tmp_path, monkeypatch):
    from reacnet_scope import dir_browser
    monkeypatch.setattr(dir_browser, "ALLOWED_ROOTS", [tmp_path])
    built = svc.prepare_dataset_workspace(str(tmp_path), base=sources["base"], kind="lewis", lewis_options=neutral())
    assert built["status"]["state"] == "ready"
    detail = svc.species_detail(sources, "[C][O]")
    assert detail["smiles"] == "[C][O]"
    assert detail["lewis_annotation"]["candidate_smiles"] == "[C-]#[O+]"
    assert len(svc.lewis_search(sources, "[C-]#[O+]")["rows"]) == 2


def test_late_dash_results_are_not_shown_for_another_dataset():
    from scripts.webapp_dash.app import create_app
    from scripts.webapp_dash.candidate_workbench import context_key
    app = create_app()
    callback = next(item["callback"].__wrapped__ for item in app.callback_map.values()
                    if item.get("callback") and item["callback"].__name__ == "_render_lewis_query")
    first = {"dataset_id": "first", "source_revision": {"fingerprint": "a"}}
    second = {"dataset_id": "second", "source_revision": {"fingerprint": "b"}}
    result = {"context": context_key(first), "request_id": 1, "rows": []}
    assert callback(result, second, {"request_id": 1}) == ("", True, True)
    assert callback(result, first, {"request_id": 2}) == ("", True, True)
