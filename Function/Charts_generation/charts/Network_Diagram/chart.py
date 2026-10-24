"""
网络图 Network Diagram - 关系
图表分类: 关系 Relationship
感知排名: ★★★★☆

统一接口:
    generate(df, mapping, options) -> ChartResult
"""
import logging
log = logging.getLogger(__name__)
import os
import sys
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple

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
            "#005B9A", "#1E88E5", "#27AE60", "#43A047", "#8E44AD",
            "#6C63FF", "#16A085", "#2C3E50", "#C0392B", "#E74C3C",
            "#7F8C8D", "#2ECC71", "#9B59B6", "#1ABC9C", "#34495E",
        ]}

__all__ = ["generate"]

_DATA_FMT = "source列 + target列 + [weight列]"
_DESC = "用节点和连边表示网络关系，支持权重编码边的粗细，并显示边权重。"

# 配色：专业蓝系 + 绿系 + 紫系 + 中性色
_NODE_COLORS = [
    "#005B9A", "#1E88E5", "#27AE60", "#43A047", "#8E44AD",
    "#6C63FF", "#16A085", "#2C3E50", "#C0392B", "#E74C3C",
    "#7F8C8D", "#2ECC71", "#9B59B6", "#1ABC9C", "#34495E",
]


def _auto_col(df: pd.DataFrame, *hints: str, exclude: set = None) -> Optional[str]:
    """根据提示自动查找匹配的列名。"""
    if not hints or not df.columns.size:
        return None

    exclude = exclude or set()
    valid_hints = [h for h in hints if h is not None]
    if not valid_hints:
        return None

    col_lower = {str(c).lower(): c for c in df.columns if c not in exclude}

    # 1. 精确匹配
    for h in valid_hints:
        h_lower = str(h).lower()
        if h_lower in col_lower:
            return col_lower[h_lower]

    # 2. 模糊匹配
    for h in valid_hints:
        h_lower = str(h).lower()
        for col in df.columns:
            if col in exclude:
                continue
            col_lower_name = str(col).lower()
            if h_lower in col_lower_name or col_lower_name in h_lower:
                return col

    # 3. 类型匹配
    hint = str(valid_hints[0]).lower() if valid_hints else ""
    strs = [c for c in df.columns if c not in exclude and (df[c].dtype == object or pd.api.types.is_string_dtype(df[c]))]
    nums = [c for c in df.columns if c not in exclude and pd.api.types.is_numeric_dtype(df[c])]

    if any(kw in hint for kw in ["source", "target", "label", "name", "from", "to", "起点", "终点"]):
        return strs[0] if strs else (nums[0] if nums else None)

    if any(kw in hint for kw in ["weight", "value", "strength", "size", "amount", "权重"]):
        return nums[0] if nums else (strs[0] if strs else None)

    return None


