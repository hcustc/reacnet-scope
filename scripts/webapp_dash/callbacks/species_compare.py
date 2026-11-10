"""Cross-source species comparison callbacks."""

from __future__ import annotations

from .. import ui_components as ui
from ..ui_state import species_query
import time
import uuid
from pathlib import Path
from typing import Any
import dash_bootstrap_components as dbc
import plotly.graph_objects as go
from dash import (
    ALL,
    MATCH,
    Input,
    Output,
    State,
    ctx,
    dcc,
    html,
    no_update,
)
from dash.exceptions import PreventUpdate
from reacnet_scope.indexes import IndexBuildInProgressError
from reacnet_scope import services as svc
from scripts.webapp_dash import dataset_library
from scripts.webapp_dash.chart_presentation import empty_chart_figure
from scripts.webapp_dash.candidate_workbench import context_key

from .common import (
    _evolution_comparison_active,
    _selected_table_row,
)


def _managed_species_file(dataset: dict[str, Any]) -> str:
    """Resolve a managed dataset's registered Species artifact when present."""
    base = str(dataset.get("base") or "")
    if svc.is_collection_path(base):
        try:
            collection = svc.read_collection(base)
        except svc.ServiceError:
            collection = None
        if collection:
            return str((collection.get("artifact_paths") or {}).get("species") or f"{base}.species")
    return f"{base}.species"


