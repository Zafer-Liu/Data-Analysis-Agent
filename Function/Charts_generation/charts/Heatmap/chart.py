"""
热力图 Heatmap - 矩阵图表
图表分类: 矩阵 Matrix | 书章节: Ch8
感知排名: ★★★★☆

统一接口:
    generate(df, mapping, options) -> ChartResult
"""
import logging
log = logging.getLogger(__name__)
import sys
from pathlib import Path
from typing import Dict, Any, Optional

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
            "#F7FBFF", "#DEEBF7", "#C6DBEF", "#9ECAE1",
            "#6BAED6", "#4292C6", "#2171B5", "#08519C",
            "#08306B",
        ]}

__all__ = ["generate"]

_DATA_FMT = "row列 + col列 + value列（三列长格式）或 宽格式(行标签 + 多个数值列)"
_DESC = "用颜色深浅表示数值大小，适合展示矩阵数据和相关性。支持宽格式数据自动转换。"


def _auto_col(df: pd.DataFrame, *hints: str) -> Optional[str]:
    """根据提示自动查找匹配的列名。
    
    策略：
    1. 精确匹配 hints 中的任何一个
    2. 模糊匹配（包含关系）
    3. 类型匹配：根据 hint 的语义推断类型
    4. 自动推断：无 hints 时返回第一个合适的列
    """
    strs = _label_columns(df)
    nums = _value_columns(df)
    col_lower = {str(c).lower(): c for c in df.columns}
    
    # 1. 精确匹配 hints
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
    
    # 3. 类型匹配：根据 hint 的语义推断应该是什么类型
    if hints:
        hint = str(hints[0]).lower()
        # 字符串类型的 hints
        if any(kw in hint for kw in ["source", "target", "label", "name", "group", "category", "phase", "row", "col", "path", "text", "word", "location", "geo"]):
            if strs:
                return strs[0]
            if nums:
                return nums[0]
        # 数值类型的 hints
        elif any(kw in hint for kw in ["value", "size", "amount", "count", "frequency", "score", "rank", "actual", "target", "range"]):
            if nums:
                return nums[0]
            if strs:
                return strs[0]
        # 通用的 x/y：x 通常是类别（字符串），y 通常是数值
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
    
    # 4. 无 hints 时自动推断
    if not hints:
        if strs:
            return strs[0]
        if nums:
            return nums[0]
    
    return None


def _label_columns(df: pd.DataFrame) -> list[str]:
    """Return columns that are naturally suited to categorical heatmap axes."""
    return [
        c for c in df.columns
        if (
            pd.api.types.is_object_dtype(df[c])
            or pd.api.types.is_string_dtype(df[c])
            or isinstance(df[c].dtype, pd.CategoricalDtype)
            or pd.api.types.is_datetime64_any_dtype(df[c])
            or pd.api.types.is_bool_dtype(df[c])
        )
    ]


def _value_columns(df: pd.DataFrame) -> list[str]:
    """Return numeric value columns, excluding booleans used as dimensions."""
    return [
        c for c in df.columns
        if pd.api.types.is_numeric_dtype(df[c]) and not pd.api.types.is_bool_dtype(df[c])
    ]


def _has_long_mapping(mapping: Dict[str, Any]) -> bool:
    """Whether the caller supplied all three long-format roles explicitly."""
    return bool(
        (mapping.get("row") or mapping.get("y"))
        and (mapping.get("col") or mapping.get("x"))
        and (mapping.get("value") or mapping.get("z"))
    )


def _detect_wide_format(
    df: pd.DataFrame,
    mapping: Dict[str, Any],
    options: Dict[str, Any],
) -> bool:
    """Detect a matrix table without overriding an explicit long mapping.

    A heatmap of a correlation matrix is often all numeric, so requiring an
    object column here used to send it into the long-format pivot path and
    corrupt the matrix.  Ambiguous three-column numeric data is treated as
    long only when it is non-square; callers can always resolve ambiguity with
    ``options.data_format`` or an explicit x/y/value mapping.
    """
    format_hint = str(options.get("data_format") or options.get("format") or "").lower()
    if format_hint in {"wide", "matrix"}:
        return True
    if format_hint in {"long", "tidy"} or _has_long_mapping(mapping):
        return False

    labels = _label_columns(df)
    nums = _value_columns(df)
    if len(labels) == 1 and nums:
        return True
    if not labels and len(nums) >= 2:
        return not (len(nums) == 3 and len(df) != len(nums))
    return False


