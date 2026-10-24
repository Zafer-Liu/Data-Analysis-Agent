"""
弧图 Arc Chart - 关系图表
图表分类: 关系 Relationship

统一接口:
    generate(df, mapping, options) -> ChartResult
"""
import logging
log = logging.getLogger(__name__)
import os
import sys
from pathlib import Path
from typing import Dict, Any, Optional, Tuple

import pandas as pd
import plotly.graph_objects as go
import plotly.io as pio
import numpy as np

_PROJECT_ROOT = Path(__file__).parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from charts.base import ChartResult

try:
    from charts.color_schemes import get_color_scheme
except ImportError as e:  # pragma: no cover - 兜底，保证独立调用可用
    log.debug("[chart] 配色模块加载失败: %s", e)
    def get_color_scheme(name: str = "mckinsey"):
        return {"colors": [
            "#1F77B4", "#D62728", "#2CA02C", "#FF7F0E", "#9467BD",
            "#8C564B", "#E377C2", "#17BECF", "#BCBD22", "#7F7F7F",
            "#003F5C", "#FFA600", "#665191", "#2F4B7C", "#A05195",
            "#D45087", "#F95D6A", "#00A676", "#3A86FF", "#FF006E"
        ]}

__all__ = ["generate"]

_DATA_FMT = "source列 + target列 + value列"
_DESC = "底部节点使用半圆表示节点规模，半圆之间的上拱弧带表示关系，弧带宽度表示关系强度；弧带颜色=流出(source)节点颜色。"


def _auto_col(df: pd.DataFrame, *hints: str, exclude: set = None) -> Optional[str]:
    exclude = exclude or set()
    strs = [c for c in df.columns if c not in exclude and (df[c].dtype == object or pd.api.types.is_string_dtype(df[c]))]
    nums = [c for c in df.columns if c not in exclude and pd.api.types.is_numeric_dtype(df[c])]
    col_lower = {str(c).lower(): c for c in df.columns if c not in exclude}

    for h in hints:
        h2 = str(h).lower()
        if h2 in col_lower:
            return col_lower[h2]

    for h in hints:
        h2 = str(h).lower()
        for col in df.columns:
            c = str(col).lower()
            if h2 in c or c in h2:
                return col

    if hints:
        h0 = str(hints[0]).lower()
        if any(k in h0 for k in ["source", "target", "label", "name", "group", "category"]):
            return strs[0] if strs else (nums[0] if nums else None)
        if any(k in h0 for k in ["value", "size", "amount", "count", "frequency"]):
            return nums[0] if nums else (strs[0] if strs else None)

    if not hints:
        return strs[0] if strs else (nums[0] if nums else None)

    return None


