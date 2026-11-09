"""Species search, detail panel, cross-entry handoff and species CSV callbacks."""

from __future__ import annotations

from .. import ui_components as ui
from ..ui_state import species_query
import time
from typing import Any
import dash_bootstrap_components as dbc
from dash import (
    ALL,
    Input,
    Output,
    State,
    ctx,
    html,
    no_update,
)
from dash.exceptions import PreventUpdate
from reacnet_scope import services as svc
from scripts.webapp_dash.candidate_workbench import context_key
from scripts.webapp_dash.navigation import (
    PAGE_LABELS,
    PAGE_WORKSPACES,
    migrate_page_state,
    resolve_page_id,
)

from .common import (
    _dt_columns,
    _fmt_num,
    _selected_table_row,
    _species_preview_tooltips,
)


def _species_identity_context_panel(detail: dict[str, Any]) -> Any:
    """Render explicit RNG identity/provenance without inferring missing settings."""

    context = detail.get("identity_context") or {}
    processing = context.get("processing") or {}
    fields = processing.get("fields") or {}
    molecular = context.get("molecular_evidence") or {}
    labels = (
        ("miso", "miso"),
        ("run_hmm", "HMM"),
        ("step_interval", "step interval"),
        ("timestep_ps", "timestep (ps)"),
        ("reacnetgenerator_version", "RNG 版本"),
        ("rng_source_branch", "RNG 分支"),
        ("rng_source_revision", "RNG 修订"),
    )

    def display_value(value: Any) -> str:
        if value is None or value == "":
            return "未知（未显式记录）"
        if isinstance(value, bool):
            return "是" if value else "否"
        return str(value)

    setting_rows: list[Any] = []
    for key, label in labels:
        field = fields.get(key) or {}
        source = str(field.get("source") or "")
        setting_rows.extend(
            (
                html.Dt(label),
                html.Dd(
                    [
                        html.Span(display_value(field.get("value"))),
                        html.Code(
                            source,
                            title=source,
                            className="rs-provenance-source",
                        )
                        if source
                        else None,
                    ]
                ),
            )
        )

    reaction_source = str(context.get("reaction_source") or "")
    molecular_source = str(molecular.get("source") or "")
    return html.Details(
        [
            html.Summary("身份、处理设置与 Molecular Evidence"),
            html.P(
                [
                    "精确 RNG Species：",
                    html.Code(
                        str(context.get("rng_species_label") or detail.get("smiles") or "")
                    ),
                    "。分子式仅用于检索与分组，不代替结构身份。",
                ],
                className="rs-step-note mt-2",
            ),
            html.Dl(
                [
                    html.Dt("Reaction Source"),
                    html.Dd(
                        html.Code(reaction_source, title=reaction_source)
                        if reaction_source
                        else "未知（未显式记录）"
                    ),
                    *setting_rows,
                    html.Dt("Molecular Evidence"),
                    html.Dd(
                        [
                            html.Span(
                                str(
                                    molecular.get("message")
                                    or "当前没有可说明的 Molecular Evidence。"
                                )
                            ),
                            html.Code(
                                molecular_source,
                                title=molecular_source,
                                className="rs-provenance-source",
                            )
                            if molecular_source
                            else None,
                        ]
                    ),
                ],
                className="mt-2",
            ),
        ],
        className="rs-species-provenance mt-2",
    )


def _species_detail_children(detail: dict[str, Any], smiles: str) -> list[Any]:
    """Render the one exact Species detail used by search and cross-entry drilldown."""
    formula = detail.get("formula") or "?"
    smiles_value = detail.get("smiles") or smiles
    from ..lewis_workbench import annotation_panel
    info_panel = html.Div(
        [
            html.Div(
                [html.Span(formula, className="rs-detail-formula"),
                 html.Code(smiles_value, className="rs-detail-smiles")],
                className="rs-detail-identity",
            ),
            html.Dl([
                html.Dt("精确质量"), html.Dd(_fmt_num(detail.get("exact_mass"))),
                html.Dt("标称质量"), html.Dd(_fmt_num(detail.get("nominal_mass"))),
                html.Dt("反应物通量"), html.Dd(_fmt_num(detail.get("tp_as_reactant"))),
                html.Dt("产物通量"), html.Dd(_fmt_num(detail.get("tp_as_product"))),
                html.Dt("总通量"), html.Dd(_fmt_num(detail.get("total_throughput"))),
                html.Dt("消耗反应数"), html.Dd(_fmt_num(detail.get("n_consume_rxns"))),
                html.Dt("生成反应数"), html.Dd(_fmt_num(detail.get("n_produce_rxns"))),
            ]),
            _species_identity_context_panel(detail),
            annotation_panel(detail.get("lewis_annotation")),
        ], className="rs-detail-stats",
    )
    svg_result = svc.render_species_svg(smiles)
    if svg_result.get("ok") and svg_result.get("svg"):
        structure_panel = html.Div(
            html.Iframe(srcDoc=_wrap_svg_doc(svg_result["svg"]),
                        style={"border": "none", "width": "100%", "height": "100%"}),
            className="rs-svg-wrap",
        )
    else:
        structure_panel = html.Div(
            svg_result.get("message") or "暂无可用结构图",
            className="rs-svg-wrap rs-empty",
        )
    return [structure_panel, info_panel]


