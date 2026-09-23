"""One file collection workflow for RNG evidence, including server-side browsing."""
from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path

from dash import ALL, Input, Output, State, ctx, dcc, html, no_update
from dash.exceptions import PreventUpdate
import dash_bootstrap_components as dbc

from reacnet_scope import services as svc
from . import dataset_library


def layout():
    return html.Div([
        dcc.Store(id="import-selection", data={"paths": [], "assignments": {}}),
        dcc.Store(id="import-preview"), dcc.Store(id="import-collect-request"),
        dcc.Store(id="import-collect-result"), dcc.Store(id="import-location"), dcc.Store(id="import-list-page", data=0),
        html.Div([html.H2("添加 RNG 数据", id="data-browser-title", tabIndex=-1),
                  html.Span("浏览此电脑上的文件夹", className="rs-picker-kind")], className="rs-import-header"),
        html.Div([
            html.Details([
                html.Summary("位置与最近使用"),
                html.H3("位置"),
                html.Div([html.Button([
                    html.Span("▱", **{"aria-hidden": "true"}),
                    html.Span("主目录" if Path(root) == Path.home() else str(root)),
                ], id={"type": "import-location-root", "path": str(root)},
                   title=str(root), className="rs-picker-location") for root in svc.ALLOWED_ROOTS]),
                html.H3("最近使用", className="rs-picker-recent-heading"),
                html.Div(id="dir-browser-recent-datasets"),
                dbc.Button("当前数据与准备任务", id="data-browser-index-btn", color="link"),
                dbc.Button("补充当前RNG 数据", id="import-supplement", color="link", style={"display": "none"}),
            ], className="rs-picker-sidebar", open=True),
            html.Section([
                html.Div([
                    html.Button("↑", id="import-browse-up", className="btn btn-outline-secondary", title="上一级", **{"aria-label": "上一级"}),
                    html.Label("文件夹路径", htmlFor="import-path", className="visually-hidden"),
                    dbc.Input(id="import-path", placeholder="输入文件夹路径", debounce=True, persistence=True, persistence_type="local"),
                    dbc.Button("打开", id="import-browse-go", outline=True, color="secondary"),
                ], className="rs-picker-address"),
                html.Label("筛选目录内容", htmlFor="import-filter", className="visually-hidden"),
                dbc.Input(id="import-filter", placeholder="筛选目录内容", debounce=False),
                html.Div(id="import-browse-message", role="status", className="rs-picker-status"),
                html.Div([html.Span("名称"), html.Span("类型"), html.Span("大小")], className="rs-picker-columns"),
                html.Div([
                    html.Div(id="import-directories", className="rs-import-directories"),
                    html.Div(id="import-file-options", className="rs-import-file-options"),
                ], className="rs-picker-list"),
                html.Div([
                    html.Span(id="import-list-range"),
                    dbc.Button("上一页", id="import-list-prev", size="sm", color="link"),
                    dbc.Button("下一页", id="import-list-next", size="sm", color="link"),
                ], className="rs-picker-pagination"),
                html.Div([
                    html.Details([
                        html.Summary("高级选项"),
                        dbc.Checkbox(id="import-manual-files", label="手动调整文件选择", value=False),
                        dbc.Button("添加当前目录的 RNG 文件", id="import-add-folder", outline=True, color="secondary", disabled=True),
                        dbc.Checkbox(id="import-recursive", label="包含子文件夹", value=False),
                        html.Label("直接添加路径", htmlFor="import-paths"),
                        dbc.Textarea(id="import-paths", placeholder="每行一个文件或文件夹路径", rows=3),
                        dbc.Button("添加路径", id="import-add-paths", color="secondary"),
                    ], className="rs-picker-more"),
                ], className="rs-picker-toolbar"),
                html.Div(id="import-collect-status", role="status"),
                dbc.Button("取消添加", id="import-collect-cancel", disabled=True, color="link"),
            ], id="import-browser-panel", className="rs-picker-browser"),
            html.Details([
                html.Summary("选择与导入", id="import-selection-title"),
                html.Div([
                    html.Div([html.H3("待分析文件"), dbc.Button("清空", id="import-clear", color="link", size="sm", style={"display": "none"})], className="rs-import-header"),
                    html.Div([html.Span("＋", **{"aria-hidden": "true"}), html.P("打开一个 RNG 输出文件夹"), html.Small("识别出的结果文件会自动加入待分析列表")], id="import-selection-empty", className="rs-picker-empty"),
                    html.Div([
                        dcc.RadioItems(id="import-group", options=[], className="rs-import-groups"),
                        html.Div(id="import-group-summary"),
                        html.Details([html.Summary("文件清单与路径"), html.Div(id="import-files")], className="rs-picker-file-details"),
                        html.Div(dbc.Checkbox(id="import-edit-groups", label="调整文件归属", value=False), id="import-group-edit-toggle"),
                        html.Div([
                            dcc.Checklist(id="import-move-files", options=[], value=[]),
                            dcc.Dropdown(id="import-move-target", options=[], placeholder="选择目标RNG 数据"),
                            dbc.Input(id="import-new-group", placeholder="或输入新RNG 数据名称"),
                            dbc.Button("移动所选文件", id="import-move", size="sm", color="secondary"),
                            dbc.Button("重新按文件名建议归组", id="import-regroup", size="sm", color="link"),
                        ], id="import-group-editor", style={"display": "none"}),
                        html.P("同组文件须来自同一次 RNG 运行。", className="rs-meta"),
                    ], id="import-selected-panel", style={"display": "none"}),
                    html.Div(id="import-feedback", role="status"),
                ], className="rs-selection-card"),
                dataset_library.import_panel(),
            ], className="rs-picker-selection", open=True),
        ], className="rs-picker-body"),
        html.Div([
            html.Div(id="data-apply-reason", className="rs-meta", role="status"),
            dbc.Button("取消", id="dir-browser-cancel-btn", outline=True, color="secondary"),
            dbc.Button("开始分析", id="data-apply-btn", color="primary", disabled=True),
        ], className="rs-picker-footer"),
    ], id="data-browser-view", className="rs-data-view d-none")


