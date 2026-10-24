#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Chart Generate - 调用 charts 目录中的实际图表生成模块"""
import sys
from pathlib import Path
import pandas as pd
from typing import Dict, List, Optional, Any
import logging
import importlib

logger = logging.getLogger(__name__)


def _detect_chart_project() -> Path:
    """定位 Charts_generation 根目录。

    源码/云端部署：``__file__`` 即真实路径，charts/ 下有全部图表子目录，
    直接返回。

    PyInstaller frozen（桌面端）：本模块从 exe 内嵌 PYZ 加载，
    ``__file__`` 是 ``<_internal>/chart_generate.py`` 伪路径——其 parent
    下的 charts/ 只有 spec 放的字体文件（无图表子目录），目录枚举会误判
    所有图表类型 not found。此时改用随包以 datas 实体分发的
    ``<_internal>/Function/Charts_generation``（business_agent.spec 的
    staged_tree("Function") 会把整棵树连同 .py 放进发行目录），枚举恢复
    正常；模块导入本身仍优先走 PYZ（FrozenImporter 在 meta_path），
    两者互不干扰。
    """

    def _has_chart_modules(base: Path) -> bool:
        charts_dir = base / "charts"
        try:
            # 图表模块都是子目录（Bar_Chart/、Line_Chart/…）；
            # 顶层 charts/ 若只有字体文件（frozen 字体资源位）则不算
            return charts_dir.is_dir() and any(
                entry.is_dir() for entry in charts_dir.iterdir()
            )
        except OSError:
            return False

    here = Path(__file__).parent
    if _has_chart_modules(here):
        return here
    meipass = getattr(sys, "_MEIPASS", None)
    if getattr(sys, "frozen", False) and meipass:
        candidate = Path(meipass) / "Function" / "Charts_generation"
        if _has_chart_modules(candidate):
            return candidate
    return here


CHART_PROJECT = _detect_chart_project()
sys.path.insert(0, str(CHART_PROJECT))

# ── 本地静态资源路径（Flask 服务，通过 /static/vendor/ 访问）──────────────
_PLOTLY_LOCAL = "/static/vendor/plotly.min.js"
_PLOTLY_CDN   = "https://cdn.plot.ly/plotly-"          # 前缀，用于检测
_PLOTLY_TAG   = f"<script src='{_PLOTLY_LOCAL}'></script>"

_ECHARTS_LOCAL = "/static/vendor/echarts.min.js"
# pyecharts 与 echarts 的 CDN/资源前缀，用于检测并替换为本地
_ECHARTS_CDN_PATTERNS = (
    "https://assets.pyecharts.org/assets/",
    "https://cdn.jsdelivr.net/npm/echarts",
    "https://cdn.bootcss.com/echarts",
)
_ECHARTS_TAG = f"<script src='{_ECHARTS_LOCAL}'></script>"

_D3_LOCAL = "/static/vendor/d3.v7.min.js"
# D3 的 CDN 外链，用于检测并替换为本地
_D3_CDN_PATTERNS = (
    "https://d3js.org/d3.v7.min.js",
    "https://cdn.jsdelivr.net/npm/d3@",
    "https://cdn.jsdelivr.net/npm/d3/dist/d3",
    "https://cdnjs.cloudflare.com/ajax/libs/d3/",
    "https://unpkg.com/d3@",
)
_D3_TAG = f"<script src='{_D3_LOCAL}'></script>"


def _chart_key(chart_type: str) -> str:
    return str(chart_type or "").strip().lower()


def _unwrap_item_dicts(mapping: Dict[str, Any]) -> Dict[str, Any]:
    """Unwrap {"item": [...]} dicts that some LLM serializers produce.

    Certain function-calling adapters convert XML-style arrays
    into {"y": {"item": ["col1"]}} instead of {"y": ["col1"]}.
    This unwraps such structures so downstream logic sees plain lists.
    """
    result = {}
    for key, value in (mapping or {}).items():
        if isinstance(value, dict):
            if "item" in value and isinstance(value["item"], (list, tuple)):
                result[key] = list(value["item"])
                continue
            if "item" in value and isinstance(value["item"], str):
                result[key] = value["item"]
                continue
            list_vals = [v for v in value.values() if isinstance(v, (list, tuple))]
            if list_vals:
                result[key] = list(list_vals[0])
                continue
            result[key] = value
        else:
            result[key] = value
    return result


