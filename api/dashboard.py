# -*- coding: utf-8 -*-
"""Blueprint: dashboard CRUD + refresh endpoints."""
import json
import logging
import os
import re
import datetime
import uuid

from flask import Blueprint, request, jsonify, render_template, abort
from infrastructure.paths import data_path

log = logging.getLogger(__name__)

bp = Blueprint("dashboard", __name__)

_DASHBOARD_DIR = str(data_path("outputs", "Dashboard"))

_SCHEMA_VERSION = 1


def _dashboard_path(dashboard_id: str) -> str:
    safe = re.sub(r'[^\w\-]', '_', dashboard_id)
    return os.path.join(_DASHBOARD_DIR, f"{safe}.json")


def _load_dashboard(dashboard_id: str) -> dict:
    path = _dashboard_path(dashboard_id)
    if not os.path.isfile(path):
        abort(404, description=f"Dashboard '{dashboard_id}' not found")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _save_dashboard(data: dict, dashboard_id: str) -> None:
    os.makedirs(_DASHBOARD_DIR, exist_ok=True)
    path = _dashboard_path(dashboard_id)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


_CHART_TYPE_ALIASES = {
    "Scatter_Chart": "Scatter_Plot",
    "Heatmap_Chart": "Heatmap",
    "Donut_Chart": "Pie_Chart",
    "Table_Chart": "Bar_Chart",
    "Grouped_Bar": "Grouped_Bar_Chart",
    "Stacked_Bar": "Stacked_Bar_Chart",
}


def _sql_guard(sql: str, workspace_authorization=None) -> str | None:
    from agent.validate import validate_tool_args
    return validate_tool_args(
        "query_data", {"sql": sql},
        workspace_authorization=workspace_authorization,
    )


def _render_kpi_from_df(df, error: str | None = None) -> dict:
    """Return KPI_Card value fields from an already-fetched DataFrame."""
    if error:
        return {"kpi_value": "—", "kpi_sub": "", "kpi_trend": None, "error": error}
    try:
        if df is None or df.empty or len(df.columns) == 0:
            return {"kpi_value": "—", "kpi_sub": "", "kpi_trend": None, "error": "No rows returned"}
        row = df.iloc[0]
        kpi_value = row.iloc[0]
        try:
            fv = float(kpi_value)
            if abs(fv) >= 1e8:
                kpi_value = f"{fv/1e8:.2f} 亿"
            elif abs(fv) >= 1e4:
                kpi_value = f"{fv/1e4:.1f} 万"
            elif fv == int(fv):
                kpi_value = str(int(fv))
            else:
                kpi_value = f"{fv:.2f}"
        except (TypeError, ValueError):
            kpi_value = str(kpi_value)
        kpi_sub = str(row.iloc[1]) if len(row) > 1 else ""
        kpi_trend = None
        if len(row) > 2:
            try:
                kpi_trend = round(float(row.iloc[2]), 1)
            except (TypeError, ValueError):
                pass
        return {"kpi_value": kpi_value, "kpi_sub": kpi_sub, "kpi_trend": kpi_trend, "error": None}
    except Exception as exc:
        log.warning("[dashboard] KPI render error: %s", exc)
        return {"kpi_value": "—", "kpi_sub": "", "kpi_trend": None, "error": str(exc)}


def _render_kpi_widget(data_source, spec: dict, workspace_authorization=None) -> dict:
    """Execute SQL for a KPI_Card widget and return scalar value fields."""
    prefetched = prefetch_dashboard_widget_data(data_source, [spec], workspace_authorization)
    item = prefetched[0] if prefetched else {"df": None, "error": "No SQL"}
    return _render_kpi_from_df(item.get("df"), item.get("error"))