def _species_return_label(frame: dict[str, Any]) -> str:
    source_entry = str(frame.get("entry") or PAGE_WORKSPACES.get(frame.get("page")))
    return f"← 返回{PAGE_LABELS.get(source_entry, '来处')}"


# ── Shared column factories ─────────────────────────────────────────


def _species_columns(query_kind: str = ""):
    if query_kind == "mass":
        return _dt_columns([
            {"field": "formula", "headerName": "候选分子式", "width": 120},
            {"field": "exact_mass", "headerName": "匹配精确质量", "width": 120, "type": "numericColumn"},
            {"field": "nominal_mass", "headerName": "匹配标称质量", "width": 110, "type": "numericColumn"},
            {"field": "mass_error", "headerName": "质量误差", "width": 100, "type": "numericColumn"},
            {"field": "ppm_error", "headerName": "误差 ppm", "width": 95, "type": "numericColumn"},
            {"field": "structure_count", "headerName": "结构数", "width": 85, "type": "numericColumn"},
            {"field": "smiles", "headerName": "代表 SMILES", "minWidth": 220},
            {"field": "tp_as_reactant", "headerName": "TP(反应物汇总)", "width": 125, "type": "numericColumn"},
            {"field": "tp_as_product", "headerName": "TP(产物汇总)", "width": 120, "type": "numericColumn"},
            {"field": "total_throughput", "headerName": "总通量汇总", "width": 110, "type": "numericColumn"},
        ])

    columns = [
        {"field": "formula", "headerName": "分子式", "width": 110},
        {"field": "smiles", "headerName": "SMILES", "flex": 2, "minWidth": 200},
        {"field": "exact_mass", "headerName": "精确质量", "width": 110, "type": "numericColumn"},
        {"field": "nominal_mass", "headerName": "标称质量", "width": 95, "type": "numericColumn"},
    ]
    columns.extend([
        {"field": "tp_as_reactant", "headerName": "TP(反应物)", "width": 105, "type": "numericColumn"},
        {"field": "tp_as_product", "headerName": "TP(产物)", "width": 100, "type": "numericColumn"},
        {"field": "total_throughput", "headerName": "总通量", "width": 100, "type": "numericColumn"},
        {"field": "n_consume_rxns", "headerName": "消耗反应", "width": 95, "type": "numericColumn"},
        {"field": "n_produce_rxns", "headerName": "生成反应", "width": 95, "type": "numericColumn"},
    ])
    return _dt_columns(columns)


def _wrap_svg_doc(svg: str) -> str:
    """Wrap an SVG string in a full HTML document with reset CSS and viewBox fix."""
    import re

    # Ensure the SVG has a viewBox attribute
    if "viewBox" not in svg:
        w_match = re.search(r'width=["\']?(\d+)', svg)
        h_match = re.search(r'height=["\']?(\d+)', svg)
        if w_match and h_match:
            w, h = w_match.group(1), h_match.group(1)
            svg = svg.replace("<svg", f'<svg viewBox="0 0 {w} {h}"', 1)

    return (
        "<!DOCTYPE html>\n"
        "<html><head><meta charset=\"utf-8\"><style>\n"
        "html,body{margin:0;padding:0;overflow:hidden;width:100%;height:100%}\n"
        "svg{max-width:100%;max-height:100%;display:block;margin:0 auto}\n"
        "</style></head><body>\n"
        + svg +
        "\n</body></html>"
    )


