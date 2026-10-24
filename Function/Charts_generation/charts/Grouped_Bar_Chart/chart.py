"""
分组柱状图 Grouped Bar - 比较图表
图表分类: 比较 Comparison
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

import pandas as pd
import plotly.express as px
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
            "#0B1F3A", "#005B9A", "#00A3E0", "#6E7B8B", "#B9C2CF",
            "#7D8998", "#4A90E2", "#2E3A46",
        ]}

__all__ = ["generate"]

_DATA_FMT = "x列(类别) + 分组列 + y列(数值) 或 宽格式(行标签 + 多个数值列)"
_DESC = "同一类别的多个分组并排显示，适合直接对比各组差异。支持宽格式数据自动转换。横坐标按行标签分组，每组内按周期并排显示。"

# 配色：蓝系 + 中性色 + 强调色
_MCKINSEY_COLORS = [
    "#0B1F3A",  # 深海军蓝
    "#005B9A",  # 麦肯锡蓝
    "#00A3E0",  # 亮蓝
    "#6E7B8B",  # 蓝灰
    "#B9C2CF",  # 浅灰蓝
    "#7D8998",  # 中性灰蓝
    "#4A90E2",  # 强调蓝
    "#2E3A46",  # 深灰
]

def _auto_col(df: pd.DataFrame, *hints: str) -> Optional[str]:
    """根据提示自动查找匹配的列名。
    
    策略：
    1. 精确匹配 hints 中的任何一个
    2. 模糊匹配（包含关系）
    3. 类型匹配：根据 hint 的语义推断类型
    4. 自动推断：无 hints 时返回第一个合适的列
    """
    strs = [c for c in df.columns if (df[c].dtype == object or pd.api.types.is_string_dtype(df[c]))]
    nums = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]
    col_lower = {str(c).lower(): c for c in df.columns}
    hints = tuple(h for h in hints if isinstance(h, str) and h)
    
    # 1. 精确匹配 hints
    for h in hints:
        h_lower = str(h).lower()
        if h_lower in col_lower:
            return col_lower[h_lower]
    
    # 2. 模糊匹配（包含关系）
    for h in hints:
        h_lower = str(h).lower()
        # Single-character role columns such as x/y must only match exactly.
        # Otherwise column "y" incorrectly matches words like "category".
        if len(h_lower) < 2:
            continue
        for col in df.columns:
            col_lower_name = str(col).lower()
            if len(col_lower_name) >= 2 and (
                h_lower in col_lower_name or col_lower_name in h_lower
            ):
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


def _detect_wide_format(df: pd.DataFrame, mapping: dict) -> bool:
    """判断是否需要将宽格式数据 melt 为长格式。

    走宽格式路径的条件（全部满足）：
    1. 只有 1 个字符串列（作为行标签 / x 轴）
    2. 有 2 个以上数值列
    3. df 中不存在可用的 color/series 列（mapping 里写的列名必须在 df 中实际存在）
    4. mapping 里明确指定的 y 列不存在于 df（若 y 列存在，说明 Agent 想画单指标柱状图）

    额外支持：mapping 里可传 value_cols 列表，显式指定要 melt 的列，此时直接走宽格式。
    """
    # 显式指定了要比较的列 → 直接走宽格式
    if mapping.get("value_cols"):
        return True

    strs = [c for c in df.columns if (df[c].dtype == object or pd.api.types.is_string_dtype(df[c]))]
    nums = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]

    # 存在真实的 color/series 列 → 已经是长格式
    color_hint = mapping.get("color")
    color_hint = color_hint if color_hint is not None else (mapping.get("series") if mapping.get("series") is not None else (mapping.get("group") if mapping.get("group") is not None else ""))
    if isinstance(color_hint, str) and color_hint in df.columns:
        return False

    # y 列真实存在 → Agent 指定了单一指标，不需要 melt
    y_hint = mapping.get("y")
    y_hint = y_hint if y_hint is not None else ""
    if isinstance(y_hint, str) and y_hint in df.columns:
        return False

    # 2个以上字符串列 → 长格式（其中一列可做分组）
    if len(strs) >= 2:
        return False

    return len(strs) == 1 and len(nums) >= 2


def _convert_wide_to_long(df: pd.DataFrame, id_col: str, value_cols: list = None,
                          extra_id_cols: list = None):
    """将宽格式转换为长格式。

    Parameters
    ----------
    df        : 原始宽格式 DataFrame
    id_col    : 行标签列（作为 x 轴）
    value_cols: 要 melt 的列名列表；为 None 时取所有数值列（排除 id_col）

    Returns
    -------
    (df_long, val_name)
    """
    if value_cols is None:
        value_cols = [c for c in df.columns
                      if c != id_col and pd.api.types.is_numeric_dtype(df[c])]

    # 动态选取不与现有列名冲突的 value_name
    existing = set(df.columns)
    val_name = "_value_"
    for candidate in ("数值", "value", "_value_", "_val_", "__val__"):
        if candidate not in existing:
            val_name = candidate
            break

    id_vars = [id_col] + [c for c in (extra_id_cols or []) if c != id_col]
    df_long = df.melt(
        id_vars=id_vars,
        value_vars=value_cols,
        var_name="指标",
        value_name=val_name,
    )
    return df_long, val_name


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
    x: str = "x",
    y: str = "y",
    color: str = "color",
    title: str = "分组柱状图",
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

    x_col = mapping.get("x")
    x_col = x_col if x_col is not None else x
    y_col = mapping.get("y")
    y_col = y_col if y_col is not None else y
    # Accept "series" as alias for "color" (Agent commonly uses "series" for grouped bar)
    color_col = mapping.get("color")
    color_col = color_col if color_col is not None else (mapping.get("series") if mapping.get("series") is not None else color)
    title = options.get("title", title)

    # ── 检测并转换宽格式数据 ──────────────────────────────
    if _detect_wide_format(df, mapping):
        # 找到字符串列作为 id_col（x 轴行标签）
        x_hint = mapping.get("x")
        x_hint = x_hint if x_hint is not None else x_col
        strs = [c for c in df.columns if (df[c].dtype == object or pd.api.types.is_string_dtype(df[c]))]
        if x_hint and x_hint in df.columns:
            id_col = x_hint
        else:
            id_col = strs[0] if strs else df.columns[0]

        # mapping 里可显式指定 value_cols 来控制 melt 哪些列
        # 未指定时取全部数值列（排除 id_col）
        explicit_cols = mapping.get("value_cols")
        if not explicit_cols and isinstance(mapping.get("y"), list):
            explicit_cols = mapping["y"]
        if explicit_cols:
            value_cols = [c for c in explicit_cols if c in df.columns]
        else:
            value_cols = None   # _convert_wide_to_long 内部自动取全部数值列

        color_hint = mapping.get("color")
        color_hint = color_hint if color_hint is not None else (mapping.get("series") if mapping.get("series") is not None else mapping.get("group"))
        source_color = color_hint if isinstance(color_hint, str) and color_hint in df.columns else None
        df, val_name = _convert_wide_to_long(
            df, id_col, value_cols, [source_color] if source_color else None
        )

        _x = id_col
        _y = val_name
        if source_color and len(value_cols or []) > 1:
            _color = "分组 · 指标"
            df[_color] = df[source_color].astype(str) + " · " + df["指标"].astype(str)
        elif source_color:
            _color = source_color
        else:
            _color = "指标"   # melt 后的分组列名

        warnings.append(f"自动转换宽格式数据：{id_col} (x) × 指标 (color) × {val_name} (y)")
    else:
        # 长格式数据：正常处理
        _x = _auto_col(df, x_col, "x", "季度", "时间", "类别", "行标签")
        _y = _auto_col(df, y_col, "y", "销售额", "销量", "value", "amount", "数值")
        if isinstance(color_col, str) and color_col in df.columns:
            _color = color_col
        elif "series" in df.columns:
            # SQL commonly aliases the grouping field as `series` while the
            # mapping still contains its source name (for example `mode`).
            _color = "series"
        elif "color" in df.columns:
            _color = "color"
        else:
            _color = _auto_col(
                df, color_col, "color", "产品", "区域", "group", "category", "周期"
            )

        # ── 回退：y 列存在但无 color 列，且还有其他数值列 → 多指标宽格式 ──────
        # 场景：Agent 传了 x='银行类型', y='ESG总分', series='指标'（不存在），
        # 数据实际是宽格式（还有环境/社会/治理等列）。
        # 此时把 y 列和其余同量纲数值列一起 melt，自动做分组柱图。
        if _x and _x in df.columns and (_color is None or _color not in df.columns):
            other_nums = [c for c in df.columns
                          if c != _x and pd.api.types.is_numeric_dtype(df[c])]
            if len(other_nums) >= 2:
                # 将所有数值列（含 _y）一起 melt
                df, val_name = _convert_wide_to_long(df, _x, other_nums)
                _y = val_name
                _color = "指标"
                warnings.append(f"检测到多数值列，自动转换为分组柱图：{_x} × 指标 × {val_name}")

    for role, col_ in [("x", _x), ("y", _y)]:
        if col_ is None or col_ not in df.columns:
            warnings.append(f"找不到必填字段 [{role}]")

    if _x is None or _x not in df.columns:
        warnings.append("找不到必填字段 [x]")
        return ChartResult(warnings=warnings)
    if _y is None or _y not in df.columns:
        warnings.append("找不到必填字段 [y]")
        return ChartResult(warnings=warnings)

    if _color and _color not in df.columns:
        _color = None

    # 自动判断 y 是否为比例小数（大多数值在 0~1 之间）
    is_ratio = False
    try:
        s = pd.to_numeric(df[_y], errors="coerce").dropna()
        if len(s) > 0:
            ratio_share = ((s >= 0) & (s <= 1)).mean()
            # 超过80%数据在[0,1]，认为是比例
            is_ratio = ratio_share >= 0.8
    except Exception as e:
        log.warning("[chart] 图表生成异常: %s", e)

    # 配色方案：优先用 options.color_scheme，回退到 _MCKINSEY_COLORS
    _scheme = get_color_scheme(options.get("color_scheme", "mckinsey"))
    _palette = _scheme.get("colors") or _MCKINSEY_COLORS

    fig = px.bar(
    df,
    x=_x,
    y=_y,
    color=_color,
    title=title,
    barmode="group",
    text_auto=".2%" if is_ratio else ".2f",   # 关键
    color_discrete_sequence=_palette,
    **kwargs
)

    # 根据分组数量自动计算合理的 bargap / bargroupgap，
    # 让柱子不过细：类别数越多越适当收窄，但始终保持足够宽度。
    n_categories = df[_x].nunique() if _x else 1
    n_groups = df[_color].nunique() if (_color and _color in df.columns) else 1
    # bargap: 类别间距；bargroupgap: 同类别内各组间距
    bargap = max(0.10, min(0.35, 0.15 + n_categories * 0.005))
    bargroupgap = max(0.02, min(0.10, 0.04 + n_groups * 0.005))

    fig.update_layout(
        font_family="Arial, Helvetica, sans-serif",
        plot_bgcolor="white",
        paper_bgcolor="white",
        margin=dict(l=40, r=40, t=60, b=40),
        xaxis_title=_x,
        yaxis_title=_y,
        legend_title=_color if _color else "",
        bargap=bargap,
        bargroupgap=bargroupgap,
    )
    fig.update_xaxes(showgrid=False, linecolor="#D9D9D9")
    fig.update_yaxes(showgrid=True, gridcolor="#E6E9EF", zeroline=False)
    # 加百分比格式控制
    if is_ratio:
        fig.update_yaxes(tickformat=".2%")  # 轴显示 0%, 10%, ...
    else:
        fig.update_yaxes(tickformat=None)

    chart_html = pio.to_html(fig, full_html=False, include_plotlyjs=False)
    html = _build_html(title, "grouped_bar", "plotly", _DATA_FMT, _DESC, chart_html)


    meta = {
        "chart_id": "grouped_bar",
        "n_rows": len(df),
        "x_col": _x,
        "y_col": _y,
        "color_col": _color,
    }

    return ChartResult(html=html, spec={}, warnings=warnings, meta=meta)
