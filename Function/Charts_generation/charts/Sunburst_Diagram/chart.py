"""
旭日图 Sunburst - 占比图表
图表分类: 占比 Proportion | 书章节: Ch4
感知排名: ★★★★☆

统一接口:
    generate(df, mapping, options) -> ChartResult
"""
import logging
log = logging.getLogger(__name__)
import os
import sys
from pathlib import Path
from typing import Dict, Any, Optional
import json
import uuid

import pandas as pd
import plotly.graph_objects as go

_PROJECT_ROOT = Path(__file__).parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from charts.base import ChartResult
from charts.color_schemes import get_color_scheme

__all__ = ["generate"]

_DATA_FMT = "labels列(节点名称) + values列(数值) + 可选parents列(父级)"
_DESC = "用圆形扇区表示占比，支持多层级嵌套展示。适合展示有层级且数量较多的分类数据。"


def _auto_col(df: pd.DataFrame, *hints: str) -> Optional[str]:
    """根据提示自动查找匹配的列名。"""
    strs = [c for c in df.columns if (df[c].dtype == object or pd.api.types.is_string_dtype(df[c]))]
    nums = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]
    col_lower = {str(c).lower(): c for c in df.columns}

    # 1. 精确匹配
    for h in hints:
        h_lower = str(h).lower()
        if h_lower in col_lower:
            return col_lower[h_lower]

    # 2. 类型匹配
    if hints:
        hint = str(hints[0]).lower()
        if any(kw in hint for kw in ["source", "target", "label", "name", "group", "category", "phase", "row", "col", "path", "text", "word", "location", "geo"]):
            if strs:
                return strs[0]
            if nums:
                return nums[0]
        elif any(kw in hint for kw in ["value", "size", "amount", "count", "frequency", "score", "rank", "actual", "target", "range"]):
            if nums:
                return nums[0]
            if strs:
                return strs[0]
        elif hint == "x":
            if strs:
                return strs[0]
            if nums:
                return nums[0]
        elif hint == "y":
            if nums:
                return nums[0]
            if strs:
                return strs[0]

    # 3. 模糊匹配
    for h in hints:
        h_lower = str(h).lower()
        for col in df.columns:
            col_lower_name = str(col).lower()
            if h_lower in col_lower_name or col_lower_name in h_lower:
                return col

    # 4. 自动推断
    if not hints:
        if strs:
            return strs[0]
        if nums:
            return nums[0]

    return None


