"""DFT initial-geometry preparation, review and download callbacks."""

from __future__ import annotations

import json
import hashlib
from typing import Any
import dash_bootstrap_components as dbc
from dash import (
    ALL,
    Input,
    Output,
    State,
    dcc,
    html,
    no_update,
)
from dash.exceptions import PreventUpdate
from reacnet_scope import services as svc


def _dft_participant_options(row: dict[str, Any], side: str) -> list[dict[str, Any]]:
    label = "反应物" if side == "reactant" else "产物"
    options = []
    for index, participant in enumerate(row.get(f"{side}_participants") or []):
        atom_ids = [int(value) for value in participant.get("atom_ids") or []]
        options.append(
            {
                "label": (
                    f"{label} {index + 1} · {participant.get('species') or '?'} · "
                    f"{len(atom_ids)} atoms · IDs {','.join(map(str, atom_ids))}"
                ),
                "value": index,
            }
        )
    return options


def _dft_output_stems(
    row: dict[str, Any],
    reactant_indices: list[int] | None,
    product_indices: list[int] | None,
    layout: str,
) -> list[str]:
    stems: list[str] = []
    for side, selected in (
        ("reactant", reactant_indices or []),
        ("product", product_indices or []),
    ):
        participants = row.get(f"{side}_participants") or []
        valid = [int(value) for value in selected if 0 <= int(value) < len(participants)]
        if not valid:
            continue
        if layout in {"combined", "both"}:
            stems.append("reactants" if side == "reactant" else "products")
        if layout in {"separate", "both"}:
            for index in valid:
                atom_ids = sorted(
                    int(value)
                    for value in (participants[index].get("atom_ids") or [])
                )
                if atom_ids:
                    stems.append(
                        f"{side}-{index + 1:02d}-atoms-{min(atom_ids)}-{max(atom_ids)}"
                    )
    return stems


def _dft_electronic_states_from_controls(
    charge_values: list[Any],
    charge_ids: list[dict[str, Any]],
    multiplicity_values: list[Any],
    multiplicity_ids: list[dict[str, Any]],
) -> dict[str, tuple[Any, Any]]:
    charges = {
        str(identifier.get("stem") or ""): value
        for identifier, value in zip(charge_ids or [], charge_values or [])
    }
    multiplicities = {
        str(identifier.get("stem") or ""): value
        for identifier, value in zip(
            multiplicity_ids or [], multiplicity_values or []
        )
    }
    states: dict[str, tuple[Any, Any]] = {}
    for stem in sorted(set(charges).union(multiplicities)):
        charge = charges.get(stem)
        multiplicity = multiplicities.get(stem)
        if charge in {None, ""} and multiplicity in {None, ""}:
            continue
        if charge in {None, ""} or multiplicity in {None, ""}:
            raise ValueError(f"{stem} 必须同时填写电荷和自旋多重度")
        states[stem] = (charge, multiplicity)
    return states


def _build_dft_bundle_from_controls(
    *,
    selected: dict[str, Any] | None,
    app_store: dict[str, Any] | None,
    reactant_indices: list[int] | None,
    product_indices: list[int] | None,
    layout: str,
    unit_confirmation: list[str] | None,
    isolated_cluster_confirmation: list[str] | None,
    charge_values: list[Any],
    charge_ids: list[dict[str, Any]],
    multiplicity_values: list[Any],
    multiplicity_ids: list[dict[str, Any]],
) -> Any:
    row = (selected or {}).get("row") or {}
    artifacts = (app_store or {}).get("artifacts") or {}
    current = svc.inspect_dataset_candidate(
        str((app_store or {}).get("folder") or ""),
        str((app_store or {}).get("base") or ""),
    )
    if (
        current["dataset_id"] != (app_store or {}).get("dataset_id")
        or current["source_revision"] != (app_store or {}).get("source_revision")
    ):
        raise svc.ServiceError(
            "Current Dataset 来源已变化；请重新验证数据集。",
            reason="source_revision_changed",
        )
    bookmark = svc.create_event_bookmark(app_store or {}, row)
    indexed_row = svc.restore_event_bookmark(
        artifacts,
        bookmark,
        dataset_id=str((app_store or {}).get("dataset_id") or ""),
        source_revision=(app_store or {}).get("source_revision") or {},
    )["row"]
    identity_fields = (
        "event_id", "reaction_key", "before_timestep", "after_timestep",
        "association_status", "atom_id_list", "reactant_bonds", "product_bonds",
        "reactant_participants", "product_participants",
    )
    if any(row.get(field) != indexed_row.get(field) for field in identity_fields):
        raise svc.ServiceError(
            "所选事件与当前已发布证据不一致；请重新选择事件。",
            reason="stale_event_selection",
        )
    trajectory = str(artifacts.get("trajectory") or "")
    confirmed_now = "angstrom" in (unit_confirmation or [])
    request = svc.DftGeometryRequest(
        include_reactants=bool(reactant_indices),
        include_products=bool(product_indices),
        reactant_indices=tuple(int(value) for value in (reactant_indices or [])),
        product_indices=tuple(int(value) for value in (product_indices or [])),
        layout=str(layout or "combined"),
        electronic_states=_dft_electronic_states_from_controls(
            charge_values,
            charge_ids,
            multiplicity_values,
            multiplicity_ids,
        ),
        source_length_unit="angstrom" if confirmed_now else None,
    )
    evaluation = svc.evaluate_reaction_readiness(
        artifacts,
        indexed_row,
        svc.ReactionReadinessRequest(
            geometry=request,
            isolated_cluster_confirmed=(
                "confirmed" in (isolated_cluster_confirmation or [])
            ),
        ),
        dataset_id=str((app_store or {}).get("dataset_id") or ""),
        source_revision=current["source_revision"],
        replicate=str((app_store or {}).get("label") or "current"),
    )
    if confirmed_now and evaluation.bundle is not None:
        svc.save_coordinate_length_unit(trajectory, "angstrom")
    return evaluation


