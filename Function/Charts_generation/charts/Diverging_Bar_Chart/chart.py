"""
对比条形图 Diverging Bar - 比较图表
图表分类: 比较 Comparison
感知排名: ★★★★☆

统一接口:
    generate(df, mapping, options) -> ChartResult
"""
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
    logger.debug("[chart] 配色模块加载失败: %s", e)
    def get_color_scheme(name: str = "mckinsey"):
        return {"colors": [
            "#005B9A", "#1E88E5", "#2E7D32", "#43A047",
            "#E74C3C", "#C0392B", "#A93226", "#922B21",
        ]}

__all__ = ["generate"]
import logging
logger = logging.getLogger(__name__)
_DATA_FMT = "单列模式: item列 + value列 | 多列模式: item列 + 负值列(多个) + 正值列(多个)"
_DESC = "以零点为基准左右展开，正值向右（蓝色），负值向左（红色）。支持单列模式和李克特量表多列模式。"

def _sanitize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    清洗列名：
    1) 转字符串
    2) 去首尾空格
    3) 去BOM
    4) 重名列自动改名 col, col__1, col__2...
    """
    d = df.copy()
    raw_cols = list(d.columns)

    cleaned = []
    counter = {}
    for c in raw_cols:
        name = str(c).replace("\ufeff", "").strip()
        if name in counter:
            counter[name] += 1
            name = f"{name}__{counter[name]}"
        else:
            counter[name] = 0
        cleaned.append(name)

    d.columns = cleaned

    if raw_cols != cleaned:
        logger.info(f"[Diverging_Bar_Chart] columns sanitized: {raw_cols} -> {cleaned}")

    return d


def _get_1d_series(df: pd.DataFrame, col: str, warnings: list, role: str = "value") -> pd.Series:
    """
    从 df 安全取一维列：
    - 正常: Series
    - 异常（重名索引/误传）: DataFrame -> 取第一列并告警
    """
    s = df[col]
    if isinstance(s, pd.DataFrame):
        warnings.append(f"{role}列[{col}]命中多列，已自动取第一列")
        logger.warning(f"[Diverging_Bar_Chart] column '{col}' resolved to DataFrame, using first column.")
        s = s.iloc[:, 0]
    return s
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

    # 2. 模糊匹配
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

    if any(kw in hint for kw in ["label", "name", "item", "category", "group", "国家", "地区", "名称", "项目"]):
        return strs[0] if strs else (nums[0] if nums else None)

    if any(kw in hint for kw in [
        "value", "score", "diff", "change",
        "得分", "分数", "净得分", "指数", "esg", "评分", "增减", "变化"
    ]):
        return nums[0] if nums else (strs[0] if strs else None)


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


def _detect_format(df: pd.DataFrame, item_col: str) -> str:
    """检测数据格式：单列 or 多列"""
    # 多列模式：除了 item 列外，还有多个数值列
    num_cols = [c for c in df.columns if c != item_col and pd.api.types.is_numeric_dtype(df[c])]
    
    if len(num_cols) > 1:
        return "multi"
    else:
        return "single"


def generate(

    df: pd.DataFrame = None,
    mapping: Dict[str, str] = None,
    options: Dict[str, Any] = None,
    excel_path: str = None,
    item: str = "item",
    value: str = "value",
    negative_cols: Optional[List[str]] = None,
    positive_cols: Optional[List[str]] = None,
    title: str = "对比条形图",
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
                return ChartResult(warnings=[f"读取Excel失败: {e}"])
        else:
            return ChartResult(warnings=["请提供 df 或 excel_path"])
    df = _sanitize_columns(df)
    logger.info(f"[Diverging_Bar_Chart] incoming columns={df.columns.tolist()}")
    logger.info(f"[Diverging_Bar_Chart] mapping={mapping}, item={item}, value={value}")

    item_col = mapping.get("item")
    item_col = item_col if item_col is not None else item
    value_col = mapping.get("value")
    value_col = value_col if value_col is not None else value
    item_col = mapping.get("item")
    item_col = item_col if item_col is not None else item
    value_col = mapping.get("value")
    value_col = value_col if value_col is not None else value
    title = options.get("title", title)

    _item = _auto_col(
        df,
        item_col,
        "item", "label", "name", "category",
        "国家", "地区", "城市", "名称", "项目", "维度", "公司", "企业", "机构"
    )
    
    if _item is None or _item not in df.columns:
        return ChartResult(warnings=["找不到必填字段 [item]"])

    # 检测数据格式
    fmt = _detect_format(df, _item)

    # 配色方案：优先用 options.color_scheme
    _scheme = get_color_scheme(options.get("color_scheme", "mckinsey"))
    _palette = _scheme.get("colors") or [
        "#005B9A", "#1E88E5", "#2E7D32", "#43A047",
        "#E74C3C", "#C0392B", "#A93226", "#922B21",
    ]

    if fmt == "multi":
        # 多列模式（李克特量表）
        return _generate_multi_column(df, _item, title, warnings, _palette)
    else:
        # 单列模式
        _value = _auto_col(
            df,
            value_col,
            "value", "score", "diff", "change",
            "得分", "分数", "净得分", "ESG", "评分", "增减", "变化"
        )

        if _value is None or _value not in df.columns:
            return ChartResult(warnings=["找不到必填字段 [value]"])

        return _generate_single_column(df, _item, _value, title, warnings, _scheme, _palette)


def _generate_single_column(df: pd.DataFrame, item_col: str, value_col: str, title: str, warnings: list, scheme: dict = None, palette: list = None) -> ChartResult:
    """单列模式：item + value"""
    try:
        d = df[[item_col, value_col]].copy()
    except Exception as e:
        return ChartResult(warnings=warnings + [f"选择列失败: {e}"])

    d = d.dropna(subset=[item_col])

    # 安全取一维
    val = _get_1d_series(d, value_col, warnings, role="value")
    d[value_col] = pd.to_numeric(val, errors="coerce")
    d = d[d[value_col].notna()]

    # item列转字符串，避免类别异常
    d[item_col] = d[item_col].astype(str).str.strip()
    d = d[d[item_col] != ""]

    if len(d) == 0:
        return ChartResult(warnings=warnings + ["处理后无有效数据"])

    # 按绝对值排序
    d = d.sort_values(value_col, key=lambda s: s.abs())

    fig = go.Figure()

    positive = d[d[value_col] >= 0]
    negative = d[d[value_col] < 0]

    _pos_color = (scheme or {}).get("primary", "#005B9A") if scheme else "#005B9A"
    _neg_color = (scheme or {}).get("negative", "#E74C3C") if scheme else "#E74C3C"

    if len(positive) > 0:
        fig.add_trace(go.Bar(
            y=positive[item_col],
            x=positive[value_col],
            orientation='h',
            marker=dict(color=_pos_color),
            name='正值',
            hovertemplate='<b>%{y}</b><br>%{x:.2f}<extra></extra>'
        ))

    if len(negative) > 0:
        fig.add_trace(go.Bar(
            y=negative[item_col],
            x=negative[value_col],
            orientation='h',
            marker=dict(color=_neg_color),
            name='负值',
            hovertemplate='<b>%{y}</b><br>%{x:.2f}<extra></extra>'
        ))

    fig.update_layout(
        title=title,
        xaxis_title='数值',
        yaxis_title='项目',
        barmode='relative',
        plot_bgcolor='white',
        paper_bgcolor='white',
        font_family="Heiti SC, Microsoft YaHei, sans-serif",
        margin=dict(l=120, r=40, t=60, b=40),
        height=max(400, len(d) * 30),
        showlegend=True,
        hovermode='closest',
        yaxis=dict(
            tickfont=dict(size=11),
            showticklabels=True
        )
    )

    fig.add_vline(x=0, line_dash="dash", line_color="gray", opacity=0.5)

    chart_html = pio.to_html(fig, full_html=False, include_plotlyjs=False)
    html = _build_html(title, "diverging_bar", "plotly", _DATA_FMT, "单列模式", chart_html)

    meta = {
        "chart_id": "diverging_bar",
        "format": "single",
        "n_rows": len(d),
        "item_col": item_col,
        "value_col": value_col,
        "positive_count": len(positive),
        "negative_count": len(negative),
    }

    return ChartResult(html=html, spec={}, warnings=warnings, meta=meta)


def _generate_multi_column(df: pd.DataFrame, item_col: str, title: str, warnings: list, palette: list = None) -> ChartResult:
    """多列模式：李克特量表（堆叠正负值）"""
    
    d = df.copy()

    # 获取所有数值列（除了 item 列）
    num_cols = [c for c in d.columns if c != item_col and pd.api.types.is_numeric_dtype(d[c])]

    if len(num_cols) < 2:
        return ChartResult(warnings=["多列模式需要至少2个数值列"])

    # 转换为数值
    for col in num_cols:
        s = _get_1d_series(d, col, warnings, role="multi-value")
        d[col] = pd.to_numeric(s, errors="coerce")

    # 仅丢弃数值列含 NaN 的行（原 d.dropna() 会因任意无关列的 NaN 误删完整行）
    before = len(d)
    d = d.dropna(subset=num_cols)
    if before != len(d):
        warnings.append(f"已忽略 {before - len(d)} 行含缺失数值的数据")

    # 分离负值列和正值列
    negative_cols = [c for c in num_cols if (d[c] < 0).any()]
    positive_cols = [c for c in num_cols if (d[c] > 0).any()]

    if not negative_cols or not positive_cols:
        return ChartResult(warnings=["需要同时包含负值列和正值列"])

    # 计算累积值
    d['negative_sum'] = d[negative_cols].sum(axis=1)
    d['positive_sum'] = d[positive_cols].sum(axis=1)

    # 按正值排序
    d = d.sort_values('positive_sum', ascending=True)

    # 创建 Plotly 图表
    fig = go.Figure()

    # 配色
    _palette = palette or []
    neg_colors = ['#E74C3C', '#C0392B', '#A93226', '#922B21']
    pos_colors = ['#005B9A', '#1E88E5', '#2E7D32', '#43A047']
    # 若有 scheme palette，从中派生正/负色
    if _palette:
        pos_colors = [_palette[i % len(_palette)] for i in range(max(1, len(positive_cols)))]
        neg_colors = [_palette[(len(_palette) // 2 + i) % len(_palette)] for i in range(max(1, len(negative_cols)))]

    # 添加负值列（从右到左堆叠）
    for idx, col in enumerate(sorted(negative_cols, reverse=True)):
        fig.add_trace(go.Bar(
            y=d[item_col],
            x=d[col],
            orientation='h',
            marker=dict(color=neg_colors[idx % len(neg_colors)]),
            name=col,
            text=d[col].round(0).astype(str),
            textposition='inside',
            textfont=dict(color='white', size=10),
            hovertemplate='<b>%{y}</b><br>' + col + ': %{x:.0f}<extra></extra>'
        ))

    # 添加正值列（从左到右堆叠）
    for idx, col in enumerate(sorted(positive_cols)):
        fig.add_trace(go.Bar(
            y=d[item_col],
            x=d[col],
            orientation='h',
            marker=dict(color=pos_colors[idx % len(pos_colors)]),
            name=col,
            text=d[col].round(0).astype(str),
            textposition='inside',
            textfont=dict(color='white', size=10),
            hovertemplate='<b>%{y}</b><br>' + col + ': %{x:.0f}<extra></extra>'
        ))

    fig.update_layout(
        title=title,
        xaxis_title='数值',
        yaxis_title='项目',
        barmode='relative',
        plot_bgcolor='white',
        paper_bgcolor='white',
        font_family="Heiti SC, Microsoft YaHei, sans-serif",
        margin=dict(l=120, r=40, t=60, b=40),
        height=max(400, len(d) * 30),
        showlegend=True,
        hovermode='closest',
        yaxis=dict(
            tickfont=dict(size=11),
            showticklabels=True
        )
    )

    # 添加零线
    fig.add_vline(x=0, line_dash="dash", line_color="gray", opacity=0.5)

    chart_html = pio.to_html(fig, full_html=False, include_plotlyjs=False)
    html = _build_html(title, "diverging_bar", "plotly", _DATA_FMT, "多列模式（李克特量表）", chart_html)


    meta = {
        "chart_id": "diverging_bar",
        "format": "multi",
        "n_rows": len(d),
        "item_col": item_col,
        "negative_cols": negative_cols,
        "positive_cols": positive_cols,
    }

    return ChartResult(html=html, spec={}, warnings=warnings, meta=meta)
