import json

from reacnet_scope.geometry_audit import GEOMETRY_AUDIT_SCHEMA
from reacnet_scope.skeleton_lineage import SKELETON_LINEAGE_SCHEMA
from scripts.rng_query_cli import main


def test_render_report_combines_saved_results_and_escapes_text(tmp_path):
    lineage = tmp_path / "lineage.json"
    lineage.write_text(json.dumps({
        "schema_version": SKELETON_LINEAGE_SCHEMA, "mode": "skeleton", "truncated": True,
        "timeline": [{"start_timestep": 0, "end_timestep": 30, "representative_species": "<b>literal</b>"}],
    }))
    audit = tmp_path / "audit.json"
    audit.write_text(json.dumps({"geometry_audit": {
        "schema_version": GEOMETRY_AUDIT_SCHEMA, "event_id": "event-123", "pairs": [],
        "verdict": "INCONCLUSIVE_WINDOW", "reasons": ["Incomplete evidence"],
    }}))
    isomers = tmp_path / "isomers.json"
    assert main(["classify-isomers", "--smiles", "Oc1ccccc1O", "--out-json", str(isomers)]) == 0
    target = tmp_path / "review.html"
    args = ["render-report", str(lineage), str(audit), str(isomers), "--out", str(target)]

    assert main(args) == 0
    html = target.read_text()
    assert all(heading in html for heading in ("Skeleton lineage", "Event geometry audit", "Isomer classification"))
    assert "1,2-ortho-dioxy" in html
    assert "INCONCLUSIVE_WINDOW" in html
    assert "Result truncated" in html and "&lt;b&gt;literal&lt;/b&gt;" in html
    assert "<b>literal</b>" not in html
    assert main(args) == 2
    assert target.read_text() == html