def _file_size(size):
    value = float(size)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024 or unit == "TiB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024


def _selection_revision(selection):
    return hashlib.sha256(json.dumps(selection or {"paths": [], "assignments": {}}, sort_keys=True).encode()).hexdigest()


def _key(path):
    return hashlib.sha256(path.encode()).hexdigest()[:20]


def _distinguishing_parent(path, paths):
    """Shortest source suffix that distinguishes equal filenames."""
    same = [Path(item).parent for item in paths if Path(item).name == Path(path).name]
    if len(same) < 2:
        return ""
    parent = Path(path).parent
    for length in range(1, len(parent.parts) + 1):
        suffix = parent.parts[-length:]
        if sum(other.parts[-length:] == suffix for other in same) == 1:
            return str(Path(*suffix))
    return str(parent)


def register_callbacks(app):
    @app.callback(
        Output("import-add-folder", "disabled"),
        Input("import-location", "data"), Input("import-collect-cancel", "disabled"),
    )
    def selection_actions(location, idle):
        return not idle or not bool((location or {}).get("path"))

    @app.callback(
        Output("import-selected-panel", "style"), Output("import-clear", "style"),
        Output("import-supplement", "style"), Output("import-group-editor", "style"),
        Output("import-selection-empty", "style"), Output("import-group-edit-toggle", "style"),
        Input("import-selection", "data"), Input("import-preview", "data"),
        Input("app-store", "data"), Input("import-edit-groups", "value"),
    )
    def selection_display(selection, preview, current, edit):
        selected = bool((selection or {}).get("paths"))
        groups = (preview or {}).get("groups", [])
        needs_edit = len(groups) > 1 or any(not group["valid"] for group in groups)
        return ({} if selected else {"display": "none"},
                {} if selected and not (selection or {}).get("folder") else {"display": "none"},
                {} if (current or {}).get("dataset_id") else {"display": "none"},
                {} if edit and needs_edit else {"display": "none"},
                {"display": "none"} if selected else {},
                {} if needs_edit else {"display": "none"})

    @app.callback(
        Output("import-location", "data"), Output("import-path", "value"),
        Output("import-browse-message", "children"),
        Output("import-browse-up", "disabled"),
        Input("page-store", "data"), Input("import-browse-go", "n_clicks"),
        Input("import-selection", "data"),
        Input("import-path", "n_submit"), Input("import-browse-up", "n_clicks"),
        Input({"type": "import-directory", "path": ALL}, "n_clicks"),
        Input({"type": "import-location-root", "path": ALL}, "n_clicks"),
        State("import-path", "value"), State("import-location", "data"),
    )
    def browse(_open, _go, selection, _submit, _up, _dirs, _roots, path, location):
        trigger = ctx.triggered_id
        if trigger == "import-selection":
            focus = (selection or {}).get("focus")
            if not focus or focus == (location or {}).get("focus"):
                raise PreventUpdate
            path = (selection or {}).get("focus_path")
        elif isinstance(trigger, dict):
            if not any((_roots if trigger.get("type") == "import-location-root" else _dirs) or []):
                raise PreventUpdate
            path = trigger["path"]
        elif trigger == "import-browse-up":
            path = (location or {}).get("parent")
        elif trigger == "page-store":
            if (location or {}).get("path"):
                raise PreventUpdate
        if not path:
            path = next((str(root) for root in svc.ALLOWED_ROOTS if Path(root).is_dir()), "")
        if not path:
            return {}, "", "没有可访问的位置，请输入路径。", True
        try:
            result = svc.browse_import_files(str(path))
        except (svc.ServiceError, OSError) as exc:
            return no_update, no_update, str(exc), no_update
        result["focus"] = (selection or {}).get("focus")
        result["restore_collection"] = trigger == "import-selection"
        result["selection_revision"] = _selection_revision(selection)
        message = "列表达到上限，请进入更具体的位置。" if result["truncated"] else (f"{len(result['files'])} 个 RNG 文件" if result['files'] else "此目录没有 RNG 文件")
        return result, result["path"], message, not bool(result["parent"])

    @app.callback(
        Output("import-list-page", "data"),
        Input("import-list-prev", "n_clicks"), Input("import-list-next", "n_clicks"),
        Input("import-location", "data"), Input("import-filter", "value"),
        State("import-list-page", "data"),
    )
    def list_page(_prev, _next, location, query, page):
        if ctx.triggered_id == "import-list-prev":
            return max(0, int(page or 0) - 1)
        if ctx.triggered_id == "import-list-next":
            return int(page or 0) + 1
        return 0

    @app.callback(
        Output("import-directories", "children"), Output("import-file-options", "children"),
        Output("import-list-range", "children"), Output("import-list-prev", "disabled"), Output("import-list-next", "disabled"),
        Input("import-location", "data"), Input("import-selection", "data"),
        Input("import-filter", "value"), Input("dataset-switch-transaction", "data"), Input("import-list-page", "data"),
        Input("import-manual-files", "value"),
    )
    def file_list(location, selection, query, transaction, page, manual):
        location = location or {}
        selected = set((selection or {}).get("paths", []))
        query = str(query or "").casefold()
        locked = (transaction or {}).get("state") == "validating"
        items = [("directory", item) for item in location.get("directories", [])] + [("file", item) for item in location.get("files", [])]
        items = [(kind, item) for kind, item in items if query in item["label"].casefold()]
        page = min(max(0, int(page or 0)), max(0, (len(items) - 1) // 50))
        visible = items[page * 50:(page + 1) * 50]
        directories_on_page = [item for kind, item in visible if kind == "directory"]
        files_on_page = [item for kind, item in visible if kind == "file"]
        directories = [html.Div([
            html.Button([html.Span("▱ " + item["label"]), html.Small("文件夹"), html.Small("—")],
                        id={"type": "import-directory", "path": item["value"]}, n_clicks=0,
                        className="rs-picker-row", title=item["value"]),
            dbc.Button("加入", id={"type": "library-add-folder", "path": item["value"]},
                       n_clicks=0, size="sm", color="link", title="加入待导入文件夹列表"),
        ], className="rs-picker-folder-entry") for item in directories_on_page]

        files = [html.Button([
            html.Span([html.Span("☑" if item["value"] in selected else "☐", **{"aria-hidden": "true"}), html.Span(item["label"])]),
            html.Small(svc.ROLE_LABELS.get(svc.artifact_role(item["value"])[0], "")),
            html.Small(_file_size(item["size"])),
        ], id={"type": "import-toggle", "path": item["value"]}, n_clicks=(selection or {}).get("click_counts", {}).get(item["value"], 0),
            role="checkbox", disabled=locked, **{"aria-checked": str(item["value"] in selected).lower()},
            title=item["value"], className="rs-picker-row" + (" is-selected" if item["value"] in selected else ""))
            for item in files_on_page]
        if not manual:
            files = [html.Div([html.Span(item["label"]), html.Small(svc.ROLE_LABELS.get(svc.artifact_role(item["value"])[0], "")),
                              html.Small(_file_size(item["size"]))], className="rs-picker-row rs-picker-detected", title=item["value"])
                     for item in files_on_page]
        return (directories, files or ([html.P("没有匹配的文件", className="rs-picker-no-files")] if query and not items else []),
                f"{page * 50 + 1}–{min((page + 1) * 50, len(items))} / {len(items)}" if items else "", page == 0, (page + 1) * 50 >= len(items))

    @app.callback(
        Output("import-collect-request", "data"),
        Input("import-add-folder", "n_clicks"), Input("import-add-paths", "n_clicks"),
        State("import-location", "data"), State("import-paths", "value"),
        State("import-recursive", "value"), State("import-selection", "data"), prevent_initial_call=True,
    )
    def request_files(_folder, _paths, location, text, recursive, selection):
        inputs = []
        if ctx.triggered_id == "import-add-folder":
            inputs = [(location or {}).get("path") or ""]
        elif ctx.triggered_id == "import-add-paths":
            inputs = str(text or "").splitlines()
        return {"token": uuid.uuid4().hex, "inputs": inputs, "recursive": bool(recursive),
                "selection_revision": _selection_revision(selection)}

    @app.callback(
        Output("import-collect-result", "data"), Input("import-collect-request", "data"),
        background=True, cancel=[Input("import-collect-cancel", "n_clicks")],
        running=[
            (Output("import-collect-status", "children"), "正在检查并添加文件…可取消。", ""),
            (Output("import-collect-cancel", "disabled"), False, True),
            (Output("import-add-paths", "disabled"), True, False),
        ], prevent_initial_call=True,
    )
    def collect(request):
        if not request:
            raise PreventUpdate
        try:
            return {**request, **svc.collect_files(request["inputs"], recursive=request["recursive"])}
        except (svc.ServiceError, OSError) as exc:
            return {**request, "paths": [], "errors": [{"path": "", "message": str(exc)}], "truncated": False}

    @app.callback(
        Output("import-selection", "data"), Output("import-feedback", "children"),
        Input("import-collect-result", "data"), Input("import-clear", "n_clicks"),
        Input("import-location", "data"),
        Input("import-supplement", "n_clicks"),
        Input("dir-browser-cancel-btn", "n_clicks"),
        Input("page-store", "data"),
        Input({"type": "import-remove", "key": ALL}, "n_clicks"),
        Input({"type": "import-toggle", "path": ALL}, "n_clicks"),
        Input("import-move", "n_clicks"), Input("import-regroup", "n_clicks"),
        Input({"type": "dir-browser-recent-entry", "index": ALL}, "n_clicks"),
        State({"type": "import-toggle", "path": ALL}, "id"),
        State("import-move-files", "value"), State("import-move-target", "value"), State("import-new-group", "value"),
        State("import-selection", "data"), State("import-preview", "data"),

        State("app-store", "data"), State("recent-datasets", "data"),
        State("dataset-switch-transaction", "data"), State("import-collect-request", "data"), prevent_initial_call=True,
    )
    def select(collected, _clear, location, _supplement, _cancel, page, _remove, _toggles, _move, _regroup, _recent,
               toggle_ids, moving, target_group, new_group, selection, preview, current, recent, transaction, collect_request):
        if (transaction or {}).get("state") == "validating":
            raise PreventUpdate
        trigger = ctx.triggered_id
        # Dash may batch page hydration with the user's click. A real draft
        # action must not be swallowed by the accompanying page-store change.
        if (page or {}).get("page") == "data-management":
            for action in ("dir-browser-cancel-btn", "import-clear", "import-supplement"):
                if any(item["prop_id"] == action + ".n_clicks" and item.get("value") for item in ctx.triggered):
                    trigger = action
                    break
        selection = dict(selection or {"paths": [], "assignments": {}})
        try:
            if isinstance(trigger, str) and trigger in {"dir-browser-cancel-btn", "page-store"}:
                if trigger == "page-store" and (page or {}).get("page") == "data-management":
                    raise PreventUpdate
                selection["epoch"] = uuid.uuid4().hex
                return selection, ""
            if trigger == "import-location":
                location = location or {}
                if (not location.get("path") or location.get("restore_collection")
                        or location.get("selection_revision") != _selection_revision(selection)):
                    raise PreventUpdate
                paths = [item["value"] for item in location.get("files", [])]
                svc.preview_file_collection(paths)
                return {"paths": paths, "assignments": {}, "folder": location["path"],
                        "folder_truncated": bool(location.get("truncated")), "epoch": uuid.uuid4().hex}, ""
            if trigger == "import-clear":
                return {"paths": [], "assignments": {}}, ""
            if trigger == "import-supplement" or isinstance(trigger, dict) and trigger.get("type") == "dir-browser-recent-entry":
                target = current or {}
                if isinstance(trigger, dict):
                    if not any(_recent or []):
                        raise PreventUpdate
                    record = svc.normalise_recent_datasets(recent)[int(trigger["index"])]
                    target = svc.validate_dataset_candidate(record["folder"], record["base"])
                if not target.get("dataset_id"):
                    return no_update, "请先打开一个RNG 数据。"
                artifacts = {role: value for role, value in (target.get("artifacts") or {}).items()
                             if role in svc.ROLE_LABELS and value}
                label = str(target.get("label") or "RNG RNG 数据")
                base = str(target.get("base") or "")
                return {"paths": list(artifacts.values()), "assignments": {p: label for p in artifacts.values()},
                        "existing_base": base if svc.is_collection_path(base) else "", "existing_group": label,
                        "focus": uuid.uuid4().hex, "focus_path": str(Path(next(iter(artifacts.values()))).parent) if artifacts else ""}, "已添加原有文件，可继续补充并开始分析。"
            if isinstance(trigger, dict) and trigger.get("type") == "import-remove":
                if not any(_remove or []):
                    raise PreventUpdate
                selection["paths"] = [p for p in selection.get("paths", []) if _key(p) != trigger["key"]]
                return selection, ""
            if trigger == "import-move":
                group = str(new_group or target_group or "").strip()
                if not group or not moving:
                    return no_update, "请选择文件和目标RNG 数据。"
                values = dict(selection.get("assignments") or {})
                for path in moving:
                    if path in selection.get("paths", []):
                        values[path] = group
                selection["assignments"] = values
                return selection, ""
            if trigger == "import-regroup":
                selection["assignments"] = {}
                selection.pop("existing_group", None)
                selection.pop("existing_base", None)
                return selection, "已恢复文件名建议，请核对运行归属。"
            if isinstance(trigger, dict) and trigger.get("type") == "import-toggle":
                counts = dict(selection.get("click_counts") or {})
                paths = list(selection.get("paths", []))
                changed = False
                for identity, count in zip(toggle_ids or [], _toggles or []):
                    path = identity["path"]
                    previous = counts.get(path, 0)
                    if int(count or 0) <= previous:
                        continue
                    changed = True
                    counts[path] = count
                    if (count - previous) % 2:
                        if path in paths:
                            paths.remove(path)
                        else:
                            paths.append(path)
                            if selection.get("existing_group"):
                                selection["assignments"] = {**selection.get("assignments", {}), path: selection["existing_group"]}
                if not changed:
                    raise PreventUpdate
                svc.preview_file_collection(paths)
                selection["paths"] = paths
                selection["click_counts"] = counts
                return selection, ""
            if not collected or collected.get("token") != (collect_request or {}).get("token"):
                raise PreventUpdate
            if collected.get("selection_revision") != _selection_revision(selection):
                return no_update, "添加期间选择已改变，本次扫描结果未加入。请重新添加。"
            result = collected
            merged = list(dict.fromkeys([*selection.get("paths", []), *result["paths"]]))
            # Enforce the collection bound before replacing the user's draft.
            svc.preview_file_collection(merged)
            if selection.get("existing_group"):
                values = dict(selection.get("assignments") or {})
                for path in result["paths"]:
                    values.setdefault(path, selection["existing_group"])
                selection["assignments"] = values
            selection["paths"] = merged
            messages = [f"已添加 {len(result['paths'])} 个文件，当前共 {len(merged)} 个。"]
            messages.extend(f"{e['path']}：{e['message']}" for e in result["errors"])
            if result["truncated"]:
                messages.append("已达到扫描上限，部分文件未添加；请缩小范围后继续添加。")
            return selection, html.Div([html.P(message) for message in messages])
        except (svc.ServiceError, OSError, IndexError, KeyError) as exc:
            return no_update, dbc.Alert(str(exc), color="danger")

    @app.callback(
        Output("import-preview", "data"), Output("import-files", "children"),
        Output("import-group", "options"), Output("import-group", "value"),
        Output("import-move-files", "options"), Output("import-move-target", "options"),
        Output("import-selection-title", "children"), Output("import-group", "style"),
        Input("import-selection", "data"), State("import-group", "value"),
    )
    def render(selection, selected):
        selection = selection or {}
        preview = svc.preview_file_collection(selection.get("paths", []), selection.get("assignments"))
        preview["folder_truncated"] = bool(selection.get("folder_truncated"))
        rows = [html.Div([
            html.Div([html.Strong(row["label"]), html.Small(_distinguishing_parent(row["path"], selection.get("paths", [])), className="rs-picker-parent"), html.Span(svc.ROLE_LABELS.get(row["role"], "无法识别"), className="rs-import-role"),
                html.Details([html.Summary("路径"), html.Code(row["path"]), dcc.Clipboard(content=row["path"], title="复制路径")]),
                html.Div(row["message"], className="text-danger")], className="rs-import-file-name"),
            dbc.Button("移除", id={"type": "import-remove", "key": _key(row["path"])}, color="link", size="sm"),
        ], className="rs-import-file") for row in preview["files"]]
        options = [{"label": f"{g['label']} · {len(g['files'])} 个文件" + (" · 需处理" if not g["valid"] else ""),
                    "value": g["key"]} for g in preview["groups"]]
        keys = [g["value"] for g in options]
        value = selected if selected in keys else keys[0] if len(keys) == 1 else None
        return (preview, rows, options, value,
                [{"label": f"{row['label']} · {Path(row['path']).parent}", "value": row["path"]} for row in preview["files"]],
                [{"label": g["label"], "value": g["key"]} for g in preview["groups"]],
                f"选择与导入 · {len(preview['files'])} 个文件", {} if len(options) > 1 else {"display": "none"})

    @app.callback(
        Output("dataset-browser-candidate", "data", allow_duplicate=True),
        Output("import-group-summary", "children"),
        Input("import-preview", "data"), Input("import-group", "value"),
        State("import-selection", "data"), prevent_initial_call=True,
    )
    def candidate(preview, selected, selection):
        group = next((g for g in (preview or {}).get("groups", []) if g["key"] == selected), None)
        if (selection or {}).get("folder_truncated"):
            return None, dbc.Alert("目录内容超过识别上限，请选择更具体的 RNG 输出文件夹。", color="warning")
        if not group:
            return None, "请选择一组数据。" if (preview or {}).get("groups") else ""
        if not group["valid"]:
            return None, dbc.Alert([html.P(e) for e in group["errors"]], color="warning")
        try:
            old_base = (selection or {}).get("existing_base", "") if selected == (selection or {}).get("existing_group") else ""
            label = Path(selection["folder"]).name if (selection or {}).get("folder") and len((preview or {}).get("groups", [])) == 1 else group["label"]
            result = svc.collection_candidate(group["artifact_paths"], label, existing_base=old_base)
        except svc.ServiceError as exc:
            return None, dbc.Alert(str(exc), color="danger")
        roles = group["artifact_paths"]
        notes = []
        if roles.get("reaction"):
            notes.append("物种检索与聚合反应")
        if roles.get("species"):
            notes.append("丰度与元素组成（自动准备）")
        if roles.get("timeline") or roles.get("reactionevent") and roles.get("molecules"):
            notes.append("观测事件（进入分析时准备）")
        elif roles.get("reactionevent"):
            notes.append("事件 CSV 缺少配套分子证据，补充后可进行事件分析")
        if roles.get("trajectory"):
            notes.append("几何可视化（按需准备坐标）")
        remaining = len((preview or {}).get("files", [])) - len(group["files"])
        return result, html.Div([html.H3(label), html.P(f"{len(group['files'])} 个文件", className="rs-meta"), html.Div([html.Span(svc.ROLE_LABELS[role], className="rs-picker-role-tag") for role in roles], className="rs-picker-role-tags"),
            html.Details([html.Summary("分析与准备"), html.Ul([html.Li(note) for note in notes])]),
            html.P(f"另有 {remaining} 个文件保留在选择列表中。" if remaining else "", className="rs-meta")])

    @app.callback(
        Output("import-auto-request", "data"), Output("import-auto-attempts", "data"),
        Input("app-store", "data"), Input("page-store", "data"), Input("import-auto-tick", "n_intervals"),
        State("import-auto-attempts", "data"), State("import-auto-request", "data"), State("import-auto-result", "data"),
        prevent_initial_call=True,
    )
    def request_preparation(current, page, _tick, attempts, active_request, result):
        # One background worker stays attached to its original dataset across
        # navigation. Do not replace its request (Dash would terminate it).
        if active_request and (result or {}).get("token") != active_request.get("token"):
            raise PreventUpdate
        current = current or {}
        if not current.get("dataset_id") or not svc.is_collection_path(current.get("base", "")):
            raise PreventUpdate
        if current.get("context_state") == "revision-changed":
            raise PreventUpdate
        kind = {"species": "composition", "evolution": "composition", "element-distribution": "composition",
                "events": "event", "trajectory": "event", "reactions": "event"}.get((page or {}).get("page"))
        artifacts = current.get("artifacts") or {}
        if not kind or kind == "composition" and not artifacts.get("species"):
            raise PreventUpdate
        if kind == "event" and not (artifacts.get("timeline") or artifacts.get("reactionevent") and artifacts.get("molecules")):
            raise PreventUpdate
        import json
        token = hashlib.sha256(json.dumps([current["dataset_id"], current.get("source_revision"), kind], sort_keys=True).encode()).hexdigest()
        if token in (attempts or []):
            raise PreventUpdate
        return {"token": token, "kind": kind, "current": current}, [*(attempts or [])[-31:], token]

    @app.callback(
        Output("import-auto-result", "data"), Input("import-auto-request", "data"),
        background=True, prevent_initial_call=True,
    )
    def prepare(request):
        if not request:
            raise PreventUpdate
        current = request["current"]
        try:
            validation = svc.validate_dataset_candidate(current["folder"], current["base"])
            if not svc.is_same_dataset_revision(current, validation):
                raise svc.ServiceError("文件关联已变化，请刷新数据后重试。", reason="source_revision_changed")
            result = svc.prepare_dataset_workspace(current["folder"], base=current["base"], kind=request["kind"], automatic=True)
            return {**request, "ok": not result.get("canceled"), "message": "准备已取消，可在RNG 数据页面继续。" if result.get("canceled") else "所需证据已准备。"}
        except (svc.ServiceError, OSError) as exc:
            return {**request, "ok": False, "message": str(exc)}

    @app.callback(
        Output("app-store", "data", allow_duplicate=True),
        Output("dataset-session-store", "data", allow_duplicate=True),
        Input("import-auto-result", "data"), State("app-store", "data"),
        prevent_initial_call=True,
    )
    def accept_preparation(result, current):
        if not result or not svc.is_same_dataset_revision(current, result.get("current")):
            raise PreventUpdate
        try:
            validation = svc.validate_dataset_candidate(current["folder"], current["base"])
        except (svc.ServiceError, OSError):
            raise PreventUpdate
        if not svc.is_same_dataset_revision(current, validation):
            raise PreventUpdate
        updated = {**current, "analysis_capabilities": validation["analysis_capabilities"], "readiness": validation["readiness"]}
        return updated, updated

    @app.callback(
        Output("import-auto-message", "children"), Output("import-auto-cancel", "disabled"),
        Output("import-auto-panel", "style"),
        Input("import-auto-request", "data"), Input("import-auto-result", "data"),
        Input("app-store", "data"), Input("import-auto-cancel-result", "data"),
    )
    def preparation_message(request, result, current, cancel):
        if not request or not svc.is_same_dataset_revision(current, request.get("current")):
            return "", True, {"display": "none"}
        if (result or {}).get("token") == request["token"]:
            if result.get("ok"):
                return "", True, {"display": "none"}
            return result["message"] + " 可打开“RNG 数据”查看详情或重试。", True, {}
        if (cancel or {}).get("token") == request["token"]:
            return cancel["message"], bool(cancel.get("requested")), {}
        label = "丰度与元素组成" if request["kind"] == "composition" else "观测事件"
        return f"正在后台准备{label}，已就绪的分析仍可使用。可在“RNG 数据”查看进度。", False, {}

    @app.callback(
        Output("import-auto-cancel-result", "data"), Input("import-auto-cancel", "n_clicks"),
        State("import-auto-request", "data"), prevent_initial_call=True,
    )
    def cancel_preparation(clicks, request):
        if not clicks or not request:
            raise PreventUpdate
        current = request["current"]
        try:
            result = svc.cancel_dataset_preparation(current["folder"], base=current["base"], kind=request["kind"])
            return {"token": request["token"], "message": result["message"], "requested": result["cancellation_requested"]}
        except svc.ServiceError as exc:
            return {"token": request["token"], "message": str(exc)}


def preparation_layout():
    return html.Div([
        dcc.Store(id="import-auto-request"), dcc.Store(id="import-auto-result"),
        dcc.Store(id="import-pending-evolution"), dcc.Store(id="import-pending-elements"), dcc.Store(id="import-pending-events"),
        dcc.Store(id="import-auto-attempts", data=[]), dcc.Store(id="import-auto-cancel-result"),
        dcc.Interval(id="import-auto-tick", interval=2000, n_intervals=0),
        html.Div([html.Span(id="import-auto-message"),
                  dbc.Button("取消准备", id="import-auto-cancel", size="sm", outline=True, color="secondary")],
                 id="import-auto-panel", className="rs-import-auto", style={"display": "none"}, role="status"),
    ])


def defer_query(kind: str, output_count: int):
    """Keep one explicit query until its preparation result is available.

    The extra input is the preparation result; the extra state/output holds a
    snapshot of the submitted arguments. Switching datasets invalidates that
    snapshot. Readers themselves never build indexes or scan missing evidence.
    """
    from functools import wraps

    def decorate(function):
        @wraps(function)
        def run(clicks, prepared, *states):
            original_states, pending = states[:-1], states[-1]
            current = original_states[-1] or {}
            original_args = [clicks, *original_states]
            if ctx.triggered_id == "import-auto-result":
                if not pending or not prepared or prepared.get("kind") != kind:
                    raise PreventUpdate
                if not svc.is_same_dataset_revision(current, pending.get("current")) or not svc.is_same_dataset_revision(current, prepared.get("current")):
                    raise PreventUpdate
                if not prepared.get("ok"):
                    return (*(no_update for _ in range(output_count)), None)
                # Preserve the exact submitted question; replace only the
                # capability snapshot with the current revision's status.
                original_args = [*pending["args"][:-1], current]
                return (*function(*original_args), None)
            capability = "species_abundance" if kind == "composition" else "event_search"
            artifacts = current.get("artifacts") or {}
            source = artifacts.get("species") if kind == "composition" else artifacts.get("timeline") or (artifacts.get("reactionevent") and artifacts.get("molecules"))
            evidence = (current.get("analysis_capabilities") or {}).get(capability) or {}
            completed = prepared and prepared.get("kind") == kind and svc.is_same_dataset_revision(current, prepared.get("current"))
            if clicks and source and not completed and svc.is_collection_path(current.get("base", "")) and evidence.get("state") != "ready":
                return (*(no_update for _ in range(output_count)), {"args": original_args, "current": current})
            return (*function(*original_args), None)
        return run
    return decorate
