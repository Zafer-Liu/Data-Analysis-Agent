"""Offline dependency/resource smoke test executed inside a frozen build."""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path


def run_frozen_smoke() -> int:
    from infrastructure.paths import data_path, resource_path

    checks: dict[str, dict] = {}
    for import_root in (
        resource_path("Function", "Charts_generation"),
        resource_path("Function", "Output"),
    ):
        sys.path.insert(0, str(import_root))

    def check_import(name: str) -> None:
        try:
            importlib.import_module(name)
            checks[name] = {"ok": True}
        except Exception as exc:
            checks[name] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    for module_name in (
        "duckdb",
        "openpyxl",
        "xlrd",
        "python_calamine",
        "pymysql",
        "psycopg2",
        "sqlalchemy.dialects.mysql.pymysql",
        "sqlalchemy.dialects.postgresql.psycopg2",
        "pmdarima",
        "statsmodels.tsa.api",
        "PPT",
    ):
        check_import(module_name)

    for relative in ("templates", "static", "commands", "skills", "Information"):
        path = resource_path(relative)
        checks[f"resource:{relative}"] = {
            "ok": path.is_dir(),
            "path": str(path),
        }
    chat_bundle = resource_path("static", "dist", "chat-app.js")
    dist_chunks = resource_path("static", "dist", "chunks")
    checks["resource:static/dist/chat-app.js"] = {
        "ok": chat_bundle.is_file() and chat_bundle.stat().st_size > 200_000,
        "path": str(chat_bundle),
        "size": chat_bundle.stat().st_size if chat_bundle.exists() else 0,
    }
    checks["resource:static/dist/chunks"] = {
        "ok": dist_chunks.is_dir() and any(dist_chunks.glob("*.js")),
        "path": str(dist_chunks),
    }

    try:
        from Function.Analyze.registry import get_all

        analyses = get_all()
        broken = sorted(name for name, item in analyses.items() if item.get("run") is None)
        checks["analysis_registry"] = {
            "ok": bool(analyses) and not broken,
            "count": len(analyses),
            "broken": broken,
        }
    except Exception as exc:
        checks["analysis_registry"] = {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
        }

    try:
        import pandas as pd

        chart_root = resource_path("Function", "Charts_generation")
        sys.path.insert(0, str(chart_root))
        from chart_generate import generate_chart

        chart = generate_chart(
            df=pd.DataFrame({"city": ["A", "B"], "revenue": [10, 20]}),
            chart_type="Bar_Chart",
            mapping={"x": "city", "y": "revenue"},
        )
        checks["chart_generation"] = {
            "ok": chart.get("success") is True and len(chart.get("html", "")) > 500,
            "error": chart.get("error", ""),
        }
    except Exception as exc:
        checks["chart_generation"] = {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
        }

    ok = all(item.get("ok") is True for item in checks.values())
    report = {
        "schema_version": 1,
        "ok": ok,
        "frozen": bool(getattr(sys, "frozen", False)),
        "checks": checks,
    }
    output = data_path("outputs", "build-smoke.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if ok else 3
