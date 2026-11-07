"""Shared Lewis annotation queries for the CLI and Dash."""

from .indexes import IndexNotReadyError
from .lewis_store import (annotation_status, get_lewis_annotation,
                          search_lewis_annotations, export_lewis_annotations, annotation_revision)
from .service_types import ServiceError


lewis_status = annotation_status


def lewis_source_fingerprint(artifacts):
    return annotation_revision(artifacts)["fingerprint"]


def lewis_task_status(folder, base):
    from .workspace_services import validate_browse_path
    from .prepare import discover_dataset, _read_preparation_task
    folder_path, base_path = validate_browse_path(folder), validate_browse_path(base)
    dataset = discover_dataset(str(folder_path), str(base_path))
    return _read_preparation_task(dataset, "lewis")


def lewis_detail(artifacts, species):
    status = annotation_status(artifacts)
    if status["state"] != "ready":
        return {"status": "not_evaluated", "index_state": status["state"],
                "reason": status.get("message", ""), "original_species": species}
    try:
        return get_lewis_annotation(artifacts, species)
    except IndexNotReadyError as exc:
        return {"status": "not_evaluated", "index_state": "stale", "reason": str(exc),
                "original_species": species}


def lewis_search(artifacts, alias="", *, limit=50, offset=0):
    try:
        return search_lewis_annotations(artifacts, alias, limit=limit, offset=offset)
    except IndexNotReadyError as exc:
        raise ServiceError(str(exc), reason="lewis_index_not_ready") from exc


def lewis_export(artifacts, *, format="json", alias=""):
    try:
        return export_lewis_annotations(artifacts, format=format, alias=alias)
    except IndexNotReadyError as exc:
        raise ServiceError(str(exc), reason="lewis_index_not_ready") from exc


def render_lewis_svg(annotation, *, width=280, height=200):
    """Draw the stored model assignment, retaining carbene lone-pair choices."""
    from rdkit import Chem
    from rdkit.Chem import rdDepictor
    from rdkit.Chem.Draw import rdMolDraw2D
    from .lewis import lewis_model_molecule
    try:
        molecule = lewis_model_molecule(annotation)
        # Prepare rings/aromaticity but do not overwrite the model's u/p
        # assignment with RDKit's SMILES valence-deficit radical inference.
        Chem.SanitizeMol(molecule, sanitizeOps=(Chem.SanitizeFlags.SANITIZE_ALL
                         ^ Chem.SanitizeFlags.SANITIZE_FINDRADICALS))
        rdDepictor.Compute2DCoords(molecule)
        drawer = rdMolDraw2D.MolDraw2DSVG(width, height)
        options = drawer.drawOptions()
        options.addStereoAnnotation = False
        options.padding = 0.08
        # Carbon lone pairs are otherwise invisible in a skeletal diagram.
        # This is the selected model convention, not an asserted spin state.
        for atom in molecule.GetAtoms():
            pairs = atom.GetIntProp("model_lone_pairs")
            if atom.GetSymbol() == "C" and atom.GetFormalCharge() == 0 and atom.GetDegree() > 0 and pairs:
                atom.SetProp("atomLabel", "C")
                atom.SetProp("atomNote", f"LP={pairs}")
        drawer.DrawMolecule(molecule)
        drawer.FinishDrawing()
        return {"ok": True, "svg": drawer.GetDrawingText(), "message": ""}
    except (ValueError, RuntimeError, TypeError, KeyError) as exc:
        return {"ok": False, "svg": "", "message": str(exc)}
