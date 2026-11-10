"""Event evidence CSV, package, trajectory and external-viewer export callbacks."""

from __future__ import annotations

from typing import Any
from dash import (
    Input,
    Output,
    State,
    dcc,
)
from dash.exceptions import PreventUpdate
from reacnet_scope import services as svc


def register(app: Any) -> None:
    """Register event exports callbacks."""

    @app.callback(
        Output("event-csv-download", "data"),
        Input("event-csv-btn", "n_clicks"),
        State("event-grid-store", "data"),
        prevent_initial_call=True,
    )
    def _export_event_csv(n_clicks, grid_store):
        if n_clicks is None:
            raise PreventUpdate
        rows = (grid_store or {}).get("rows") or []
        if not rows:
            raise PreventUpdate
        return {"content": svc.rows_to_csv(rows), "filename": "event_evidence.csv", "type": "text/csv"}

    @app.callback(
        Output("event-frames-csv-download", "data"),
        Input("event-frames-csv-btn", "n_clicks"),
        State("event-viewer-store", "data"),
        prevent_initial_call=True,
    )
    def _download_event_frames(n_clicks, viewer):
        if n_clicks is None or not viewer:
            raise PreventUpdate
        event_id = str(viewer.get("event_id") or "event")
        return {
            "content": svc.event_viewer_frames_csv(viewer),
            "filename": f"{event_id}_frames.csv",
            "type": "text/csv",
        }

    @app.callback(
        Output("event-distances-csv-download", "data"),
        Input("event-distances-csv-btn", "n_clicks"),
        State("event-viewer-store", "data"),
        prevent_initial_call=True,
    )
    def _download_event_changed_bond_distances(n_clicks, viewer):
        if n_clicks is None or not viewer:
            raise PreventUpdate
        event_id = str(viewer.get("event_id") or "event")
        return {
            "content": svc.event_viewer_changed_bond_distances_csv(viewer),
            "filename": f"{event_id}_changed_bond_distances.csv",
            "type": "text/csv",
        }

    @app.callback(
        Output("event-package-download", "data"),
        Input("event-package-btn", "n_clicks"),
        State("event-viewer-store", "data"),
        State("event-view-scope", "value"),
        prevent_initial_call=True,
    )
    def _download_event_package(n_clicks, viewer, scope):
        if n_clicks is None or not viewer:
            raise PreventUpdate
        event_id = str(viewer.get("event_id") or "event")
        package_scope = (
            "environment"
            if scope == "context"
            else (scope or "participants")
        )
        return dcc.send_bytes(
            svc.build_event_package(viewer, scope=package_scope),
            f"{event_id}_evidence.zip",
            type="application/zip",
        )

    @app.callback(
        Output("event-trajectory-download", "data"),
        Input("event-trajectory-btn", "n_clicks"),
        State("event-viewer-store", "data"),
        prevent_initial_call=True,
    )
    def _download_event_trajectory(n_clicks, viewer):
        if n_clicks is None or not viewer:
            raise PreventUpdate
        event_id = str(viewer.get("event_id") or "event")
        return {
            "content": svc.event_viewer_trajectory_text(viewer),
            "filename": f"{event_id}_subset.lammpstrj",
            "type": "text/plain",
        }

    @app.callback(
        Output("event-ovito-launch-status", "children"),
        Input("event-ovito-open-btn", "n_clicks"),
        State("event-viewer-store", "data"),
        prevent_initial_call=True,
    )
    def _launch_event_ovito(n_clicks, viewer):
        if n_clicks is None or not viewer:
            raise PreventUpdate
        try:
            launched = svc.launch_event_in_ovito(viewer)
        except svc.ServiceError as exc:
            return str(exc.message)
        return f"OVITO 已启动（PID {launched['pid']}）。"

    @app.callback(
        Output("event-ovito-download", "data"),
        Input("event-ovito-btn", "n_clicks"),
        State("event-viewer-store", "data"),
        prevent_initial_call=True,
    )
    def _download_event_ovito_script(n_clicks, viewer):
        if n_clicks is None or not viewer:
            raise PreventUpdate
        event_id = str(viewer.get("event_id") or "event")
        trajectory_name = f"{event_id}_subset.lammpstrj"
        return {
            "content": svc.event_viewer_ovito_script(
                viewer,
                trajectory_name=trajectory_name,
            ),
            "filename": f"{event_id}_view_ovito.py",
            "type": "text/x-python",
        }

    @app.callback(
        Output("event-vmd-download", "data"),
        Input("event-vmd-btn", "n_clicks"),
        State("event-viewer-store", "data"),
        prevent_initial_call=True,
    )
    def _download_event_vmd(n_clicks, viewer):
        if n_clicks is None or not viewer:
            raise PreventUpdate
        event_id = str(viewer.get("event_id") or "event")
        trajectory_name = f"{event_id}_subset.lammpstrj"
        return {
            "content": svc.event_viewer_vmd_script(
                viewer,
                trajectory_name=trajectory_name,
            ),
            "filename": f"{event_id}_view.tcl",
            "type": "text/plain",
        }
