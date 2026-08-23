# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller onedir specification; consumes only the audited staging tree."""

from pathlib import Path
import os
import sys

from PyInstaller.utils.hooks import collect_submodules, copy_metadata


raw_staging = os.environ.get("BAA_STAGING_ROOT", "").strip()
if not raw_staging:
    raise SystemExit("BAA_STAGING_ROOT must point to a clean packaging staging directory")

STAGING = Path(raw_staging).expanduser().resolve(strict=True)
ENTRY = STAGING / "packaging" / "desktop_launcher.py"
if not ENTRY.is_file():
    raise SystemExit(f"desktop launcher is missing from staging: {ENTRY}")
if any((STAGING / name).exists() for name in ("MCP", "uploads", "outputs")):
    raise SystemExit("staging contains a forbidden MCP/uploads/outputs directory")

CHART_ROOT = STAGING / "Function" / "Charts_generation"
OUTPUT_ROOT = STAGING / "Function" / "Output"
for import_root in (STAGING, CHART_ROOT, OUTPUT_ROOT):
    sys.path.insert(0, str(import_root))


def staged_tree(relative: str, *, suffixes: set[str] | None = None):
    source = STAGING / relative
    if not source.is_dir():
        raise SystemExit(f"required staged resource is missing: {relative}")
    result = []
    for item in sorted(source.rglob("*")):
        if not item.is_file() or "__pycache__" in item.parts or item.suffix == ".pyc":
            continue
        if suffixes is not None and item.suffix.lower() not in suffixes:
            continue
        destination = item.parent.relative_to(STAGING).as_posix()
        result.append((str(item), destination))
    if not result:
        raise SystemExit(f"staged resource tree is empty: {relative}")
    return result


def staged_file(relative: str, destination: str):
    source = STAGING / relative
    if not source.is_file():
        raise SystemExit(f"required staged resource is missing: {relative}")
    return (str(source), destination)


datas = []
for resource_tree in ("templates", "static", "commands", "skills", "Information"):
    datas.extend(staged_tree(resource_tree))
datas.extend(
    staged_tree("Function", suffixes={".py", ".ttf", ".pptx"})
)
datas.extend([
    # Analyze registry loads analyze.py by path; chart/PPT modules also need
    # their fonts and template assets available below resource_root().
    staged_file("LLM/chart_rules.yaml", "LLM"),
    # Hidden charts live as top-level ``charts`` modules; Bar_Chart resolves
    # this font relative to that package's frozen __file__ location.
    staged_file(
        "Function/Charts_generation/charts/AlibabaPuHuiTi-3-55-Regular.ttf",
        "charts",
    ),
])
# pmdarima resolves its version through importlib.metadata during import.
datas.extend(copy_metadata("pmdarima"))
# tokenizers needs its metadata for version checks at runtime.
datas.extend(copy_metadata("tokenizers"))

hiddenimports = [
    # pandas selects Excel engines dynamically.
    "openpyxl",
    "xlrd",
    "python_calamine",
    # SQLAlchemy/DuckDB select drivers and dialects at runtime.
    "duckdb",
    "pymysql",
    "psycopg2",
    "pyodbc",
    "sqlalchemy.dialects.mysql.pymysql",
    "sqlalchemy.dialects.postgresql.psycopg2",
    "sqlalchemy.dialects.mssql.pyodbc",
    # Analyze modules are source-loaded and import these lazily.
    "pmdarima",
    "statsmodels.tsa.api",
    "statsmodels.tsa.arima.model",
    "statsmodels.tsa.statespace.sarimax",
    "statsmodels.tsa.stattools",
    "statsmodels.tsa.seasonal",
    # Neural embedding (BGE-small-zh) uses ONNX Runtime; tokenizers is
    # imported lazily and therefore needs an explicit inclusion.
    "tokenizers",
    # G2 remote GPU connectivity imports these lazily at runtime.
    "paramiko",
    "keyring",
    # Torch_MLP is loaded by the path-based analysis registry, so its training
    # target selector is invisible to PyInstaller's normal import discovery.
    "infrastructure.training_router",
]
hiddenimports += collect_submodules("charts")
hiddenimports += collect_submodules("PPT")


a = Analysis(
    [str(ENTRY)],
    pathex=[str(STAGING), str(CHART_ROOT), str(OUTPUT_ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=sorted(set(hiddenimports)),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "MCP",
        "MCP.flowchart_server",
        "gunicorn",
        "pytest",
    ],
    noarchive=False,
    optimize=1,
)


def unnecessary_dependency_data(item) -> bool:
    relative = str(item[0]).replace("\\", "/").lower()
    return (
        "/matplotlib/mpl-data/sample_data/" in f"/{relative}"
        or (
            "/matplotlib/mpl-data/images/" in f"/{relative}"
            and relative.endswith(".pdf")
        )
        or "/sklearn/datasets/data/" in f"/{relative}"
    )


a.datas = [item for item in a.datas if not unnecessary_dependency_data(item)]
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="BusinessAnalyticsAgent",
    icon=str(STAGING / "static" / "Images" / "icon.png"),
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="BusinessAnalyticsAgent",
)

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="Business Analytics Agent.app",
        icon=None,
        bundle_identifier="com.businessanalytics.agent",
        version="1.0.0",
        info_plist={
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "12.0",
        },
    )