def _build_html(title: str, chart_name: str, library: str, data_fmt: str, desc: str, embed: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8">
<title>{title}</title>
<style>
body{{font-family:"Heiti SC","Microsoft YaHei",sans-serif;margin:40px;background:#fafafa}}
.chart-wrap{{background:white;border-radius:12px;box-shadow:0 2px 12px rgba(0,0,0,.08);padding:24px;margin-bottom:32px}}
h1{{color:#222;font-size:22px;margin-bottom:6px}}
.subtitle{{color:#888;font-size:13px;margin-bottom:24px}}
.desc{{color:#555;font-size:14px;line-height:1.7;margin-top:20px}}
</style></head>
<body><div class="chart-wrap">
<h1>{title}</h1><div class="subtitle">{chart_name} | {library}</div>
{embed}
</div><div class="desc">
<strong>数据格式：</strong>{data_fmt}<br>
<strong>说明：</strong>{desc}
</div></body></html>"""


def _validate_network_data(df: pd.DataFrame, src_col: str, tgt_col: str, weight_col: Optional[str]) -> list:
    """验证网络图数据有效性。"""
    warnings = []

    # 检查自环
    self_loops = df[df[src_col] == df[tgt_col]]
    if len(self_loops) > 0:
        warnings.append(f"检测到 {len(self_loops)} 条自环，已保留")

    # 检查重复边
    edge_counts = df.groupby([src_col, tgt_col]).size()
    duplicates = edge_counts[edge_counts > 1]
    if len(duplicates) > 0:
        warnings.append(f"检测到 {len(duplicates)} 条重复边，已自动合并")

    # 检查权重
    if weight_col and weight_col in df.columns:
        weights = pd.to_numeric(df[weight_col], errors="coerce")
        invalid_weights = df[(weights.isna()) | (weights <= 0)]
        if len(invalid_weights) > 0:
            warnings.append(f"检测到 {len(invalid_weights)} 条无效/零值/负值权重，已过滤或修正")

    # 检查节点数
    all_nodes = set(df[src_col].unique()) | set(df[tgt_col].unique())
    if len(all_nodes) > 100:
        warnings.append(f"节点数过多（{len(all_nodes)} 个），可能导致图表混乱")

    return warnings


def _hex_to_rgb(hex_color: str) -> Tuple[int, int, int]:
    """#RRGGBB -> (r, g, b)"""
    s = hex_color.lstrip("#")
    if len(s) != 6:
        return (110, 123, 139)
    return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))


def _interpolate_color(color1: str, color2: str, t: float, alpha: float = 0.8) -> str:
    """在两个颜色之间插值。t=0 返回 color1，t=1 返回 color2"""
    r1, g1, b1 = _hex_to_rgb(color1)
    r2, g2, b2 = _hex_to_rgb(color2)

    r = int(r1 + (r2 - r1) * t)
    g = int(g1 + (g2 - g1) * t)
    b = int(b1 + (b2 - b1) * t)

    return f"rgba({r},{g},{b},{alpha})"


def _spring_layout(edges: List[Tuple[int, int]], n_nodes: int, iterations: int = 80) -> Dict[int, Tuple[float, float]]:
    """简单的力导向布局算法。"""
    np.random.seed(42)
    pos = {i: (np.random.random() * 2 - 1, np.random.random() * 2 - 1) for i in range(n_nodes)}

    for _ in range(iterations):
        forces = {i: np.array([0.0, 0.0]) for i in range(n_nodes)}

        # 斥力（节点间）
        for i in range(n_nodes):
            for j in range(i + 1, n_nodes):
                dx = pos[j][0] - pos[i][0]
                dy = pos[j][1] - pos[i][1]
                dist = np.sqrt(dx**2 + dy**2) + 0.01
                force = 0.5 / (dist + 0.1)
                forces[i][0] -= force * dx / dist
                forces[i][1] -= force * dy / dist
                forces[j][0] += force * dx / dist
                forces[j][1] += force * dy / dist

        # 引力（边连接）
        for i, j in edges:
            dx = pos[j][0] - pos[i][0]
            dy = pos[j][1] - pos[i][1]
            dist = np.sqrt(dx**2 + dy**2) + 0.01
            force = 0.12 * dist
            forces[i][0] += force * dx / dist
            forces[i][1] += force * dy / dist
            forces[j][0] -= force * dx / dist
            forces[j][1] -= force * dy / dist

        # 更新位置
        for i in range(n_nodes):
            pos[i] = (
                pos[i][0] + forces[i][0] * 0.01,
                pos[i][1] + forces[i][1] * 0.01
            )

    return pos


def _assign_unique_colors(all_nodes: List[str], palette: List[str]) -> Dict[str, str]:
    """为每个节点分配唯一颜色。"""
    node_colors = {}
    n_nodes = len(all_nodes)
    n_colors = len(palette)

    if n_nodes <= n_colors:
        for i, node in enumerate(all_nodes):
            node_colors[node] = palette[i]
    else:
        for i, node in enumerate(all_nodes):
            idx = i % n_colors
            node_colors[node] = palette[idx]

    return node_colors


def _compute_edge_widths(weights: List[float]) -> List[float]:
    """根据权重计算更明显的边宽。"""
    if not weights:
        return []

    weights = [float(w) for w in weights]
    min_weight = min(weights)
    max_weight = max(weights)

    if max_weight == min_weight:
        return [5.0] * len(weights)

    # 使用 min-max 拉伸到更明显的区间
    min_width = 2.0
    max_width = 12.0

    return [
        min_width + (w - min_weight) / (max_weight - min_weight) * (max_width - min_width)
        for w in weights
    ]


def generate(
    df: pd.DataFrame = None,
    mapping: Dict[str, str] = None,
    options: Dict[str, Any] = None,
    # 向后兼容旧接口
    excel_path: str = None,
    source: str = "source",
    target: str = "target",
    weight: Optional[str] = None,
    title: str = "网络图",
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

    src_col = mapping.get("source")
    src_col = src_col if src_col is not None else source
    tgt_col = mapping.get("target")
    tgt_col = tgt_col if tgt_col is not None else target
    weight_col = mapping.get("weight")
    weight_col = weight_col if weight_col is not None else weight
    title = options.get("title", title)

    # 链式 exclude：target 排除已解析的 source，避免无 hint 时两者回退到
    # 同一 strs[0]，groupby([src, tgt]) 撞列报错
    _src = _auto_col(df, src_col, "source", "起点", "from", "origin")
    _tgt = _auto_col(df, tgt_col, "target", "终点", "to", "destination",
                     exclude={_src} if _src else None)
    if _src is not None and _src == _tgt:
        _tgt = None

    # 仅在用户显式提供 weight 提示时才自动识别权重列。
    # 此前即使 weight_col=None 也传 "value"/"amount"/"size" 作提示词，
    # 会把任意数值列自动选为权重，造成用户意图外的加权。现在无显式提示
    # 则默认不加权（_weight=None），边宽统一。
    if weight_col:
        _weight = _auto_col(df, weight_col, "weight", "权重", "strength")
    else:
        _weight = None

    for role, col in [("source", _src), ("target", _tgt)]:
        if col is None or col not in df.columns:
            warnings.append(f"找不到必填字段 [{role}]")

    if _src not in df.columns or _tgt not in df.columns:
        return ChartResult(warnings=warnings)

    use_cols = [_src, _tgt]
    if _weight and _weight in df.columns:
        use_cols.append(_weight)

    d = df[use_cols].copy()
    d = d.dropna(subset=[_src, _tgt])

    # 数据验证
    validation_warnings = _validate_network_data(d, _src, _tgt, _weight)
    warnings.extend(validation_warnings)

    # 处理权重
    if _weight and _weight in d.columns:
        d[_weight] = pd.to_numeric(d[_weight], errors="coerce")
        d = d.dropna(subset=[_weight])
        d = d[d[_weight] > 0]

    # 合并重复边
    if _weight and _weight in d.columns:
        d = d.groupby([_src, _tgt], as_index=False)[_weight].sum()
    else:
        d = d.drop_duplicates()

    if len(d) == 0:
        return ChartResult(warnings=warnings + ["处理后无有效数据"])

    # 构建节点和边
    all_nodes = sorted(list(set(d[_src].unique()) | set(d[_tgt].unique())))
    node_map = {n: i for i, n in enumerate(all_nodes)}
    edge_list = [(node_map[s], node_map[t]) for s, t in d[[_src, _tgt]].values]

    # 力导向布局
    pos = _spring_layout(edge_list, len(all_nodes), iterations=100)

    # 节点坐标
    node_x = [pos[i][0] for i in range(len(all_nodes))]
    node_y = [pos[i][1] for i in range(len(all_nodes))]

    # 边权重 -> 线宽
    if _weight and _weight in d.columns:
        edge_weights = d[_weight].astype(float).tolist()
        edge_widths = _compute_edge_widths(edge_weights)
    else:
        edge_weights = [None] * len(edge_list)
        edge_widths = [3.0] * len(edge_list)

    # 节点颜色
    # 配色方案：优先用 options.color_scheme，回退到 _NODE_COLORS
    _scheme = get_color_scheme(options.get("color_scheme", "mckinsey"))
    _palette = _scheme.get("colors") or _NODE_COLORS
    node_color_map = _assign_unique_colors(all_nodes, _palette)
    node_colors = [node_color_map[n] for n in all_nodes]

    # 创建 Plotly 图表
    fig = go.Figure()

    # 边标签数据
    edge_label_x = []
    edge_label_y = []
    edge_label_text = []

    # 添加边
    for i, (s, t) in enumerate(edge_list):
        src_color = node_colors[s]
        tgt_color = node_colors[t]
        edge_color = _interpolate_color(src_color, tgt_color, 0.5, alpha=0.85)

        x0, y0 = pos[s]
        x1, y1 = pos[t]

        weight_text = ""
        if edge_weights[i] is not None:
            # 更友好的格式
            w = edge_weights[i]
            weight_text = str(int(w)) if float(w).is_integer() else f"{w:.2f}"

        fig.add_trace(go.Scatter(
            x=[x0, x1],
            y=[y0, y1],
            mode="lines",
            line=dict(width=edge_widths[i], color=edge_color),
            hovertemplate=(
                f"源节点: {all_nodes[s]}<br>"
                f"目标节点: {all_nodes[t]}<br>"
                f"权重: {weight_text if weight_text else 'N/A'}"
                f"<extra></extra>"
            ),
            showlegend=False
        ))

        # 中点标签
        if weight_text:
            mid_x = (x0 + x1) / 2
            mid_y = (y0 + y1) / 2

            # 轻微偏移，避免正好压在线上
            dx = x1 - x0
            dy = y1 - y0
            norm = np.sqrt(dx**2 + dy**2) + 1e-9
            offset = 0.03
            label_x = mid_x - dy / norm * offset
            label_y = mid_y + dx / norm * offset

            edge_label_x.append(label_x)
            edge_label_y.append(label_y)
            edge_label_text.append(weight_text)

    # 添加边权重标签
    if edge_label_text:
        fig.add_trace(go.Scatter(
            x=edge_label_x,
            y=edge_label_y,
            mode="text",
            text=edge_label_text,
            textposition="middle center",
            textfont=dict(size=11, color="#333"),
            hoverinfo="skip",
            showlegend=False
        ))

    # 添加节点
    fig.add_trace(go.Scatter(
        x=node_x,
        y=node_y,
        mode="markers+text",
        marker=dict(
            size=22,
            color=node_colors,
            line=dict(color="white", width=2)
        ),
        text=all_nodes,
        textposition="top center",
        textfont=dict(size=11),
        hovertemplate="<b>%{text}</b><extra></extra>",
        showlegend=False
    ))

    fig.update_layout(
        title=title,
        showlegend=False,
        hovermode="closest",
        margin=dict(b=20, l=20, r=20, t=60),
        xaxis=dict(showgrid=False, zeroline=False, showticklabels=False),
        yaxis=dict(showgrid=False, zeroline=False, showticklabels=False),
        plot_bgcolor="white",
        paper_bgcolor="white",
        font_family="Heiti SC, Microsoft YaHei, sans-serif"
    )

    chart_html = pio.to_html(fig, full_html=False, include_plotlyjs=False)
    html = _build_html(title, "network_diagram", "plotly", _DATA_FMT, _DESC, chart_html)


    meta = {
        "chart_id": "network_diagram",
        "n_nodes": len(all_nodes),
        "n_edges": len(edge_list),
        "source_col": _src,
        "target_col": _tgt,
        "weight_col": _weight,
        "has_weight": bool(_weight and _weight in d.columns),
    }

    return ChartResult(html=html, spec={}, warnings=warnings, meta=meta)
