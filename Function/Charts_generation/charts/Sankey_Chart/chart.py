"""
桑基图 Sankey Diagram - 流向图表
图表分类: 流向 Flow
感知排名: ★★★★☆

统一接口:
    generate(df, mapping, options) -> ChartResult
"""
import logging
log = logging.getLogger(__name__)
import os
import sys
from pathlib import Path
from typing import Dict, Any, Optional, List

import pandas as pd
import plotly.graph_objects as go
import plotly.io as pio

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
            "#1A2B4C", "#2C5F8A", "#3A7CA5", "#4A90B4", "#D4A843",
            "#C65D3B", "#5D8C6B", "#7A8A9A", "#E8EDF2", "#2E4057",
            "#C2A144", "#A3B5C8", "#8B5E3C", "#4A6B8A", "#D9E2EC",
        ]}

__all__ = ["generate"]

_DATA_FMT = "source列 + target列 + value列"
_DESC = "用带宽度箭头表示从起点到终点的流量/转移，适合能源/资金/用户流动分析。"

# 配色：专业蓝系 + 绿系 + 紫系 + 中性色（避免黄色刺眼）
_DISTINCT_COLORS = [
    "#1A2B4C",    # 深海军蓝 - 主色调，体现专业与权威
    "#2C5F8A",    # 沉稳蓝 - 标题、重点板块
    "#3A7CA5",    # 中蓝 - 图表主色、强调元素
    "#4A90B4",    # 柔和蓝 - 辅助图表色
    "#D4A843",    # 暗金 - 高亮点缀、关键数据
    "#C65D3B",    # 陶土红 - 警示/负面数据
    "#5D8C6B",    # 鼠尾草绿 - 增长/正面数据
    "#7A8A9A",    # 岩板灰 - 次要文字
    "#E8EDF2",    # 浅灰蓝 - 背景色
    "#2E4057",    # 暗灰蓝 - 深色背景替代
    "#C2A144",    # 暖金色 - 第二强调色
    "#A3B5C8",    # 雾蓝灰 - 边框/分隔线
    "#8B5E3C",    # 深棕 - 温暖点缀（可选）
    "#4A6B8A",    # 灰蓝 - 中性数据
    "#D9E2EC"    # 极浅蓝 - 表格斑马纹
]


def _auto_col(df: pd.DataFrame, *hints: str) -> Optional[str]:
    """根据提示自动查找匹配的列名。"""
    if not hints or not df.columns.size:
        return None

    col_lower = {str(c).lower(): c for c in df.columns}

    # 1. 精确匹配
    for h in hints:
        h_lower = str(h).lower()
        if h_lower in col_lower:
            return col_lower[h_lower]

    # 2. 模糊匹配（包含关系）
    for h in hints:
        h_lower = str(h).lower()
        for col in df.columns:
            col_lower_name = str(col).lower()
            if h_lower in col_lower_name or col_lower_name in h_lower:
                return col

    # 3. 类型匹配
    hint = str(hints[0]).lower() if hints else ""
    strs = [c for c in df.columns if (df[c].dtype == object or pd.api.types.is_string_dtype(df[c]))]
    nums = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]

    if any(
        kw in hint
        for kw in [
            "source", "target", "label", "name", "group", "category", "phase",
            "row", "col", "path", "text", "word", "location", "geo"
        ]
    ):
        return strs[0] if strs else (nums[0] if nums else None)

    if any(
        kw in hint
        for kw in [
            "value", "size", "amount", "count", "frequency", "score", "rank",
            "actual", "target", "range"
        ]
    ):
        return nums[0] if nums else (strs[0] if strs else None)

    if hint == "x":
        return strs[0] if strs else (nums[0] if nums else None)
    if hint == "y":
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


def _validate_sankey_data(df: pd.DataFrame, src_col: str, tgt_col: str, val_col: str) -> list:
    """验证桑基图数据有效性，返回警告列表。"""
    warnings = []

    self_loops = df[df[src_col] == df[tgt_col]]
    if len(self_loops) > 0:
        warnings.append(f"检测到 {len(self_loops)} 条自环（source=target），已保留")

    edge_counts = df.groupby([src_col, tgt_col]).size()
    duplicates = edge_counts[edge_counts > 1]
    if len(duplicates) > 0:
        warnings.append(f"检测到 {len(duplicates)} 条重复边，已自动合并")

    zero_vals = df[df[val_col] <= 0]
    if len(zero_vals) > 0:
        warnings.append(f"检测到 {len(zero_vals)} 条零值或负值流量，已过滤")

    all_nodes = set(df[src_col].unique()) | set(df[tgt_col].unique())
    if len(all_nodes) > 30:
        warnings.append(f"节点数过多（{len(all_nodes)} 个），可能导致图表混乱，建议简化数据")

    return warnings