def _comparison_preparation_entry(
    path: str,
    sources: list[dict[str, Any]] | None,
    managed: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Find the selected, registered dataset behind a comparison source."""
    source = next(
        (item for item in sources or [] if item.get("species_file") == path), None
    )
    if not source:
        return None
    for entry in (managed or {}).get("datasets") or []:
        if not entry.get("folder") or not entry.get("base") or not entry.get("dataset_id"):
            continue
        if str(Path(_managed_species_file(entry)).expanduser().resolve()) != path:
            continue
        if source.get("dataset_id") and source["dataset_id"] != entry["dataset_id"]:
            continue
        return entry
    return None


def register(app: Any) -> None:
    """Register species compare callbacks."""

    @app.callback(
        Output("evolution-current-panel", "style"),
        Output("compare-species-panel", "style"),
        Output("evolution-open-compare-btn", "style"),
        Output("evolution-close-compare-btn", "style"),
        Output("evolution-current-label", "children"),
        Input("page-store", "data"),
        Input("evolution-open-compare-btn", "n_clicks"),
        Input("evolution-close-compare-btn", "n_clicks"),
        Input("species-result-compare-btn", "n_clicks"),
        Input("data-open-batch-compare-btn", "n_clicks"),
    )
    def _show_evolution_sources(page, _open, _close, _species_open, _data_open):
        clicked = {
            str(item.get("prop_id") or "").removesuffix(".n_clicks")
            for item in ctx.triggered
            if str(item.get("prop_id") or "").endswith(".n_clicks")
            and isinstance(item.get("value"), (int, float)) and item["value"] > 0
        }
        if clicked & {"evolution-open-compare-btn", "species-result-compare-btn",
                      "data-open-batch-compare-btn"}:
            comparing = True
        elif "evolution-close-compare-btn" in clicked:
            comparing = False
        else:
            comparing = _evolution_comparison_active(page)
        hidden = {"display": "none"}
        return (hidden if comparing else {}, {} if comparing else hidden,
                hidden if comparing else {}, {} if comparing else hidden,
                "对比来源" if comparing else "当前数据")

    # ── Batch comparison ────────────────────────────────────────────

    @app.callback(
        Output("species-compare-managed", "options"),
        Output("species-compare-managed", "value"),
        Input("batch-managed-store", "data"),
        State("species-compare-catalog-store", "data"),
        State("species-compare-sources-store", "data"),
        State("species-compare-managed", "options"),
    )
    def _species_compare_managed_options(managed, _catalogs, sources, previous_options):
        options = []
        # Keep option identities in one order as Current Dataset changes, so an
        # open picker does not move entries underneath a user's selection.
        datasets = sorted((managed or {}).get("datasets", []),
                          key=lambda item: str(item.get("id") or ""))
        managed_paths = {
            _managed_species_file(item)
            for item in datasets
            if item.get("base")
        }
        selected_paths = {
            str(item.get("species_file") or "")
            for item in (sources or [])
            if item.get("source_origin") == "managed"
            or (
                not item.get("source_origin")
                and item.get("species_file") in managed_paths
            )
        }
        selected = []
        for item in datasets:
            if not item.get("id"):
                continue
            species_file = _managed_species_file(item)
            if species_file in selected_paths:
                selected.append(str(item["id"]))
            catalog = svc.species_compare_picker(species_file)
            status = str(catalog.get("status") or "missing_source")
            status_label = {
                "ready": "丰度索引可用",
                "missing_index": "需准备丰度索引",
                "missing_source": "缺少 Species 来源",
            }.get(status, "状态未知")
            options.append(
                {
                    "label": (
                        f"{str(item.get('label') or item.get('base'))} · "
                        f"{status_label}"
                    ),
                    "value": str(item["id"]),
                }
            )
        same_ids = ({str(option.get("value")) for option in previous_options or []}
                    == {option["value"] for option in options})
        # Status/current-dataset refreshes must not apply a stale selection
        # snapshot after the user has changed the browser's current value.
        keep_selection = ctx.triggered_id == "species-compare-catalog-store" or same_ids
        return options, no_update if keep_selection else selected

    @app.callback(
        Output("species-compare-managed", "options", allow_duplicate=True),
        Input("species-compare-catalog-store", "data"),
        State("batch-managed-store", "data"),
        prevent_initial_call=True,
    )
    def _refresh_species_compare_managed_status(catalogs, managed):
        # Capability refreshes follow source edits. Keeping this separate avoids
        # blocking initial source options behind their own downstream catalog.
        return _species_compare_managed_options(managed, catalogs, [], [])[0]

    @app.callback(
        Output("species-compare-sources-store", "data"),
        Output("species-compare-entry-note", "children"),
        Input("species-compare-managed", "value"),
        Input("species-compare-add-path", "n_clicks"),
        Input("evolution-open-compare-btn", "n_clicks"),
        Input({"type": "species-compare-remove", "path": ALL}, "n_clicks"),
        State("batch-managed-store", "data"),
        State("species-compare-path", "value"),
        State("species-compare-new-label", "value"),
        State("species-compare-sources-store", "data"),
        State({"type": "species-compare-target", "path": ALL}, "value"),
        State({"type": "species-compare-target", "path": ALL}, "id"),
        State({"type": "species-compare-label", "path": ALL}, "value"),
        State({"type": "species-compare-label", "path": ALL}, "id"),
        State({"type": "species-compare-model-iteration", "path": ALL}, "value"),
        State({"type": "species-compare-model-iteration", "path": ALL}, "id"),
        State({"type": "species-compare-condition", "path": ALL}, "value"),
        State({"type": "species-compare-condition", "path": ALL}, "id"),
        State({"type": "species-compare-replicate", "path": ALL}, "value"),
        State({"type": "species-compare-replicate", "path": ALL}, "id"),
        State("app-store", "data"),
        prevent_initial_call=True,
    )
    def _edit_species_compare_sources(
        managed_ids, _add_path, _handoff, _remove, managed, path_text,
        new_label, sources, targets, target_ids, labels, label_ids,
        model_iterations, model_iteration_ids, conditions, condition_ids,
        replicates, replicate_ids, app_store,
    ):
        sources = [dict(item) for item in (sources or [])]
        current = {item["species_file"]: item for item in sources}
        for value, item_id in zip(targets or [], target_ids or []):
            path = item_id["path"]
            if path in current:
                current[path]["target_smiles"] = str(value or "").strip()
        for value, item_id in zip(labels or [], label_ids or []):
            path = item_id["path"]
            if path in current:
                current[path]["label"] = str(value or "").strip()
        for field, values, item_ids in (
            ("model_iteration", model_iterations, model_iteration_ids),
            ("simulation_condition", conditions, condition_ids),
            ("replicate", replicates, replicate_ids),
        ):
            for value, item_id in zip(values or [], item_ids or []):
                path = item_id["path"]
                if path in current:
                    current[path][field] = str(value or "").strip()

        triggered = ctx.triggered_id
        if isinstance(triggered, dict) and triggered.get("type") == "species-compare-remove":
            if not any(int(clicks or 0) > 0 for clicks in (_remove or [])):
                raise PreventUpdate
            return [
                item
                for item in sources
                if item["species_file"] != triggered.get("path")
            ], no_update
        if triggered == "evolution-open-compare-btn":
            store = app_store or {}
            species_file = str(
                (store.get("artifacts") or {}).get("species") or ""
            ).strip()
            if not species_file:
                return sources, "选择已导入的 RNG 数据，或添加 Species 文件。"
            path = str(Path(species_file).expanduser().resolve())
            selected_smiles = str(store.get("selected_smiles") or "").strip()
            existing = next(
                (item for item in sources if item.get("species_file") == path),
                None,
            )
            if existing is None:
                sources.append(
                    {
                        "species_file": path,
                        "label": str(store.get("label") or Path(path).stem),
                        "target_smiles": selected_smiles,
                        "model_iteration": "",
                        "simulation_condition": "",
                        "replicate": "",
                        "dataset_id": str(store.get("dataset_id") or ""),
                        "source_revision": store.get("source_revision") or {},
                        "source_origin": "handoff",
                    }
                )
            elif selected_smiles and not str(
                existing.get("target_smiles") or ""
            ).strip():
                existing["target_smiles"] = selected_smiles
            message = (
                f"已加入当前RNG 数据，并交接精确 Species：{selected_smiles}。"
                if selected_smiles
                else "已加入当前RNG 数据；请为每个来源分别确认精确 Species。"
            )
            return sources, dbc.Alert(
                message,
                color="info",
                className="py-1 px-2 mb-0",
            )
        additions: list[dict[str, Any]] = []
        if triggered == "species-compare-managed":
            lookup = {str(item.get("id")): item for item in (managed or {}).get("datasets", [])}
            selected_ids = {str(dataset_id) for dataset_id in (managed_ids or [])}
            selected_paths = {
                str(Path(_managed_species_file(lookup[dataset_id])).expanduser().resolve())
                for dataset_id in selected_ids
                if dataset_id in lookup
            }
            known_managed_paths = {
                str(Path(_managed_species_file(item)).expanduser().resolve())
                for item in lookup.values()
                if item.get("base")
            }
            sources = [
                item for item in sources
                if (
                    item.get("source_origin") != "managed"
                    and not (
                        not item.get("source_origin")
                        and item.get("species_file") in known_managed_paths
                    )
                )
                or item.get("species_file") in selected_paths
            ]
            current = {item["species_file"]: item for item in sources}
            for item in sources:
                if (
                    not item.get("source_origin")
                    and item.get("species_file") in selected_paths
                ):
                    item["source_origin"] = "managed"
            for dataset_id in managed_ids or []:
                dataset = lookup.get(str(dataset_id))
                if dataset:
                    additions.append(
                        {
                            "path": _managed_species_file(dataset),
                            "label": str(dataset.get("label") or ""),
                            "dataset_id": str(dataset.get("dataset_id") or ""),
                            "source_revision": dataset.get("source_revision") or {},
                            "source_origin": "managed",
                        }
                    )
        elif triggered == "species-compare-add-path" and str(path_text or "").strip():
            additions.append(
                {
                    "path": str(path_text).strip(),
                    "label": str(new_label or "").strip(),
                }
            )
        else:
            raise PreventUpdate
        for addition in additions:
            path = str(addition.get("path") or "")
            label = str(addition.get("label") or "")
            resolved = str(Path(path).expanduser().resolve())
            if resolved not in current:
                item = {
                    "species_file": resolved,
                    "label": label or Path(resolved).stem,
                    "target_smiles": "",
                    "model_iteration": "",
                    "simulation_condition": "",
                    "replicate": "",
                    "dataset_id": str(addition.get("dataset_id") or ""),
                    "source_revision": addition.get("source_revision") or {},
                    "source_origin": str(addition.get("source_origin") or "manual"),
                }
                sources.append(item)
                current[resolved] = item
        return sources, no_update

    @app.callback(
        Output("species-compare-sources", "children"),
        Input("species-compare-sources-store", "data"),
    )
    def _render_species_compare_sources(sources):
        if not sources:
            return html.Div(
                [
                    html.Div("尚未添加来源", className="rs-batch-empty-title"),
                    html.Div(
                        "从已管理RNG 数据中选择，或展开高级入口添加 Species 文件。",
                        className="rs-batch-empty-hint",
                    ),
                ],
                className="rs-compare-source-empty",
            )
        rows = []
        for index, source in enumerate(sources, 1):
            path = source["species_file"]
            catalog = svc.species_compare_picker(path, selected=source.get("target_smiles") or "")
            label = str(source.get("label") or Path(path).stem)
            catalog_status = str(catalog.get("status") or "missing_source")
            badge_color = {
                "ready": "success",
                "missing_index": "warning",
                "missing_source": "danger",
            }.get(catalog_status, "secondary")
            badge_label = {
                "ready": "丰度索引可用",
                "missing_index": "需准备索引",
                "missing_source": "来源缺失",
            }.get(catalog_status, "状态未知")
            rows.append(html.Div([
                html.Div([
                    html.Div([
                        html.Span(f"来源 {index:02d}", className="rs-compare-source-kicker"),
                        html.Strong(label, className="rs-compare-source-name"),
                        dbc.Badge(
                            badge_label, color=badge_color, pill=True,
                            id={"type": "species-compare-index-badge", "path": path},
                        ),
                    ], className="rs-compare-source-title"),
                    (
                        html.Span("在上方取消选择", className="rs-compare-source-remove-hint")
                        if source.get("source_origin") == "managed"
                        else dbc.Button(
                            "移除",
                            id={"type": "species-compare-remove", "path": path},
                            size="sm",
                            color="danger",
                            outline=True,
                        )
                    ),
                ], className="rs-compare-source-header"),
                html.Div([
                    html.Details([
                        html.Summary("按分子式 / SMILES / 质量数检索"),
                        html.Div([
                            dcc.Dropdown(
                                id={"type": "species-compare-query-kind", "path": path},
                                options=[
                                    {"label": "自动识别", "value": "auto"},
                                    {"label": "分子式", "value": "formula"},
                                    {"label": "SMILES", "value": "smiles"},
                                    {"label": "质量数", "value": "mass"},
                                ],
                                value="auto", clearable=False,
                                className="rs-compare-query-kind",
                            ),
                            dcc.Input(
                                id={"type": "species-compare-query", "path": path},
                                placeholder="分子式、SMILES 或质量数",
                                type="text", className="rs-compare-query-input",
                            ),
                            html.Label([
                                html.Span("± Da", className="rs-compare-mass-caption"),
                                dcc.Input(
                                    id={"type": "species-compare-mass-tol", "path": path},
                                    value=0.5, min=0, type="number", step="any",
                                    className="rs-compare-mass-tol",
                                ),
                            ], className="rs-compare-mass-field"),
                            dbc.Button(
                                "检索", id={"type": "species-compare-search", "path": path},
                                color="secondary", outline=True, size="sm",
                            ),
                        ], className="rs-compare-search-row"),
                        html.Div(
                            "检索结果需要再确认具体 SMILES。",
                            id={"type": "species-compare-search-status", "path": path},
                            className="rs-compare-search-status", role="status",
                        ),
                    ], className="rs-compare-search-details"),
                    dcc.Store(id={"type": "species-compare-search-result", "path": path}),
                    html.Div([
                        dbc.Label("精确 Species", className="rs-compare-field-label"),
                        dcc.Dropdown(
                            id={"type": "species-compare-target", "path": path},
                            options=catalog["options"],
                            value=source.get("target_smiles") or None,
                            placeholder="选择检索结果或直接输入 SMILES",
                        ),
                    ], className="rs-compare-target-field"),
                ], className="rs-compare-source-main"),
                html.Div([
                    html.Div(
                        str(catalog.get("message") or ""),
                        id={"type": "species-compare-index-status", "path": path},
                        className="rs-compare-index-state",
                        role="status",
                    ),
                    dbc.Button(
                        "准备丰度索引",
                        id={"type": "species-compare-prepare", "path": path},
                        color="secondary", outline=True, size="sm", disabled=True,
                        style={"display": "none"},
                    ),
                    dbc.Button(
                        "取消准备",
                        id={"type": "species-compare-cancel", "path": path},
                        color="secondary", outline=True, size="sm", disabled=True,
                        style={"display": "none"},
                    ),
                    dcc.Store(id={"type": "species-compare-prepare-request", "path": path}),
                    dcc.Store(id={"type": "species-compare-prepare-result", "path": path}),
                ], className="rs-compare-source-status rs-compare-index-control"),
                html.Details([
                    html.Summary("来源名称、分组信息与文件位置"),
                    html.Div([
                        html.Div([
                            dbc.Label("来源名称"),
                            dcc.Input(
                                id={"type": "species-compare-label", "path": path},
                                value=source.get("label") or "",
                                placeholder="来源名称",
                            ),
                        ], className="rs-evolution-field"),
                        html.Div([
                            dbc.Label("模型迭代"),
                            dcc.Input(
                                id={"type": "species-compare-model-iteration", "path": path},
                                value=source.get("model_iteration") or "",
                                placeholder="未知可留空",
                            ),
                        ], className="rs-evolution-field"),
                        html.Div([
                            dbc.Label("Simulation Condition"),
                            dcc.Input(
                                id={"type": "species-compare-condition", "path": path},
                                value=source.get("simulation_condition") or "",
                                placeholder="未知可留空",
                            ),
                        ], className="rs-evolution-field"),
                        html.Div([
                            dbc.Label("Replicate"),
                            dcc.Input(
                                id={"type": "species-compare-replicate", "path": path},
                                value=source.get("replicate") or "",
                                placeholder="未知可留空",
                            ),
                        ], className="rs-evolution-field"),
                    ], className="rs-compare-source-metadata"),
                    html.Div([
                        html.Span("Species 文件", className="rs-compare-path-label"),
                        html.Code(path),
                    ], className="rs-compare-source-path"),
                ], className="rs-compare-source-details"),
            ], className="rs-compare-source-card", key=path))
        return rows

    @app.callback(
        Output("species-compare-index-refresh", "disabled"),
        Input("page-store", "data"),
        Input("species-compare-sources-store", "data"),
    )
    def _poll_species_compare_indexes(page, sources):
        return not _evolution_comparison_active(page) or not bool(sources)

    @app.callback(
        Output({"type": "species-compare-index-status", "path": ALL}, "children"),
        Output({"type": "species-compare-index-badge", "path": ALL}, "children"),
        Output({"type": "species-compare-index-badge", "path": ALL}, "color"),
        Output({"type": "species-compare-prepare", "path": ALL}, "children"),
        Output({"type": "species-compare-prepare", "path": ALL}, "disabled"),
        Output({"type": "species-compare-prepare", "path": ALL}, "style"),
        Output({"type": "species-compare-cancel", "path": ALL}, "disabled"),
        Output({"type": "species-compare-cancel", "path": ALL}, "style"),
        Output("species-compare-catalog-store", "data"),
        Input("species-compare-sources-store", "data"),
        Input("batch-managed-store", "data"),
        Input("species-compare-index-refresh", "n_intervals"),
        Input({"type": "species-compare-prepare-request", "path": ALL}, "data"),
        Input({"type": "species-compare-prepare-result", "path": ALL}, "data"),
        Input("species-compare-cancel-result", "data"),
        State({"type": "species-compare-index-status", "path": ALL}, "id"),
        State({"type": "species-compare-index-badge", "path": ALL}, "id"),
        State({"type": "species-compare-prepare", "path": ALL}, "id"),
        State({"type": "species-compare-cancel", "path": ALL}, "id"),
        State("species-compare-catalog-store", "data"),
    )
    def _species_compare_index_status(
        sources, managed, _tick, requests, results, cancel_result,
        status_ids, badge_ids, prepare_ids, cancel_ids, previous_catalogs,
    ):
        requests_by_path = {
            item["path"]: item for item in requests or []
            if isinstance(item, dict) and item.get("path")
        }
        results_by_path = {
            item["path"]: item for item in results or []
            if isinstance(item, dict) and item.get("path")
        }
        views = {}
        catalogs = {}
        for source in sources or []:
            path = str(source.get("species_file") or "")
            if not path:
                continue
            catalog = svc.species_compare_picker(path, selected=source.get("target_smiles") or "")
            catalogs[path] = {
                key: catalog[key] for key in ("status", "message") if key in catalog
            }
            catalog_status = str(catalog.get("status") or "missing_source")
            badge_label, badge_color = {
                "ready": ("丰度索引可用", "success"),
                "missing_index": ("需准备索引", "warning"),
                "missing_source": ("来源缺失", "danger"),
            }.get(catalog_status, ("状态未知", "secondary"))
            view = {
                "content": str(catalog.get("message") or ""),
                "badge": badge_label,
                "color": badge_color,
                "build_label": "准备丰度索引",
                "build_disabled": True,
                "build_style": {"display": "none"},
                "cancel_disabled": True,
                "cancel_style": {"display": "none"},
            }
            entry = _comparison_preparation_entry(path, sources, managed)
            if entry:
                try:
                    svc.validate_browse_path(entry["folder"])
                    svc.validate_browse_path(entry["base"])
                    status = svc.dataset_preparation_status(entry["folder"], base=entry["base"])
                    if str(status.get("dataset_id") or "") != str(entry["dataset_id"]):
                        raise svc.ServiceError("RNG 数据身份已变化，请重新导入。", reason="dataset_identity_changed")
                    request = requests_by_path.get(path)
                    feedback = results_by_path.get(path)
                    if (request and feedback
                            and request.get("token") != feedback.get("token")):
                        feedback = None
                    if (feedback and feedback.get("ok") and feedback.get("source_revision")):
                        validation = svc.validate_dataset_candidate(entry["folder"], entry["base"])
                        if not svc.is_same_dataset_revision(feedback, validation):
                            feedback = None
                    controls = dataset_library._library_index_control(
                        entry, "composition", status.get("composition") or {},
                        request, feedback, cancel_result,
                    )
                    build = next(item for item in controls if isinstance(item, dbc.Button)
                                 and (item.id or {}).get("type") == "library-build-index")
                    cancel = next((item for item in controls if isinstance(item, dbc.Button)
                                   and (item.id or {}).get("type") == "library-cancel-index"), None)
                    view.update(
                        content=[item for item in controls if not isinstance(item, dbc.Button)],
                        build_label=build.children,
                        build_disabled=build.disabled,
                        build_style=build.style,
                        cancel_disabled=cancel.disabled if cancel else True,
                        cancel_style=getattr(cancel, "style", None) if cancel else {"display": "none"},
                    )
                    if (status.get("composition") or {}).get("state") == "building":
                        view.update(badge="准备中", color="info")
                except (svc.ServiceError, OSError, IndexBuildInProgressError) as exc:
                    view["content"] = f"状态不可用：{exc}"
                    view.update(badge="状态不可用", color="danger")
            elif catalog_status == "missing_index":
                view["content"] = "请先将此来源导入RNG 数据，再准备丰度索引。"
            views[path] = view

        def field(ids, key):
            return [views.get(item["path"], {}).get(key) for item in ids or []]

        return (
            field(status_ids, "content"),
            field(badge_ids, "badge"),
            field(badge_ids, "color"),
            field(prepare_ids, "build_label"),
            field(prepare_ids, "build_disabled"),
            field(prepare_ids, "build_style"),
            field(cancel_ids, "cancel_disabled"),
            field(cancel_ids, "cancel_style"),
            catalogs if catalogs != (previous_catalogs or {}) else no_update,
        )

    @app.callback(
        Output({"type": "species-compare-prepare-request", "path": MATCH}, "data"),
        Input({"type": "species-compare-prepare", "path": MATCH}, "n_clicks"),
        State("species-compare-sources-store", "data"),
        State("batch-managed-store", "data"),
        prevent_initial_call=True,
    )
    def _request_species_compare_index(clicks, sources, managed):
        trigger = ctx.triggered_id
        if not clicks or not isinstance(trigger, dict):
            raise PreventUpdate
        path = str(trigger.get("path") or "")
        entry = _comparison_preparation_entry(path, sources, managed)
        if not entry:
            raise PreventUpdate
        source = next(item for item in sources if item.get("species_file") == path)
        return {
            **entry, "kind": "composition", "path": path,
            "source_revision": source.get("source_revision") or {},
            "token": uuid.uuid4().hex,
        }

    @app.callback(
        Output({"type": "species-compare-prepare-result", "path": MATCH}, "data"),
        Input({"type": "species-compare-prepare-request", "path": MATCH}, "data"),
        background=True, prevent_initial_call=True,
    )
    def _prepare_species_compare_index(request):
        if not isinstance(request, dict) or not request.get("token"):
            raise PreventUpdate
        try:
            validation = dataset_library._verified_library_target(request)
            if (request.get("source_revision")
                    and not svc.is_same_dataset_revision(request, validation)):
                raise svc.ServiceError("来源文件已变化，请重新选择该来源。", reason="source_revision_changed")
            bound_request = {**request, "source_revision": validation["source_revision"]}
            result = dataset_library._build_library_index(bound_request)
        except (svc.ServiceError, OSError, IndexBuildInProgressError) as exc:
            return {**request, "ok": False, "message": str(exc)}
        message = (
            "同类任务已在运行。" if result.get("existing_task")
            else "任务已取消，检查点已保留。" if result.get("canceled")
            else "丰度索引已就绪。"
        )
        return {**bound_request, "ok": True, "message": message}

    @app.callback(
        Output("species-compare-cancel-result", "data"),
        Input({"type": "species-compare-cancel", "path": ALL}, "n_clicks"),
        State("species-compare-sources-store", "data"),
        State("batch-managed-store", "data"),
        prevent_initial_call=True,
    )
    def _cancel_species_compare_index(_clicks, sources, managed):
        trigger = ctx.triggered_id
        if not isinstance(trigger, dict) or not dataset_library._clicked(trigger):
            raise PreventUpdate
        path = str(trigger.get("path") or "")
        entry = _comparison_preparation_entry(path, sources, managed)
        if not entry:
            raise PreventUpdate
        try:
            dataset_library._verified_library_target(entry)
            result = svc.cancel_dataset_preparation(
                entry["folder"], base=entry["base"], kind="composition",
            )
            message = result["message"]
        except (svc.ServiceError, OSError) as exc:
            message = str(exc)
        return {**entry, "path": path, "kind": "composition", "message": message}

    @app.callback(
        Output({"type": "species-compare-search-result", "path": MATCH}, "data"),
        Output({"type": "species-compare-search-status", "path": MATCH}, "children"),
        Input({"type": "species-compare-search", "path": MATCH}, "n_clicks"),
        State({"type": "species-compare-query", "path": MATCH}, "value"),
        State({"type": "species-compare-query-kind", "path": MATCH}, "value"),
        State({"type": "species-compare-mass-tol", "path": MATCH}, "value"),
        State({"type": "species-compare-search", "path": MATCH}, "id"),
        prevent_initial_call=True,
    )
    def _query_species_compare_target(_clicks, query, kind, mass_tol, search_id):
        try:
            submitted = species_query(query, kind, mass_tol)
            result = svc.search_species_compare_targets(
                search_id["path"], submitted["query"],
                kind=submitted["kind"], mass_tolerance=submitted["mass_tolerance"],
            )
        except (svc.ServiceError, ValueError, TypeError) as exc:
            message = str(getattr(exc, "message", exc))
            return {"options": []}, message
        return {"options": result["options"]}, result["message"]

    @app.callback(
        Output({"type": "species-compare-target", "path": MATCH}, "options"),
        Input({"type": "species-compare-target", "path": MATCH}, "search_value"),
        Input("species-compare-catalog-store", "data"),
        Input({"type": "species-compare-search-result", "path": MATCH}, "data"),
        State({"type": "species-compare-target", "path": MATCH}, "value"),
        State({"type": "species-compare-target", "path": MATCH}, "id"),
        prevent_initial_call=True,
    )
    def _search_species_compare_target(search_value, _catalogs, search_result, selected, target_id):
        path = target_id["path"]
        if search_value:
            return svc.species_compare_picker(
                path, query=search_value, selected=selected or ""
            )["options"]
        if isinstance(search_result, dict) and ctx.triggered_id != "species-compare-catalog-store":
            options = list(search_result.get("options") or [])
            if selected and all(option["value"] != selected for option in options):
                selected_option = svc.species_compare_picker(path, selected=selected)["options"]
                options.extend(option for option in selected_option if option["value"] == selected)
            return options
        return svc.species_compare_picker(path, selected=selected or "")["options"]

    @app.callback(
        Output("species-compare-readiness", "children"),
        Output("species-compare-run", "disabled"),
        Input("species-compare-sources-store", "data"),
        Input({"type": "species-compare-target", "path": ALL}, "value"),
        Input({"type": "species-compare-target", "path": ALL}, "id"),
        Input("species-compare-catalog-store", "data"),
        Input("species-compare-axis", "value"),
    )
    def _validate_species_compare_sources(sources, targets, target_ids, catalogs, axis):
        current = {
            str(item.get("species_file") or ""): dict(item)
            for item in (sources or [])
            if item.get("species_file")
        }
        for value, item_id in zip(targets or [], target_ids or []):
            path = str(item_id.get("path") or "")
            if path in current:
                current[path]["target_smiles"] = str(value or "").strip()
        if len(current) < 2:
            return dbc.Alert(
                f"已添加 {len(current)} 个来源；至少需要 2 个。",
                color="secondary",
                className="py-1 px-2 mb-0",
            ), True
        blockers = []
        ready = 0
        for source in current.values():
            label = str(source.get("label") or Path(source["species_file"]).stem)
            catalog = (catalogs or {}).get(source["species_file"])
            if not catalog:
                blockers.append(f"{label}：正在检查丰度索引")
                continue
            if catalog.get("status") != "ready":
                blockers.append(f"{label}：{catalog.get('message') or '丰度索引不可用'}")
                continue
            target = str(source.get("target_smiles") or "").strip()
            if not target:
                blockers.append(f"{label}：尚未确认精确 Species")
                continue
            if not svc.species_compare_target_exists(source["species_file"], target):
                blockers.append(f"{label}：索引中没有该精确 Species")
                continue
            ready += 1
        if blockers:
            return dbc.Alert(
                [
                    html.Div(f"{ready}/{len(current)} 个来源已就绪。"),
                    html.Ul([html.Li(item) for item in blockers], className="mb-0 ps-3"),
                ],
                color="warning",
                className="py-1 px-2 mb-0",
            ), True
        time_note = (
            "运行时将核验每个来源已保存的物理时间换算。"
            if axis in {"ps", "ns"}
            else "将按各来源独立的原始 timestep 展示。"
        )
        return dbc.Alert(
            f"{ready}/{len(current)} 个来源已就绪；{time_note}",
            color="success",
            className="py-1 px-2 mb-0",
        ), False

    @app.callback(
        Output("species-compare-graph", "figure"),
        Output("species-compare-summary", "rowData"),
        Output("species-compare-summary", "columnDefs"),
        Output("species-compare-alert", "children"),
        Output("species-compare-result-store", "data"),
        Output("species-compare-export", "disabled"),
        Input("species-compare-run", "n_clicks"),
        Input("species-compare-sources-store", "data"),
        Input({"type": "species-compare-target", "path": ALL}, "value"),
        Input({"type": "species-compare-target", "path": ALL}, "id"),
        Input({"type": "species-compare-label", "path": ALL}, "value"),
        Input({"type": "species-compare-label", "path": ALL}, "id"),
        Input({"type": "species-compare-model-iteration", "path": ALL}, "value"),
        Input({"type": "species-compare-model-iteration", "path": ALL}, "id"),
        Input({"type": "species-compare-condition", "path": ALL}, "value"),
        Input({"type": "species-compare-condition", "path": ALL}, "id"),
        Input({"type": "species-compare-replicate", "path": ALL}, "value"),
        Input({"type": "species-compare-replicate", "path": ALL}, "id"),
        Input("species-compare-axis", "value"),
        prevent_initial_call=True,
    )
    def _run_species_compare(
        _clicks, sources, targets, target_ids, labels, label_ids,
        model_iterations, model_iteration_ids, conditions, condition_ids,
        replicates, replicate_ids, axis,
    ):
        empty = (
            empty_chart_figure(
                "对比条件已变化",
                "检查来源和精确 Species 后，重新运行比较。",
            ),
            [], [], "来源或目标变化后，请重新比较。", None, True,
        )
        if ctx.triggered_id != "species-compare-run":
            return empty
        current = {item["species_file"]: dict(item) for item in (sources or [])}
        for value, item_id in zip(targets or [], target_ids or []):
            if item_id["path"] in current:
                current[item_id["path"]]["target_smiles"] = str(value or "").strip()
        for value, item_id in zip(labels or [], label_ids or []):
            if item_id["path"] in current:
                current[item_id["path"]]["label"] = str(value or "").strip()
        for field, values, item_ids in (
            ("model_iteration", model_iterations, model_iteration_ids),
            ("simulation_condition", conditions, condition_ids),
            ("replicate", replicates, replicate_ids),
        ):
            for value, item_id in zip(values or [], item_ids or []):
                if item_id["path"] in current:
                    current[item_id["path"]][field] = str(value or "").strip()
        requests = []
        for item in current.values():
            request = dict(item)
            request.pop("source_origin", None)
            requests.append(request)
        try:
            payload = svc.compare_species_sources(requests, x_axis=axis or "step")
        except svc.ServiceError as exc:
            return (
                empty_chart_figure("暂时无法比较", str(exc.message)),
                [], [], exc.message, None, True,
            )
        fig = go.Figure()
        for index, curve in enumerate(payload["curves"], 1):
            fig.add_trace(go.Scatter(x=curve["x_values"], y=curve["values"], mode="lines", name=f"{index}. {curve['label']}",
                                     customdata=[curve["target_smiles"]] * len(curve["values"]),
                                     hovertemplate="%{fullData.name}<br>%{customdata}<br>%{x}: %{y}<extra></extra>"))
        fig.update_layout(xaxis_title=payload["x_name"], yaxis_title="丰度", template="plotly_white", hovermode="x unified")
        columns = ui.columns([{"name": name, "id": field} for field, name in [
            ("label", "来源"),
            ("target_smiles", "精确 SMILES"),
            ("status_text", "状态"),
            ("time_basis_text", "时间口径"),
            ("initial", "初始值"),
            ("final", "末值"),
            ("peak", "峰值"),
            ("peak_time", "峰值时间"),
            ("message", "说明"),
        ]])
        comparability = payload.get("comparability") or {}
        alert = dbc.Alert(
            str(comparability.get("message") or "对比完成"),
            color=(
                "warning"
                if comparability.get("status") == "not_comparable"
                else "info"
            ),
            className="py-1 px-2 mb-0",
        )
        return fig, payload["summary"], columns, alert, payload, False

    @app.callback(
        Output("species-compare-switch-btn", "disabled"),
        Input("species-compare-summary", "selectedRows"),
        State("species-compare-summary", "rowData"),
    )
    def _compare_switch_disabled(selected, rows):
        row = _selected_table_row(selected, rows)
        return not bool(row and row.get("target_smiles") and row.get("status") in {"ready", "zero"})

    @app.callback(
        Output("species-compare-switch-request", "data"),
        Output("species-detail-handoff", "data", allow_duplicate=True),
        Output("species-compare-switch-status", "children"),
        Input("species-compare-switch-btn", "n_clicks"),
        State("species-compare-summary", "selectedRows"),
        State("species-compare-summary", "rowData"),
        State("dataset-library", "data"),
        State("app-store", "data"),
        prevent_initial_call=True,
    )
    def _switch_to_compared_species(clicks, selected, rows, library, dataset):
        if not clicks:
            raise PreventUpdate
        row = _selected_table_row(selected, rows)
        if not row or row.get("status") not in {"ready", "zero"}:
            return None, no_update, "请选择一个有精确物种结果的来源。"
        target = str(row.get("target_smiles") or "").strip()
        dataset_id = str(row.get("dataset_id") or "")
        expected_revision = str(row.get("source_revision") or "")
        if not target or not dataset_id or not expected_revision:
            return None, no_update, "比较结果缺少精确目标或来源修订，请重新比较。"
        origin_frame = {"page": "evolution", "compare_sources": True,
                        "independent": True, "focus": {}, "context": None}
        store = dataset or {}
        current_revision = store.get("source_revision") or {}
        if (dataset_id == str(store.get("dataset_id") or "")
                and expected_revision == str(current_revision.get("fingerprint") or "")):
            return None, {"species": target, "formula": "", "context": context_key(store),
                          "origin_frame": origin_frame, "request_id": time.time_ns()}, ""
        candidate = next((item for item in svc.normalise_dataset_library(library)
                          if str(item.get("dataset_id") or "") == dataset_id), None)
        if not candidate:
            return None, no_update, "该来源尚未导入 RNG 数据列表，请先导入后再切换。"
        return ({"candidate": candidate, "target_smiles": target,
                 "expected_source_revision": expected_revision,
                 "origin_frame": origin_frame, "request_id": str(time.time_ns())},
                no_update, f"正在核验并切换到 {row.get('label') or '所选来源'}…")

    @app.callback(
        Output("species-detail-handoff", "data", allow_duplicate=True),
        Output("species-compare-switch-status", "children", allow_duplicate=True),
        Input("dataset-context-commit", "data"),
        State("dataset-switch-transaction", "data"),
        prevent_initial_call=True,
    )
    def _finish_compared_species_switch(marker, transaction):
        request = transaction or {}
        origin = request.get("origin") or {}
        if (not marker or request.get("state") != "succeeded"
                or marker.get("request_id") != request.get("request_id")
                or not origin.get("target_smiles")):
            raise PreventUpdate
        new_store = svc.current_dataset_from_validation(request.get("validation") or {})
        fingerprint = str((new_store.get("source_revision") or {}).get("fingerprint") or "")
        if fingerprint != str(origin.get("expected_source_revision") or ""):
            return no_update, "来源已切换，但修订与比较时不同；请重新比较后再分析该物种。"
        return ({"species": origin["target_smiles"], "formula": "",
                 "context": context_key(new_store), "origin_frame": origin.get("origin_frame"),
                 "request_id": request.get("request_id")}, "")

    @app.callback(
        Output("species-compare-download", "data"),
        Input("species-compare-export", "n_clicks"),
        State("species-compare-result-store", "data"),
        prevent_initial_call=True,
    )
    def _export_species_compare(_clicks, payload):
        if not payload:
            raise PreventUpdate
        return dcc.send_bytes(svc.species_comparison_zip(payload), "species_comparison.zip")
