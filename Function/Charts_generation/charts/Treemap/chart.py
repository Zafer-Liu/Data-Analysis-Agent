"""
矩形树图 Treemap - 占比图表
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

_DATA_FMT = "labels列(类别名称) + values列(数值) + 可选parents列(父级)"
_DESC = "用矩形面积表示占比，支持多层级嵌套展示。适合展示有层级且数量较多的分类数据。"


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


def generate(
    df: pd.DataFrame = None,
    mapping: Dict[str, str] = None,
    options: Dict[str, Any] = None,
    excel_path: str = None,
    path: str = "path",
    value: str = "value",
    parents: str = None,
    title: str = "矩形树图",
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

    # 调度器 _normalize_chart_mapping 把 labels→label、values→value 归一化，
    # 而 _auto_detect_mapping 对 treemap 设 labels/parents/values。
    # 同时兼容两套 key + 旧别名 path/value。
    path_col = (mapping.get("label") or mapping.get("labels")
                or mapping.get("path") or path)
    value_col = mapping.get("value")
    value_col = value_col if value_col is not None else (mapping.get("values") if mapping.get("values") is not None else value)
    parents_col = mapping.get("parents")
    parents_col = parents_col if parents_col is not None else (mapping.get("parent") if mapping.get("parent") is not None else parents)
    title = options.get("title", title)

    color_scheme_name = options.get("color_scheme", "mckinsey")
    color_scheme = get_color_scheme(color_scheme_name)
    marker_colors = color_scheme.get("colors", ["#003D7A", "#0084D1", "#00A4EF"])

    # 优先使用 mapping 中的列名，如果没有才自动检测
    if mapping.get("label") or mapping.get("labels"):
        _path = mapping.get("label")
        _path = _path if _path is not None else mapping.get("labels")
    elif mapping.get("path"):
        _path = mapping.get("path")
    else:
        _path = _auto_col(df, "label", "labels", "category", "name", "类别")

    if mapping.get("value") or mapping.get("values"):
        _value = mapping.get("value")
        _value = _value if _value is not None else mapping.get("values")
    elif mapping.get("value"):
        _value = mapping.get("value")
    else:
        _value = _auto_col(df, "value", "values", "销售额", "销量", "amount", "num")

    if mapping.get("parents") or mapping.get("parent"):
        _parents = mapping.get("parents")
        _parents = _parents if _parents is not None else mapping.get("parent")
    else:
        _parents = None

    # 智能检测：如果有多个文本列且未指定 parents，自动推断层级关系
    if _parents is None and not (mapping.get("label") or mapping.get("labels")):
        text_cols = [c for c in df.columns if (df[c].dtype == object or pd.api.types.is_string_dtype(df[c]))]
        if len(text_cols) >= 2:
            # 检查是否存在父子关系（第一列的唯一值数量 < 第二列）
            first_col_unique = df[text_cols[0]].nunique()
            second_col_unique = df[text_cols[1]].nunique()

            if first_col_unique < second_col_unique:
                # 第一列是父级（类别少），第二列是子级（类别多）
                _parents = text_cols[0]
                _path = text_cols[1]
                warnings.append(f"自动检测到层级关系：{_parents}(父级) -> {_path}(子级)")

    if _path is None or _path not in df.columns:
        warnings.append("找不到必填字段 [labels/path]")
        return ChartResult(warnings=warnings)

    if _value is None or _value not in df.columns:
        warnings.append("找不到必填字段 [values/value]")
        return ChartResult(warnings=warnings)

    df = df.copy()
    df = df[df[_path].notna()].copy()

    if df.empty:
        return ChartResult(warnings=["错误：labels/path 列无有效数据"])

    if (_value in df.columns) and (df[_value] <= 0).any():
        warnings.append(f"警告：{_value} 列包含非正数，已过滤")
        df = df[df[_value] > 0].copy()
        if len(df) == 0:
            return ChartResult(warnings=[f"错误：{_value} 列无有效数据（需>0）"])

    df[_value] = pd.to_numeric(df[_value], errors="coerce")
    df = df[df[_value].notna()].copy()
    if df.empty:
        return ChartResult(warnings=[f"错误：{_value} 列无法解析为有效数值"])

    df = df.copy()
    df[_path] = df[_path].astype(str)
    df[_value] = pd.to_numeric(df[_value], errors="coerce")
    df = df[df[_value].notna() & (df[_value] > 0)].copy()

    # 如果有父级列，就构造树（需要手动添加父节点）
    if _parents and _parents in df.columns:
        df[_parents] = df[_parents].fillna("").astype(str)

        # 使用唯一 ids 而非原始 label 字符串，避免不同分支下同名标签
        # （如两个 "Other"）互相混淆或漏节点。plotly 通过 (label,parent)
        # 配对连接节点，若同名 label 出现在不同父级下且未显式给 ids，
        # 会把节点错误合并。这里给每个节点一个 "parent::label" 形式的 id。
        ids = []
        all_labels = []
        all_parents = []
        all_values = []

        seen_ids = set()

        # 先写子节点
        for _, row in df.iterrows():
            lbl = str(row[_path])
            par = str(row[_parents])
            if par == "" or par == "nan":
                nid = lbl
                par_id = ""
            else:
                nid = f"{par}::{lbl}"
                par_id = par
            if nid in seen_ids:
                continue
            seen_ids.add(nid)
            ids.append(nid)
            all_labels.append(lbl)
            all_parents.append(par_id)
            all_values.append(float(row[_value]))

        # 补充父节点：父节点的 id = 其自身 label，其 parent 为 ""（根）。
        # 用确定顺序（按出现顺序去重）而非 set()，保证可复现。
        unique_parents = []
        for p in all_parents:
            if p and p not in unique_parents and p not in seen_ids:
                unique_parents.append(p)
        for parent in unique_parents:
            seen_ids.add(parent)
            ids.append(parent)
            all_labels.append(parent)
            all_parents.append("")
            # 父节点的值 = 其所有子节点之和
            parent_value = df[df[_parents].astype(str) == parent][_value].sum()
            all_values.append(float(parent_value))

        labels = all_labels
        parents_list = all_parents
        values = all_values
    else:
        labels = df[_path].astype(str).tolist()
        parents_list = [""] * len(df)
        values = df[_value].astype(float).tolist()
        ids = labels[:]  # 无父级时 label 唯一性由数据保证，ids 即 labels

    # 长度保护
    if not (len(labels) == len(values) == len(parents_list)):
        return ChartResult(warnings=["错误：labels / values / parents 长度不一致"])

    # 颜色扩展到数据长度
    colors = (marker_colors * (len(labels) // len(marker_colors) + 1))[:len(labels)]

    trace_data = {
        "type": "treemap",
        "ids": ids,
        "labels": labels,
        "values": values,
        "parents": parents_list,
        "branchvalues": "total",
        "marker": {
            "line": {"width": 0},
            "colors": colors
        },
        "hovertemplate": f"{_path}=%{{label}}<br>{_value}=%{{value}}<extra></extra>"
    }

    if _parents and _parents in df.columns:
        trace_data["hovertemplate"] = (
            f"{_path}=%{{label}}<br>{_value}=%{{value}}<br>{_parents}=%{{parent}}<extra></extra>"
        )

    layout_data = {
        "title": {"text": title, "font": {"size": 16}},
        "font": {"family": "Heiti SC, Microsoft YaHei, sans-serif"},
        "plot_bgcolor": "white",
        "paper_bgcolor": "white",
        "margin": {"l": 0, "r": 0, "t": 40, "b": 0},
        "hovermode": "closest",
        "height": 500
    }

    fig_id = f"treemap-{uuid.uuid4().hex}"
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
                console.error("Treemap render error:", e);
                var el = document.getElementById('{fig_id}');
                if (el) {{
                    el.innerHTML = '<div style="color:#c00;padding:12px;">图表渲染失败，请打开控制台查看错误。</div>';
                }}
            }}
        }})();
    </script>
</div>"""

    html = _build_html(title, "treemap", "plotly", _DATA_FMT, _DESC, chart_html)

    meta = {
        "chart_id": "treemap",
        "n_rows": len(df),
        "labels_col": _path,
        "values_col": _value,
        "parents_col": _parents,
        "color_scheme": color_scheme_name,
    }

    return ChartResult(html=html, spec={}, warnings=warnings, meta=meta)