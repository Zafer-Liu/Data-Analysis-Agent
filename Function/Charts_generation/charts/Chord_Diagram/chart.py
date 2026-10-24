"""
弦图 Chord Diagram - 关系图表
使用 D3.js chord 布局实现
"""
import logging
log = logging.getLogger(__name__)
import os, sys, json, html as _html
from pathlib import Path
from typing import Dict, Any, Optional, Tuple
import pandas as pd

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
            "#003D7A", "#0084D1", "#00A4EF", "#7FBA00", "#FFB81C",
            "#F7630C", "#DA3B01", "#A4373A", "#6B2C91", "#00B4EF",
        ]}

__all__ = ["generate"]

MCKINSEY_COLORS = [
    "#003D7A", "#0084D1", "#00A4EF", "#7FBA00", "#FFB81C",
    "#F7630C", "#DA3B01", "#A4373A", "#6B2C91", "#00B4EF",
]

def _auto_col(df: pd.DataFrame, *hints: str, exclude: set = None) -> Optional[str]:
    exclude = exclude or set()
    strs = [c for c in df.columns if (df[c].dtype == object or pd.api.types.is_string_dtype(df[c])) and c not in exclude]
    nums = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c]) and c not in exclude]
    col_lower = {str(c).lower(): c for c in df.columns if c not in exclude}

    for h in hints:
        h_lower = str(h).lower()
        if h_lower in col_lower:
            return col_lower[h_lower]

    for h in hints:
        h_lower = str(h).lower()
        for col in df.columns:
            if col in exclude:
                continue
            col_lower_name = str(col).lower()
            if h_lower in col_lower_name or col_lower_name in h_lower:
                return col

    if hints:
        hint = str(hints[0]).lower()
        if any(kw in hint for kw in ["source", "target", "label", "name", "group"]):
            if strs:
                return strs[0]
            if nums:
                return nums[0]
        elif any(kw in hint for kw in ["value", "size", "amount", "count"]):
            if nums:
                return nums[0]
            if strs:
                return strs[0]

    if not hints:
        if strs:
            return strs[0]
        if nums:
            return nums[0]

    return None


def _detect_format(df: pd.DataFrame) -> Tuple[str, Optional[str], Optional[str], Optional[str]]:
    if set(df.index) == set(df.columns):
        return ("adjacency", None, None, None)

    if len(df.columns) > 1:
        first_col = df.columns[0]
        if df[first_col].dtype == object or pd.api.types.is_string_dtype(df[first_col]):
            remaining_cols = df.columns[1:]
            all_numeric = all(pd.api.types.is_numeric_dtype(df[col]) for col in remaining_cols)
            if all_numeric:
                first_col_values = set(str(v) for v in df[first_col].unique())
                remaining_col_names = set(str(c) for c in remaining_cols)
                if first_col_values & remaining_col_names:
                    return ("adjacency_with_label_col", first_col, None, None)
                if len(df) >= len(remaining_cols) * 0.8:
                    return ("adjacency_with_label_col", first_col, None, None)

    exclude = set()
    src = _auto_col(df, "source", "from", "起点", exclude=exclude)
    if src:
        exclude.add(src)
        tgt = _auto_col(df, "target", "to", "终点", exclude=exclude)
        if tgt:
            exclude.add(tgt)
            val = _auto_col(df, "value", "amount", "流量", exclude=exclude)
            if val:
                return ("edge_list", src, tgt, val)

    return ("adjacency", None, None, None)


def _edge_list_to_adjacency(df: pd.DataFrame, src_col: str, tgt_col: str, val_col: str, symmetric: bool = True) -> pd.DataFrame:
    d = df[[src_col, tgt_col, val_col]].copy()
    d[val_col] = pd.to_numeric(d[val_col], errors="coerce").fillna(0)
    d = d[d[val_col] > 0].dropna()

    nodes = sorted(set(d[src_col].astype(str).values) | set(d[tgt_col].astype(str).values))
    matrix = pd.DataFrame(0.0, index=nodes, columns=nodes)

    for _, row in d.iterrows():
        s = str(row[src_col])
        t = str(row[tgt_col])
        v = float(row[val_col])
        matrix.loc[s, t] += v
        if symmetric and s != t:
            matrix.loc[t, s] += v

    return matrix


