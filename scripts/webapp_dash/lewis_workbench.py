"""Optional Lewis preparation and alias lookup in the Species workspace."""

from __future__ import annotations

import json
import time

from dash import ALL, Input, Output, State, ctx, dcc, html, no_update
from dash.exceptions import PreventUpdate
import dash_bootstrap_components as dbc

from reacnet_scope import services as svc
from reacnet_scope.lewis import LewisOptions
from .candidate_workbench import context_key


_STATUS = {"generated": "已生成候选", "unsupported": "不支持", "input_incomplete": "输入不完整",
           "needs_input": "需要明确假设", "timeout": "超时", "truncated": "搜索截断",
           "no_candidate": "模型下无候选", "solver_failed": "求解失败", "not_evaluated": "未检查"}
_STATUS["candidate_rejected"] = "候选未通过表示检查"
_REASONS = {"neutral_assumption_required": "需明确选择中性片段假设",
            "nonzero_fragment_charge": "当前模型不支持已知非零片段电荷",
            "spin_constraint": "当前模型不支持指定自旋约束",
            "hydrogen_count_unresolved": "无法可靠确定完整氢数",
            "smiles_parse_failed": "无法解析代表 SMILES",
            "missing_or_unknown_structure": "代表结构缺失或为未知标签",
            "one_heuristic_candidate": "仅生成一个启发式候选，未穷举候选或确定真实电子态",
            "model_infeasible": "在当前假设与规则下未找到可行解",
            "solver_budget": "达到求解时间或节点预算",
            "atom_budget": "超过单结构原子数预算",
            "candidate_rdkit_check_failed": "返回的候选未通过 RDKit 表示检查，未穷举其他候选",
            "no_allowed_atom_state": "连接关系超出当前原子状态规则",
            "species_not_in_prepared_catalogue": "该物种不在已准备的代表结构目录中"}


def reason_label(value):
    if str(value).startswith("unsupported_elements:"):
        return "不支持的元素：" + value.split(":", 1)[1]
    return _REASONS.get(value, value or "")


def annotation_panel(annotation):
    value = annotation or {}
    children = [html.Strong("Lewis 候选注释 · " + _STATUS.get(value.get("status"), "未检查")),
                html.P(reason_label(value.get("reason")), className="small mb-1")]
    candidate = value.get("candidate_smiles")
    if candidate:
        children.append(html.Code(candidate, style={"overflowWrap": "anywhere"}))
        image = svc.render_species_svg(candidate)
        if image.get("ok"):
            children.append(html.Iframe(srcDoc=image["svg"], title="Lewis 候选结构",
                                       style={"border": "none", "width": "100%", "height": "220px"}))
    if value.get("coverage"):
        scope = {"selected_representative_only": "仅所选代表结构，不覆盖 miso=2 全组",
                 "representative_scope_unknown": "miso 未知，代表范围未知",
                 "representative_connectivity": "当前代表结构的元素与连接图"}
        children.append(html.P(scope.get(value["coverage"], value["coverage"]), className="small mb-1"))
        children.append(html.P("模型：中性片段；自旋未确定。候选不替换 RNG 原始身份。", className="small mb-1"))
        children.append(html.Details([html.Summary("方法、假设与来源"),
                                     html.Pre(json.dumps(value, ensure_ascii=False, indent=2),
                                              style={"maxHeight": "280px", "overflow": "auto"})]))
    return html.Div(children, className="mt-3 p-2 border rounded")