def register(app: Any) -> None:
    """Register species callbacks."""

    @ui.guarded_query(app,
        Output("species-grid", "rowData"),
        Output("species-grid", "columnDefs"),
        Output("species-grid-previews", "data"),
        Output("species-alert", "children"),
        Output("species-grid-store", "data"),
        Output("species-grid", "selectedRows"),
        Output("species-grid-page-size", "data"),
        Output("species-grid", "paginationGoTo"),
        Output("species-csv-btn", "children"),
        Output(
            "species-structure-results",
            "style",
            allow_duplicate=True,
        ),
        Output(
            "species-structure-title",
            "children",
            allow_duplicate=True,
        ),
        Output(
            "species-structure-alert",
            "children",
            allow_duplicate=True,
        ),
        Output(
            "species-structure-grid",
            "rowData",
            allow_duplicate=True,
        ),
        Output(
            "species-structure-grid",
            "columnDefs",
            allow_duplicate=True,
        ),
        Output(
            "species-structure-grid-previews",
            "data",
            allow_duplicate=True,
        ),
        Output(
            "species-structure-grid",
            "selectedRows",
            allow_duplicate=True,
        ),
        Output(
            "species-structure-grid",
            "paginationGoTo",
            allow_duplicate=True,
        ),
        Output(
            "species-structure-csv-btn",
            "disabled",
            allow_duplicate=True,
        ),
        Output("app-store", "data", allow_duplicate=True),
        Output("evolution-targets", "value", allow_duplicate=True),
        Input("species-search-btn", "n_clicks"),
        State("species-query", "value"),
        State("species-query-kind", "value"),
        State("species-mass-tol", "value"),
        State("app-store", "data"),
        prevent_initial_call=True,
        running=[
            (
                Output({"type": "dataset-bound-operation", "name": "species"}, "data"),
                True,
                False,
            ),
        ],
    )
    def _search_species(n_clicks, query, kind, mass_tol, store):
        if n_clicks is None:
            raise PreventUpdate
        cleared_structure_results = (
            {"display": "none"},
            "分子式对应结构",
            "",
            [],
            _species_columns(),
            [],
            [],
            0,
            True,
        )
        store = store or {}
        cleared_store = {
            **store,
            "selected_smiles": "",
            "selected_formula": "",
            "selected_species_source": "",
            # This callback executes a fresh species query, so preserved
            # inputs are no longer pending.  The shared marker callback sets
            # the same value; keeping them identical avoids a last-writer
            # race between callbacks triggered by the same button.
            "inputs_pending": False,
        }
        artifacts = store.get("artifacts", {}) or {}
        if not artifacts.get("reaction"):
            return (
                [],
                _species_columns(),
                [],
                '请先在「RNG 数据」中选择包含 reactionabcd 的数据文件夹。',
                {"rows": [], "state": "blocked", "message": "请先选择可检索物种的RNG 数据。"},
                [],
                50,
                0,
                "导出全部 CSV",
                *cleared_structure_results,
                cleared_store,
                "",
            )
        try:
            submitted = species_query(query, kind, mass_tol)
            result = svc.search_species(
                artifacts,
                query or "",
                kind=kind or "auto",
                mass_tolerance=submitted["mass_tolerance"],
            )
        except (svc.ServiceError, ValueError, TypeError) as exc:
            message = str(getattr(exc, "message", exc))
            return (
                [],
                _species_columns(),
                [],
                message,
                {"rows": [], "state": "error", "message": message},
                [],
                50,
                0,
                "导出全部 CSV",
                *cleared_structure_results,
                cleared_store,
                "",
            )

        rows = result.get("rows") or []
        query_kind = str(result.get("query_kind") or "")
        matching_count = int(result.get("n_rows") or len(rows))
        page_size = 20 if query_kind == "mass" else 50
        if rows:
            unit = "个匹配分子式" if query_kind == "mass" else "条匹配物种"
            message = f"找到 {matching_count} {unit}；每页显示 {page_size} 条"
            if query_kind == "mass":
                message += "；选择分子式可查看原始结构结果"
        else:
            message = ("未找到匹配物种；可以放宽质量容差。" if query_kind == "mass"
                       else "未找到匹配物种；请检查分子式、SMILES 或查询类型。")
        return (
            rows,
            _species_columns(query_kind),
            _species_preview_tooltips(rows),
            message,
            {
                "rows": rows,
                "context": context_key(store),
                "query_kind": query_kind,
                "n_rows": matching_count,
                "n_visible_rows": len(rows),
                "searched": True,
                "state": "ready" if rows else "empty",
                "query": submitted,
                "message": message,
            },
            [],
            page_size,
            0,
            (
                "导出全部分子式 CSV"
                if query_kind == "mass"
                else "导出全部结构 CSV"
            ),
            *cleared_structure_results,
            cleared_store,
            "",
        )

    @app.callback(
        Output("species-empty-copy", "children"),
        Output("species-empty-state", "style"),
        Output("species-results", "style"),
        Output("species-open-data-modal", "style"),
        Output("species-search-btn", "disabled"),
        Output("species-csv-btn", "disabled"),
        Output("species-query-card", "style"),
        Output("species-results-card", "style"),
        Input("app-store", "data"),
        Input("species-grid-store", "data"),
        Input({"type": "dataset-bound-operation", "name": "species"}, "data"),
    )
    def _update_species_state(store, grid_store, running):
        store = store or {}
        grid_store = grid_store or {}
        has_reaction_data = bool((store.get("artifacts") or {}).get("reaction"))
        rows = grid_store.get("rows") or []
        searched = bool(grid_store.get("searched"))

        if not has_reaction_data:
            if not store.get("dataset_id"):
                empty = [
                    html.H5("尚未选择当前RNG 数据", className="rs-empty-title"),
                    html.P("单来源物种检索需要选择一个RNG 数据；多来源趋势对比可直接选择多个已导入来源。",
                           className="rs-empty-copy"),
                ]
            else:
                empty = [
                    html.H5("当前RNG 数据缺少反应数据", className="rs-empty-title"),
                    html.P("物种检索需要 reactionabcd 数据；仍可对比其他已导入来源的物种趋势。",
                           className="rs-empty-copy"),
                ]
            return empty, {"display": "flex"}, {"display": "none"}, {}, True, True, {"display": "none"}, {}

        if (store.get("selected_species_source") == "cross_entry_detail"
                and store.get("selected_smiles")):
            # This detail is opened without a Species search result. Keep its
            # mounted card visible when app-store changes to the local focus.
            return ([], {"display": "none"}, {"display": "block"},
                    {"display": "none"}, False, not bool(rows),
                    {"display": "none"}, {"display": "block"})

        if not rows and not searched and not running and grid_store.get("state") not in {"error", "blocked"}:
            return [], {"display": "none"}, {"display": "none"}, {"display": "none"}, False, True, {}, {"display": "none"}

        if rows:
            return [], {"display": "none"}, {"display": "block"}, {"display": "none"}, bool(running), False, {}, {}

        if running:
            title, text = "正在查询", "请稍候，查询完成后更新结果。"
        elif grid_store.get("state") in {"error", "blocked"}:
            title = "查询失败" if grid_store["state"] == "error" else "暂时无法查询"
            text = grid_store.get("message", "请检查输入并重试。")
        elif searched:
            text = grid_store.get("message") or "未找到匹配物种；可以放宽质量容差或切换查询类型。"
            title = "没有匹配结果"
        empty = [
            html.Div(title, className="rs-empty-title"),
            html.P(text, className="rs-empty-copy"),
        ]
        return empty, {"display": "flex"}, {"display": "none"}, {"display": "none"}, bool(running), True, {}, {}

    @app.callback(
        Output("species-structure-results", "style"),
        Output("species-structure-title", "children"),
        Output("species-structure-alert", "children"),
        Output("species-structure-grid", "rowData"),
        Output("species-structure-grid", "columnDefs"),
        Output("species-structure-grid-previews", "data"),
        Output("species-structure-grid", "selectedRows"),
        Output("species-structure-grid", "paginationGoTo"),
        Output("species-structure-csv-btn", "disabled"),
        Output("app-store", "data", allow_duplicate=True),
        Input("species-grid", "selectedRows"),
        State("species-grid", "rowData"),
        State("species-grid-store", "data"),
        State("app-store", "data"),
        prevent_initial_call=True,
        running=[
            (
                Output(
                    {
                        "type": "dataset-bound-operation",
                        "name": "species-structures",
                    },
                    "data",
                ),
                True,
                False,
            ),
        ],
    )
    def _load_mass_formula_structures(
        selected_rows,
        formula_rows,
        grid_store,
        store,
    ):
        if str((grid_store or {}).get("query_kind") or "") != "mass":
            return (
                {"display": "none"},
                "分子式对应结构",
                "",
                [],
                _species_columns(),
                [],
                [],
                0,
                True,
                no_update,
            )
        row = _selected_table_row(selected_rows, formula_rows)
        formula = str((row or {}).get("formula") or "").strip()
        current_store = store if isinstance(store, dict) else {}
        cleared_store = {
            **current_store,
            "selected_smiles": "",
            "selected_formula": "",
            "selected_species_source": "",
        }
        if not formula:
            return (
                {"display": "none"},
                "分子式对应结构",
                "选择一个候选分子式以查看原始结构结果。",
                [],
                _species_columns(),
                [],
                [],
                0,
                True,
                cleared_store,
            )
        artifacts = ((store or {}).get("artifacts") or {})
        try:
            result = svc.search_species(
                artifacts,
                formula,
                kind="formula",
            )
        except svc.ServiceError as exc:
            return (
                {"display": "block"},
                f"{formula} 的结构结果",
                str(exc.message),
                [],
                _species_columns(),
                [],
                [],
                0,
                True,
                cleared_store,
            )
        rows = result.get("rows") or []
        total = int(result.get("n_rows") or len(rows))
        message = f"共 {total} 个结构；每页显示 50 条"
        return (
            {"display": "block"},
            f"{formula} 的结构结果",
            message,
            rows,
            _species_columns(),
            _species_preview_tooltips(rows),
            [],
            0,
            not bool(rows),
            cleared_store,
        )

    @app.callback(
        Output("species-workspace-stage", "data"),
        Input("species-search-btn", "n_clicks"),
        Input("species-grid", "selectedRows"),
        Input("species-structure-grid", "selectedRows"),
        Input("species-stage-results-btn", "n_clicks"),
        Input("species-stage-structures-btn", "n_clicks"),
        Input("species-stage-detail-btn", "n_clicks"),
        Input("species-stage-back-btn", "n_clicks"),
        State("species-workspace-stage", "data"),
        State("species-grid-store", "data"),
        State("species-structure-grid", "rowData"),
        prevent_initial_call=True,
    )
    def _select_species_workspace_stage(
        _search_clicks,
        formula_selected_rows,
        structure_selected_rows,
        _results_clicks,
        _structures_clicks,
        _detail_clicks,
        _back_clicks,
        current_stage,
        grid_store,
        structure_rows,
    ):
        triggered_id = ctx.triggered_id
        query_kind = str((grid_store or {}).get("query_kind") or "")
        is_mass_search = query_kind == "mass"
        has_structures = is_mass_search and bool(structure_rows)
        has_detail = bool(
            structure_selected_rows
            if is_mass_search
            else formula_selected_rows
        )

        if triggered_id in {"species-search-btn", "species-stage-results-btn"}:
            return "results"
        if triggered_id == "species-grid":
            if not formula_selected_rows:
                return "results"
            return "structures" if is_mass_search else "detail"
        if triggered_id == "species-structure-grid":
            if structure_selected_rows:
                return "detail"
            return "structures" if has_structures else "results"
        if triggered_id == "species-stage-structures-btn":
            if has_structures:
                return "structures"
            raise PreventUpdate
        if triggered_id == "species-stage-detail-btn":
            if has_detail:
                return "detail"
            raise PreventUpdate
        if triggered_id == "species-stage-back-btn":
            if current_stage == "detail" and has_structures:
                return "structures"
            if current_stage in {"detail", "structures"}:
                return "results"
        raise PreventUpdate

    @app.callback(
        Output("species-query-card", "style"),
        Output("species-result-stage", "style"),
        Output("species-structure-stage", "style"),
        Output("species-detail-stage", "style"),
        Output("species-stage-results-btn", "active"),
        Output("species-stage-structures-btn", "active"),
        Output("species-stage-detail-btn", "active"),
        Output("species-stage-results-btn", "children"),
        Output("species-stage-structures-btn", "children"),
        Output("species-stage-structures-btn", "disabled"),
        Output("species-stage-detail-btn", "disabled"),
        Output("species-stage-back-btn", "children"),
        Output("species-stage-back-btn", "disabled"),
        Output("species-stage-back-btn", "style"),
        Input("species-workspace-stage", "data"),
        Input("species-grid-store", "data"),
        Input("species-structure-grid", "rowData"),
        Input("species-grid", "selectedRows"),
        Input("species-structure-grid", "selectedRows"),
        Input("species-detail-handoff", "data"),
    )
    def _render_species_workspace_stage(
        requested_stage,
        grid_store,
        structure_rows,
        formula_selected_rows,
        structure_selected_rows,
        external_handoff,
    ):
        is_mass_search = (
            str((grid_store or {}).get("query_kind") or "") == "mass"
        )
        has_structures = is_mass_search and bool(structure_rows)
        has_detail = bool(
            structure_selected_rows
            if is_mass_search
            else formula_selected_rows
        ) or bool((external_handoff or {}).get("species"))
        stage = str(requested_stage or "results")
        if stage == "structures" and not has_structures:
            stage = "results"
        elif stage == "detail" and not has_detail:
            stage = "structures" if has_structures else "results"

        result_count = int(
            (grid_store or {}).get("n_visible_rows")
            or len((grid_store or {}).get("rows") or [])
        )
        result_name = "候选分子式" if is_mass_search else "检索结果"
        result_label = (
            f"{result_name} · {result_count}" if result_count else result_name
        )
        structure_label = (
            f"结构列表 · {len(structure_rows or [])}"
            if has_structures
            else "结构列表"
        )
        back_label = (
            "← 返回结构列表"
            if stage == "detail" and has_structures
            else f"← 返回{result_name}"
        )
        back_visible = stage != "results"
        return (
            {} if stage == "results" else {"display": "none"},
            {} if stage == "results" or (not is_mass_search and result_count > 0) else {"display": "none"},
            {} if is_mass_search and stage == "structures" else {"display": "none"},
            {} if stage == "detail" else {"display": "none"},
            stage == "results",
            stage == "structures",
            stage == "detail",
            result_label,
            structure_label,
            not has_structures,
            not has_detail,
            back_label,
            not back_visible,
            {} if back_visible else {"display": "none"},
        )

    # ── Species detail panel ────────────────────────────────────────

    @app.callback(
        Output("detail-panel", "style"),
        Output("detail-body", "style"),
        Output("detail-body", "children"),
        Output("detail-empty", "style"),
        Output("species-to-channels-btn", "disabled"),
        Output("species-to-evolution-btn", "disabled"),
        Output("app-store", "data", allow_duplicate=True),
        Output("evolution-targets", "value"),
        Input("species-grid", "selectedRows"),
        Input("species-structure-grid", "selectedRows"),
        State("species-grid", "rowData"),
        State("species-structure-grid", "rowData"),
        State("app-store", "data"),
        State("species-grid-store", "data"),
        prevent_initial_call=True,
        running=[
            (
                Output(
                    {
                        "type": "dataset-bound-operation",
                        "name": "species-detail",
                    },
                    "data",
                ),
                True,
                False,
            ),
        ],
    )
    def _show_species_detail(
        formula_selected_rows,
        structure_selected_rows,
        formula_rows,
        structure_rows,
        store,
        grid_store,
    ):
        store = store or {}
        is_mass_search = str((grid_store or {}).get("query_kind") or "") == "mass"
        if is_mass_search:
            selected_rows = structure_selected_rows
            table_rows = structure_rows or []
            selected_source = "mass_structure"
            if ctx.triggered_id == "species-grid":
                selected_rows = []
        else:
            selected_rows = formula_selected_rows
            table_rows = formula_rows or (grid_store or {}).get("rows") or []
            selected_source = "species_grid"
        if not selected_rows or len(selected_rows) == 0:
            cleared_store = {
                **store,
                "selected_smiles": "",
                "selected_formula": "",
                "selected_species_source": "",
            }
            return (
                {"display": "none"},
                {"display": "none"},
                [],
                {"display": "block"},
                True,
                True,
                cleared_store if store.get("selected_smiles") else no_update,
                "",
            )
        row = _selected_table_row(selected_rows, table_rows)
        if row is None:
            raise PreventUpdate
        smiles = (row.get("smiles") or "").strip()
        if not smiles:
            cleared_store = {
                **store,
                "selected_smiles": "",
                "selected_formula": "",
                "selected_species_source": "",
            }
            return (
                {"display": "none"},
                {"display": "none"},
                [],
                {"display": "block"},
                True,
                True,
                cleared_store if store.get("selected_smiles") else no_update,
                "",
            )
        artifacts = store.get("artifacts", {}) or {}
        try:
            detail = svc.species_detail(artifacts, smiles)
        except svc.ServiceError:
            detail = {"ok": True, "smiles": smiles, "formula": row.get("formula") or "?"}

        svg_result = svc.render_species_svg(smiles)

        formula = detail.get("formula") or "?"
        smiles_value = detail.get("smiles") or smiles
        evolution_target = f"smiles:{smiles}"

        from ..lewis_workbench import annotation_panel
        info_panel = html.Div(
            [
                html.Div(
                    [
                        html.Span(formula, className="rs-detail-formula"),
                        html.Code(smiles_value, className="rs-detail-smiles"),
                    ],
                    className="rs-detail-identity",
                ),
                html.Dl(
                    [
                        html.Dt("精确质量"),
                        html.Dd(_fmt_num(detail.get("exact_mass"))),
                        html.Dt("标称质量"),
                        html.Dd(_fmt_num(detail.get("nominal_mass"))),
                        html.Dt("反应物通量"),
                        html.Dd(_fmt_num(detail.get("tp_as_reactant"))),
                        html.Dt("产物通量"),
                        html.Dd(_fmt_num(detail.get("tp_as_product"))),
                        html.Dt("总通量"),
                        html.Dd(_fmt_num(detail.get("total_throughput"))),
                        html.Dt("消耗反应数"),
                        html.Dd(_fmt_num(detail.get("n_consume_rxns"))),
                        html.Dt("生成反应数"),
                        html.Dd(_fmt_num(detail.get("n_produce_rxns"))),
                    ]
                ),
                _species_identity_context_panel(detail),
                annotation_panel(detail.get("lewis_annotation")),
            ],
            className="rs-detail-stats",
        )

        if svg_result.get("ok") and svg_result.get("svg"):
            svg_raw = svg_result["svg"]
            svg_wrapped = _wrap_svg_doc(svg_raw)
            structure_panel = html.Div(
                html.Iframe(
                    srcDoc=svg_wrapped,
                    style={"border": "none", "width": "100%", "height": "100%"},
                ),
                className="rs-svg-wrap",
            )
        elif svg_result.get("message"):
            structure_panel = html.Div(svg_result["message"], className="rs-svg-wrap rs-empty")
        else:
            structure_panel = html.Div("暂无可用结构图", className="rs-svg-wrap rs-empty")

        children = [structure_panel, info_panel]

        updated_store = {
            **store,
            "selected_smiles": smiles,
            "selected_formula": formula,
            "selected_species_source": selected_source,
        }
        return (
            {"display": "block"},
            {"display": "grid"},
            children,
            {"display": "none"},
            False,
            False,
            updated_store,
            evolution_target,
        )

    # ── CSV export: species ─────────────────────────────────────────

    @app.callback(
        Output("species-csv-download", "data"),
        Input("species-csv-btn", "n_clicks"),
        State("species-grid-store", "data"),
        prevent_initial_call=True,
    )
    def _export_species_csv(n_clicks, grid_store):
        if n_clicks is None:
            raise PreventUpdate
        grid_store = grid_store or {}
        rows = grid_store.get("rows") or []
        if not rows:
            raise PreventUpdate
        import csv
        import io

        buf = io.StringIO()
        if grid_store.get("query_kind") == "mass":
            keys = [
                "formula",
                "exact_mass",
                "nominal_mass",
                "mass_error",
                "ppm_error",
                "structure_count",
                "smiles",
                "tp_as_reactant",
                "tp_as_product",
                "total_throughput",
            ]
            filename = "mass_formula_search.csv"
        else:
            keys = ["smiles", "formula", "exact_mass", "nominal_mass", "tp_as_reactant", "tp_as_product", "total_throughput", "n_consume_rxns", "n_produce_rxns"]
            filename = "species_search.csv"
        writer = csv.DictWriter(buf, fieldnames=keys, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
        return {"content": buf.getvalue(), "filename": filename, "type": "text/csv"}

    @app.callback(
        Output("species-structure-csv-download", "data"),
        Input("species-structure-csv-btn", "n_clicks"),
        State("species-structure-grid", "rowData"),
        prevent_initial_call=True,
    )
    def _export_species_structure_csv(n_clicks, rows):
        if n_clicks is None or not rows:
            raise PreventUpdate
        import csv
        import io

        buf = io.StringIO()
        keys = [
            "smiles",
            "formula",
            "exact_mass",
            "nominal_mass",
            "tp_as_reactant",
            "tp_as_product",
            "total_throughput",
            "n_consume_rxns",
            "n_produce_rxns",
        ]
        writer = csv.DictWriter(buf, fieldnames=keys, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
        formula = str((rows[0] or {}).get("formula") or "formula")
        return {
            "content": buf.getvalue(),
            "filename": f"{formula}_structures.csv",
            "type": "text/csv",
        }

    @app.callback(
        Output("species-detail-handoff", "data"),
        Input({"type": "species-open-detail", "scope": ALL, "side": ALL,
               "index": ALL, "smiles": ALL, "formula": ALL}, "n_clicks"),
        Input("evolution-rank-open-btn", "n_clicks"),
        Input("evolution-group-member-open-btn", "n_clicks"),
        Input("element-distribution-open-species-btn", "n_clicks"),
        State("evolution-rank-grid", "selectedRows"),
        State("evolution-rank-grid", "rowData"),
        State("evolution-group-members-grid", "selectedRows"),
        State("evolution-group-members-grid", "rowData"),
        State("element-distribution-composition-table", "selectedRows"),
        State("element-distribution-composition-table", "rowData"),
        State("app-store", "data"),
        prevent_initial_call=True,
    )
    def _request_species_detail(_participants, rank_clicks, group_clicks, distribution_clicks,
                                rank_selected, rank_rows, group_selected, group_rows,
                                distribution_selected, distribution_rows, dataset):
        triggered = ctx.triggered_id
        if isinstance(triggered, dict):
            triggered_clicks = [item.get("value") for item in ctx.triggered
                                if "species-open-detail" in str(item.get("prop_id") or "")]
            click_values = triggered_clicks if triggered_clicks else (_participants or [])
            if not any(isinstance(value, (int, float)) and value > 0
                       for value in click_values):
                raise PreventUpdate
            smiles = str(triggered.get("smiles") or "").strip()
            formula = str(triggered.get("formula") or "").strip()
        elif triggered == "evolution-rank-open-btn" and rank_clicks:
            row = _selected_table_row(rank_selected, rank_rows)
            smiles, formula = str((row or {}).get("smiles") or ""), str((row or {}).get("formula") or "")
        elif triggered == "evolution-group-member-open-btn" and group_clicks:
            row = _selected_table_row(group_selected, group_rows)
            smiles, formula = str((row or {}).get("smiles") or ""), str((row or {}).get("formula") or "")
        elif triggered == "element-distribution-open-species-btn" and distribution_clicks:
            row = _selected_table_row(distribution_selected, distribution_rows)
            smiles, formula = str((row or {}).get("smiles") or ""), str((row or {}).get("formula") or "")
        else:
            raise PreventUpdate
        if not smiles:
            raise PreventUpdate
        return {"species": smiles, "formula": formula, "context": context_key(dataset),
                "request_id": time.time_ns()}

    @app.callback(
        Output("app-store", "data", allow_duplicate=True),
        Output("species-workspace-stage", "data", allow_duplicate=True),
        Output("species-results-card", "style", allow_duplicate=True),
        Output("species-results", "style", allow_duplicate=True),
        Output("detail-panel", "style", allow_duplicate=True),
        Output("detail-body", "style", allow_duplicate=True),
        Output("detail-body", "children", allow_duplicate=True),
        Output("detail-empty", "style", allow_duplicate=True),
        Output("evolution-targets", "value", allow_duplicate=True),
        Output("page-store", "data", allow_duplicate=True),
        Output("navigation-command", "data", allow_duplicate=True),
        Output("species-handoff-back-btn", "style", allow_duplicate=True),
        Output("species-handoff-back-btn", "children", allow_duplicate=True),
        Output("species-query-card", "style", allow_duplicate=True),
        Output("species-result-stage", "style", allow_duplicate=True),
        Output("species-structure-stage", "style", allow_duplicate=True),
        Output("species-detail-stage", "style", allow_duplicate=True),
        Input("species-detail-handoff", "data"),
        State("app-store", "data"),
        State("page-store", "data"),
        prevent_initial_call=True,
    )
    def _open_species_from_another_entry(handoff, dataset, page):
        store = dataset or {}
        if not isinstance(handoff, dict) or handoff.get("context") != context_key(store):
            raise PreventUpdate
        smiles = str(handoff.get("species") or "").strip()
        if not smiles:
            raise PreventUpdate
        try:
            detail = svc.species_detail(store.get("artifacts") or {}, smiles)
        except svc.ServiceError:
            detail = {"smiles": smiles, "formula": handoff.get("formula") or "?"}
        formula = str(detail.get("formula") or handoff.get("formula") or "?")
        state = migrate_page_state(page)
        frame = {
            "page": state["page"], "entry": state.get("entry"),
            "compare_sources": state.get("compare_sources"),
            "context": context_key(store),
            "focus": {key: store.get(key) for key in
                      ("selected_smiles", "selected_formula", "selected_species_source")},
        }
        if isinstance(handoff.get("origin_frame"), dict):
            frame = dict(handoff["origin_frame"])
        stack = [*(state.get("return_stack") or []), frame][-4:]
        back_label = _species_return_label(frame)
        updated_store = {**store, "selected_smiles": smiles,
                         "selected_formula": formula,
                         "selected_species_source": "cross_entry_detail"}
        return (updated_store, "detail", {"display": "block"}, {"display": "block"},
                {"display": "block"}, {"display": "grid"},
                _species_detail_children(detail, smiles), {"display": "none"},
                f"smiles:{smiles}", {"page": "species", "version": 2, "return_stack": stack},
                {"page": "species", "token": handoff.get("request_id")},
                {}, back_label, {"display": "none"}, {"display": "none"},
                {"display": "none"}, {})

    @app.callback(
        Output("species-handoff-back-btn", "style"),
        Output("species-handoff-back-btn", "children"),
        Input("page-store", "data"),
    )
    def _render_species_handoff_back(page):
        state = migrate_page_state(page)
        stack = state.get("return_stack") or []
        if state["page"] != "species" or not stack:
            return {"display": "none"}, "← 返回来处"
        return {}, _species_return_label(stack[-1])

    @app.callback(
        Output("page-store", "data", allow_duplicate=True),
        Output("app-store", "data", allow_duplicate=True),
        Output("species-detail-handoff", "data", allow_duplicate=True),
        Output("species-query-feedback", "children", allow_duplicate=True),
        Output("navigation-command", "data", allow_duplicate=True),
        Output("species-handoff-back-btn", "style", allow_duplicate=True),
        Output("species-handoff-back-btn", "children", allow_duplicate=True),
        Output("species-workspace-stage", "data", allow_duplicate=True),
        Output("species-query-card", "style", allow_duplicate=True),
        Output("species-result-stage", "style", allow_duplicate=True),
        Output("species-structure-stage", "style", allow_duplicate=True),
        Output("species-detail-stage", "style", allow_duplicate=True),
        Output("detail-panel", "style", allow_duplicate=True),
        Input("species-handoff-back-btn", "n_clicks"),
        State("page-store", "data"),
        State("app-store", "data"),
        prevent_initial_call=True,
    )
    def _return_from_species_detail(clicks, page, dataset):
        state = migrate_page_state(page)
        stack = list(state.get("return_stack") or [])
        if not clicks or not stack:
            raise PreventUpdate
        frame = stack.pop()
        store = dataset or {}
        if not frame.get("independent") and frame.get("context") != context_key(store):
            return ({"page": "species", "version": 2}, no_update, None,
                    dbc.Alert("来源或修订已变化，旧结果无法恢复。", color="warning"),
                    {"page": "species", "token": time.time_ns()},
                    {"display": "none"}, "← 返回来处", "results", {}, {},
                    {"display": "none"}, {"display": "none"}, {"display": "none"})
        focus = frame.get("focus") or {}
        updated_store = (no_update if frame.get("independent") else
                         {**store, **{key: focus.get(key) or "" for key in
                                      ("selected_smiles", "selected_formula", "selected_species_source")}})
        previous = {"page": resolve_page_id(frame.get("page")), "version": 2}
        if frame.get("entry"):
            previous["entry"] = frame["entry"]
        if frame.get("compare_sources"):
            previous["compare_sources"] = True
        if stack:
            previous["return_stack"] = stack
        return previous, updated_store, None, no_update, {
            "page": previous["page"], "entry": previous.get("entry"), "token": time.time_ns(),
        }, ({"display": "none"} if previous["page"] != "species" or not stack else {}), (
            _species_return_label(stack[-1]) if stack else "← 返回来处"
        ), "results", {}, {}, {"display": "none"}, {"display": "none"}, {"display": "none"}