def _normalize_matrix(matrix: pd.DataFrame) -> pd.DataFrame:
    matrix = matrix.copy()
    matrix.index = matrix.index.astype(str)
    matrix.columns = matrix.columns.astype(str)

    all_nodes = list(dict.fromkeys(list(matrix.index) + list(matrix.columns)))
    matrix = matrix.reindex(index=all_nodes, columns=all_nodes, fill_value=0.0)
    matrix = matrix.fillna(0.0).astype(float)
    return matrix


def _safe_json(obj) -> str:
    """JSON 序列化并转义 `</`，防止嵌入 <script> 时 `</script>` 破壳注入。
    节点名来自 SQL 列名/数据值，用户/LLM 可控，含 `</script>` 即可提前闭合脚本块。
    """
    return json.dumps(obj, ensure_ascii=False).replace("</", "<\\/")


def _build_chord_html(matrix: pd.DataFrame, title: str, highlight_node: Optional[str] = None, highlight_color: str = "#003D7A", palette: list = None) -> str:
    nodes = list(matrix.index)
    n = len(nodes)

    matrix_array = matrix.values.tolist()

    _palette = palette or MCKINSEY_COLORS
    node_colors = []
    for i, node in enumerate(nodes):
        if highlight_node and node == highlight_node:
            node_colors.append(highlight_color)
        else:
            node_colors.append(_palette[i % len(_palette)])

    # 安全 JSON：转义 </ 防止 </script> 破壳
    matrix_json = _safe_json(matrix_array)
    nodes_json = _safe_json([{"id": node, "color": color} for node, color in zip(nodes, node_colors)])
    # title 用户/LLM 可控，HTML 转义防注入
    esc_title = _html.escape(title)

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <title>{esc_title}</title>
    <script src="https://d3js.org/d3.v7.min.js"></script>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: "Microsoft YaHei", sans-serif; background: #fafafa; padding: 40px; }}
        .container {{ background: white; border-radius: 12px; box-shadow: 0 2px 12px rgba(0,0,0,0.08); padding: 24px; max-width: 1200px; margin: 0 auto; }}
        h1 {{ color: #222; font-size: 22px; margin-bottom: 6px; }}
        .subtitle {{ color: #888; font-size: 13px; margin-bottom: 24px; }}
        #chord-container {{ display: flex; justify-content: center; align-items: center; min-height: 600px; }}
        .ribbon {{ fill-opacity: 0.67; stroke: none; }}
        .node-arc {{ stroke: white; stroke-width: 2px; }}
        .node-label {{ font-size: 12px; font-weight: 500; text-anchor: middle; pointer-events: none; }}
        .tooltip {{
            position: absolute; background: rgba(0,0,0,0.8); color: white; padding: 8px 12px;
            border-radius: 4px; font-size: 12px; pointer-events: none; display: none; z-index: 1000;
        }}
    </style>
</head>
<body>
<div class="container">
    <h1>{esc_title}</h1>
    <div class="subtitle">弦图 Chord Diagram | D3.js</div>
    <div id="chord-container"></div>
</div>
<div class="tooltip" id="tooltip"></div>

<script>
const matrix = {matrix_json};
const nodes = {nodes_json};

const width = 800, height = 800;
const innerRadius = Math.min(width, height) * 0.3;
const outerRadius = innerRadius + 30;

const svg = d3.select("#chord-container")
    .append("svg")
    .attr("width", width)
    .attr("height", height)
    .attr("viewBox", [-width / 2, -height / 2, width, height]);

const chord = d3.chord()
    .padAngle(0.05)
    .sortSubgroups(d3.descending)
    .sortChords(d3.descending);

const chords = chord(matrix);

const arc = d3.arc()
    .innerRadius(innerRadius)
    .outerRadius(outerRadius);

const ribbon = d3.ribbon()
    .radius(innerRadius);

const g = svg.append("g");

g.selectAll(".node-arc")
    .data(chords.groups)
    .join("path")
    .attr("class", "node-arc")
    .attr("fill", (d, i) => nodes[i].color)
    .attr("stroke", (d, i) => d3.rgb(nodes[i].color).darker())
    .attr("d", arc);

g.selectAll(".ribbon")
    .data(chords)
    .join("path")
    .attr("class", "ribbon")
    .attr("d", ribbon)
    .attr("fill", d => d3.rgb(nodes[d.source.index].color).copy({{opacity: 0.6}}))
    .attr("stroke", d => d3.rgb(nodes[d.source.index].color).darker())
    .on("mouseover", function(event, d) {{
        const tooltip = document.getElementById("tooltip");
        const sourceName = nodes[d.source.index].id;
        const targetName = nodes[d.target.index].id;
        const value = matrix[d.source.index][d.target.index];
        tooltip.innerHTML = `<strong>${{sourceName}} → ${{targetName}}</strong><br>强度: ${{value.toFixed(2)}}`;
        tooltip.style.display = "block";
        tooltip.style.left = (event.pageX + 10) + "px";
        tooltip.style.top = (event.pageY + 10) + "px";
    }})
    .on("mousemove", function(event) {{
        const tooltip = document.getElementById("tooltip");
        tooltip.style.left = (event.pageX + 10) + "px";
        tooltip.style.top = (event.pageY + 10) + "px";
    }})
    .on("mouseout", function() {{
        document.getElementById("tooltip").style.display = "none";
    }});

g.selectAll(".node-label")
    .data(chords.groups)
    .join("text")
    .attr("class", "node-label")
    .attr("dy", "0.35em")
    .attr("transform", d => {{
        const angle = (d.startAngle + d.endAngle) / 2;
        const x = Math.cos(angle - Math.PI / 2) * (outerRadius + 40);
        const y = Math.sin(angle - Math.PI / 2) * (outerRadius + 40);
        return `translate(${{x}},${{y}})rotate(${{angle * 180 / Math.PI - 90}})`;
    }})
    .text((d, i) => nodes[i].id)
    .attr("fill", "#333");
</script>
</body>
</html>"""

    return html


def generate(
    df: pd.DataFrame = None,
    mapping: Dict[str, str] = None,
    options: Dict[str, Any] = None,
    excel_path: str = None,
    source: str = "source",
    target: str = "target",
    value: str = "value",
    title: str = "弦图",
    **kwargs
) -> ChartResult:
    warnings = []
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

    title = options.get("title", title)
    highlight_node = options.get("highlight_node")
    highlight_color = options.get("highlight_color", "#003D7A")
    symmetric = options.get("symmetric", True)

    fmt, src_col, tgt_col, val_col = _detect_format(df)

    if fmt == "adjacency_with_label_col":
        label_col = src_col
        matrix = df.set_index(label_col).copy()
        matrix = _normalize_matrix(matrix)
    elif fmt == "adjacency":
        matrix = _normalize_matrix(df.copy())
    elif fmt == "edge_list":
        # _detect_format 解析三元组时已用 exclude 保证 src/tgt 不撞列；
        # 这里直接复用其结果，mapping 显式指定的角色优先。此前无 exclude
        # 地重新解析会让 src/tgt 回退到同一 strs[0]，df[[c,c,v]] 产生重复列。
        _src = mapping.get("source") if mapping.get("source") in df.columns else src_col
        _tgt = mapping.get("target") if mapping.get("target") in df.columns else tgt_col
        _val = mapping.get("value") if mapping.get("value") in df.columns else val_col

        if _src is None or _src not in df.columns:
            return ChartResult(warnings=["找不到必填字段 [source]"])
        if _tgt is None or _tgt not in df.columns:
            return ChartResult(warnings=["找不到必填字段 [target]"])
        if _val is None or _val not in df.columns:
            return ChartResult(warnings=["找不到必填字段 [value]"])

        matrix = _edge_list_to_adjacency(df, _src, _tgt, _val, symmetric=symmetric)
        matrix = _normalize_matrix(matrix)
    else:
        return ChartResult(warnings=["无法识别数据格式"])

    if matrix.empty:
        return ChartResult(warnings=["无有效数据"])

    try:
        matrix = matrix.astype(float)
    except Exception as e:
        log.warning("[chart] 图表生成异常: %s", e)
        return ChartResult(warnings=[f"数据类型转换失败: {e}"])

    # 配色方案：优先用 options.color_scheme，回退到 MCKINSEY_COLORS
    _scheme = get_color_scheme(options.get("color_scheme", "mckinsey"))
    _palette = _scheme.get("colors") or MCKINSEY_COLORS

    html = _build_chord_html(matrix, title, highlight_node, highlight_color, _palette)

    meta = {
        "chart_id": "chord_diagram",
        "n_nodes": len(matrix),
        "n_edges": int((matrix > 0).sum().sum()),
        "format": fmt,
    }
    return ChartResult(html=html, spec={}, warnings=warnings, meta=meta)