def _dft_request_fingerprint(**controls: Any) -> str:
    """Bind a preview to the exact browser request and dataset context."""
    context = dict(controls)
    store = context.pop("app_store") or {}
    context["dataset"] = {
        key: store.get(key)
        for key in ("dataset_id", "source_revision", "label", "artifacts")
    }
    context["selected"] = (context.get("selected") or {}).get("row") or {}
    encoded = json.dumps(context, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _dft_review_token(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return "review:" + hashlib.sha256(encoded).hexdigest()


def _dft_readiness_validation(report: dict[str, Any] | None) -> Any:
    value = report or {}
    qc_handoff = value.get("qc_handoff") or {}
    status = str(qc_handoff.get("status") or "blocked")
    colors = {
        "blocked": "danger",
        "needs_input": "info",
        "review_required": "warning",
        "ready": "success",
    }
    labels = {
        "blocked": "被证据或结构条件阻断",
        "needs_input": "需要补充或确认输入",
        "review_required": "需要人工复核后才能导出",
        "ready": "可交给外部 TS 流程",
    }
    checks = list(qc_handoff.get("checks") or [])
    groups = []
    for group_status in ("blocked", "needs_input", "review_required", "pass"):
        items = [item for item in checks if item.get("status") == group_status]
        if not items:
            continue
        groups.append(
            html.Div(
                [
                    html.Strong(f"{group_status} ({len(items)})"),
                    html.Ul(
                        [
                            html.Li(
                                [
                                    html.Code(str(item.get("id") or "check")),
                                    " · ",
                                    "；".join(
                                        str(value)
                                        for value in (
                                            (item.get("evidence") or {}).get(
                                                "message"
                                            ),
                                            item.get("remediation")
                                            or item.get("claim_limit"),
                                        )
                                        if value
                                    )
                                    or "检查通过",
                                ]
                            )
                            for item in items
                        ],
                        className="mb-1 mt-1",
                    ),
                ],
                className="mt-2",
            )
        )
    return dbc.Alert(
        [
            html.Div(
                [
                    html.Code(status),
                    " · ",
                    labels.get(status, "检查状态未知"),
                ]
            ),
            *groups,
            html.Div(
                "ready 不表示已验证过渡态、动力学模型或可直接计算速率。",
                className="rs-step-note mt-2",
            ),
        ],
        color=colors.get(status, "secondary"),
        className="py-2 mb-0",
    )


def register(app: Any) -> None:
    """Register dft callbacks."""

    @app.callback(
        Output("event-dft-card", "style"),
        Output("event-dft-reactants", "options"),
        Output("event-dft-reactants", "value"),
        Output("event-dft-products", "options"),
        Output("event-dft-products", "value"),
        Output("event-dft-unit-confirmation", "value"),
        Output("event-dft-isolated-cluster-confirmation", "value"),
        Output("event-dft-preview-btn", "disabled"),
        Output("event-dft-alert", "children"),
        Input("event-selected-store", "data"),
        Input("event-viewer-store", "data"),
        State("app-store", "data"),
    )
    def _prepare_dft_geometry(selected, viewer, app_store):
        row = (selected or {}).get("row") or {}
        viewer_event_id = str((viewer or {}).get("event_id") or "")
        event_id = str(row.get("event_id") or "")
        if (
            not row
            or row.get("association_status") != "matched"
            or not viewer
            or viewer_event_id != event_id
        ):
            return {"display": "none"}, [], [], [], [], [], [], True, (
                "只有具有精确 Molecular Evidence 的 matched 事件可以导出 DFT 几何。"
            )
        reactants = _dft_participant_options(row, "reactant")
        products = _dft_participant_options(row, "product")
        trajectory = str(
            (((app_store or {}).get("artifacts") or {}).get("trajectory") or "")
        )
        try:
            confirmed = svc.load_coordinate_length_unit(trajectory) == "angstrom"
        except (OSError, ValueError):
            confirmed = False
        message = (
            f"反应物使用 timestep {row.get('before_timestep')}；"
            f"产物使用 timestep {row.get('after_timestep')}。"
        )
        return (
            {"display": "block"},
            reactants,
            [option["value"] for option in reactants],
            products,
            [option["value"] for option in products],
            ["angstrom"] if confirmed else [],
            [],
            not bool(reactants or products),
            message,
        )

    @app.callback(
        Output("event-dft-electronic-states", "children"),
        Input("event-dft-reactants", "value"),
        Input("event-dft-products", "value"),
        Input("event-dft-layout", "value"),
        State("event-selected-store", "data"),
    )
    def _render_dft_electronic_states(
        reactant_indices,
        product_indices,
        layout,
        selected,
    ):
        row = (selected or {}).get("row") or {}
        stems = _dft_output_stems(
            row,
            reactant_indices,
            product_indices,
            str(layout or "combined"),
        )
        if not stems:
            return html.Div("选择至少一个 Molecule Instance。", className="rs-step-note")
        return [
            html.Div(
                [
                    html.Code(stem, className="rs-dft-state-label"),
                    dbc.Input(
                        id={"type": "event-dft-charge", "stem": stem},
                        type="number",
                        step=1,
                        placeholder="总电荷",
                        className="rs-dft-state-input",
                    ),
                    dbc.Input(
                        id={"type": "event-dft-multiplicity", "stem": stem},
                        type="number",
                        min=1,
                        step=1,
                        placeholder="多重度",
                        className="rs-dft-state-input",
                    ),
                ],
                className="rs-dft-state-row",
            )
            for stem in stems
        ]

    app.clientside_callback(
        """function(n, reactants, products, layout, unit, isolated, charges,
                    chargeIds, multiplicities, multiplicityIds, selected, store) {
            if (!n) return window.dash_clientside.no_update;
            const id = (window.crypto && window.crypto.randomUUID)
                ? window.crypto.randomUUID()
                : String(Date.now()) + '-' + String(Math.random());
            return {id, controls: {
                reactant_indices: reactants ?? [], product_indices: products ?? [],
                layout: layout ?? 'combined', unit_confirmation: unit ?? [],
                isolated_cluster_confirmation: isolated ?? [],
                charge_values: charges ?? [], charge_ids: chargeIds ?? [],
                multiplicity_values: multiplicities ?? [], multiplicity_ids: multiplicityIds ?? [],
                selected: selected ?? null, app_store: store ?? null
            }};
        }""",
        Output("event-dft-request", "data"),
        Input("event-dft-preview-btn", "n_clicks"),
        State("event-dft-reactants", "value"),
        State("event-dft-products", "value"),
        State("event-dft-layout", "value"),
        State("event-dft-unit-confirmation", "value"),
        State("event-dft-isolated-cluster-confirmation", "value"),
        State({"type": "event-dft-charge", "stem": ALL}, "value"),
        State({"type": "event-dft-charge", "stem": ALL}, "id"),
        State({"type": "event-dft-multiplicity", "stem": ALL}, "value"),
        State({"type": "event-dft-multiplicity", "stem": ALL}, "id"),
        State("event-selected-store", "data"),
        State("app-store", "data"),
        prevent_initial_call=True,
    )

    @app.callback(
        Output("event-dft-response", "data"),
        Input("event-dft-request", "data"),
        prevent_initial_call=True,
        running=[
            (
                Output(
                    {"type": "dataset-bound-operation", "name": "dft-geometry"},
                    "data",
                ),
                True,
                False,
            )
        ],
    )
    def _preview_dft_geometry(request):
        if not request:
            raise PreventUpdate
        controls = request["controls"]
        request_id = request["id"]
        try:
            evaluation = _build_dft_bundle_from_controls(**controls)
        except (
            svc.DftGeometryError,
            svc.ServiceError,
            OSError,
            RuntimeError,
            TypeError,
            ValueError,
        ) as exc:
            message = exc.message if isinstance(exc, svc.ServiceError) else str(exc)
            return {"request_id": request_id, "payload": None,
                    "validation": dbc.Alert(message, color="danger", className="py-2 mb-0"),
                    "summary": [], "options": [], "file": None,
                    "panel": {"display": "none"}, "disabled": True}
        report = evaluation.report
        status = str((report.get("qc_handoff") or {}).get("status") or "blocked")
        validation = _dft_readiness_validation(report)
        if evaluation.bundle is None:
            return {"request_id": request_id,
                    "payload": {"readiness_report": report,
                                "request_fingerprint": _dft_request_fingerprint(**controls)},
                    "validation": validation,
                    "summary": [html.Span(status, className="rs-stat-chip")],
                    "options": [], "file": None,
                    "panel": {"display": "none"}, "disabled": True}
        bundle = evaluation.bundle
        manifest = bundle.manifest
        warnings = [
            warning
            for geometry in manifest.get("geometries") or []
            for warning in geometry.get("warnings") or []
        ]
        geometry_meta = manifest.get("geometries") or []
        summary = [
            html.Span(status, className="rs-stat-chip"),
            html.Span(f"文件 {len(bundle.geometries)}", className="rs-stat-chip"),
            html.Span(
                f"最大 {max(int(item.get('atom_count') or 0) for item in geometry_meta)} 原子",
                className="rs-stat-chip",
            ),
            html.Span(f"警告 {len(warnings)}", className="rs-stat-chip"),
            html.Span("单位 Å", className="rs-stat-chip"),
        ]
        options = [
            {"label": name, "value": name} for name in sorted(bundle.geometries)
        ]
        return {"request_id": request_id,
                "payload": {**bundle.preview_payload(),
                            "request_fingerprint": _dft_request_fingerprint(**controls)},
                "validation": validation, "summary": summary,
                "options": options, "file": options[0]["value"],
                "panel": {"display": "block"}, "disabled": status != "ready"}

    app.clientside_callback(
        """function(response, request, reactants, products, layout, unit,
                    isolated, charges, chargeIds, multiplicities, multiplicityIds,
                    selected, store) {
            const empty = [null, [], [], [], null, {display: 'none'}, true, []];
            if (!response || !request || response.request_id !== request.id)
                return empty;
            const current = {
                reactant_indices: reactants ?? [], product_indices: products ?? [],
                layout: layout ?? 'combined', unit_confirmation: unit ?? [],
                isolated_cluster_confirmation: isolated ?? [],
                charge_values: charges ?? [], charge_ids: chargeIds ?? [],
                multiplicity_values: multiplicities ?? [], multiplicity_ids: multiplicityIds ?? [],
                selected: selected ?? null, app_store: store ?? null
            };
            const stable = x => x && typeof x === 'object'
                ? (Array.isArray(x) ? x.map(stable)
                    : Object.fromEntries(Object.keys(x).sort().map(k => [k, stable(x[k])])))
                : x;
            if (JSON.stringify(stable(current)) !== JSON.stringify(stable(request.controls)))
                return empty;
            return [response.payload, response.validation, response.summary,
                    response.options, response.file, response.panel,
                    response.disabled, []];
        }""",
        Output("event-dft-store", "data", allow_duplicate=True),
        Output("event-dft-validation", "children", allow_duplicate=True),
        Output("event-dft-summary", "children", allow_duplicate=True),
        Output("event-dft-preview-file", "options", allow_duplicate=True),
        Output("event-dft-preview-file", "value", allow_duplicate=True),
        Output("event-dft-preview-panel", "style", allow_duplicate=True),
        Output("event-dft-download-btn", "disabled", allow_duplicate=True),
        Output("event-dft-review-confirmation", "value", allow_duplicate=True),
        Input("event-dft-response", "data"),
        Input("event-dft-request", "data"),
        Input("event-dft-reactants", "value"),
        Input("event-dft-products", "value"),
        Input("event-dft-layout", "value"),
        Input("event-dft-unit-confirmation", "value"),
        Input("event-dft-isolated-cluster-confirmation", "value"),
        Input({"type": "event-dft-charge", "stem": ALL}, "value"),
        Input({"type": "event-dft-charge", "stem": ALL}, "id"),
        Input({"type": "event-dft-multiplicity", "stem": ALL}, "value"),
        Input({"type": "event-dft-multiplicity", "stem": ALL}, "id"),
        Input("event-selected-store", "data"),
        Input("app-store", "data"),
        prevent_initial_call=True,
    )

    @app.callback(
        Output("event-dft-review-confirmation", "options"),
        Input("event-dft-store", "data"),
        prevent_initial_call=True,
    )
    def _bind_dft_review_to_report(payload):
        report = (payload or {}).get("readiness_report") or {}
        if (report.get("qc_handoff") or {}).get("status") != "review_required":
            return []
        return [{"label": "我已逐项复核当前报告的 review_required 警告",
                 "value": _dft_review_token(payload)}]

    @app.callback(
        Output("event-dft-download-btn", "disabled", allow_duplicate=True),
        Input("event-dft-review-confirmation", "value"),
        State("event-dft-store", "data"),
        prevent_initial_call=True,
    )
    def _acknowledge_dft_review(confirmation, payload):
        report = (payload or {}).get("readiness_report") or {}
        status = str((report.get("qc_handoff") or {}).get("status") or "")
        if status == "ready":
            return False
        return not (
            status == "review_required"
            and _dft_review_token(payload) in (confirmation or [])
            and bool((payload or {}).get("geometries"))
        )

    @app.callback(
        Output("event-dft-preview-text", "children"),
        Input("event-dft-preview-file", "value"),
        Input("event-dft-store", "data"),
    )
    def _render_dft_preview(filename, payload):
        return str(((payload or {}).get("geometries") or {}).get(filename) or "")

    @app.callback(
        Output("event-dft-download", "data"),
        Output("event-dft-validation", "children", allow_duplicate=True),
        Output("event-dft-download-btn", "disabled", allow_duplicate=True),
        Input("event-dft-download-btn", "n_clicks"),
        State("event-dft-store", "data"),
        State("event-selected-store", "data"),
        State("event-dft-review-confirmation", "value"),
        State("app-store", "data"),
        State("event-dft-reactants", "value"),
        State("event-dft-products", "value"),
        State("event-dft-layout", "value"),
        State("event-dft-unit-confirmation", "value"),
        State("event-dft-isolated-cluster-confirmation", "value"),
        State({"type": "event-dft-charge", "stem": ALL}, "value"),
        State({"type": "event-dft-charge", "stem": ALL}, "id"),
        State({"type": "event-dft-multiplicity", "stem": ALL}, "value"),
        State({"type": "event-dft-multiplicity", "stem": ALL}, "id"),
        prevent_initial_call=True,
    )
    def _download_dft_geometry(
        n_clicks,
        payload,
        selected,
        review_confirmation,
        app_store,
        reactant_indices,
        product_indices,
        layout,
        unit_confirmation,
        isolated_cluster_confirmation,
        charge_values,
        charge_ids,
        multiplicity_values,
        multiplicity_ids,
    ):
        if n_clicks is None or not payload:
            raise PreventUpdate

        def require_new_preview(message: str):
            return no_update, dbc.Alert(message, color="warning", className="py-2 mb-0"), True

        report = payload.get("readiness_report") or {}
        status = str((report.get("qc_handoff") or {}).get("status") or "")
        if status not in {"ready", "review_required"}:
            return require_new_preview("交接条件尚未满足；请补全输入后重新预检。")
        if status == "review_required" and _dft_review_token(payload) not in (
            review_confirmation or []
        ):
            return require_new_preview("请重新复核并确认当前报告的全部警告，然后下载。")
        controls = dict(
            selected=selected, app_store=app_store,
            reactant_indices=reactant_indices, product_indices=product_indices,
            layout=layout, unit_confirmation=unit_confirmation,
            isolated_cluster_confirmation=isolated_cluster_confirmation,
            charge_values=charge_values, charge_ids=charge_ids,
            multiplicity_values=multiplicity_values, multiplicity_ids=multiplicity_ids,
        )
        if payload.get("request_fingerprint") != _dft_request_fingerprint(**controls):
            return require_new_preview("事件、数据集或几何参数已变化；请重新预检后下载。")
        try:
            evaluation = _build_dft_bundle_from_controls(**controls)
        except (svc.DftGeometryError, svc.ServiceError, OSError, RuntimeError, TypeError, ValueError):
            return require_new_preview("交接证据已变化或暂不可读取；请检查数据并重新预检。")
        bundle = evaluation.bundle
        expected = {key: value for key, value in payload.items() if key != "request_fingerprint"}
        if bundle is None or bundle.preview_payload() != expected:
            return require_new_preview("源数据、设置或检查结果已变化；请重新预检并复核当前报告。")
        event_id = str(((selected or {}).get("row") or {}).get("event_id") or "event")
        return (
            dcc.send_bytes(bundle.to_zip(), f"{event_id}_qc_handoff.zip", type="application/zip"),
            no_update,
            no_update,
        )