def _render_widget_from_df(
    chart_store, color_scheme: str, spec: dict, df, error: str | None = None,
) -> tuple[str | None, str | None]:
    """Generate chart HTML from an already-fetched DataFrame."""
    if error:
        return None, error
    try:
        from chart_generate import generate_chart as _gen
        if df is None or df.empty:
            return None, "Query returned no rows"

        opts = {}
        if spec.get("title"):
            opts["title"] = spec["title"]
        opts.update(spec.get("options", {}))

        result = _gen(
            df=df,
            chart_type=_CHART_TYPE_ALIASES.get(spec.get("chart_type", "Bar_Chart"), spec.get("chart_type", "Bar_Chart")),
            mapping=spec.get("field_mapping", {}),
            options=opts,
            color_scheme=color_scheme,
        )
        if "error" in result:
            return None, result["error"]

        chart_id = str(uuid.uuid4())
        chart_store[chart_id] = result["html"]
        return chart_id, None

    except Exception as exc:
        log.warning("[dashboard] widget render error: %s", exc)
        return None, str(exc)


def _render_widget(
    data_source, chart_store, color_scheme: str, spec: dict,
    workspace_authorization=None,
) -> tuple[str | None, str | None]:
    """Execute SQL and generate chart HTML. Returns (chart_id, error)."""
    prefetched = prefetch_dashboard_widget_data(data_source, [spec], workspace_authorization)
    item = prefetched[0] if prefetched else {"df": None, "error": "No SQL defined"}
    return _render_widget_from_df(
        chart_store, color_scheme, spec, item.get("df"), item.get("error"),
    )


def prefetch_dashboard_widget_data(
    data_source, widgets_spec: list, workspace_authorization=None,
) -> list[dict]:
    """Fetch widget SQL results on the caller thread before any worker rendering."""
    prefetched = []
    for spec in widgets_spec:
        sql = spec.get("sql", "")
        error = ""
        df = None
        if not sql or not data_source:
            error = "No SQL defined" if not sql else "No data source"
        else:
            guard_error = _sql_guard(sql, workspace_authorization)
            if guard_error:
                error = guard_error
            else:
                try:
                    df, err = data_source.execute_query(sql)
                    if err:
                        error = f"SQL error: {err}"
                except Exception as exc:
                    log.warning("[dashboard] widget SQL error: %s", exc)
                    error = str(exc)
        prefetched.append({"spec": spec, "df": df, "error": error or None})
    return prefetched


# ── Page route ────────────────────────────────────────────────────────────────

@bp.get("/dashboard/<dashboard_id>")
def dashboard_page(dashboard_id: str):
    return render_template("dashboard.html", dashboard_id=dashboard_id)


# ── API: create (called by agent generate_dashboard tool) ────────────────────

def build_dashboard(
    data_source, chart_store, *, session_id: str, workspace_id: str,
    name: str, widgets_spec: list, color_scheme: str,
    workspace_authorization=None,
) -> dict:
    """Build a dashboard from an already-leased data-source snapshot."""
    prefetched = prefetch_dashboard_widget_data(
        data_source, widgets_spec, workspace_authorization,
    )
    return build_dashboard_from_prefetched_widgets(
        chart_store,
        session_id=session_id,
        workspace_id=workspace_id,
        name=name,
        widget_inputs=prefetched,
        color_scheme=color_scheme,
    )


def build_dashboard_from_prefetched_widgets(
    chart_store, *, session_id: str, workspace_id: str, name: str,
    widget_inputs: list, color_scheme: str,
) -> dict:
    """Build dashboard JSON from caller-thread SQL results."""
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_name = re.sub(r'[^\w\-]', '_', name)
    dashboard_id = f"{safe_name}_{ts}"

    built_widgets = []
    for item in widget_inputs:
        spec = item.get("spec", {})
        df = item.get("df")
        error = item.get("error")
        widget_id = spec.get("id") or str(uuid.uuid4())[:8]
        chart_type = spec.get("chart_type", "Bar_Chart")
        base = {
            "id": widget_id,
            "title": spec.get("title", ""),
            "chart_type": chart_type,
            "sql": spec.get("sql", ""),
            "field_mapping": spec.get("field_mapping", {}),
            "options": spec.get("options", {}),
            "grid": spec.get("grid", {"x": 0, "y": 0, "w": 6, "h": 4}),
        }
        if chart_type == "KPI_Card":
            base.update(_render_kpi_from_df(df, error))
        else:
            chart_id, error = _render_widget_from_df(
                chart_store, color_scheme, spec, df, error,
            )
            base["chart_id"] = chart_id
            base["error"] = error
        built_widgets.append(base)

    dashboard = {
        "_schema_version": _SCHEMA_VERSION,
        "id": dashboard_id,
        "name": name,
        "created_at": datetime.datetime.now().isoformat(),
        "color_scheme": color_scheme,
        "session_id": session_id,
        "workspace_id": workspace_id,
        "widgets": built_widgets,
    }
    _save_dashboard(dashboard, dashboard_id)
    return {"dashboard_id": dashboard_id, "url": f"/dashboard/{dashboard_id}"}

