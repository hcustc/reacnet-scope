"""Object selection for abundance curves; results retain their source context."""

import math
import re
from urllib.parse import quote

from dash import ALL, Input, Output, State, ctx, dcc, html, no_update
from dash.exceptions import PreventUpdate
import dash_bootstrap_components as dbc

from reacnet_scope import services as svc
from .candidate_workbench import context_key
from . import ui_components as ui
from .navigation import migrate_page_state


def element_controls(prefix):
    return html.Details([
        html.Summary("元素条件 · 包含、排除、原子数范围"),
        html.Div([
            dbc.Label("必须包含"), dcc.Dropdown(id=f"{prefix}-required", multi=True, options=[], value=[], placeholder="例如 C、Cl"),
            dbc.Label("必须排除"), dcc.Dropdown(id=f"{prefix}-excluded", multi=True, options=[], value=[], placeholder="例如 N、S"),
            dbc.Label("原子数范围（可选）"),
            html.Div([
                dcc.Dropdown(id=f"{prefix}-element", options=[], value=None, placeholder="元素"),
                dcc.Input(id=f"{prefix}-min", type="number", min=0, step=1, placeholder="最少"),
                dcc.Input(id=f"{prefix}-max", type="number", min=0, step=1, placeholder="最多"),
            ], className="rs-evolution-filter-range"),
        ], className="rs-evolution-filter-fields"),
    ], id=f"{prefix}-elements", className="rs-evolution-disclosure")


def element_filter_query(required, excluded, element, minimum, maximum):
    for bound in (minimum, maximum):
        if bound is not None and (not math.isfinite(float(bound)) or bound < 0 or float(bound) != int(bound)):
            raise ValueError("元素原子数须为非负整数")
    required, excluded = set(required or []), set(excluded or [])
    if required & excluded:
        raise ValueError("同一元素不能同时要求包含和排除")
    filters = {item: {"mode": "present"} for item in required}
    filters.update({item: {"mode": "absent"} for item in excluded})
    if minimum is not None or maximum is not None:
        if not element:
            raise ValueError("请先选择原子数范围对应的元素")
        if element in excluded:
            if minimum is not None and minimum > 0:
                raise ValueError("已排除的元素不能要求正原子数")
            maximum = 0
        if element in required:
            minimum = max(1, minimum or 0)
        filters[element] = {"mode": "range", "min": minimum, "max": maximum}
    return filters


def finder_layout(*, ranking):
    return html.Div([
        html.Div([
            html.Div([
                dcc.Dropdown(id="evolution-filter-kind", clearable=False, searchable=False, value="auto", options=[
                    {"label": "自动识别", "value": "auto"}, {"label": "完整分子式", "value": "formula"},
                    {"label": "元素原子数（C6）", "value": "element"},
                    {"label": "精确 RNG SMILES", "value": "smiles"}, {"label": "精确质量 / Da", "value": "mass"},
                ]),
                dcc.Input(id="evolution-filter-query", type="text", value="", placeholder="检索词；也可仅设置元素条件", debounce=False),
            ], className="rs-evolution-filter-fields"),
            html.Div([dbc.Label("质量容差 ± Da"), dcc.Input(id="evolution-filter-tolerance", type="number", min=0,
                       step="any", value=0.5)], id="evolution-filter-mass", style={"display": "none"}),
            element_controls("evolution-filter"),
            dbc.Button("筛选物种", id="evolution-filter-search", size="sm", outline=True, color="primary"),
            html.Div(id="evolution-filter-status", role="status", className="rs-evolution-filter-status"),
            html.Div([
                ui.result_grid("evolution-filter-grid", selection="multi", height=240, definitions=[
                    {"field": "formula_mass", "headerName": "物种 / 质量", "minWidth": 110,
                     "cellStyle": {"whiteSpace": "pre-line", "lineHeight": "16px", "fontSize": "12px"},
                     "tooltipComponent": "ScopeStructureTooltip", "tooltipField": "smiles"},
                    {"field": "smiles", "headerName": "精确结构", "minWidth": 130},
                ]),
                html.Div([
                    dbc.Button("上一页", id="evolution-filter-prev", size="sm", outline=True, disabled=True),
                    dbc.Button("下一页", id="evolution-filter-next", size="sm", outline=True, disabled=True),
                ], className="rs-query-row"),
                html.Div([
                    dbc.Button("替换当前目标", id="evolution-filter-replace", size="sm", outline=True, disabled=True),
                    dbc.Button("加入比较", id="evolution-filter-add", size="sm", color="primary", disabled=True),
                ], className="rs-query-row"),
            ], id="evolution-filter-results", style={"display": "none"}),
            dcc.Store(id="evolution-filter-result"),
        ], className="rs-evolution-filter-fields"),
        ranking,
    ], id="evolution-species-finder")