def _build_html(title: str, chart_name: str, library: str,
                data_fmt: str, desc: str, embed: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
html, body {{ width: 100%; height: 100%; }}
body {{ font-family: "Heiti SC", "Microsoft YaHei", sans-serif; background: #fafafa; display: flex; flex-direction: column; }}
.container {{ flex: 1; display: flex; flex-direction: column; min-height: 0; }}
.chart-wrap {{ flex: 1; background: white; border-radius: 12px; box-shadow: 0 2px 12px rgba(0,0,0,.08); padding: 20px; margin: 20px; min-height: 0; overflow: hidden; display: flex; flex-direction: column; }}
h1 {{ color: #222; font-size: 22px; margin-bottom: 6px; flex-shrink: 0; }}
.subtitle {{ color: #888; font-size: 13px; margin-bottom: 20px; flex-shrink: 0; }}
.plotly-graph-div {{ flex: 1; width: 100% !important; min-height: 0; }}
.desc {{ color: #555; font-size: 14px; line-height: 1.7; padding: 0 20px 20px 20px; flex-shrink: 0; }}
</style></head>
<body><div class="container">
<div class="chart-wrap">
<h1>{title}</h1><div class="subtitle">{chart_name} | {library}</div>
{embed}
</div>
<div class="desc">
<strong>数据格式：</strong>{data_fmt}<br>
<strong>说明：</strong>{desc}
</div>
</div></body></html>"""


def _normalize(v: float, vmin: float, vmax: float, out_min: float, out_max: float) -> float:
    if vmax <= vmin:
        return (out_min + out_max) / 2
    t = (v - vmin) / (vmax - vmin)
    return out_min + t * (out_max - out_min)


def _arc_curve(x1: float, x2: float, strength: float, smin: float, smax: float, n: int = 90) -> Tuple[np.ndarray, np.ndarray, float]:
    l, r = (x1, x2) if x1 <= x2 else (x2, x1)
    span = max(r - l, 1e-9)
    scale = _normalize(strength, smin, smax, 0.85, 1.35)
    peak = max(0.45, 0.42 * span) * scale

    x = np.linspace(l, r, n)
    mid = (l + r) / 2
    half = span / 2
    y = peak * (1 - ((x - mid) / half) ** 2)
    y = np.maximum(y, 0)
    return x, y, peak


def _half_circle(cx: float, r: float, n: int = 80) -> Tuple[np.ndarray, np.ndarray]:
    theta = np.linspace(np.pi, 0, n)
    x = cx + r * np.cos(theta)
    y = r * np.sin(theta)
    return x, y


def _hex_to_rgb(hex_color: str) -> Tuple[int, int, int]:
    h = hex_color.strip().replace("#", "")
    if len(h) != 6:
        return 31, 119, 180
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _arc_band_polygon(
    x1: float, x2: float, strength: float, smin: float, smax: float,
    n: int = 260, wmin: float = 0.03, wmax: float = 0.10
) -> Tuple[np.ndarray, np.ndarray, float]:
    x, y, peak = _arc_curve(x1, x2, strength, smin, smax, n=n)
    band_w = _normalize(strength, smin, smax, wmin, wmax)

    y_top = y + band_w / 2
    y_bot = np.maximum(y - band_w / 2, 0)

    xp = np.concatenate([x, x[::-1]])
    yp = np.concatenate([y_top, y_bot[::-1]])
    return xp, yp, peak


def generate(
    df: pd.DataFrame = None,
    mapping: Dict[str, str] = None,
    options: Dict[str, Any] = None,
    excel_path: str = None,
    source: str = "source",
    target: str = "target",
    value: str = "value",
    title: str = "弧图",
    **kwargs
) -> ChartResult:
    warnings: list = []
    options = options or {}
    mapping = mapping or {}

    if df is None:
        if excel_path:
            try:
                df = pd.read_excel(excel_path)
            except Exception as e:
                log.warning("[chart] 图表生成异常: %s", e)
                return ChartResult(warnings=[f"读取Excel失败: {e}"])
        else:
            return ChartResult(warnings=["请提供 df 或 excel_path"])

    if df is None or len(df) == 0:
        return ChartResult(warnings=["输入数据为空"])

    # chart_selector 声明 required_roles=["x","y","z"]（流出x + 流入y + 流出值z），
    # 调度器不做 x→source 转换。这里同时接受 selector 规范 (x/y/z) 与语义别名
    # (source/target/value)，使两种 payload 都能正确解析。
    src_col = mapping.get("source")
    src_col = src_col if src_col is not None else (mapping.get("x") if mapping.get("x") is not None else source)
    tgt_col = mapping.get("target")
    tgt_col = tgt_col if tgt_col is not None else (mapping.get("y") if mapping.get("y") is not None else target)
    val_col = mapping.get("value")
    val_col = val_col if val_col is not None else (mapping.get("z") if mapping.get("z") is not None else value)
    title = options.get("title", title)

    # 链式 exclude：source/target/value 依次排除已解析列，避免无 hint 时
    # 三者回退到同一 strs[0]，产生 df[[c,c,v]] 重复列导致下游崩溃
    _excl: set = set()
    _src = _auto_col(df, src_col, "source", "x", "起点", "from")
    if _src:
        _excl.add(_src)
    _tgt = _auto_col(df, tgt_col, "target", "y", "终点", "to", exclude=_excl)
    if _tgt:
        _excl.add(_tgt)
    _val = _auto_col(df, val_col, "value", "z", "数值", "amount", "权重", exclude=_excl)

    for role, col_ in [("source", _src), ("target", _tgt), ("value", _val)]:
        if col_ is None or col_ not in df.columns:
            warnings.append(f"找不到必填字段 [{role}]")
    if _src is None or _tgt is None or _val is None or _src == _tgt:
        if _src == _tgt and _src is not None:
            warnings.append("source 与 target 解析到同一列，请提供显式映射")
        return ChartResult(warnings=warnings)

    d = df[[_src, _tgt, _val]].dropna().copy()
    d[_val] = pd.to_numeric(d[_val], errors="coerce")
    d = d.dropna(subset=[_val])
    d = d[d[_src] != d[_tgt]].copy()
    if len(d) == 0:
        return ChartResult(warnings=["没有有效关系数据（全为自环或空）"])

    top_n = int(options.get("top_n", 200))
    min_value = options.get("min_value", None)
    min_percentile = options.get("min_percentile", None)

    if min_percentile is not None:
        q = min(max(1 - float(min_percentile), 0), 1)
        th = d[_val].quantile(q)
        d = d[d[_val] >= th]
    if min_value is not None:
        d = d[d[_val] >= float(min_value)]
    d = d.nlargest(top_n, _val)
    if len(d) == 0:
        return ChartResult(warnings=["过滤后没有数据"])

    node_size = {}
    for _, r in d.iterrows():
        s, t, v = r[_src], r[_tgt], float(r[_val])
        node_size[s] = node_size.get(s, 0.0) + v
        node_size[t] = node_size.get(t, 0.0) + v

    nodes = sorted(node_size.keys(), key=lambda n: (-node_size[n], str(n)))
    node_gap = float(options.get("node_gap", 2.2))
    node_pos = {n: i * node_gap for i, n in enumerate(nodes)}

    ns_min, ns_max = min(node_size.values()), max(node_size.values())
    r_min = float(options.get("node_radius_min", 0.18))
    r_max = float(options.get("node_radius_max", 0.55))
    node_r = {n: _normalize(node_size[n], ns_min, ns_max, r_min, r_max) for n in nodes}

    # 高对比类别色板（避免浅色，强化类别区分）
    # 配色方案：优先用 options.color_scheme，回退到内置色板
    _scheme = get_color_scheme(options.get("color_scheme", "mckinsey"))
    _palette = _scheme.get("colors") or [
        "#1F77B4", "#D62728", "#2CA02C", "#FF7F0E", "#9467BD",
        "#8C564B", "#E377C2", "#17BECF", "#BCBD22", "#7F7F7F",
        "#003F5C", "#FFA600", "#665191", "#2F4B7C", "#A05195",
        "#D45087", "#F95D6A", "#00A676", "#3A86FF", "#FF006E"
    ]
    strong_palette = options.get("palette", _palette)
    node_color = {n: strong_palette[i % len(strong_palette)] for i, n in enumerate(nodes)}

    fig = go.Figure()
    vmin, vmax = float(d[_val].min()), float(d[_val].max())
    y_max = 0.0

    # 弧带参数（单色，不渐变）
    band_min = float(options.get("edge_band_min", 0.03))
    band_max = float(options.get("edge_band_max", 0.10))
    edge_alpha = float(options.get("edge_alpha", 0.32))

    # 先画边：颜色固定为 source 节点色
    for _, r in d.iterrows():
        s, t, v = r[_src], r[_tgt], float(r[_val])
        x1, x2 = node_pos[s], node_pos[t]

        xp, yp, peak = _arc_band_polygon(
            x1, x2, v, vmin, vmax,
            n=int(options.get("edge_points", 260)),
            wmin=band_min, wmax=band_max)
        y_max = max(y_max, peak)

        c = node_color[s]
        rr, gg, bb = _hex_to_rgb(c)
        fill_color = f"rgba({rr},{gg},{bb},{edge_alpha})"
        line_color = f"rgba({rr},{gg},{bb},{min(edge_alpha + 0.18, 0.65)})"

        fig.add_trace(go.Scatter(
            x=xp, y=yp,
            mode="lines",
            line=dict(color=line_color, width=0.45, shape="spline",
                      smoothing=float(options.get("edge_smoothing", 1.15))),
            fill="toself",
            fillcolor=fill_color,
            hovertemplate=f"{s} → {t}<br>关系值: {v:.2f}<extra></extra>",
            showlegend=False
        ))

    # 再画节点底座 + 半圆
    base_h = float(options.get("node_base_height", 0.055))
    base_w_scale = float(options.get("node_base_width_scale", 2.4))

    for n in nodes:
        cx, rr = node_pos[n], node_r[n]
        c = node_color[n]
        rch, gch, bch = _hex_to_rgb(c)

        # 底座长条
        base_half_w = max(rr * base_w_scale, rr * 1.8)
        bx = [cx - base_half_w, cx + base_half_w, cx + base_half_w, cx - base_half_w, cx - base_half_w]
        by = [-base_h, -base_h, 0, 0, -base_h]
        base_fill = f"rgba({rch},{gch},{bch},0.24)"
        base_line = f"rgba({rch},{gch},{bch},0.52)"

        fig.add_trace(go.Scatter(
            x=bx, y=by, mode="lines",
            line=dict(color=base_line, width=0.6),
            fill="toself",
            fillcolor=base_fill,
            hoverinfo="skip",
            showlegend=False
        ))

        # 半圆
        xs, ys = _half_circle(cx, rr, n=int(options.get("node_points", 180)))
        node_fill_color = f"rgba({rch},{gch},{bch},0.36)"  # 用当前节点颜色生成填充色

        fig.add_trace(go.Scatter(
            x=xs, y=ys, mode="lines",
            line=dict(color=c, width=1.3, shape="spline", smoothing=float(options.get("node_smoothing", 1.0))),
            fill="tozeroy",
            fillcolor=node_fill_color,  # 不要再用 fill_color
            hovertemplate=f"{n}<br>节点规模: {node_size[n]:.2f}<extra></extra>",
            showlegend=False
        ))

        fig.add_annotation(
            x=cx, y=-max(r_max * 0.9, rr * 0.95) - base_h,
            text=str(n),
            showarrow=False,
            font=dict(size=11, color="#2b2b2b"),
            xanchor="center", yanchor="top"
        )

    fig.add_hline(y=0, line_width=1, line_color="rgba(0,0,0,0.25)")

    xs_all = [node_pos[n] for n in nodes]
    left = min(xs_all) - node_gap * 0.8 if xs_all else -1
    right = max(xs_all) + node_gap * 0.8 if xs_all else 1
    y_top = max(y_max * 1.16, r_max * 3.0, 1.5)
    extra_bottom = float(options.get("bottom_padding", 0.25))
    y_bottom = -max(r_max * 1.9 + extra_bottom, 1.0)

    fig.update_layout(
        title=title,
        showlegend=False,
        hovermode="closest",
        margin=dict(l=40, r=40, t=60, b=110),
        xaxis=dict(showgrid=False, zeroline=False, showticklabels=False, range=[left, right]),
        yaxis=dict(showgrid=False, zeroline=False, showticklabels=False, range=[y_bottom, y_top]),
        plot_bgcolor="white",
        paper_bgcolor="white",
        font_family="Heiti SC, Microsoft YaHei, sans-serif",
        autosize=True,
        height=620,
    )

    chart_html = pio.to_html(fig, full_html=False, include_plotlyjs=False)
    html = _build_html(title, "arc_chart", "plotly", _DATA_FMT, _DESC, chart_html)


    meta = {
        "chart_id": "arc_chart",
        "n_nodes": len(nodes),
        "n_edges": len(d),
        "max_value": float(vmax),
        "min_value": float(vmin),
        "avg_value": float(d[_val].mean()),
    }
    return ChartResult(html=html, spec={}, warnings=warnings, meta=meta)