@bp.post("/api/dashboard/generate")
def create_dashboard():
    from .state import session_manager, chart_store
    body = request.get_json(force=True)
    sid = body.get("session_id", "")
    name = body.get("name", "Dashboard")
    widgets_spec = body.get("widgets", [])
    color_scheme = body.get("color_scheme", "mckinsey")

    sess = session_manager.get(sid)
    data_source = sess.data_source if sess else None
    from data.workspace import workspace_manager
    current_workspace_id = workspace_manager.workspace_id_for_session(sid) or ""
    requested_workspace_id = str(body.get("workspace_id") or "")
    if requested_workspace_id and requested_workspace_id != current_workspace_id:
        return jsonify({"error": "Workspace changed before dashboard generation"}), 409
    workspace_authorization = workspace_manager.path_authorization(current_workspace_id)

    return jsonify(build_dashboard(
        data_source,
        chart_store,
        session_id=sid,
        workspace_id=current_workspace_id,
        name=name,
        widgets_spec=widgets_spec,
        color_scheme=color_scheme,
        workspace_authorization=workspace_authorization,
    ))


# ── API: get ──────────────────────────────────────────────────────────────────

@bp.get("/api/dashboard/<dashboard_id>")
def get_dashboard(dashboard_id: str):
    return jsonify(_load_dashboard(dashboard_id))


# ── API: list ─────────────────────────────────────────────────────────────────

@bp.get("/api/dashboards")
def list_dashboards():
    os.makedirs(_DASHBOARD_DIR, exist_ok=True)
    results = []
    for fname in sorted(os.listdir(_DASHBOARD_DIR), reverse=True):
        if not fname.endswith(".json"):
            continue
        fpath = os.path.join(_DASHBOARD_DIR, fname)
        try:
            with open(fpath, encoding="utf-8") as f:
                d = json.load(f)
            results.append({
                "id": d.get("id", fname[:-5]),
                "name": d.get("name", fname[:-5]),
                "created_at": d.get("created_at", ""),
                "widget_count": len(d.get("widgets", [])),
            })
        except Exception as e:
            log.debug("[dashboard] failed to load dashboard %s: %s", fname, e)
            continue
    return jsonify(results)


# ── API: update layout ────────────────────────────────────────────────────────

@bp.put("/api/dashboard/<dashboard_id>")
def update_dashboard(dashboard_id: str):
    body = request.get_json(force=True)
    dashboard = _load_dashboard(dashboard_id)

    grid_updates = {w["id"]: w["grid"] for w in body.get("widgets", []) if "id" in w and "grid" in w}
    if grid_updates:
        for widget in dashboard["widgets"]:
            if widget["id"] in grid_updates:
                widget["grid"] = grid_updates[widget["id"]]

    if "name" in body:
        dashboard["name"] = body["name"]

    if "container_width" in body:
        dashboard["container_width"] = body["container_width"]

    dashboard["updated_at"] = datetime.datetime.now().isoformat()
    _save_dashboard(dashboard, dashboard_id)
    return jsonify({"ok": True})


# ── API: delete ───────────────────────────────────────────────────────────────

@bp.delete("/api/dashboard/<dashboard_id>")
def delete_dashboard(dashboard_id: str):
    path = _dashboard_path(dashboard_id)
    if not os.path.isfile(path):
        abort(404)
    os.remove(path)
    return jsonify({"ok": True})