def _hex_to_rgba(hex_color: str, alpha: float = 0.42) -> str:
    """#RRGGBB -> rgba(r,g,b,a)"""
    s = hex_color.lstrip("#")
    if len(s) != 6:
        return f"rgba(110,123,139,{alpha})"
    r = int(s[0:2], 16)
    g = int(s[2:4], 16)
    b = int(s[4:6], 16)
    return f"rgba({r},{g},{b},{alpha})"

def _stable_color(node_name: str, palette: List[str]) -> str:
    """稳定颜色映射。"""
    s = str(node_name)
    idx = sum((i + 1) * ord(ch) for i, ch in enumerate(s)) % len(palette)
    return palette[idx]

def _assign_unique_colors(all_nodes: List[str], palette: List[str]) -> Dict[str, str]:
    """为每个节点分配唯一颜色。
    
    策略：
    1. 如果节点数 <= 调色板大小，直接按顺序分配
    2. 如果节点数 > 调色板大小，循环使用但确保相邻节点颜色不同
    """
    node_colors = {}
    n_nodes = len(all_nodes)
    n_colors = len(palette)
    
    if n_nodes <= n_colors:
        # 直接分配，每个节点一个颜色
        for i, node in enumerate(all_nodes):
            node_colors[node] = palette[i]
    else:
        # 节点过多，循环分配但避免相邻重复
        for i, node in enumerate(all_nodes):
            # 使用 (i * 2) % n_colors 确保相邻节点颜色差异大
            idx = (i * 2) % n_colors
            node_colors[node] = palette[idx]
    
    return node_colors

def _build_node_order(d: pd.DataFrame, src_col: str, tgt_col: str, val_col: str) -> List[str]:
    """
    节点排序优化：
    1) 先尝试拓扑分层（DAG效果最佳）
    2) 同层按流量(入+出)降序
    3) 若有环，退化为“源节点优先 + 高流量优先”
    """
    out_sum = d.groupby(src_col)[val_col].sum().to_dict()
    in_sum = d.groupby(tgt_col)[val_col].sum().to_dict()
    # 去重：源列可含重复值（A→B, A→C 很常见）。若不去重，node_map 会把
    # 同名节点映射到多个索引，产生孤儿节点并破坏拓扑排序的 indegree 计数。
    all_nodes = list(dict.fromkeys(
        list(pd.Index(d[src_col]).append(pd.Index(d[tgt_col])))
    ))

    indegree = {n: 0 for n in all_nodes}
    adj = {n: [] for n in all_nodes}
    for s, t in d[[src_col, tgt_col]].itertuples(index=False):
        adj[s].append(t)
        indegree[t] += 1

    current = [n for n in all_nodes if indegree[n] == 0]
    layers: List[List[str]] = []
    visited = set()

    while current:
        current = sorted(
            current,
            key=lambda n: (out_sum.get(n, 0) + in_sum.get(n, 0)),
            reverse=True
        )
        layers.append(current)

        next_nodes = []
        for n in current:
            visited.add(n)
            for nb in adj.get(n, []):
                indegree[nb] -= 1
                if indegree[nb] == 0:
                    next_nodes.append(nb)
        current = list(set(next_nodes))

    if len(visited) < len(all_nodes):
        remain = [n for n in all_nodes if n not in visited]
        remain = sorted(
            remain,
            key=lambda n: (
                0 if in_sum.get(n, 0) == 0 else 1,
                -(out_sum.get(n, 0) + in_sum.get(n, 0))
            )
        )
        layers.append(remain)

    return [n for layer in layers for n in layer]