def _build_html(title: str, chart_name: str, library: str,
                data_fmt: str, desc: str, embed: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<style>
* {{
  margin: 0;
  padding: 0;
  box-sizing: border-box;
}}
html, body {{
  width: 100%;
  height: 100%;
}}
body {{
  font-family: "Heiti SC", "Microsoft YaHei", sans-serif;
  background: #fafafa;
  padding: 20px;
}}
.container {{
  max-width: 1200px;
  margin: 0 auto;
}}
.chart-wrap {{
  background: white;
  border-radius: 12px;
  box-shadow: 0 2px 12px rgba(0,0,0,.08);
  padding: 24px;
  margin-bottom: 32px;
  min-height: 600px;
  display: flex;
  flex-direction: column;
}}
.chart-wrap h1 {{
  color: #222;
  font-size: 22px;
  margin-bottom: 6px;
}}
.chart-wrap .subtitle {{
  color: #888;
  font-size: 13px;
  margin-bottom: 24px;
}}
.chart-container {{
  flex: 1;
  width: 100%;
  min-height: 500px;
}}
.plotly-graph-div {{
  width: 100% !important;
  height: 100% !important;
}}
.desc {{
  background: white;
  border-radius: 12px;
  box-shadow: 0 2px 12px rgba(0,0,0,.08);
  padding: 24px;
  color: #555;
  font-size: 14px;
  line-height: 1.7;
}}
.desc strong {{
  color: #222;
}}
</style>
</head>
<body>
<div class="container">
<div class="chart-wrap">
<h1>{title}</h1>
<div class="subtitle">{chart_name} | {library}</div>
<div class="chart-container">
{embed}
</div>
</div>
<div class="desc">
<strong>数据格式：</strong>{data_fmt}<br>
<strong>说明：</strong>{desc}
</div>
</div>
</body>
</html>"""


from typing import List, Tuple


def _is_text_col(s: pd.Series) -> bool:
    return s.dtype == object or pd.api.types.is_string_dtype(s)


def _pick_text_cols(df: pd.DataFrame, value_col: str) -> List[str]:
    cols = []
    for c in df.columns:
        if c == value_col:
            continue
        if _is_text_col(df[c]):
            cols.append(c)
    return cols


def _parent_child_consistency(df: pd.DataFrame, parent_col: str, child_col: str) -> float:
    """
    衡量 parent->child 的层级合理性：
    一个 child 对应多个 parent 的比例越低越好（理想为1对1归属）
    """
    x = df[[parent_col, child_col]].dropna().copy()
    if x.empty:
        return 0.0
    grp = x.groupby(child_col)[parent_col].nunique()
    # child 只归属 1 个 parent 的比例
    score = (grp == 1).mean()
    return float(score)


def _infer_hierarchy_cols(df: pd.DataFrame, text_cols: List[str], max_levels: int = 3) -> List[str]:
    """
    推断层级顺序：从父到子（例如 省->市->区）
    策略：
    1) 候选列按 unique 数升序（父级通常更少）
    2) 邻接列用一致性校验，必要时交换
    """
    if not text_cols:
        return []

    # 先按基数从小到大
    ranked = sorted(text_cols, key=lambda c: df[c].nunique(dropna=True))
    ranked = ranked[:max_levels]

    # 邻接一致性修正（简单稳健版）
    changed = True
    while changed:
        changed = False
        for i in range(len(ranked) - 1):
            a, b = ranked[i], ranked[i + 1]
            s_ab = _parent_child_consistency(df, a, b)  # a父 b子
            s_ba = _parent_child_consistency(df, b, a)  # b父 a子
            if s_ba > s_ab + 0.05:  # 有明显优势则交换
                ranked[i], ranked[i + 1] = ranked[i + 1], ranked[i]
                changed = True

    return ranked  # 父->子


def _build_sunburst_tree_from_levels(
        df: pd.DataFrame,
        level_cols: List[str],  # 父->子，如 [省, 市, 区]
        value_col: str
) -> Tuple[List[str], List[str], List[str], List[float]]:
    """
    生成 plotly sunburst 所需 ids/labels/parents/values
    - 使用 ids 防重名：例如 "省=浙江|市=杭州|区=西湖"
    - 父节点值 = 子节点汇总
    """
    if not level_cols:
        return [], [], [], []

    d = df.copy()
    # 清理
    for c in level_cols:
        d[c] = d[c].astype(str).str.strip()
        d = d[d[c].notna() & (d[c] != "")]
    d[value_col] = pd.to_numeric(d[value_col], errors="coerce")
    d = d[d[value_col].notna() & (d[value_col] > 0)]

    if d.empty:
        return [], [], [], []

    # 先聚合到最细粒度（全部层级）
    leaf = d.groupby(level_cols, dropna=False, as_index=False)[value_col].sum()

    node_value = {}  # node_id -> value
    node_label = {}  # node_id -> label
    node_parent = {}  # node_id -> parent_id ("")

    def make_id(path_cols: List[str], path_vals: List[str]) -> str:
        return "|".join([f"{k}={v}" for k, v in zip(path_cols, path_vals)])

    # 对每个叶子路径，把各层节点都累计一遍
    for _, row in leaf.iterrows():
        v = float(row[value_col])
        vals = [str(row[c]) for c in level_cols]

        for i in range(len(level_cols)):  # 0..L-1
            cur_cols = level_cols[: i + 1]
            cur_vals = vals[: i + 1]
            cur_id = make_id(cur_cols, cur_vals)

            if i == 0:
                parent_id = ""
            else:
                parent_id = make_id(level_cols[:i], vals[:i])

            node_value[cur_id] = node_value.get(cur_id, 0.0) + v
            node_label[cur_id] = cur_vals[-1]
            node_parent[cur_id] = parent_id

    # 输出（按层级+label稳定排序，便于复现）
    all_ids = list(node_value.keys())
    all_ids.sort(key=lambda nid: (nid.count("|"), nid))

    ids = all_ids
    labels = [node_label[nid] for nid in ids]
    parents = [node_parent[nid] for nid in ids]
    values = [float(node_value[nid]) for nid in ids]

    return ids, labels, parents, values

def generate(
    df: pd.DataFrame = None,
    mapping: Dict[str, str] = None,
    options: Dict[str, Any] = None,
    excel_path: str = None,
    title: str = "旭日图",
    **kwargs
) -> ChartResult:
    warnings: list = []
    options = options or {}

    def _is_text_col(s: pd.Series) -> bool:
        return s.dtype == object or str(s.dtype) in ("string", "str")

    def _pick_value_col(_df: pd.DataFrame) -> Optional[str]:
        # 优先最后一列是数值
        last_col = _df.columns[-1]
        if pd.api.types.is_numeric_dtype(_df[last_col]):
            return last_col

        # 否则取最后一个数值列
        num_cols = [c for c in _df.columns if pd.api.types.is_numeric_dtype(_df[c])]
        return num_cols[-1] if num_cols else None

    def _build_tree(_df: pd.DataFrame, level_cols: list, value_col: str):
        d = _df.copy()

        # 清洗
        for c in level_cols:
            d[c] = d[c].astype(str).str.strip()
            d = d[d[c].notna() & (d[c] != "")]
        d[value_col] = pd.to_numeric(d[value_col], errors="coerce")
        d = d[d[value_col].notna() & (d[value_col] > 0)]

        if d.empty:
            return [], [], [], []

        leaf = d.groupby(level_cols, as_index=False)[value_col].sum()

        node_value, node_label, node_parent = {}, {}, {}

        def make_id(vals):
            return "|".join([f"{c}={v}" for c, v in zip(level_cols, vals)])

        for _, row in leaf.iterrows():
            v = float(row[value_col])
            vals = [str(row[c]) for c in level_cols]

            for i in range(len(level_cols)):
                cur_id = make_id(vals[: i + 1])
                parent_id = "" if i == 0 else make_id(vals[:i])

                node_value[cur_id] = node_value.get(cur_id, 0.0) + v
                node_label[cur_id] = vals[i]
                node_parent[cur_id] = parent_id

        ids = sorted(node_value.keys(), key=lambda x: (x.count("|"), x))
        labels = [node_label[i] for i in ids]
        parents = [node_parent[i] for i in ids]
        values = [float(node_value[i]) for i in ids]
        return ids, labels, parents, values

    # 读取数据
    if df is None:
        if excel_path:
            try:
                df = pd.read_excel(excel_path)
            except Exception as e:
                log.warning("[chart] 图表生成异常: %s", e)
                return ChartResult(warnings=[f"读取Excel失败: {e}"])
        else:
            return ChartResult(warnings=["请提供 df 或 excel_path"])

    if df is None or df.empty:
        return ChartResult(warnings=["输入数据为空"])

    title = options.get("title", title)

    color_scheme_name = options.get("color_scheme", "mckinsey")
    color_scheme = get_color_scheme(color_scheme_name)
    marker_colors = color_scheme.get("colors", ["#003D7A", "#0084D1", "#00A4EF"])

    # 调度器 _auto_detect_mapping 对 sunburst 设 labels/parents/values
    # （扁平层级格式：每行一个节点）。模块此前完全忽略 mapping，只支持
    # 多文本列作为层级的宽格式。这里补齐对 mapping 的支持：
    #   - 若 labels + parents 列都在 df 中 → 走扁平层级路径
    #   - 否则 → 走多层级列推断路径（原逻辑），但尊重 mapping.values
    map_labels = mapping.get("labels")
    map_labels = map_labels if map_labels is not None else mapping.get("label")
    map_parents = mapping.get("parents")
    map_parents = map_parents if map_parents is not None else mapping.get("parent")
    map_values = mapping.get("values")
    map_values = map_values if map_values is not None else mapping.get("value")

    flat_hierarchy = (
        isinstance(map_labels, str) and map_labels in df.columns
        and isinstance(map_parents, str) and map_parents in df.columns
    )

    if flat_hierarchy:
        # ── 扁平层级格式：labels + parents + values 每行一个节点 ──
        lbl_col, par_col = map_labels, map_parents
        val_col = map_values if (isinstance(map_values, str) and map_values in df.columns) else None
        if val_col is None:
            # 回退：取最后一个数值列
            num_cols = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]
            val_col = num_cols[-1] if num_cols else None
        if val_col is None:
            return ChartResult(warnings=["找不到数值列 (values)"])

        work_df = df.copy()
        work_df[lbl_col] = work_df[lbl_col].astype(str).str.strip()
        work_df[par_col] = work_df[par_col].fillna("").astype(str).str.strip()
        work_df[val_col] = pd.to_numeric(work_df[val_col], errors="coerce")
        invalid_n = int(work_df[val_col].isna().sum())
        if invalid_n > 0:
            warnings.append(f"警告：{val_col} 列有 {invalid_n} 行无法转为数值，已忽略")

        # 零/负值叶节点会被丢弃（sunburst 要求正数），需明确警告
        nonpos_n = int((work_df[val_col] <= 0).sum())
        if nonpos_n > 0:
            warnings.append(f"警告：{val_col} 列有 {nonpos_n} 行 ≤0，已忽略（旭日图需正数）")
        work_df = work_df[work_df[val_col].notna() & (work_df[val_col] > 0)].copy()

        if work_df.empty:
            return ChartResult(warnings=[f"错误：{val_col} 列无有效数据（需为正数）"])

        # 空字符串 parent 视为根节点
        ids = work_df[lbl_col].astype(str).tolist()
        labels = ids[:]
        parents = [p if p != "" else "" for p in work_df[par_col].astype(str).tolist()]
        values = work_df[val_col].astype(float).tolist()
        level_cols = [lbl_col, par_col]
        values_col = val_col

        warnings.append(f"使用扁平层级映射：labels={lbl_col}, parents={par_col}, values={val_col}")
    else:
        # ── 多层级列推断格式（原逻辑）──
        # 尊重 mapping.values（若有），否则取最后一列/最后数值列
        if isinstance(map_values, str) and map_values in df.columns:
            values_col = map_values
        else:
            last_col = df.columns[-1]
            if pd.api.types.is_numeric_dtype(df[last_col]):
                values_col = last_col
            else:
                num_cols = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]
                values_col = num_cols[-1] if num_cols else None

        if values_col is None:
            return ChartResult(warnings=["找不到数值列（最后一列或最后一个数值列）"])

        work_df = df.copy()
        work_df[values_col] = pd.to_numeric(work_df[values_col], errors="coerce")
        invalid_n = int(work_df[values_col].isna().sum())
        if invalid_n > 0:
            warnings.append(f"警告：{values_col} 列有 {invalid_n} 行无法转为数值，已忽略")
        nonpos_n = int((work_df[values_col] <= 0).sum())
        if nonpos_n > 0:
            warnings.append(f"警告：{values_col} 列有 {nonpos_n} 行 ≤0，已忽略（旭日图需正数）")
        work_df = work_df[work_df[values_col].notna() & (work_df[values_col] > 0)].copy()

        if work_df.empty:
            return ChartResult(warnings=[f"错误：{values_col} 列无有效数据（需为正数）"])

        # 除 values 外，按列顺序取文本列作为层级列
        level_cols = [c for c in work_df.columns if c != values_col and _is_text_col(work_df[c])]

        if not level_cols:
            return ChartResult(warnings=["找不到可用于层级的文本列"])

        # 只取前3层
        level_cols = level_cols[:3]

        ids, labels, parents, values = _build_tree(work_df, level_cols, values_col)
        if not ids:
            return ChartResult(warnings=["错误：构建 sunburst 树失败（无有效节点）"])
        warnings.append(f"按列顺序自动识别层级：{level_cols}")

    colors = (marker_colors * (len(labels) // len(marker_colors) + 1))[:len(labels)]

    # 构建 hovertemplate，显示层级路径和数值。
    # 扁平层级路径的 level_cols=[lbl,par] 与多层级路径语义不同，分别构造。
    customdata = []
    if flat_hierarchy:
        # 扁平层级：customdata 存 [自身label, parent]
        for i, nid in enumerate(ids):
            customdata.append([labels[i], parents[i]])
        hovertemplate = f"{lbl_col}=%{{customdata[0]}}<br>{par_col}=%{{customdata[1]}}<br>{values_col}=%{{value}}<extra></extra>"
    else:
        # 多层级路径：从 k=v|k=v 形式的 id 解析各层值
        for node_id in ids:
            parts = node_id.split("|")
            level_values = []
            for part in parts:
                if "=" in part:
                    level_values.append(part.split("=", 1)[1])
            while len(level_values) < len(level_cols):
                level_values.append("")
            customdata.append(level_values)
        hover_parts = []
        for col in level_cols:
            hover_parts.append(f"{col}=%{{customdata[{len(hover_parts)}]}}")
        hover_parts.append(f"{values_col}=%{{value}}")
        hovertemplate = "<br>".join(hover_parts) + "<extra></extra>"

    trace_data = {
        "type": "sunburst",
        "ids": ids,
        "labels": labels,
        "values": values,
        "parents": parents,
        "branchvalues": "total",
        "hole": 0.35,  # 0~1，越大中间越空
        "marker": {"line": {"width": 0}, "colors": colors},
        "customdata": customdata,
        "hovertemplate": hovertemplate
    }

    layout_data = {
        "title": {"text": title, "font": {"size": 16}},
        "font": {"family": "Heiti SC, Microsoft YaHei, sans-serif"},
        "plot_bgcolor": "white",
        "paper_bgcolor": "white",
        "margin": {"l": 0, "r": 0, "t": 40, "b": 0},
        "hovermode": "closest",
        "height": 500
    }

    fig_id = f"sunburst-{uuid.uuid4().hex}"
    fig_json = json.dumps({"data": [trace_data], "layout": layout_data}, ensure_ascii=False)

    chart_html = f"""<div>
    <script>window.PlotlyConfig = {{MathJaxConfig: 'local'}};</script>
    <script charset="utf-8" src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
    <div id="{fig_id}" class="plotly-graph-div" style="height:500px; width:100%;"></div>
    <script>
        (function() {{
            try {{
                var fig = {fig_json};
                Plotly.newPlot('{fig_id}', fig.data, fig.layout, {{
                    responsive: true,
                    displayModeBar: false
                }});
            }} catch (e) {{
                console.error("Sunburst render error:", e);
                var el = document.getElementById('{fig_id}');
                if (el) {{
                    el.innerHTML = '<div style="color:#c00;padding:12px;">图表渲染失败，请打开控制台查看错误。</div>';
                }}
            }}
        }})();
    </script>
</div>"""

    html = _build_html(title, "sunburst", "plotly", _DATA_FMT, _DESC, chart_html)

    meta = {
        "chart_id": "sunburst",
        "n_rows": len(work_df),
        "values_col": values_col,
        "level_cols": level_cols,
        "color_scheme": color_scheme_name,
    }

    return ChartResult(html=html, spec={}, warnings=warnings, meta=meta)