# ── API: refresh all widgets ───────────────────────────────────────────────────

@bp.post("/api/dashboard/<dashboard_id>/refresh")
def refresh_dashboard(dashboard_id: str):
    from .state import session_manager, chart_store
    body = request.get_json(force=True)
    sid = body.get("session_id", "")

    sess = session_manager.get(sid)
    if not sess:
        return jsonify({"error": "Session not found — please open the dashboard from an active chat session"}), 404

    data_source = sess.data_source
    if not data_source:
        return jsonify({"error": "No data source connected in the session. Upload data first."}), 400

    dashboard = _load_dashboard(dashboard_id)
    from data.workspace import workspace_manager
    current_workspace_id = workspace_manager.workspace_id_for_session(sid) or ""
    dashboard_workspace_id = str(dashboard.get("workspace_id") or "")
    if dashboard_workspace_id and dashboard_workspace_id != current_workspace_id:
        return jsonify({"error": "Dashboard belongs to a different Workspace"}), 409
    workspace_authorization = workspace_manager.path_authorization(current_workspace_id)
    color_scheme = dashboard.get("color_scheme", "mckinsey")

    widget_results = []
    kpi_results = []
    for widget in dashboard["widgets"]:
        if widget.get("chart_type") == "KPI_Card":
            # KPI cards: re-execute SQL and extract scalar value
            kpi = _render_kpi_widget(data_source, widget, workspace_authorization)
            widget.update(kpi)
            kpi_results.append({"id": widget["id"], **kpi})
        else:
            chart_id, error = _render_widget(
                data_source, chart_store, color_scheme, widget, workspace_authorization,
            )
            widget["chart_id"] = chart_id
            widget["error"] = error
            widget_results.append({"id": widget["id"], "chart_id": chart_id, "error": error})

    dashboard["refreshed_at"] = datetime.datetime.now().isoformat()
    _save_dashboard(dashboard, dashboard_id)

    return jsonify({"ok": True, "widgets": widget_results, "kpi_widgets": kpi_results})


# ── API: refresh single widget ─────────────────────────────────────────────────

@bp.post("/api/dashboard/<dashboard_id>/widget/<widget_id>/refresh")
def refresh_widget(dashboard_id: str, widget_id: str):
    from .state import session_manager, chart_store
    body = request.get_json(force=True)
    sid = body.get("session_id", "")

    sess = session_manager.get(sid)
    if not sess:
        return jsonify({"error": "Session not found"}), 404

    data_source = sess.data_source
    if not data_source:
        return jsonify({"error": "No data source connected"}), 400

    dashboard = _load_dashboard(dashboard_id)
    from data.workspace import workspace_manager
    current_workspace_id = workspace_manager.workspace_id_for_session(sid) or ""
    dashboard_workspace_id = str(dashboard.get("workspace_id") or "")
    if dashboard_workspace_id and dashboard_workspace_id != current_workspace_id:
        return jsonify({"error": "Dashboard belongs to a different Workspace"}), 409
    workspace_authorization = workspace_manager.path_authorization(current_workspace_id)
    widget = next((w for w in dashboard["widgets"] if w["id"] == widget_id), None)
    if not widget:
        return jsonify({"error": f"Widget '{widget_id}' not found"}), 404

    color_scheme = dashboard.get("color_scheme", "mckinsey")

    if widget.get("chart_type") == "KPI_Card":
        kpi = _render_kpi_widget(data_source, widget, workspace_authorization)
        widget.update(kpi)
        _save_dashboard(dashboard, dashboard_id)
        return jsonify({"ok": True, "id": widget_id, **kpi})
    else:
        chart_id, error = _render_widget(
            data_source, chart_store, color_scheme, widget, workspace_authorization,
        )
        widget["chart_id"] = chart_id
        widget["error"] = error
        _save_dashboard(dashboard, dashboard_id)
        return jsonify({"ok": True, "id": widget_id, "chart_id": chart_id, "error": error})