def _normalize_chart_mapping(
    chart_type: str,
    mapping: Dict[str, Any],
    columns=None,
) -> Dict[str, Any]:
    """Normalize common LLM field-mapping aliases before chart dispatch."""
    # Unwrap {"item": [...]} wrapping from certain LLM serializers BEFORE
    # any str() coercion or comma-split logic sees it.
    normalized = _unwrap_item_dicts(mapping or {})
    chart_key = _chart_key(chart_type)
    column_names = {str(col) for col in ([] if columns is None else columns)}

    # Registry roles use singular names. Some models still emit common plotting
    # aliases; normalize those centrally instead of making every chart support
    # the same typo/variant independently.
    aliases = {
        "categories": "x",
        "labels": "label",
        "values": "value",
    }
    for alias, canonical in aliases.items():
        if alias in normalized and canonical not in normalized:
            normalized[canonical] = normalized[alias]

    # Heatmap's renderer uses semantic row/col names while the central chart
    # registry declares the equivalent x/y roles. Preserve both forms so a
    # caller-provided long-format mapping passes dispatcher validation without
    # losing the clearer row/col contract used by the renderer.
    if chart_key == "heatmap":
        if normalized.get("row") and not normalized.get("y"):
            normalized["y"] = normalized["row"]
        if normalized.get("col") and not normalized.get("x"):
            normalized["x"] = normalized["col"]

    # 部分模块的历史实现读取的 mapping 角色名与注册表 required_roles 不一致。
    # 调度层校验放行后若不做归一化，LLM 按注册表传的列会被模块静默丢弃、
    # 退化为 _auto_col 启发式猜列。这里统一补写模块实际读取的角色键
    # （注册表角色保留不动，预校验仍按注册表执行）。
    _module_role_aliases: Dict[str, Dict[str, str]] = {
        "pyramid_chart": {"label": "age", "left_value": "male", "right_value": "female"},
        "sparkline": {"x": "label"},
        "error_bar_chart": {"label": "x", "value": "y"},
        "histogram_pareto_chart": {"label": "x", "value": "y"},
        "diverging_bar_chart": {"label": "item"},
        "dot_plot": {"category": "y"},
        "waffle": {"category": "label"},
    }
    aliases_for_chart = _module_role_aliases.get(chart_key)
    if aliases_for_chart:
        for registry_role, module_role in aliases_for_chart.items():
            value = normalized.get(registry_role)
            if value and not normalized.get(module_role):
                normalized[module_role] = value

    # Models occasionally serialize a column list as one comma-separated
    # string. Only split when the full string is not itself a real column name.
    for key in ("y", "value_cols", "dimensions"):
        value = normalized.get(key)
        if (
            isinstance(value, str)
            and "," in value
            and value not in column_names
        ):
            normalized[key] = [part.strip() for part in value.split(",") if part.strip()]

    # Wide tables sometimes arrive as series=[col1, col2, ...]. In all three
    # charts a list means value columns, not the name of a grouping column.
    series = normalized.get("series")
    if isinstance(series, (list, tuple, set)):
        series_items = list(series)
        series_cols = [str(item) for item in series_items if isinstance(item, str)]

        # A list of {name, color} objects is presentation metadata, not a field
        # mapping. Ignore it; the actual columns must come from y/value_cols.
        if len(series_cols) == len(series_items):
            if chart_key in {"line_chart", "area_chart", "stacked_area_chart"}:
                normalized.setdefault("y", series_cols)
            else:
                normalized.setdefault("value_cols", series_cols)
        normalized.pop("series", None)

    # Multi-series bar charts accept y=[...] as wide-format shorthand.
    y_value = normalized.get("y")
    if chart_key in {"grouped_bar_chart", "stacked_bar_chart"} and isinstance(
        y_value, (list, tuple, set)
    ):
        normalized.setdefault("value_cols", list(y_value))

    # Wide-format bars are defined by x + value_cols. Ignore invented long-
    # format fields and literal color strings that are not SQL result columns;
    # otherwise the module may silently render only the first numeric column.
    if chart_key in {"grouped_bar_chart", "stacked_bar_chart"} and normalized.get("value_cols"):
        for key in ("series", "color"):
            value = normalized.get(key)
            if isinstance(value, str) and value not in column_names:
                normalized.pop(key, None)

    return normalized


