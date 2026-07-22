from __future__ import annotations

from pathlib import Path

from reacnet_scope.indexes import ROUTE_INDEX_STORE
from scripts.webapp_dash import services as svc


def test_dataset_preparation_status_and_safe_clear(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "cache"))
    trajectory = tmp_path / "run.lammpstrj"
    route = Path(f"{trajectory}.route")
    reaction = Path(f"{trajectory}.reactionabcd")
    species = Path(f"{trajectory}.species")
    reactionevent = Path(f"{trajectory}.reactionevent.csv")
    molecules = Path(f"{trajectory}.molecules.csv")
    trajectory.write_text("ITEM: TIMESTEP\n0\n", encoding="utf-8")
    route.write_text("Atom 1 C: 0 C -> 10 O\n", encoding="utf-8")
    reaction.write_text("1 C->O\n", encoding="utf-8")
    species.write_text("Timestep 0: C 1\n", encoding="utf-8")
    reactionevent.write_text("Timestep_Index,Reactant,Product\n0,C,O\n", encoding="utf-8")
    molecules.write_text(
        "Timestep,Species,AtomIDs,BondIDs\n0,C,0,\n10,O,0,\n",
        encoding="utf-8",
    )
    ROUTE_INDEX_STORE.build(str(route))

    payload = svc.dataset_preparation_status(str(tmp_path))
    assert payload["dataset_id"]
    assert "/datasets/" in payload["cache_dir"]
    assert payload["events"]["state"] == "ready"
    assert payload["trajectory"]["state"] == "missing"
    assert payload["rng_event_command"] == "--reaction-event --show-molecule-time"

    cleared = svc.clear_dataset_index(str(tmp_path), kind="route")
    assert cleared["released_bytes"] > 0
    assert route.exists()
    assert ROUTE_INDEX_STORE.status(str(route))["state"] == "missing"