def _convert_wide_to_matrix(df: pd.DataFrame, id_col: Optional[str]) -> tuple:
    """将宽格式转换为矩阵格式
    
    输入：
        行标签  15    16    17    18    19
        标签二  36.39 31.97 29.03 28.11 25.46
        标签三  49.72 44.48 35.79 35.32 30.22
    
    输出：
        z: 矩阵数据
        y: 行标签列表
        x: 列标签列表
    """
    # 获取所有数值列（按原始顺序）。没有单独标签列的相关矩阵以
    # DataFrame 索引作为行标签，至少保留原始矩阵本身而不做错误透视。
    value_cols = [c for c in _value_columns(df) if c != id_col]
    if id_col is None:
        df_matrix = df[value_cols]
        y_labels = [str(index) for index in df_matrix.index]
    else:
        df_matrix = df.set_index(id_col)[value_cols]
        y_labels = [str(index) for index in df_matrix.index]

    return df_matrix.values, y_labels, [str(col) for col in df_matrix.columns]


def _build_html(title: str, chart_name: str, library: str,
                data_fmt: str, desc: str, embed: str) -> str:
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


def generate(
    df: pd.DataFrame = None,
    mapping: Dict[str, str] = None,
    options: Dict[str, Any] = None,
    # ── 向后兼容旧接口 ──────────────────────────────
    excel_path: str = None,
    row: str = "row",
    col: str = "col",
    value: str = "value",
    title: str = "热力图",
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

    # ``generate`` is also used directly in tests and legacy call sites. Keep
    # its column handling consistent with the dispatcher, which stringifies
    # Excel headers before chart selection.
    df = df.copy()
    df.columns = df.columns.map(str)
    mapping = {
        str(key): (
            value if isinstance(value, (list, tuple, dict))
            else (str(value) if value is not None else value)
        )
        for key, value in mapping.items()
    }

    # Support both (row/col/value) and (x/y/z) mapping keys.
    # y → row axis (True Label), x → col axis (Predicted Label), z → value.
    row_col = mapping.get("row")
    row_col = row_col if row_col is not None else (mapping.get("y") if mapping.get("y") is not None else row)
    col_col = mapping.get("col")
    col_col = col_col if col_col is not None else (mapping.get("x") if mapping.get("x") is not None else col)
    val_col = mapping.get("value")
    val_col = val_col if val_col is not None else (mapping.get("z") if mapping.get("z") is not None else value)
    title = options.get("title", title)

    # 配色方案：优先用 options.color_scheme，作为 plotly colorscale
    _scheme = get_color_scheme(options.get("color_scheme", "mckinsey"))
    _palette = _scheme.get("colors") or [
        "#F7FBFF", "#DEEBF7", "#C6DBEF", "#9ECAE1",
        "#6BAED6", "#4292C6", "#2171B5", "#08519C",
        "#08306B",
    ]

    # ── 检测并转换宽格式数据 ──────────────────────────────
    if _detect_wide_format(df, mapping, options):
        # 优先使用调用方指定的行字段；纯数值矩阵没有单独的行标签列。
        configured_row = mapping.get("row")
        configured_row = configured_row if configured_row is not None else mapping.get("y")
        labels = _label_columns(df)
        id_col = (
            configured_row if configured_row in labels
            else (labels[0] if len(labels) == 1 else None)
        )
        z_data, y_labels, x_labels = _convert_wide_to_matrix(df, id_col)

        if not x_labels:
            return ChartResult(warnings=["宽格式热力图没有可用的数值列"])

        y_label = options.get("y_title") or (id_col or "行")
        x_label = options.get("x_title") or "列"
        warnings.append(
            f"自动转换宽格式数据为矩阵：{y_label} (行) × {x_label} (列)"
        )
        
        # 创建热力图
        fig = go.Figure(data=go.Heatmap(
            z=z_data,
            y=y_labels,
            x=x_labels,
            colorscale=_palette,
            text=z_data.tolist(),
            texttemplate="%{text:.2f}",
            textfont={"size": 10},
            hovertemplate=f"{y_label}: %{{y}}<br>{x_label}: %{{x}}<br>值: %{{z:.3g}}<extra></extra>"
        ))
        
        fig.update_layout(
            title=dict(text=title, x=0.02, xanchor="left", font=dict(size=18, color="#1F1F1F")),
            font=dict(family="Arial, Helvetica, sans-serif", size=12, color="#1F1F1F"),
            xaxis=dict(title=x_label, side="bottom"),
            yaxis=dict(title=y_label, autorange="reversed"),
            margin=dict(l=80, r=40, t=70, b=70),
            height=500,
            paper_bgcolor="white",
            plot_bgcolor="white",
        )
    else:
        # 长格式数据：正常处理
        explicit_row = mapping.get("row")
        explicit_row = explicit_row if explicit_row is not None else mapping.get("y")
        explicit_col = mapping.get("col")
        explicit_col = explicit_col if explicit_col is not None else mapping.get("x")
        explicit_val = mapping.get("value")
        explicit_val = explicit_val if explicit_val is not None else mapping.get("z")
        _row = explicit_row or _auto_col(
            df, row_col, "row", "产品", "类别", "y", "行标签"
        )
        remaining_for_col = df.drop(columns=[_row], errors="ignore")
        _col = explicit_col or _auto_col(
            remaining_for_col, col_col, "col", "区域", "x", "周期"
        )
        remaining_for_val = df.drop(columns=[_row, _col], errors="ignore")
        _val = explicit_val or _auto_col(
            remaining_for_val, val_col, "value", "销售额", "销量", "amount", "num", "数值"
        )

        for role, col_ in [("row", _row), ("col", _col), ("value", _val)]:
            if col_ is None or col_ not in df.columns:
                warnings.append(f"找不到必填字段 [{role}]")

        if _row not in df.columns or _col not in df.columns or _val not in df.columns:
            return ChartResult(warnings=warnings)

        if len({_row, _col, _val}) != 3:
            return ChartResult(warnings=[
                "热力图的行、列和值字段必须是三个不同的列"
            ])

        matrix_df = df[[_row, _col, _val]].copy()
        matrix_df[_val] = pd.to_numeric(matrix_df[_val], errors="coerce")
        invalid_values = int(
            df[_val].notna().sum() - matrix_df[_val].notna().sum()
        )
        if invalid_values:
            warnings.append(f"已忽略 {invalid_values} 个无法转换为数值的值")
        matrix_df = matrix_df.dropna(subset=[_val])
        if matrix_df.empty:
            return ChartResult(warnings=warnings + ["value 字段不含可用数值"])

        duplicate_rows = int(matrix_df.duplicated([_row, _col], keep=False).sum())
        aggfunc = str(options.get("aggfunc") or "sum").lower()
        valid_aggfuncs = {"sum", "mean", "median", "min", "max", "first", "last", "count"}
        if aggfunc not in valid_aggfuncs:
            return ChartResult(warnings=[
                "options.aggfunc 必须是 sum、mean、median、min、max、first、last 或 count"
            ])
        if duplicate_rows:
            warnings.append(
                f"检测到 {duplicate_rows} 条重复的行/列组合，已按 {aggfunc} 聚合；"
                "可通过 options.aggfunc 指定聚合方式"
            )

        # 转换为透视表（矩阵）。不填充缺失组合，以免把“未观测”伪装成零值。
        pivot_df = matrix_df.pivot_table(
            values=_val, index=_row, columns=_col,
            aggfunc=aggfunc, observed=True, dropna=False
        )
        if pivot_df.empty:
            return ChartResult(warnings=warnings + ["无法从输入生成热力图矩阵"])
        pivot_df.columns = [str(c) for c in pivot_df.columns]
        pivot_df.index   = [str(i) for i in pivot_df.index]

        # 坐标轴标签：直接用实际列名
        x_label = _col
        y_label = _row

        z_vals = pivot_df.values.tolist()
        y_vals = pivot_df.index.tolist()
        x_vals = pivot_df.columns.tolist()

        # 数值显示格式：整数用 %{text:.0f}，小数用 %{text:.2f}，自动判断。
        # 大矩阵仅保留 hover，避免标签覆盖色块。
        _flat = [v for row_ in z_vals for v in row_ if pd.notna(v)]
        _all_int = all(float(v) == int(v) for v in _flat) if _flat else True
        _texttemplate = "%{text:.0f}" if _all_int else "%{text:.2f}"
        show_text = len(y_vals) * len(x_vals) <= 225
        if not show_text:
            warnings.append("矩阵超过 225 个单元格，已隐藏数值标签；请通过悬停查看数值")

        heatmap_kwargs = dict(
            z=z_vals,
            y=y_vals,
            x=x_vals,
            colorscale=_palette,
            textfont={"size": 11},
            hovertemplate=f"{y_label}: %{{y}}<br>{x_label}: %{{x}}<br>值: %{{z:.3g}}<extra></extra>"
        )
        if show_text:
            heatmap_kwargs.update(text=z_vals, texttemplate=_texttemplate)
        fig = go.Figure(data=go.Heatmap(**heatmap_kwargs))

        fig.update_layout(
            title=dict(text=title, x=0.02, xanchor="left", font=dict(size=18, color="#1F1F1F")),
            font=dict(family="Arial, Helvetica, sans-serif", size=12, color="#1F1F1F"),
            xaxis=dict(
                title=x_label,
                side="bottom",
            ),
            yaxis=dict(
                title=y_label,
                autorange="reversed",
            ),
            margin=dict(l=80, r=40, t=70, b=70),
            height=500,
            paper_bgcolor="white",
            plot_bgcolor="white",
        )

    chart_html = pio.to_html(fig, full_html=False, include_plotlyjs=False)
    html = _build_html(title, "heatmap", "plotly", _DATA_FMT, _DESC, chart_html)


    meta = {
        "chart_id": "heatmap",
        "n_rows": len(df),
    }

    return ChartResult(html=html, spec={}, warnings=warnings, meta=meta)