def _inject_plotly(html: str) -> str:
    """确保 HTML 内有本地 plotly 脚本，且脚本在所有 inline <script> 之前加载。

    三种场景：
    1. 已有 CDN plotly 外链（include_plotlyjs="cdn" 模式）→ 替换为本地路径。
    2. 已有内联 plotly bundle（include_plotlyjs=True 模式）→ 不处理，保持原样。
    3. 没有任何 plotly 脚本（include_plotlyjs=False 模式）→ 在 <head> 里注入本地
       外链，并把所有 inline Plotly.newPlot/react 调用包裹进 DOMContentLoaded
       回调，确保库加载完毕后再执行，消除竞态。
    """
    import re

    # 场景 1：替换 CDN 外链为本地路径
    html = re.sub(
        r"<script[^>]+src=['\"]https?://cdn\.plot\.ly/plotly-[^'\"]+['\"][^>]*></script>",
        _PLOTLY_TAG,
        html,
    )
    html = re.sub(
        r"<script[^>]+src=['\"]https?://cdn\.jsdelivr\.net/npm/plotly\.js[^'\"]*['\"][^>]*></script>",
        _PLOTLY_TAG,
        html,
    )

    # 场景 2/3：检查是否已经有 plotly 脚本（外链 src 或内联 bundle）
    has_plotly_src    = bool(re.search(r"<script[^>]+plotly", html, re.IGNORECASE))
    has_plotly_inline = "Plotly=" in html or "window.Plotly" in html  # 内联 bundle 特征

    if not has_plotly_src and not has_plotly_inline:
        # 场景 3：库缺失。
        # 把外链注入到 </head> 前（或 <body> 前），保证在 inline script 之前被
        # 解析器看到；同时把所有 inline Plotly.* 调用包进 DOMContentLoaded，
        # 消除外链脚本尚未执行时 inline script 就运行的竞态。
        if "</head>" in html:
            html = html.replace("</head>", f"{_PLOTLY_TAG}\n</head>", 1)
        elif "<body" in html:
            html = re.sub(r"<body[^>]*>",
                          lambda m: m.group(0) + f"\n{_PLOTLY_TAG}", html, count=1)
        else:
            html = _PLOTLY_TAG + "\n" + html

        # 把 inline Plotly.newPlot / Plotly.react 包进 DOMContentLoaded，
        # 确保外链 plotly.min.js 执行完毕后再调用。
        # 匹配：<script>...Plotly.newPlot(...)...</script>（不含 src 属性）
        def _wrap_inline(m: re.Match) -> str:
            body = m.group(1)
            if "Plotly." not in body:
                return m.group(0)
            return (
                "<script>"
                "document.addEventListener('DOMContentLoaded',function(){"
                + body +
                "});"
                "</script>"
            )

        html = re.sub(
            r"<script(?![^>]+\bsrc\b)[^>]*>([\s\S]*?)</script>",
            _wrap_inline,
            html,
        )

    return html


def _inject_echarts(html: str) -> str:
    """确保 pyecharts 生成的 HTML 内有本地 echarts 库。

    pyecharts 的 ``render_embed()`` 只输出图表片段（div + inline
    ``echarts.init(...)``），不包含 echarts 库本身。在 iframe 内或单独打开
    时，inline 脚本因 ``echarts`` 未定义而静默失败，页面只剩空白 div。

    本函数做三件事：
    1. 把已存在的 echarts CDN 外链替换为本地 ``/static/vendor/echarts.min.js``。
    2. 若页面有 ``echarts.init`` 调用但没有任何 echarts 库脚本，在 ``</head>``
       前注入本地外链。
    3. 把 inline ``echarts.init`` 调用包进 DOMContentLoaded，避免外链未就绪。

    注意：pyecharts 的地图数据文件（如 china.js）仍从其 CDN 加载，离线环境
    下地图数据不可用是已知限制；echarts 库本身由本地提供。
    """
    import re

    # 1. 替换任何已存在的 echarts CDN 外链为本地。
    #    (?![^'\"]*maps/) 排除地图数据脚本（china.js 等必须保留原 URL，
    #    误换成 echarts 库会导致 "Map china not exists" 空白地图）。
    for pat in _ECHARTS_CDN_PATTERNS:
        html = re.sub(
            r"<script[^>]+src=['\"]" + re.escape(pat)
            + r"(?![^'\"]*maps/)[^'\"]*['\"][^>]*></script>",
            _ECHARTS_TAG,
            html,
        )

    # 2. 检测是否需要注入库：页面引用了 echarts 但没有库脚本
    has_echarts_ref = "echarts.init" in html or "echarts.connect" in html
    has_echarts_lib = bool(re.search(r"<script[^>]+echarts", html, re.IGNORECASE)) or \
                      "window.echarts" in html

    if has_echarts_ref and not has_echarts_lib:
        if "</head>" in html:
            html = html.replace("</head>", f"{_ECHARTS_TAG}\n</head>", 1)
        elif "<body" in html:
            html = re.sub(r"<body[^>]*>",
                          lambda m: m.group(0) + f"\n{_ECHARTS_TAG}", html, count=1)
        else:
            html = _ECHARTS_TAG + "\n" + html

        # 3. 把 inline echarts.init 调用包进 DOMContentLoaded
        def _wrap_echarts_inline(m: "re.Match") -> str:
            body = m.group(1)
            if "echarts." not in body:
                return m.group(0)
            return (
                "<script>"
                "document.addEventListener('DOMContentLoaded',function(){"
                + body +
                "});"
                "</script>"
            )

        html = re.sub(
            r"<script(?![^>]+\bsrc\b)[^>]*>([\s\S]*?)</script>",
            _wrap_echarts_inline,
            html,
        )

    return html