def layout():
    return html.Details([
        html.Summary("Lewis 候选结构注释（可选）"),
        html.P("根据代表结构的元素与连接关系生成候选表示，用于对照和别名检索。原始物种、反应和计数保持不变。",
               className="small mt-2"),
        dbc.Checklist(id="lewis-neutral", options=[{"label": "明确采用中性片段假设（不指定自旋）", "value": "neutral"}], value=[]),
        html.Div([
            dbc.Button("准备 Lewis 注释", id="lewis-prepare", size="sm", className="me-2"),
            dbc.Button("取消任务", id="lewis-cancel", size="sm", color="secondary", outline=True, className="me-2"),
            dbc.Button("刷新", id="lewis-refresh", size="sm", color="secondary", outline=True),
        ], className="my-2"),
        html.Div(id="lewis-status", role="status"),
        dcc.Input(id="lewis-alias", placeholder="Lewis 候选 SMILES；留空浏览全部注释", value="",
                  style={"width": "100%"}),
        html.Div([
            dbc.Button("查找原始物种", id="lewis-search", size="sm", className="me-2"),
            dbc.Button("上一页", id="lewis-prev", size="sm", outline=True, className="me-2"),
            dbc.Button("下一页", id="lewis-next", size="sm", outline=True, className="me-2"),
            dbc.Button("导出匹配注释 JSON", id="lewis-export-json", size="sm", outline=True, className="me-2"),
            dbc.Button("导出匹配注释 CSV", id="lewis-export-csv", size="sm", outline=True),
        ], className="my-2 d-flex flex-wrap gap-1"),
        html.Div(id="lewis-results"), html.Div(id="lewis-export-error", role="status"),
        dcc.Store(id="lewis-prepare-request"), dcc.Store(id="lewis-prepare-result"),
        dcc.Store(id="lewis-query"), dcc.Store(id="lewis-query-result"),
        dcc.Interval(id="lewis-poll", interval=1500, disabled=True),
        dcc.Download(id="lewis-download"),
    ], className="rs-card p-3 my-2", id="lewis-workbench")


