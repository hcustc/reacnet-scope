from __future__ import annotations

from pathlib import Path
import sqlite3

import pytest

from reacnet_scope import services as svc
from reacnet_scope.event_index import EVENT_EVIDENCE_STORE
from reacnet_scope.indexes import IndexInvalidError
from reacnet_scope.rng_events import canonical_reaction_key
from reacnet_scope.trajectory import save_timestep_ps

def write_rng_fixture(tmp_path: Path) -> tuple[Path, Path]:
    source = tmp_path / "run.lammpstrj.reactionevent.csv"
    molecules = tmp_path / "run.lammpstrj.molecules.csv"
    source.write_text(
        "Timestep_Index,Reactant,Product\n"
        "0,[H]+[O],[H][O]\n"
        "0,[O]+[H],[H][O]\n",
        encoding="utf-8",
    )
    molecules.write_text(
        "Timestep,Species,AtomIDs,BondIDs\n"
        "0,[H],0,\n"
        "0,[O],1,\n"
        "10,[H][O],0;1,0-1-1\n"
        "20,[H][O],0;1,0-1-1\n",
        encoding="utf-8",
    )
    return source, molecules


def test_timing_uses_all_events_and_pages_unresolved(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "cache"))
    source, molecules = write_rng_fixture(tmp_path)
    EVENT_EVIDENCE_STORE.build(str(source), str(molecules))
    artifacts = {"reactionevent": str(source), "molecules": str(molecules)}
    row = {"reactant_smiles": ["[H]", "[O]"],
           "product_smiles": ["[H][O]"]}

    status = svc.reaction_timing_summaries(artifacts, [row])
    assert status["status"] == "time_conversion_missing"
    assert (row["timing_event_count"], row["first_after_timestep"],
            row["last_after_timestep"]) == (2, 10, 10)
    assert row["first_time_ps"] is None

    save_timestep_ps(str(source), 0.1)
    svc.reaction_timing_summaries(artifacts, [row])
    assert row["first_time_ps"] == row["last_time_ps"] == 1.0
    distribution = svc.reaction_time_distribution(
        artifacts, row["reactant_smiles"], row["product_smiles"],
        start=1.0, end=1.2, width=0.1,
    )
    assert distribution["unit"] == "ps"
    assert distribution["forward_total"] == 2
    assert sum(item["forward"] for item in distribution["bins"]) == 2
    assert distribution["reverse_total"] == 0
    selected = distribution["bins"][0]
    key = distribution["reaction_key"]
    first = svc.reaction_time_events(
        artifacts, key, start_raw=selected["start_raw"],
        end_raw=selected["end_raw"], limit=1,
    )
    second = svc.reaction_time_events(
        artifacts, key, start_raw=selected["start_raw"],
        end_raw=selected["end_raw"], limit=1, offset=1,
    )
    assert first["total"] == second["total"] == 2
    assert first["rows"][0]["event_id"] != second["rows"][0]["event_id"]
    assert {first["rows"][0]["association_status"],
            second["rows"][0]["association_status"]} == {
                "matched", "unresolved_hmm_timeline"
            }
    assert first["rows"][0]["after_time_ps"] == 1.0
    csv_text = svc.rows_to_csv(first["rows"])
    assert "event_id" in csv_text and "timestep_ps" in csv_text
    assert "reaction_key" in csv_text and "after_time_ps" in csv_text
    connection = sqlite3.connect(
        EVENT_EVIDENCE_STORE.open_required(str(source), str(molecules))["index_path"]
    )
    try:
        plan = connection.execute(
            "EXPLAIN QUERY PLAN SELECT event_id FROM events "
            "WHERE reaction_key=? AND after_timestep>=? AND after_timestep<? "
            "ORDER BY after_timestep,event_id LIMIT 25",
            (key, 10, 11),
        ).fetchall()
    finally:
        connection.close()
    assert any("events_by_reaction_time" in str(row) for row in plan)


def test_timing_lookup_index_is_required(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "cache"))
    source, molecules = write_rng_fixture(tmp_path)
    built = EVENT_EVIDENCE_STORE.build(str(source), str(molecules))
    connection = sqlite3.connect(built["index_path"])
    try:
        connection.execute("DROP INDEX events_by_reaction_time")
        connection.commit()
    finally:
        connection.close()
    with pytest.raises(IndexInvalidError, match="timing lookup"):
        EVENT_EVIDENCE_STORE.open_required(str(source), str(molecules))


def test_timing_keeps_exact_stoichiometry_and_analyzed_frame_unit(
    tmp_path: Path, monkeypatch,
) -> None:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "cache"))
    source = tmp_path / "only.reactionevent.csv"
    source.write_text(
        "Timestep_Index,Reactant,Product\n"
        "0,[H]+[H],[H][H]\n"
        "1,[H][H],[H]+[H]\n"
        "2,[H]+[O],[H][O]\n"
        "3,[C][O],[O][C]\n"
        "4,[H],[H]\n",
        encoding="utf-8",
    )
    EVENT_EVIDENCE_STORE.build(str(source))
    artifacts = {"reactionevent": str(source)}
    save_timestep_ps(str(source), 0.2)
    result = svc.reaction_time_distribution(
        artifacts, ["[H]", "[H]"], ["[H][H]"],
        start=0, end=3, width=1,
    )
    assert result["unit"] == "analyzed_frame"
    assert result["forward_total"] == result["reverse_total"] == 1
    assert [item["forward"] for item in result["bins"]] == [0, 1, 0]
    assert [item["reverse"] for item in result["bins"]] == [0, 0, 1]
    key = canonical_reaction_key(["[H]", "[H]"], ["[H][H]"])
    page = svc.reaction_time_events(
        artifacts, key, start_raw=1, end_raw=2,
    )
    assert page["total"] == 1
    assert page["rows"][0]["after_time_ps"] is None
    assert page["rows"][0]["time_unit"] == "analyzed_frame"
    assert svc.reaction_time_distribution(
        artifacts, ["[O][C]"], ["[C][O]"],
    )["forward_total"] == 0
    zero_row = {"reactant_smiles": ["[O][C]"], "product_smiles": ["[C][O]"]}
    svc.reaction_timing_summaries(artifacts, [zero_row])
    assert zero_row["timing_event_count"] == 0
    assert zero_row["first_time"] is zero_row["last_time"] is None
    self_reverse = svc.reaction_time_distribution(artifacts, ["[H]"], ["[H]"])
    assert self_reverse["self_reverse"] is True
    assert self_reverse["forward_total"] == 1
    assert self_reverse["reverse_total"] == 0
    with pytest.raises(svc.ServiceError, match="500-bin"):
        svc.reaction_time_distribution(
            artifacts, ["[H]", "[H]"], ["[H][H]"],
            start=0, end=1000, width=1,
        )


def test_timing_reports_missing_and_stale_evidence(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "cache"))
    row = {"reactant_smiles": ["[H]"], "product_smiles": ["[O]"]}
    assert svc.reaction_timing_summaries({}, [row])["status"] == "missing_reactionevent"
    source, molecules = write_rng_fixture(tmp_path)
    EVENT_EVIDENCE_STORE.build(str(source), str(molecules))
    source.write_text(source.read_text(encoding="utf-8") + "1,[H],[O]\n", encoding="utf-8")
    status = svc.reaction_timing_summaries(
        {"reactionevent": str(source), "molecules": str(molecules)}, [row]
    )
    assert status["status"] == "event_index_stale"
    assert row["first_time"] is None