def _inject_d3(html: str) -> str:
    """确保 D3.js 图表 HTML（如 Chord_Diagram）内加载本地 d3 库。

    Chord_Diagram 等模块硬编码 ``https://d3js.org/d3.v7.min.js`` CDN 外链，
    离线/内网渲染时 d3 未定义，页面只剩空白容器。

    本函数做两件事：
    1. 把已存在的 D3 CDN 外链替换为本地 ``/static/vendor/d3.v7.min.js``。
    2. 若页面使用了 d3 API（``d3.select`` 等）但没有任何 d3 库脚本，在
       ``</head>`` 前注入本地外链。
    """
    import re

    # 1. 替换任何已存在的 D3 CDN 外链为本地
    for pat in _D3_CDN_PATTERNS:
        html = re.sub(
            r"<script[^>]+src=['\"]" + re.escape(pat) + r"[^'\"]*['\"][^>]*></script>",
            _D3_TAG,
            html,
        )

    # 2. 检测是否需要注入库：页面引用了 d3 但没有库脚本
    has_d3_ref = "d3.select" in html or "d3.chord" in html or "d3.arc" in html
    has_d3_lib = _D3_TAG in html or bool(
        re.search(r"<script[^>]+src=['\"][^'\"]*\bd3[\.-][^'\"]*\.js", html, re.IGNORECASE)
    )

    if has_d3_ref and not has_d3_lib:
        if "</head>" in html:
            html = html.replace("</head>", f"{_D3_TAG}\n</head>", 1)
        elif "<body" in html:
            html = re.sub(r"<body[^>]*>",
                          lambda m: m.group(0) + f"\n{_D3_TAG}", html, count=1)
        else:
            html = _D3_TAG + "\n" + html

    return html


# Injected into every chart HTML so it renders cleanly inside a dashboard iframe.
_EMBED_STYLE = (
    "<style>"
    "html,body{margin:0!important;padding:0!important;overflow:hidden!important;"
    "background:transparent!important;}"
    ".chart-wrap{margin:8px!important;}"
    ".plotly-graph-div[style*='height:100%']{min-height:360px!important;}"
    ".desc{display:none!important;}"          # hide the data-format footer in iframes
    "</style>"
)

def _inject_embed_style(html: str) -> str:
    """Prepend embed-mode CSS so chart pages look clean inside dashboard iframes."""
    if "</head>" in html:
        return html.replace("</head>", _EMBED_STYLE + "\n</head>", 1)
    # Fallback: prepend to body
    return html.replace("<body", _EMBED_STYLE + "\n<body", 1)

try:
    from charts.base import ChartResult, FieldMapping
except ImportError as e:
    logger.error(f"Failed to import charts.base: {e}")
    # 定义备用类
    class ChartResult:
        def __init__(self, html="", spec=None, warnings=None, meta=None):
            self.html = html
            self.spec = spec or {}
            self.warnings = warnings or []
            self.meta = meta or {}
        def is_valid(self):
            return bool(self.html.strip()) and len(self.html) > 500
    
    class FieldMapping:
        pass


def _iter_mapping_columns(mapping: Dict[str, Any]):
    for role, value in (mapping or {}).items():
        if isinstance(value, str):
            yield str(role), value
        elif isinstance(value, (list, tuple, set)):
            for item in value:
                if isinstance(item, str):
                    yield str(role), item
        elif isinstance(value, dict):
            # Unwrap {"item": [...]} or similar dict wrapping
            for sub in value.values():
                if isinstance(sub, (list, tuple)):
                    for item in sub:
                        if isinstance(item, str):
                            yield str(role), item
                elif isinstance(sub, str):
                    yield str(role), sub


def _validate_mapping_columns(mapping: Dict[str, Any], columns) -> Optional[str]:
    column_names = {str(col) for col in columns}
    missing = sorted({
        col
        for role, col in _iter_mapping_columns(mapping)
        if col not in column_names and role not in {"series", "color", "group"}
    })
    if missing:
        return "field_mapping references missing SQL result columns: " + ", ".join(missing)
    return None