def register_callbacks(app):
    @app.callback(Output("lewis-workbench", "style"), Input("species-workspace-stage", "data"))
    def _lewis_stage(stage):
        return {"display": "none"} if stage == "detail" else {}

    @app.callback(Output("lewis-prepare-request", "data"), Input("lewis-prepare", "n_clicks"),
                  State("lewis-neutral", "value"), State("app-store", "data"), prevent_initial_call=True)
    def _request_lewis_preparation(clicks, neutral, dataset):
        if not clicks or not dataset:
            raise PreventUpdate
        return {"context": context_key(dataset), "folder": dataset.get("folder"), "base": dataset.get("base"),
                "dataset_id": dataset.get("dataset_id"), "neutral": "neutral" in (neutral or []),
                "source_revision": dataset.get("source_revision"),
                "annotation_revision": svc.lewis_source_fingerprint(dataset.get("artifacts") or {}),
                "request_id": time.time_ns()}

    @app.callback(Output("lewis-prepare-result", "data"), Input("lewis-prepare-request", "data"),
                  background=True, prevent_initial_call=True)
    def _prepare_lewis(request):
        if not request:
            raise PreventUpdate
        result = {"context": request["context"], "request_id": request["request_id"]}
        if not request["neutral"]:
            return {**result, "error": "请先明确选择中性片段假设。"}
        try:
            current = svc.validate_dataset_candidate(request["folder"], request["base"])
            if not svc.is_same_dataset_revision(request, current):
                return {**result, "error": "RNG 数据来源已变化，请重新选择后准备。"}
            prepared = svc.prepare_dataset_workspace(
                request["folder"], base=request["base"], kind="lewis",
                lewis_expected_revision=request["annotation_revision"],
                expected_dataset_id=request["dataset_id"], lewis_options=LewisOptions(assume_neutral=True))
            return {**result, "ok": True, "canceled": bool(prepared.get("canceled"))}
        except (svc.ServiceError, ValueError, OSError) as exc:
            return {**result, "error": str(exc)}

    @app.callback(Output("lewis-status", "children"), Output("lewis-poll", "disabled"),
                  Output("lewis-prepare", "disabled"), Output("lewis-cancel", "disabled"),
                  Input("app-store", "data"), Input("lewis-poll", "n_intervals"),
                  Input("lewis-refresh", "n_clicks"), Input("lewis-prepare-request", "data"),
                  Input("lewis-prepare-result", "data"))
    def _lewis_status(dataset, _tick, _refresh, request, result):
        dataset = dataset or {}
        context = context_key(dataset)
        status = svc.lewis_status(dataset.get("artifacts") or {})
        pending = bool(request and request.get("context") == context and
                       (not result or result.get("request_id") != request.get("request_id")))
        task = {}
        if dataset.get("folder") and dataset.get("base"):
            try:
                task = svc.lewis_task_status(dataset["folder"], dataset["base"])
            except (svc.ServiceError, OSError, RuntimeError):
                task = {}
        active = task.get("state") in {"running", "cancel_requested"}
        if active:
            return (task.get("message") or "Lewis 注释准备中"), False, True, task.get("state") == "cancel_requested"
        if pending:
            if task.get("state") in {"failed", "interrupted", "superseded", "canceled"} and int(task.get("updated_at_epoch") or 0) >= int(request["request_id"] // 1_000_000_000):
                return dbc.Alert(task.get("message") or "Lewis 任务已停止，可重新准备。", color="warning"), True, False, True
            return "Lewis 注释任务已提交；切换 RNG 数据不会取消原任务。", False, True, True
        if result and result.get("context") == context and result.get("error"):
            return dbc.Alert(result["error"], color="warning"), True, False, True
        if task.get("state") in {"failed", "interrupted", "superseded", "canceled"}:
            message = task.get("message") or "Lewis 任务已停止；未完成结果没有发布，可重新准备。"
            return dbc.Alert(message, color="warning"), True, False, True
        if status["state"] == "ready":
            counts = "、".join(f"{_STATUS.get(k, k)} {v}" for k, v in status["statuses"].items())
            return f"已准备 {status['count']} 个原始物种：{counts}。", True, False, True
        return status.get("message", "注释尚未准备"), True, not bool(dataset.get("base")), True

    @app.callback(Output("lewis-prepare-result", "data", allow_duplicate=True),
                  Input("lewis-cancel", "n_clicks"), State("lewis-prepare-request", "data"),
                  State("app-store", "data"), prevent_initial_call=True)
    def _cancel_lewis(clicks, request, dataset):
        if not clicks or not dataset or not dataset.get("base"):
            raise PreventUpdate
        # Cancellation is persisted against the original task; do not terminate
        # the Dash worker before it can clean up the unpublished staging file.
        svc.cancel_dataset_preparation(dataset["folder"], base=dataset["base"], kind="lewis")
        return no_update

    @app.callback(Output("lewis-alias", "value"), Input("app-store", "data"),
                  State("lewis-query", "data"), prevent_initial_call=True)
    def _reset_lewis_alias(dataset, query):
        if query and query.get("context") == context_key(dataset):
            raise PreventUpdate
        return ""

    @app.callback(Output("lewis-query", "data"), Input("lewis-search", "n_clicks"),
                  Input("lewis-next", "n_clicks"), Input("lewis-prev", "n_clicks"),
                  Input("app-store", "data"), Input("lewis-prepare-result", "data"),
                  Input("lewis-refresh", "n_clicks"), State("lewis-alias", "value"),
                  State("lewis-query", "data"))
    def _lewis_query(_search, _next, _prev, dataset, _prepared, _refresh, alias, previous):
        context = context_key(dataset)
        old = previous if previous and previous.get("context") == context else {}
        trigger = ctx.triggered_id
        if trigger == "lewis-prepare-result" and (not _prepared or _prepared.get("context") != context):
            raise PreventUpdate
        if trigger == "app-store" and old:
            raise PreventUpdate
        if trigger in {"lewis-next", "lewis-prev"}:
            return {**old, "context": context, "request_id": time.time_ns(),
                    "offset": max(0, old.get("offset", 0) + (50 if trigger == "lewis-next" else -50))}
        return {"context": context, "alias": str(alias or "").strip() if old or trigger == "lewis-search" else "",
                "offset": 0, "request_id": time.time_ns()}

    @app.callback(Output("lewis-query-result", "data"), Input("lewis-query", "data"),
                  State("app-store", "data"))
    def _read_lewis_query(query, dataset):
        if not query or query.get("context") != context_key(dataset):
            raise PreventUpdate
        try:
            return {"context": query["context"], "request_id": query.get("request_id"),
                    **svc.lewis_search((dataset or {}).get("artifacts") or {}, query.get("alias", ""), offset=query.get("offset", 0))}
        except svc.ServiceError as exc:
            return {"context": query["context"], "request_id": query.get("request_id"), "error": str(exc)}

    @app.callback(Output("lewis-results", "children"), Output("lewis-next", "disabled"),
                  Output("lewis-prev", "disabled"), Input("lewis-query-result", "data"),
                  Input("app-store", "data"), Input("lewis-query", "data"))
    def _render_lewis_query(result, dataset, query):
        if not result or result.get("context") != context_key(dataset) or result.get("request_id") != (query or {}).get("request_id"):
            return "", True, True
        if result.get("error"):
            return result["error"], True, True
        rows = result.get("rows", [])
        rendered = []
        for row in rows:
            rendered.append(html.Tr([
                html.Td(dbc.Button(row["original_species"],
                                   id={"type": "lewis-open", "species": row["original_species"],
                                       "request": str(result["request_id"])},
                                   color="link", size="sm", style={"overflowWrap": "anywhere"})),
                html.Td(html.Code(row.get("candidate_smiles") or "—")),
                html.Td(_STATUS.get(row["status"], row["status"])),
                html.Td(reason_label(row.get("reason"))),
            ]))
        table = dbc.Table([html.Thead(html.Tr([html.Th(t) for t in ("原始物种（点击查看）", "Lewis 候选", "状态", "原因")])),
                           html.Tbody(rendered)], bordered=True, size="sm", responsive=True)
        return ([table, html.P(f"第 {result['offset'] // 50 + 1} 页；本页 {len(rows)} 条原物种。")]
                if rows else "没有匹配的注释。"), not result.get("has_more"), result.get("offset", 0) == 0

    @app.callback(Output("species-detail-handoff", "data", allow_duplicate=True),
                  Input({"type": "lewis-open", "species": ALL, "request": ALL}, "n_clicks"),
                  State("lewis-query-result", "data"), State("app-store", "data"),
                  State("lewis-query", "data"), prevent_initial_call=True)
    def _open_lewis_species(clicks, result, dataset, query):
        if (not any(clicks or []) or not result or result.get("context") != context_key(dataset)
                or result.get("request_id") != (query or {}).get("request_id")):
            raise PreventUpdate
        trigger = ctx.triggered_id
        if (not isinstance(trigger, dict) or trigger.get("request") != str(query["request_id"])
                or trigger.get("species") not in {row["original_species"] for row in result["rows"]}):
            raise PreventUpdate
        return {"species": trigger["species"], "context": result["context"], "request_id": time.time_ns()}

    @app.callback(Output("lewis-download", "data"), Output("lewis-export-error", "children"),
                  Input("lewis-export-json", "n_clicks"), Input("lewis-export-csv", "n_clicks"),
                  State("lewis-query", "data"), State("app-store", "data"), prevent_initial_call=True)
    def _export_lewis(_json, _csv, query, dataset):
        if not query or query.get("context") != context_key(dataset):
            raise PreventUpdate
        format = "csv" if ctx.triggered_id == "lewis-export-csv" else "json"
        try:
            content = svc.lewis_export(dataset.get("artifacts") or {}, format=format, alias=query.get("alias", ""))
            return dcc.send_string(content, f"lewis-annotations.{format}"), ""
        except svc.ServiceError as exc:
            return no_update, str(exc)