def generate(
    df: pd.DataFrame = None,
    mapping: Dict[str, str] = None,
    options: Dict[str, Any] = None,
    excel_path: str = None,  # 向后兼容旧接口
    source: str = "source",
    target: str = "target",
    value: str = "value",
    title: str = "桑基图",
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
    val_col = mapping.get("value")
    val_col = val_col if val_col is not None else value
    title = options.get("title", title)

    _src = _auto_col(df, src_col, "source", "起点", "from", "origin")
    _tgt = _auto_col(df, tgt_col, "target", "终点", "to", "destination")
    _val = _auto_col(df, val_col, "value", "流量", "flow", "amount", "num")

    for role, col in [("source", _src), ("target", _tgt), ("value", _val)]:
        if col is None or col not in df.columns:
            warnings.append(f"找不到必填字段 [{role}]")

    if _src not in df.columns or _tgt not in df.columns or _val not in df.columns:
        return ChartResult(warnings=warnings)

    d = df[[_src, _tgt, _val]].copy()
    d = d.dropna()
    d[_val] = pd.to_numeric(d[_val], errors="coerce").fillna(0)

    warnings.extend(_validate_sankey_data(d, _src, _tgt, _val))

    d = d[d[_val] > 0]
    d = d.groupby([_src, _tgt], as_index=False)[_val].sum()

    if len(d) == 0:
        return ChartResult(warnings=warnings + ["处理后无有效数据"])

    all_nodes = _build_node_order(d, _src, _tgt, _val)
    node_map = {n: i for i, n in enumerate(all_nodes)}

    link_src = [node_map[s] for s in d[_src]]
    link_tgt = [node_map[t] for t in d[_tgt]]
    link_val = d[_val].tolist()
    total_flow = float(sum(link_val)) if link_val else 0.0

    out_sum = d.groupby(_src)[_val].sum().to_dict()
    in_sum = d.groupby(_tgt)[_val].sum().to_dict()

    # 节点颜色：为每个节点分配唯一颜色
    # 配色方案：优先用 options.color_scheme，回退到 _DISTINCT_COLORS
    _scheme = get_color_scheme(options.get("color_scheme", "mckinsey"))
    _palette = _scheme.get("colors") or _DISTINCT_COLORS
    node_color_map = _assign_unique_colors(all_nodes, _palette)
    node_color: List[str] = [node_color_map[n] for n in all_nodes]

    # 连线颜色：继承 source 节点色，降低透明度避免喧宾夺主
    link_color = [_hex_to_rgba(node_color[s], alpha=0.35) for s in link_src]

    if total_flow > 0:
        link_pct = [f"{(v / total_flow) * 100:.1f}%" for v in link_val]
    else:
        link_pct = ["0.0%" for _ in link_val]

    node_in = [float(in_sum.get(n, 0)) for n in all_nodes]
    node_out = [float(out_sum.get(n, 0)) for n in all_nodes]
    node_throughput = [max(i, o) for i, o in zip(node_in, node_out)]
    node_pct = [(t / total_flow * 100.0) if total_flow > 0 else 0.0 for t in node_throughput]
    node_custom = list(zip(node_in, node_out, node_throughput, node_pct))

    fig = go.Figure(
        data=[
            go.Sankey(
                arrangement=options.get("arrangement", "snap"),
                node=dict(
                    label=all_nodes,
                    color=node_color,
                    pad=options.get("node_pad", 18),
                    thickness=options.get("node_thickness", 18),
                    line=dict(color="rgba(255,255,255,0.95)", width=1.2),
                    customdata=node_custom,
                    hovertemplate=(
                        "<b>%{label}</b><br>"
                        "入流: %{customdata[0]:,.2f}<br>"
                        "出流: %{customdata[1]:,.2f}<br>"
                        "吞吐: %{customdata[2]:,.2f}<br>"
                        "占总流: %{customdata[3]:.1f}%"
                        "<extra></extra>"
                    ),
                ),
                link=dict(
                    source=link_src,
                    target=link_tgt,
                    value=link_val,
                    color=link_color,
                    customdata=link_pct,
                    hovertemplate=(
                        "<b>%{source.label}</b> → <b>%{target.label}</b><br>"
                        "流量: %{value:,.2f}<br>"
                        "占总流: %{customdata}"
                        "<extra></extra>"
                    ),
                ),
            )
        ]
    )

    fig.update_layout(
        title=title,
        font_family="Heiti SC, Microsoft YaHei, sans-serif",
        margin=dict(l=20, r=20, t=50, b=20),
        paper_bgcolor="white",
        plot_bgcolor="white",
    )

    chart_html = pio.to_html(fig, full_html=False, include_plotlyjs=False)
    html = _build_html(title, "sankey", "plotly", _DATA_FMT, _DESC, chart_html)


    meta = {
        "chart_id": "sankey",
        "n_nodes": len(all_nodes),
        "n_edges": len(d),
        "source_col": _src,
        "target_col": _tgt,
        "value_col": _val,
        "total_flow": total_flow,
    }

    return ChartResult(html=html, spec={}, warnings=warnings, meta=meta)