# ── 图表必填角色注册表（来自 LLM/chart_selector._CHARTS）─────────────────
# 用于调度器预校验：LLM 未提供 required_roles 时提前报错，而不是等模块
# 内部 _auto_col 启发式选错列。导入失败时降级为旧硬编码子集，行为不变。
_REQUIRED_ROLES: Optional[Dict[str, tuple]] = None
_CHART_REGISTRY: Optional[Dict[str, dict]] = None


def _load_chart_registry() -> Dict[str, dict]:
    global _REQUIRED_ROLES, _CHART_REGISTRY
    if _CHART_REGISTRY is not None:
        return _CHART_REGISTRY
    try:
        from LLM.chart_selector import _CHARTS
        entries = {}
        for c in _CHARTS:
            cid = _chart_key(c.get("chart_id"))
            if cid:
                entries[cid] = c
        _CHART_REGISTRY = entries
        _REQUIRED_ROLES = {
            cid: tuple(c.get("required_roles") or [])
            for cid, c in entries.items()
            if c.get("required_roles")
        }
    except Exception as e:
        logger.debug(f"chart_selector registry unavailable, falling back to minimal set: {e}")
        _CHART_REGISTRY = {}
        _REQUIRED_ROLES = {
            "bar_chart": ("x", "y"),
            "line_chart": ("x", "y"),
            "grouped_bar_chart": ("x",),
            "stacked_bar_chart": ("x",),
        }
    return _CHART_REGISTRY


def _load_required_roles() -> Dict[str, tuple]:
    _load_chart_registry()
    return _REQUIRED_ROLES


def _is_wide_format(mapping: Dict[str, Any]) -> bool:
    """宽格式信号：显式 value_cols，或 y/list 型角色包含 ≥2 个数值列。"""
    if mapping.get("value_cols"):
        return True
    y = mapping.get("y")
    return isinstance(y, (list, tuple)) and len(y) >= 2


def _required_mapping_error(chart_type: str, mapping: Dict[str, Any]) -> Optional[str]:
    registry = _load_chart_registry()
    chart_key = _chart_key(chart_type)
    required = _load_required_roles().get(chart_key)
    if not required:
        return None
    # 宽格式豁免：Grouped/Stacked Bar 等模块支持 x + 多数值列（y=[...] 或
    # value_cols），此时 series 由列名充当，不应被调度层预校验拦截。
    optional_roles = registry.get(chart_key, {}).get("optional_roles") or []
    wide_ok = (
        "value_cols" in optional_roles
        and "series" in required
        and _is_wide_format(mapping)
    )
    missing = [role for role in required if not mapping.get(role)]
    if wide_ok and "series" in missing:
        missing.remove("series")
    if mapping.get("value_cols") and "y" in missing:
        missing.remove("y")
    if not missing:
        return None
    # 附上图表期望的数据格式与宽格式替代写法，让 LLM 一次修正而非盲猜。
    entry = registry.get(chart_key) or {}
    hints = [f"required roles: {', '.join(required)}"]
    if entry.get("data_format"):
        hints.append(f"expected data format: {entry['data_format']}")
    if "series" in missing and "value_cols" in optional_roles:
        hints.append(
            "for wide-format data (x + multiple numeric columns), pass y=[\"colA\",\"colB\"] "
            "or value_cols=[\"colA\",\"colB\"] instead of a series column"
        )
    return f"field_mapping missing required role(s): {', '.join(missing)} ({'; '.join(hints)})"


def _resolve_chart_module_name(chart_type: str) -> Optional[str]:
    """把 chart_type 解析为 charts/ 下实际的目录名。

    注册表 chart_id 与磁盘目录大小写一致时直接返回原值；否则做大小写
    不敏感匹配（Windows 文件系统本身不区分大小写，但 Linux 上
    importlib.import_module 严格区分，目录名大小写不匹配会 ImportError）。
    注意：不能依赖 ``is_dir()`` 做精确判断 —— Windows 上它对任意大小写
    都返回 True；必须枚举目录条目比对真实名称。
    """
    name = str(chart_type or "").strip().rstrip("/")
    if not name:
        return None
    # 去掉非法模块字符，防路径注入（chart_type 来自 LLM 输出）
    if not all(ch.isalnum() or ch in "-_" for ch in name):
        return None
    charts_dir = CHART_PROJECT / "charts"
    try:
        entries = {entry.name: entry for entry in charts_dir.iterdir() if entry.is_dir()}
    except OSError:
        return None
    if name in entries:
        return name
    lowered = {k.lower(): v for k, v in entries.items()}
    hit = lowered.get(name.lower())
    return hit.name if hit is not None else None