def _selected_species(selected, result):
    identities = set(selected.get("ids") or []) if isinstance(selected, dict) else {
        ui.row_identity(row) for row in selected or [] if isinstance(row, dict)
    }
    return [row["smiles"] for row in (result or {}).get("rows") or [] if ui.row_identity(row) in identities]


def _target_items(text):
    return list(dict.fromkeys(item.strip() for item in re.split(r"[,;\n]+", text or "") if item.strip()))


def _merge_targets(existing, pending, editing=None):
    items, additions = _target_items(existing), _target_items(pending)
    if editing:
        if editing not in items:
            raise ValueError("正在编辑的目标已变化，请重新选择。")
        if not additions:
            raise ValueError("请输入修改后的目标，或取消编辑。")
        index = items.index(editing)
        items[index:index + 1] = additions
    else:
        items.extend(additions)
    return list(dict.fromkeys(items))


def _editor_response(*, targets=no_update, entry=no_update, editor=no_update, clicks=no_update,
                     message=no_update, opened=no_update, batch=no_update, batch_message=no_update):
    picker = [] if targets is not no_update else no_update
    return targets, entry, editor, clicks, message, opened, batch, batch_message, picker


def register_callbacks(app):
    @app.callback(
        Output("evolution-rank-request", "data"),
        Input("evolution-rank-grid", "selectedRows"),
        State("evolution-rank-store", "data"), State("app-store", "data"),
        prevent_initial_call=True,
    )
    def request_ranked_curve(selected, ranking, dataset):
        # Clearing a selection is UI cleanup, not another plot request. Even a
        # PreventUpdate in the plot callback would supersede its in-flight result.
        context = context_key(dataset or {})
        if not ranking or ranking.get("context") != context or not _selected_species(selected, ranking):
            raise PreventUpdate
        return {"selection": selected, "context": context}

    @app.callback(
        Output("evolution-finder-drawer", "is_open"), Output("evolution-settings-drawer", "is_open"),
        Input("evolution-open-finder", "n_clicks"), Input("evolution-open-settings", "n_clicks"),
        Input("evolution-settings-apply", "n_clicks"), Input("evolution-search-btn", "n_clicks"),
        Input("evolution-filter-replace", "n_clicks"), Input("evolution-filter-add", "n_clicks"),
        Input("page-store", "data"), Input("app-store", "data"),
        prevent_initial_call=True,
    )
    def drawers(_finder, _settings, _apply, _search, _replace, _add, _page, _dataset):
        return ctx.triggered_id == "evolution-open-finder", ctx.triggered_id == "evolution-open-settings"

    @app.callback(
        Output("evolution-targets", "value", allow_duplicate=True),
        Output("evolution-target-entry", "value"), Output("evolution-editor-state", "data"),
        Output("evolution-search-btn", "n_clicks", allow_duplicate=True),
        Output("evolution-target-message", "children"), Output("evolution-batch-modal", "is_open"),
        Output("evolution-batch-text", "value"), Output("evolution-batch-message", "children"),
        Output("evolution-species-picker", "value", allow_duplicate=True),
        Input("evolution-target-add", "n_clicks"), Input("evolution-target-entry", "n_submit"),
        Input("evolution-target-cancel", "n_clicks"), Input("evolution-query-run", "n_clicks"),
        Input("evolution-settings-apply", "n_clicks"),
        Input("evolution-batch-open", "n_clicks"), Input("evolution-batch-cancel", "n_clicks"), Input("evolution-batch-save", "n_clicks"),
        Input({"type": "evolution-target-remove", "target": ALL, "context": ALL}, "n_clicks"),
        Input({"type": "evolution-target-edit", "target": ALL, "context": ALL}, "n_clicks"),
        Input("evolution-filter-replace", "n_clicks"), Input("evolution-filter-add", "n_clicks"),
        Input("evolution-rank-request", "data"), Input("species-to-evolution-btn", "n_clicks"),
        Input("page-store", "data"), Input("app-store", "data"),
        State("evolution-targets", "value"), State("evolution-target-entry", "value"),
        State("evolution-editor-state", "data"), State("evolution-search-btn", "n_clicks"),
        State("evolution-batch-text", "value"), State("evolution-filter-grid", "selectedRows"),
        State("evolution-filter-result", "data"), prevent_initial_call=True,
    )
    def edit_targets(_add, _enter, _cancel, _run, _apply, _open, _batch_cancel, _save,
                     removals, edits, _replace, _select_add, rank_request, _shortcut, page, dataset,
                     existing, pending, editor, clicks, batch, selected, result):
        trigger = ctx.triggered_id
        context = context_key(dataset or {})
        clean = {"context": context, "editing": None}
        editor = editor or clean
        if trigger == "app-store":
            if editor.get("context") == context and editor.get("initialized"):
                raise PreventUpdate
            return _editor_response(entry="", editor={**clean, "initialized": True}, message="", opened=False, batch="", batch_message="")
        if trigger == "page-store":
            return _editor_response(opened=False, batch_message="")
        if editor.get("context") != context:
            return _editor_response(entry="", editor=clean, message="数据来源已变化，请重新输入。", opened=False)
        editing = editor.get("editing")
        actions = {
            "evolution-target-add": _add, "evolution-target-entry": _enter, "evolution-target-cancel": _cancel,
            "evolution-query-run": _run, "evolution-settings-apply": _apply, "evolution-batch-open": _open,
            "evolution-batch-cancel": _batch_cancel, "evolution-batch-save": _save,
            "evolution-filter-replace": _replace, "evolution-filter-add": _select_add,
            "species-to-evolution-btn": _shortcut,
        }
        if isinstance(trigger, str) and trigger != "evolution-rank-request" and not actions.get(trigger):
            raise PreventUpdate
        if trigger in ("species-to-evolution-btn", "evolution-rank-request"):
            if trigger == "evolution-rank-request" and (rank_request or {}).get("context") != context:
                raise PreventUpdate
            return _editor_response(entry="", editor={**clean, "initialized": True}, message="", opened=False)
        if trigger == "evolution-target-cancel":
            return _editor_response(entry="", editor={**clean, "initialized": True}, message="")
        if trigger == "evolution-batch-cancel":
            return _editor_response(opened=False, batch_message="")
        if isinstance(trigger, dict):
            is_edit = trigger.get("type") == "evolution-target-edit"
            if trigger.get("context") != context or not any((edits if is_edit else removals) or []):
                raise PreventUpdate
            target = trigger.get("target")
            items = _target_items(existing)
            if target not in items:
                raise PreventUpdate
            if is_edit:
                return _editor_response(entry=target, editor={**clean, "editing": target, "initialized": True}, message="")
            return _editor_response(targets="\n".join(item for item in items if item != target),
                                    entry="" if editing == target else no_update,
                                    editor={**clean, "initialized": True} if editing == target else no_update, message="")
        try:
            if trigger == "evolution-batch-open":
                items = _merge_targets(existing, pending, editing)
                return _editor_response(opened=True, batch="\n".join(items), batch_message="")
            if trigger == "evolution-batch-save":
                items = _target_items(batch)
            elif trigger in ("evolution-filter-replace", "evolution-filter-add"):
                if not result or result.get("context") != context:
                    raise PreventUpdate
                species = _selected_species(selected, result)
                if not species:
                    raise PreventUpdate
                items = _merge_targets(existing, pending, editing) if trigger == "evolution-filter-add" else []
                items = list(dict.fromkeys([*items, *(f"smiles:{value}" for value in species)]))
            else:
                if trigger in ("evolution-target-add", "evolution-target-entry") and not str(pending or "").strip():
                    raise PreventUpdate
                items = _merge_targets(existing, pending, editing)
            should_plot = trigger in ("evolution-query-run", "evolution-settings-apply", "evolution-filter-add", "evolution-filter-replace")
            if should_plot and not items:
                raise ValueError("请先添加查询目标。")
            text = "\n".join(items)
            svc.describe_abundance_targets(text)
        except ValueError as exc:
            if trigger == "evolution-batch-save":
                return _editor_response(opened=True, batch_message=str(exc))
            return _editor_response(message=str(exc))
        # Commit the text and the plot trigger in one response, so the query sees
        # the new target list even when the user did not press Add first.
        return _editor_response(targets=text, entry="", editor={**clean, "initialized": True},
                                clicks=int(clicks or 0) + 1 if should_plot else no_update,
                                message="", opened=False, batch_message="")

    @app.callback(Output("evolution-settings-apply", "disabled"), Output("evolution-query-run", "disabled"),
                  Input("evolution-search-btn", "disabled"))
    def settings_available(disabled):
        return (disabled is not False,) * 2

    @app.callback(Output("evolution-target-add", "children"), Output("evolution-target-cancel", "style"),
                  Input("evolution-editor-state", "data"), prevent_initial_call=True)
    def editing_state(editor):
        editing = bool((editor or {}).get("editing"))
        return ("保存" if editing else "添加"), ({} if editing else {"display": "none"})

    app.clientside_callback("""function(state) {
        if (state && state.editing) {
            requestAnimationFrame(() => {
                const input = document.getElementById('evolution-target-entry');
                if (input) { input.focus(); input.select(); }
            });
        }
        return window.dash_clientside.no_update;
    }""", Output("evolution-editor-focus", "data"), Input("evolution-editor-state", "data"))

    @app.callback(Output("evolution-target-meaning", "children"),
                  Output("evolution-target-preview", "children"), Output("evolution-target-count", "children"),
                  Input("evolution-targets", "value"), Input("app-store", "data"))
    def explain_targets(targets, dataset):
        raw_targets = _target_items(targets)
        context = context_key(dataset or {})
        try:
            items = svc.describe_abundance_targets("\n".join(raw_targets), structured=True)
        except ValueError as exc:
            return str(exc), html.Span(targets, className="rs-target-placeholder"), "请检查输入"
        if not items:
            return [], [], "尚未添加目标"
        chips = [html.Div([
            html.Button([
                *([html.Small({"formula": "分子式", "smiles": "结构"}[item["kind"]])]
                  if item["kind"] != "element" else []),
                html.Span(item["label"].removeprefix("formula:") if item["kind"] == "formula" else item["label"],
                          className="rs-target-chip-label"),
            ], id={"type": "evolution-target-edit", "target": raw, "context": context}, n_clicks=0,
               className="rs-target-chip-edit", title=f"编辑 {raw} · {item['description']}", **{"aria-label": f"编辑 {raw}"}),
            html.Button("×", id={"type": "evolution-target-remove", "target": raw, "context": context}, n_clicks=0,
                        className="rs-target-chip-remove", title=f"删除 {raw}", **{"aria-label": f"删除 {raw}"}),
        ], className="rs-target-chip") for raw, item in zip(raw_targets, items)]
        groups = {}
        for item in items:
            key = item.get("element") if item["kind"] == "element" else item["kind"]
            groups.setdefault(key, []).append(item)
        meanings = []
        for members in groups.values():
            first = members[0]
            if first["kind"] == "element" and len(members) > 1:
                counts = "、".join(str(item["count"]) for item in members)
                meaning = f"按 {first['element']} 原子数分别匹配：{counts}；其他元素不限"
            elif first["kind"] == "smiles":
                meaning = f"{len(members)} 个精确 RNG 结构"
            elif len(members) > 1:
                meaning = f"{len(members)} 个完整分子式"
            else:
                meaning = first["description"]
            meanings.append(html.Span(meaning))
        return meanings, chips, f"{len(items)} 个目标"

    @app.callback(Output("evolution-back-btn", "style"), Input("page-store", "data"))
    def back_visibility(page):
        state = migrate_page_state(page)
        return {} if state.get("entry") == "species" else {"display": "none"}

    @app.callback(
        *[Output(f"{prefix}-{field}", "options") for prefix in ("evolution-filter", "evolution-group-filter")
          for field in ("required", "excluded", "element")],
        Input("app-store", "data"),
        prevent_initial_call=True,
    )
    def options(dataset):
        status = svc.composition_index_status((dataset or {}).get("artifacts") or {})
        values = [{"label": element, "value": element} for element in status.get("available_elements") or []]
        return (values,) * 6

    @app.callback(Output("evolution-filter-mass", "style"), Input("evolution-filter-kind", "value"),
                  Input("evolution-filter-query", "value"))
    def mass_visibility(kind, query):
        mass = kind == "mass" or (kind == "auto" and query and svc.detect_query_kind(query) == "mass")
        return {} if mass else {"display": "none"}

    @app.callback(
        Output("evolution-filter-result", "data"), Output("evolution-filter-grid", "rowData"),
        Output("evolution-filter-grid", "selectedRows"), Output("evolution-filter-status", "children"),
        Output("evolution-filter-results", "style"), Output("evolution-filter-prev", "disabled"), Output("evolution-filter-next", "disabled"),
        Input("evolution-filter-search", "n_clicks"), Input("evolution-filter-query", "n_submit"),
        Input("evolution-filter-prev", "n_clicks"), Input("evolution-filter-next", "n_clicks"), Input("app-store", "data"),
        State("evolution-filter-query", "value"), State("evolution-filter-kind", "value"), State("evolution-filter-tolerance", "value"),
        *[State(f"evolution-filter-{field}", "value") for field in ("required", "excluded", "element", "min", "max")],
        State("evolution-filter-result", "data"), prevent_initial_call=True,
        running=[(Output("evolution-filter-search", "disabled"), True, False)],
    )
    def find(_click, _enter, _prev, _next, dataset, text, kind, tolerance, required, excluded, element, minimum, maximum, previous):
        context = context_key(dataset or {})
        if ctx.triggered_id == "app-store":
            if previous and previous.get("context") == context:
                raise PreventUpdate
            return None, [], [], "", {"display": "none"}, True, True
        try:
            offset = 0
            if ctx.triggered_id in {"evolution-filter-prev", "evolution-filter-next"}:
                if not previous or previous.get("context") != context:
                    raise PreventUpdate
                query = previous["submitted"]
                offset = max(0, previous["offset"] + (-50 if ctx.triggered_id.endswith("prev") else 50))
            else:
                query = {"query": text or "", "kind": kind or "auto", "mass_tolerance": 0.5 if tolerance is None else tolerance,
                         "element_filters": element_filter_query(required, excluded, element, minimum, maximum)}
            result = svc.search_abundance_species((dataset or {}).get("artifacts") or {}, **query, offset=offset, limit=50)
        except (svc.ServiceError, ValueError) as exc:
            return None, [], [], str(exc), {"display": "none"}, True, True
        rows = [{**row, "formula_mass": row["formula"] + "\n" + (
            f"{row['exact_mass']:.4f} Da" if row.get("exact_mass") is not None else "质量未知"
        )} for row in result["rows"]]
        message = f"显示第 {offset + 1}–{offset + len(rows)} 项精确结构" if rows else "没有符合条件的物种。"
        if result["has_more"]:
            message += "；可翻到下一页或收窄条件。"
        return {**result, "rows": rows, "context": context, "submitted": query}, rows, [], message, ({} if rows else {"display": "none"}), offset == 0, not result["has_more"]

    @app.callback(Output("evolution-filter-grid-previews", "data"), Input("evolution-filter-result", "data"), prevent_initial_call=True)
    def previews(result):
        return [{field: {"type": "markdown", "value": f"![结构](/api/structure.svg?smiles={quote(row['smiles'], safe='')}&width=280&height=190)"}
                 for field in ("formula_mass", "smiles")}
                for row in (result or {}).get("rows") or []]

    @app.callback(Output("evolution-filter-replace", "disabled"), Output("evolution-filter-add", "disabled"),
                  Input("evolution-filter-grid", "selectedRows"), Input("evolution-filter-result", "data"), prevent_initial_call=True)
    def selection_enabled(selected, result):
        disabled = not _selected_species(selected, result)
        return disabled, disabled
