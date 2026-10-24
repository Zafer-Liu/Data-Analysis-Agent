# -*- coding: utf-8 -*-
"""
统一主题层 —— 图表视觉的单一事实来源（single source of truth）

所有图表的"解剖结构"级视觉（字体、网格线、坐标轴刻度、标题、图例、悬浮框）
统一从语义角色（semantic roles）取值，而不是每个模块各自硬编码。参考
editorial 设计系统的做法：中性色固定、品牌色来自 color_schemes 可换肤。

语义角色：
    paper   页面/绘图区背景
    ink     主文字、标题
    muted   次要文字、刻度、图例
    rule    网格线、坐标轴线等发丝线
    accent  焦点色 —— 焦点规则：每张图最多强调 1-2 个元素
    dim     失焦元素的降饱和色
    series  常规系列色（来自 color_schemes）

接入方式（零模块改动）：dispatcher（chart_generate.py）在 HTML 产出后调用
``postprocess_plotly_html``，对内嵌的 Plotly.newPlot/react figure JSON 应用
主题与焦点规则 —— 与既有的 _inject_* 属同一后处理层。模块作者也可以直接
使用 ``style_figure`` / ``apply_focus`` 提前接入（操作 plotly figure dict，
兼容 fig.to_dict() 的输出）。echarts/d3 图表暂不处理，原样返回。
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

try:  # 作为 charts 包成员导入
    from charts.color_schemes import get_color_scheme
except ImportError:  # 直接以文件路径运行（脚本/测试）时的兜底
    from color_schemes import get_color_scheme


__all__ = [
    "FONT_FAMILY",
    "SIZE_PRESETS",
    "semantic_roles",
    "normalize_highlight",
    "style_figure",
    "apply_size",
    "apply_focus",
    "postprocess_plotly_html",
    "parse_plotly_figures",
    "plotly_figure_objects",
    "render_png_bytes",
]


# 中文字体栈与各模块现用一致
FONT_FAMILY = "Heiti SC, Microsoft YaHei, sans-serif"

# 固定中性色（不随配色方案变化的"纸张/墨水"），accent/series 来自配色方案
_NEUTRAL = {
    "paper": "#ffffff",
    "ink": "#2b3038",
    "muted": "#7a8399",
    "rule": "#e6e8ec",
    "dim": "#c9ced6",
}


def semantic_roles(scheme_name: str = "mckinsey") -> Dict[str, str]:
    """把配色方案映射为图表解剖级的语义角色。"""
    scheme = get_color_scheme(scheme_name)
    series = [c for c in (scheme.get("colors") or []) if isinstance(c, str)]
    primary = scheme.get("primary") or (series[0] if series else _NEUTRAL["ink"])
    accent = scheme.get("accent") or (series[1] if len(series) > 1 else primary)
    roles = dict(_NEUTRAL)
    roles.update(
        series=series,
        primary=primary,
        accent=accent,
        positive=scheme.get("positive"),
        negative=scheme.get("negative"),
    )
    return roles


def normalize_highlight(value: Any) -> List[str]:
    """把 highlight 参数归一化为字符串列表。

    接受 ``"华东,华南"``、``["华东", "华南"]`` 等形态；去空、去重、保序。
    """
    if not value:
        return []
    if isinstance(value, str):
        items: List[Any] = value.split(",")
    elif isinstance(value, (list, tuple, set)):
        items = list(value)
    else:
        items = [value]
    out: List[str] = []
    for v in items:
        s = str(v).strip()
        if s and s not in out:
            out.append(s)
    return out


def _norm_label(v: Any) -> str:
    return str(v).strip()


# ── 布局铬件（layout chrome） ────────────────────────────────────────────────

_AXIS_KEY_RE = re.compile(r"[xy]axis\d*$")


def style_figure(fig: Dict[str, Any], roles: Optional[Dict[str, str]] = None,
                 scheme_name: str = "mckinsey") -> None:
    """就地应用主题到 figure dict（``fig.to_dict()`` 形态，data+layout）。

    只统一"铬件"（字体/网格/刻度/标题/图例/悬浮框/背景），不重绘系列颜色 ——
    系列色由各模块按数据语义选择（primary/positive/negative/序列色）。
    polar/geo/scene/ternary 坐标系不动，避免破坏地图类布局。
    """
    roles = roles or semantic_roles(scheme_name)
    layout = fig.get("layout")
    if not isinstance(layout, dict):
        layout = {}
        fig["layout"] = layout

    # 全局字体
    font = dict(layout.get("font") or {})
    font.setdefault("size", 12)
    font["family"] = FONT_FAMILY
    font["color"] = roles["ink"]
    layout["font"] = font

    # 背景与系列色
    layout["paper_bgcolor"] = roles["paper"]
    layout["plot_bgcolor"] = roles["paper"]
    if roles.get("series"):
        layout["colorway"] = list(roles["series"])

    # 标题：左对齐（editorial 惯例），仅在有标题时介入
    title = layout.get("title")
    if isinstance(title, str):
        title = {"text": title}
    if isinstance(title, dict) and str(title.get("text") or "").strip():
        tfont = dict(title.get("font") or {})
        tfont.setdefault("size", 16)
        tfont["color"] = roles["ink"]
        title["font"] = tfont
        title.setdefault("x", 0.0)
        title.setdefault("xanchor", "left")
        layout["title"] = title

    # 图例文字
    legend = dict(layout.get("legend") or {})
    lfont = dict(legend.get("font") or {})
    lfont.setdefault("size", 11)
    lfont["color"] = roles["muted"]
    lfont["family"] = FONT_FAMILY
    legend["font"] = lfont
    layout["legend"] = legend

    # 悬浮框
    hover = dict(layout.get("hoverlabel") or {})
    hfont = dict(hover.get("font") or {})
    hfont["family"] = FONT_FAMILY
    hover["font"] = hfont
    hover.setdefault("bgcolor", "white")
    hover.setdefault("bordercolor", roles["rule"])
    layout["hoverlabel"] = hover

    # 笛卡尔坐标轴（xaxis/yaxis/xaxis2…）：发丝网格 + 次要刻度文字
    for key, axis in list(layout.items()):
        if not isinstance(key, str) or not _AXIS_KEY_RE.fullmatch(key):
            continue
        if not isinstance(axis, dict):
            continue
        if axis.get("showgrid") is not False:
            axis["gridcolor"] = roles["rule"]
        axis["zerolinecolor"] = roles["rule"]
        axis["linecolor"] = roles["rule"]
        tick = dict(axis.get("tickfont") or {})
        tick.setdefault("size", 11)
        tick["color"] = roles["muted"]
        tick["family"] = FONT_FAMILY
        axis["tickfont"] = tick
        atitle = axis.get("title")
        if isinstance(atitle, str):
            atitle = {"text": atitle}
        if isinstance(atitle, dict) and str(atitle.get("text") or "").strip():
            afont = dict(atitle.get("font") or {})
            afont.setdefault("size", 12)
            afont["color"] = roles["muted"]
            atitle["font"] = afont
            axis["title"] = atitle


# ── 尺寸预设（size presets） ──────────────────────────────────────────────────
# chat 内嵌保持响应式（不写死宽高）；其余预设面向看板卡片 / 16:9 幻灯片整页 /
# 报告方形插图 / 高清导出场景。key 为 generate_chart 的 size 参数取值。
SIZE_PRESETS: Dict[str, Optional[Dict[str, int]]] = {
    "chat": None,                                   # 响应式，随 iframe 自适应（默认）
    "dashboard": {"width": 960, "height": 480},     # 看板卡片
    "slide": {"width": 1280, "height": 720},        # 16:9 幻灯片整页
    "square": {"width": 800, "height": 800},        # 报告方形插图
    "print": {"width": 1600, "height": 900},        # 高清导出 / 打印
}


def apply_size(fig: Dict[str, Any], size: Any) -> None:
    """按预设写死布局宽高。chat（响应式）与未知取值不动作。"""
    preset = SIZE_PRESETS.get(str(size or "").strip().lower())
    if not preset:
        return
    layout = fig.get("layout")
    if not isinstance(layout, dict):
        layout = {}
        fig["layout"] = layout
    layout.update(preset)
    layout["autosize"] = False


# ── 焦点规则（focus rule） ───────────────────────────────────────────────────

def _set_trace_color(trace: Dict[str, Any], color: str) -> None:
    """把焦点系列重着色为 accent。保守起见，色标/色轴映射的 trace 不动。"""
    if trace.get("coloraxis"):
        return
    marker = trace.get("marker")
    if isinstance(marker, dict):
        if marker.get("colorscale") or marker.get("coloraxis"):
            return
        mc = marker.get("color")
        if mc is None or isinstance(mc, str):
            marker["color"] = color
    line = trace.get("line")
    if isinstance(line, dict):
        lc = line.get("color")
        if lc is None or isinstance(lc, str):
            line["color"] = color
    # fillcolor / textfont 保持原样：焦点语义只落在主体标记上


def apply_focus(fig: Dict[str, Any], highlight: Any,
                roles: Optional[Dict[str, str]] = None,
                scheme_name: str = "mckinsey") -> Optional[str]:
    """焦点规则：accent 只给 highlight 命中的元素，其余降为 dim/半透明。

    - 多系列（≥2 个命名 trace）：命中的系列重着色为 accent，未命中的系列
      opacity 降至 0.3。
    - 单系列：highlight 按"类别值"匹配 x/labels，命中的位置用 accent，
      其余用 dim（柱状图/饼图最常用）。

    返回一条 warning 文案（未命中时），供 QC 回传 LLM 修正；命中返回 None。
    """
    roles = roles or semantic_roles(scheme_name)
    names = {h for h in normalize_highlight(highlight)}
    if not names:
        return None
    traces = [t for t in (fig.get("data") or []) if isinstance(t, dict)]
    if not traces:
        return None

    named = [t for t in traces if t.get("name")]
    if len(traces) > 1 and len(named) > 1:
        matched = False
        for t in traces:
            nm = _norm_label(t.get("name") or "")
            if nm and nm in names:
                matched = True
                _set_trace_color(t, roles["accent"])
            elif nm:
                t["opacity"] = 0.3
        if not matched:
            available = ", ".join(sorted({_norm_label(t.get("name")) for t in named}))
            return f"highlight 未匹配任何系列（可用系列名: {available}）"
        return None

    # 单系列 → 按类别值匹配
    t = traces[0]
    cats = t.get("labels") if t.get("type") == "pie" else t.get("x")
    if not isinstance(cats, list) or not cats:
        return None
    hits = {i for i, c in enumerate(cats) if _norm_label(c) in names}
    if not hits:
        sample = ", ".join(_norm_label(c) for c in cats[:5])
        return f"highlight 未匹配任何类别值（数据中的类别示例: {sample}）"
    marker = t.get("marker")
    if not isinstance(marker, dict):
        marker = {}
        t["marker"] = marker
    if marker.get("colorscale") or marker.get("coloraxis") or t.get("coloraxis"):
        return None  # 数值映射色的 trace 不做焦点重着色
    existing = marker.get("color") if t.get("type") != "pie" else marker.get("colors")
    if isinstance(existing, list):
        return None  # 已有逐点配色（可能有语义），不覆盖
    colors = [roles["accent"] if i in hits else roles["dim"] for i in range(len(cats))]
    if t.get("type") == "pie":
        marker["colors"] = colors
    else:
        marker["color"] = colors
    return None


# ── Plotly.newPlot / Plotly.react 内嵌 JSON 重写 ─────────────────────────────

_CALL_RE = re.compile(r"Plotly\.(newPlot|react)\s*\(")
# pio.to_html(full_html=False) 的另一种内嵌形态：figure 存放在 ``var fig = {...}``
# 字面量里，newPlot 引用 fig.data / fig.layout（Sunburst/Treemap 等模块在用）。
_FIG_RE = re.compile(r"\bvar\s+fig\s*=\s*")
# newPlot 调用参数引用字面量（而非内联 JSON）的形态 —— 这不是解析失败，
# figure 由 ``_iter_fig_literals`` 负责
_FIG_REF_RE = re.compile(r"\s*(?:'[^']*'|\"[^\"]*\")\s*,\s*fig\.")
_DECODER = json.JSONDecoder()
_WS = " \t\r\n"


def _skip_ws(s: str, i: int) -> int:
    while i < len(s) and s[i] in _WS:
        i += 1
    return i


def _parse_call(html: str, end: int) -> Optional[Tuple[Any, Any, Any, int, Any]]:
    """从 ``Plotly.newPlot(`` 之后的 end 位置解析调用参数。

    返回 ``(div_id, data, layout, arg_end, config)``，arg_end 指向收尾 ``)``；
    config 为第 4 参（可能为 None，原样回写不参与修改）。兼容 3 参/4 参调用。
    任何不符合预期的形态返回 None（调用方保持原文不动）。
    """
    try:
        i = _skip_ws(html, end)
        div_id, i = _DECODER.raw_decode(html, i)
        i = _skip_ws(html, i)
        if i >= len(html) or html[i] != ",":
            return None
        i = _skip_ws(html, i + 1)
        data, i = _DECODER.raw_decode(html, i)
        i = _skip_ws(html, i)
        if i >= len(html) or html[i] != ",":
            return None
        i = _skip_ws(html, i + 1)
        layout, i = _DECODER.raw_decode(html, i)
        i = _skip_ws(html, i)
        # 可选的 config 第 4 参；多解析一段但只在整体以 ')' 收尾时接受
        parsed_extra = None
        if i < len(html) and html[i] == ",":
            j = _skip_ws(html, i + 1)
            if j < len(html) and html[j] not in ")":
                parsed_extra, i = _DECODER.raw_decode(html, j)
                i = _skip_ws(html, i)
        if i >= len(html) or html[i] != ")":
            return None
        return div_id, data, layout, i, parsed_extra
    except (ValueError, json.JSONDecodeError):
        return None


def _dump(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False, separators=(",", ":"))


def _iter_fig_literals(html: str):
    """解析 ``var fig = <JSON>`` 字面量，产出 ``(json_start, json_end, fig_dict)``。

    只接受 dict 且含 "data" 列表的字面量，其余同名变量静默跳过。
    """
    for m in _FIG_RE.finditer(html):
        try:
            obj, end = _DECODER.raw_decode(html, m.end())
        except (ValueError, json.JSONDecodeError):
            continue
        if isinstance(obj, dict) and isinstance(obj.get("data"), list):
            yield m.end(), end, obj


def _fig_from_parts(data: Any, layout: Any) -> Dict[str, Any]:
    return {
        "data": data if isinstance(data, list) else [data],
        "layout": layout if isinstance(layout, dict) else {},
    }


def parse_plotly_figures(html: str) -> Tuple[List[Dict[str, Any]], List[str]]:
    """只解析不修改：返回内嵌 Plotly figure 的 figure dict 列表。

    与 ``postprocess_plotly_html`` 共用解析器（``_parse_call`` +
    ``_iter_fig_literals``），供 PNG 导出、存量图表检查等"读不改"场景使用。
    支持两种内嵌形态：newPlot/react 内联 JSON，以及 ``var fig = {...}``
    字面量（pio.to_html 的 full_html 形态）。解析失败的调用跳过并记 warning。
    """
    if not html:
        return [], []
    if "newPlot" not in html and "react" not in html and not _FIG_RE.search(html):
        return [], []
    figures: List[Dict[str, Any]] = []
    warnings: List[str] = []
    for m in _CALL_RE.finditer(html):
        parsed = _parse_call(html, m.end())
        if parsed is None:
            # newPlot('id', fig.data, fig.layout) 引用字面量的形态不是解析失败
            if not _FIG_REF_RE.match(html, m.end()):
                warnings.append("plotly figure 解析失败，已跳过（不影响渲染）")
            continue
        div_id, data, layout, _arg_end, _config = parsed
        figures.append(_fig_from_parts(data, layout))
    for _start, _end, obj in _iter_fig_literals(html):
        figures.append(_fig_from_parts(obj["data"], obj.get("layout")))
    return figures, warnings


def plotly_figure_objects(html: str) -> List[Any]:
    """把内嵌 figure dict 重构为 ``plotly.graph_objects.Figure`` 列表。

    用 ``plotly.io.from_json`` 走官方兼容路径（按 plotly.js figure JSON 语义），
    供服务端静态导出使用。重构失败时抛出对应异常，由调用方决定降级行为。
    """
    import plotly.io as pio

    figures, _warnings = parse_plotly_figures(html)
    return [pio.from_json(_dump(f)) for f in figures]


def render_png_bytes(
    html: str, width: int = 1280, height: int = 720, scale: int = 2,
) -> bytes:
    """把图表 HTML 渲染为 PNG 字节（服务端导出，WYSIWYG：不重设主题）。

    - 仅支持内嵌 Plotly 且单 figure 的图表；否则抛 ``ValueError``
      （echarts/d3 地图等走浏览器截图另行处理）。
    - kaleido 未安装时抛 ``ImportError``（带安装提示）——它是可选依赖，
      不应让图表主链路背上它的体积。
    """
    figures, _warnings = parse_plotly_figures(html)
    if not figures:
        raise ValueError("PNG 导出仅支持内嵌 Plotly 的图表，当前 HTML 未解析出 figure")
    if len(figures) > 1:
        raise ValueError(
            f"图表包含 {len(figures)} 个子图（如迷你图表格），PNG 导出暂只支持单图"
        )
    try:
        import kaleido  # noqa: F401 —— plotly.io 的静态导出引擎
    except ImportError as exc:
        raise ImportError(
            "PNG 导出需要可选依赖 kaleido（pip install -U kaleido），图表本身不受影响"
        ) from exc
    import plotly.io as pio

    fig = pio.from_json(_dump(figures[0]))
    return pio.to_image(fig, format="png", width=width, height=height, scale=scale)


def postprocess_plotly_html(
    html: str,
    color_scheme: str = "mckinsey",
    options: Optional[Dict[str, Any]] = None,
) -> Tuple[str, List[Dict[str, Any]], List[str]]:
    """对图表 HTML 应用主题 + 焦点规则。

    返回 ``(html, figures, warnings)``：figures 是解析出的 figure dict 列表
    （已应用修改），供 chart_qc 做质检；非 plotly HTML 原样返回。
    同时支持 newPlot/react 内联 JSON 与 ``var fig = {...}`` 字面量两种
    内嵌形态（后者改写字面量即可，newPlot 引用 fig.data 自动生效）。
    """
    if not html:
        return html, [], []
    if "newPlot" not in html and "react" not in html and not _FIG_RE.search(html):
        return html, [], []

    highlight = normalize_highlight((options or {}).get("highlight"))
    roles = semantic_roles(color_scheme)
    warnings: List[str] = []
    figures: List[Dict[str, Any]] = []

    def _apply(fig: Dict[str, Any]) -> None:
        style_figure(fig, roles=roles)
        apply_size(fig, (options or {}).get("size"))
        if highlight:
            w = apply_focus(fig, highlight, roles=roles)
            if w:
                warnings.append(w)

    out: List[str] = []
    pos = 0
    for m in _CALL_RE.finditer(html):
        out.append(html[pos:m.start()])
        parsed = _parse_call(html, m.end())
        if parsed is None:
            # newPlot('id', fig.data, fig.layout) 引用字面量的形态：调用本身
            # 原样保留，样式改写由下方 var fig 字面量通道完成
            if not _FIG_REF_RE.match(html, m.end()):
                warnings.append("plotly figure 解析失败，已跳过主题后处理（不影响渲染）")
            out.append(m.group(0))
            pos = m.end()
            continue
        div_id, data, layout, arg_end, config = parsed
        fig = _fig_from_parts(data, layout)
        _apply(fig)
        figures.append(fig)

        parts = [_dump(div_id), _dump(fig["data"]), _dump(fig["layout"])]
        if config is not None:
            parts.append(_dump(config))
        out.append(f"Plotly.{m.group(1)}(" + ", ".join(parts) + ")")
        pos = arg_end + 1  # 跳过 ')'

    out.append(html[pos:])
    html = "".join(out)

    # var fig = {...} 字面量通道：改写 JSON 字面量，newPlot 引用随之生效
    fig_spans = list(_iter_fig_literals(html))
    if fig_spans:
        buf: List[str] = []
        pos = 0
        for start, end, obj in fig_spans:
            fig = _fig_from_parts(obj["data"], obj.get("layout"))
            _apply(fig)
            figures.append(fig)
            buf.append(html[pos:start])
            buf.append(_dump(fig))
            pos = end
        buf.append(html[pos:])
        html = "".join(buf)

    return html, figures, warnings