def _finalize_chart_html(
    html: str,
    chart_type: str,
    options: Dict[str, Any],
    color_scheme: str,
    prior_warnings: Optional[List[str]] = None,
) -> tuple:
    """主题/焦点后处理 + 渲染后质检（QC）。

    在模块产出的 HTML 上统一应用 charts.theme 的语义主题与焦点规则
    （只处理内嵌的 Plotly figure，echarts/d3 原样跳过），再跑 charts.chart_qc
    的复杂度预算等检查。warnings 合并返回，由调用方透传给 agent 以便
    LLM 自我修正。任何内部失败都不阻塞渲染——增强层绝不把能出的图变 error。

    返回 (html, warnings)。
    """
    warnings: List[str] = []
    try:
        from charts.theme import postprocess_plotly_html
        from charts import chart_qc
        html, figures, theme_warnings = postprocess_plotly_html(
            html, color_scheme=color_scheme, options=options or {}
        )
        warnings.extend(theme_warnings)
        warnings.extend(
            chart_qc.qc_figures(chart_type, figures, html, prior_warnings=prior_warnings)
        )
    except Exception as e:
        logger.debug(f"chart theme/QC post-processing skipped: {e}")
    return html, warnings


def generate_chart(
    df: pd.DataFrame = None,
    excel_path: str = None,
    chart_type: str = "bar_chart",
    mapping: Dict[str, str] = None,
    options: Dict[str, Any] = None,
    color_scheme: str = "mckinsey",
    **kwargs
) -> Dict[str, Any]:
    """生成图表 - 调用 charts 目录中的实际模块"""
    try:
        if df is None and excel_path:
            df = pd.read_excel(excel_path)
        
        if df is None or df.empty:
            return {"error": "No data"}
        
        logger.info(f"Generating {chart_type} with {len(df)} rows, {len(df.columns)} columns")

        # ✅ 统一列名类型：避免 heatmap 等图表里对列名做 .lower() 时遇到 int
        df = df.copy()
        df.columns = df.columns.map(str)
        
        # 动态导入图表模块（大小写不敏感解析目录名，兼容 Linux 部署）
        try:
            module_name = _resolve_chart_module_name(chart_type)
            if module_name is None:
                logger.error(f"Chart type {chart_type!r} does not match any charts/ directory")
                return {"error": f"Chart type {chart_type} not found"}
            module = importlib.import_module(f"charts.{module_name}")
            generate_func = getattr(module, "generate", None)

            if not generate_func:
                logger.error(f"Module charts.{module_name} has no generate function")
                return {"error": f"Chart type {chart_type} not found"}
        except ImportError as e:
            logger.error(f"Failed to import charts.{chart_type}: {e}")
            return {"error": f"Chart type {chart_type} not found"}
        
        # 自动检测字段映射
        user_supplied_mapping = bool(mapping)
        if not mapping:
            mapping = _auto_detect_mapping(df, chart_type)

        # Unwrap {"item": [...]} wrapping before any str() coercion
        # to prevent dict repr like "{'item': [...]}" being split
        if mapping:
            mapping = _unwrap_item_dicts(mapping)
        # 统一 mapping 中的列名为字符串（mapping 的 value 通常是列名）
        # 但对于列表/dict 类型保持原样
        if mapping:
            mapping = {k: (v if isinstance(v, (list, tuple, dict)) else (str(v) if v is not None else v)) for k, v in mapping.items()}
        mapping = _normalize_chart_mapping(chart_type, mapping or {}, df.columns)
        mapping_error = _validate_mapping_columns(mapping, df.columns)
        if mapping_error:
            return {"error": mapping_error}
        # 预校验只对用户/LLM 提供的 mapping 生效。auto-detect 的结果可能
        # 不满足 registry 必填角色（各模块有自己的 _auto_col 兜底），拦住
        # 反而破坏原本可用的自动路径。
        if user_supplied_mapping:
            required_error = _required_mapping_error(chart_type, mapping)
            if required_error:
                return {"error": required_error}
        
        # 合并 options，添加 color_scheme
        merged_options = options or {}
        merged_options['color_scheme'] = color_scheme
        
        # 调用图表生成函数
        result = generate_func(df=df, mapping=mapping, options=merged_options)
        
        # 检查返回类型
        if isinstance(result, ChartResult):
            if result.is_valid():
                final_html, qc_warnings = _finalize_chart_html(
                    result.html, chart_type, merged_options, color_scheme,
                    prior_warnings=list(result.warnings or []),
                )
                return {
                    "success": True,
                    "html": _inject_embed_style(_inject_d3(_inject_echarts(_inject_plotly(final_html)))),
                    "chart_type": chart_type,
                    "warnings": list(result.warnings or []) + qc_warnings,
                    "meta": result.meta
                }
            else:
                html_len = len(result.html.strip())
                logger.error(f"Chart invalid: html length={html_len}, warnings={result.warnings}")
                msg = result.warnings[0] if result.warnings else f"Generated chart is invalid (html={html_len} chars)"
                return {"error": msg}
        elif hasattr(result, "html") and hasattr(result, "warnings"):
            # 模块自带 ChartResult 类（非 charts.base.ChartResult），duck-typing 兼容
            html = result.html or ""
            if html.strip() and len(html) > 500:
                final_html, qc_warnings = _finalize_chart_html(
                    html, chart_type, merged_options, color_scheme,
                    prior_warnings=list(getattr(result, "warnings", []) or []),
                )
                return {
                    "success": True,
                    "html": _inject_embed_style(_inject_d3(_inject_echarts(_inject_plotly(final_html)))),
                    "chart_type": chart_type,
                    "warnings": list(getattr(result, "warnings", []) or []) + qc_warnings,
                    "meta": getattr(result, "meta", {})
                }
            else:
                html_len = len(html.strip())
                ws = getattr(result, "warnings", [])
                msg = ws[0] if ws else f"Generated chart is invalid (html={html_len} chars)"
                return {"error": msg}
        elif isinstance(result, dict):
            if result.get("html"):
                final_html, qc_warnings = _finalize_chart_html(
                    result.get("html"), chart_type, merged_options, color_scheme,
                    prior_warnings=list(result.get("warnings") or []),
                )
                return {
                    "success": True,
                    "html": _inject_embed_style(_inject_d3(_inject_echarts(_inject_plotly(final_html)))),
                    "chart_type": chart_type,
                    "warnings": list(result.get("warnings") or []) + qc_warnings
                }
            else:
                return {"error": result.get("error", "Unknown error")}
        else:
            return {"error": f"Unexpected result type: {type(result)}"}
    
    except Exception as e:
        logger.error(f"Chart generation error: {e}", exc_info=True)
        return {"error": str(e)}


def _auto_detect_mapping(df: pd.DataFrame, chart_type: str) -> Dict[str, str]:
    """自动检测字段映射"""
    try:
        df = df.copy()
        df.columns = df.columns.map(str)
        numeric_cols = df.select_dtypes(include=['number']).columns.tolist()
        string_cols = df.select_dtypes(include=['object', 'string']).columns.tolist()
        
        mapping = {}
        chart_key = _chart_key(chart_type)
        
        # 根据图表类型推荐映射
        if chart_key in {"bar_chart", "bar", "grouped_bar", "grouped_bar_chart", "stacked_bar", "stacked_bar_chart"}:
            if string_cols and numeric_cols:
                mapping["x"] = string_cols[0]
                mapping["y"] = numeric_cols[0]
                if chart_key in {"grouped_bar", "grouped_bar_chart", "stacked_bar", "stacked_bar_chart"}:
                    if len(string_cols) > 1:
                        mapping["series"] = string_cols[1]
                    elif len(numeric_cols) > 1:
                        mapping["value_cols"] = numeric_cols
        
        elif chart_key == "line_chart":
            if string_cols and numeric_cols:
                mapping["x"] = string_cols[0]
                mapping["y"] = numeric_cols[0]
            elif len(numeric_cols) >= 2:
                mapping["x"] = numeric_cols[0]
                mapping["y"] = numeric_cols[1]
        
        elif chart_key == "scatter_plot":
            if len(numeric_cols) >= 2:
                mapping["x"] = numeric_cols[0]
                mapping["y"] = numeric_cols[1]
                if len(numeric_cols) >= 3:
                    mapping["size"] = numeric_cols[2]
        
        elif chart_key == "pie_chart":
            if string_cols and numeric_cols:
                mapping["label"] = string_cols[0]
                mapping["value"] = numeric_cols[0]
        
        elif chart_key == "heatmap":
            # A long-format heatmap normally has two dimensions and one value
            # column (for example: actual, predicted, count).  The previous
            # implementation looked for *three numeric* columns, which left
            # the production ``Heatmap`` ID unmapped and made the renderer
            # select the first text column for both axes.
            if len(string_cols) >= 2 and numeric_cols:
                mapping["y"] = string_cols[0]
                mapping["x"] = string_cols[1]
                mapping["value"] = numeric_cols[0]
            elif len(numeric_cols) == 3 and len(df) != len(numeric_cols):
                # Numeric long-format data has no categorical columns.  A
                # square all-numeric table is more likely to be a matrix and
                # is deliberately left unmapped for the Heatmap module's wide
                # format path.
                mapping["x"] = numeric_cols[0]
                mapping["y"] = numeric_cols[1]
                mapping["value"] = numeric_cols[2]
        
        elif chart_key == "histogram_pareto_chart":
            if numeric_cols:
                mapping["value"] = numeric_cols[0]

        elif chart_key == "box-and-whisker_plot":
            if string_cols and numeric_cols:
                mapping["x"] = string_cols[0]
                mapping["y"] = numeric_cols[0]
            elif numeric_cols:
                mapping["y"] = numeric_cols[0]

        elif chart_key == "violin_chart":
            if string_cols and numeric_cols:
                mapping["x"] = string_cols[0]
                mapping["y"] = numeric_cols[0]
            elif numeric_cols:
                mapping["y"] = numeric_cols[0]

        elif chart_key == "ridgeline_plot":
            if string_cols and numeric_cols:
                mapping["group"] = string_cols[0]
                mapping["value"] = numeric_cols[0]
            elif numeric_cols:
                mapping["value"] = numeric_cols[0]

        elif chart_key == "beeswarm_plot":
            if string_cols and numeric_cols:
                mapping["group"] = string_cols[0]
                mapping["value"] = numeric_cols[0]
            elif numeric_cols:
                mapping["value"] = numeric_cols[0]

        elif chart_key == "waterfall":
            if string_cols and numeric_cols:
                mapping["x"] = string_cols[0]
                mapping["y"] = numeric_cols[0]

        # sunburst_diagram / treemap：不留分支。模块自身的层级自动识别
        # （空 mapping 时按列序识别 父级→子级→数值）方向正确；此前调度层
        # 猜的 labels/parents 会把父子方向弄反，产出悬空父节点的空白图。

        elif chart_key in {"marimekko_abs", "marimekko_pct"}:
            # 两个分类列（x 轴行业 + 组内构成）+ 一个数值列
            if len(string_cols) >= 2 and numeric_cols:
                mapping["x"] = string_cols[0]
                mapping["group"] = string_cols[1]
                mapping["y"] = numeric_cols[0]
            elif string_cols and numeric_cols:
                mapping["x"] = string_cols[0]
                mapping["y"] = numeric_cols[0]

        elif chart_key == "network_diagram":
            # source/target/weight；weight 需显式给出，模块设计为
            # "无显式 weight 不加权"，由调度层代选第一个数值列
            if len(string_cols) >= 2:
                mapping["source"] = string_cols[0]
                mapping["target"] = string_cols[1]
                if numeric_cols:
                    mapping["weight"] = numeric_cols[0]

        elif chart_key == "parallel_coordinates_plot":
            # 平行坐标图：所有数值列作为维度
            if numeric_cols:
                mapping["dimensions"] = numeric_cols
                # 如果有字符串列，用第一个作为color
                if string_cols:
                    mapping["color"] = string_cols[0]
        
        elif chart_key == "connected_scatter":
            # 连线散点图：需要x、y、order、size
            if len(numeric_cols) >= 2:
                mapping["x"] = numeric_cols[0]
                mapping["y"] = numeric_cols[1]
                if len(numeric_cols) >= 3:
                    mapping["size"] = numeric_cols[2]
                if string_cols:
                    mapping["order"] = string_cols[0]
        
        logger.debug(f"Auto-detected mapping for {chart_type}: {mapping}")
        return mapping
    
    except Exception as e:
        logger.warning(f"Failed to auto-detect mapping: {e}")
        return {}


def recommend_charts(df: pd.DataFrame = None, excel_path: str = None, limit: int = 5) -> List[Dict]:
    """推荐图表"""
    try:
        if df is None and excel_path:
            df = pd.read_excel(excel_path)
        
        if df is None:
            return []
        
        numeric_cols = df.select_dtypes(include=['number']).columns.tolist()
        string_cols = df.select_dtypes(include=['object', 'string']).columns.tolist()
        
        recommendations = []
        
        # 基于数据特征推荐
        if string_cols and numeric_cols:
            recommendations.append({"chart_id": "bar_chart", "score": 0.95})
            recommendations.append({"chart_id": "grouped_bar", "score": 0.90})
            recommendations.append({"chart_id": "line_chart", "score": 0.85})
        
        if len(numeric_cols) >= 2:
            recommendations.append({"chart_id": "scatter_plot", "score": 0.80})
            recommendations.append({"chart_id": "heatmap", "score": 0.75})
        
        if string_cols and numeric_cols:
            recommendations.append({"chart_id": "pie", "score": 0.70})
        
        return recommendations[:limit]
    
    except Exception as e:
        logger.error(f"Recommend error: {e}")
        return []
