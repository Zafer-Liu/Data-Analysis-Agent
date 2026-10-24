"""
地平线图 Horizon Chart - 趋势（标准平移折叠版）
图表分类: 趋势 Trend
感知排名: ★★★☆☆

统一接口:
    generate(df, mapping, options) -> ChartResult
"""
import logging
log = logging.getLogger(__name__)
import sys
from pathlib import Path
from typing import Dict, Any, Optional, List

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
            "#003D7A", "#0084D1", "#00A4EF", "#7FBA00", "#FFB81C",
            "#F7630C", "#DA3B01", "#A4373A", "#6B2C91", "#00B4EF",
        ]}

__all__ = ["generate"]

_DATA_FMT = "时间列 + 一个或多个数值列"
_DESC = "将时间序列分层着色并进行平移折叠（standard horizon），通过颜色深浅表示幅度层级，适合紧凑展示多指标长期趋势和异常识别。"

MCKINSEY_COLORS = [
    "#003D7A", "#0084D1", "#00A4EF", "#7FBA00", "#FFB81C",
    "#F7630C", "#DA3B01", "#A4373A", "#6B2C91", "#00B4EF",
]


def _auto_col(df: pd.DataFrame, *hints: str) -> Optional[str]:
    strs = [c for c in df.columns if (df[c].dtype == object or pd.api.types.is_string_dtype(df[c]))]
    nums = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]
    col_lower = {str(c).lower(): c for c in df.columns}

    for h in hints:
        h_lower = str(h).lower()
        if h_lower in col_lower:
            return col_lower[h_lower]

    for h in hints:
        h_lower = str(h).lower()
        for col in df.columns:
            if h_lower in str(col).lower() or str(col).lower() in h_lower:
                return col

    if hints:
        hint = str(hints[0]).lower()
        if any(kw in hint for kw in ["source", "target", "label", "name", "group", "category", "phase", "row", "col", "path", "text", "word", "location", "geo"]):
            return strs[0] if strs else (nums[0] if nums else None)
        elif any(kw in hint for kw in ["value", "size", "amount", "count", "frequency", "score", "rank", "actual", "target", "range"]):
            return nums[0] if nums else (strs[0] if strs else None)
        elif hint == "x":
            return strs[0] if strs else (nums[0] if nums else None)
        elif hint == "y":
            return nums[0] if nums else (strs[0] if strs else None)

    return strs[0] if strs else (nums[0] if nums else None)


def _get_numeric_cols(df: pd.DataFrame, exclude: set = None) -> List[str]:
    exclude = exclude or set()
    return [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c]) and c not in exclude]


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


def _detect_wide_format(df: pd.DataFrame) -> tuple:
    if df is None or len(df.columns) < 3:
        return False, None, []

    first_col = df.columns[0]
    rest_cols = list(df.columns[1:])
    first_is_dimension = not pd.api.types.is_numeric_dtype(df[first_col])

    numeric_like_cols = []
    for c in rest_cols:
        s = pd.to_numeric(df[c], errors="coerce")
        ok = s.notna().sum() >= max(1, int(df[c].notna().sum() * 0.5))
        if ok:
            numeric_like_cols.append(c)

    if first_is_dimension and len(numeric_like_cols) >= 2:
        return True, first_col, numeric_like_cols
    return False, None, []


def generate(
    df: pd.DataFrame = None,
    mapping: Dict[str, str] = None,
    options: Dict[str, Any] = None,
    excel_path: str = None,
    x: str = "x",
    y: str = "y",
    title: str = "地平线图（标准平移折叠）",
    bands: int = 3,
    **kwargs
) -> ChartResult:
    from plotly.subplots import make_subplots

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

    is_wide, id_col, value_cols = _detect_wide_format(df)

    if is_wide:
        # 宽表契约：首列是时间/维度（x 轴），其余数值列各成一个系列。
        # melt 后 var_name 是原数值列名 → 系列；首列保留 → x 轴。
        df = df.melt(
            id_vars=[id_col],
            value_vars=value_cols,
            var_name="_series",
            value_name="value"
        )

        df["value"] = pd.to_numeric(df["value"], errors="coerce")
        df = df.dropna(subset=["value"]).sort_values(["_series", id_col])

        x_col = id_col
        y_cols = ["value"]
        series_col = "_series"
        warnings.append("检测到宽格式数据，已自动转换")
    else:
        x_col = mapping.get("x")
        x_col = x_col if x_col is not None else x
        y_cols = mapping.get("y")
        y_cols = y_cols if y_cols is not None else y
        series_col = mapping.get("series")
        series_col = series_col if series_col is not None else options.get("series")

    title = options.get("title", title)
    bands = max(1, int(options.get("bands", bands)))

    if y_cols == "y" or y_cols is None:
        y_cols = []
    elif isinstance(y_cols, str):
        y_cols = [y_cols]
    elif not isinstance(y_cols, list):
        y_cols = [str(y_cols)]

    _x = _auto_col(df, x_col, "x", "日期", "时间", "date", "time", "year")
    if _x is None or _x not in df.columns:
        return ChartResult(warnings=["找不到必填字段 [x]"])

    s_raw = df[_x]
    s_num = pd.to_numeric(s_raw, errors="coerce")
    num_ratio = s_num.notna().mean() if len(s_num) else 0

    is_year_like = False
    if num_ratio > 0.8:
        s_valid = s_num.dropna()
        s_int = s_valid.round().astype(int)
        year_ratio = ((s_int >= 1900) & (s_int <= 2100)).mean() if len(s_int) else 0
        int_ratio = np.isclose(s_valid.values, s_int.values, atol=1e-9).mean() if len(s_int) else 0
        is_year_like = (year_ratio > 0.8 and int_ratio > 0.95)

    if is_year_like:
        df[_x] = s_num.round().astype("Int64")
    else:
        if not pd.api.types.is_datetime64_any_dtype(df[_x]):
            s_dt = pd.to_datetime(s_raw, errors="coerce")
            dt_ratio = s_dt.notna().mean() if len(s_dt) else 0
            if dt_ratio > 0.8:
                df[_x] = s_dt
            elif num_ratio > 0.8:
                df[_x] = s_num

    if is_wide:
        long_df = df.rename(columns={"value": "_value"})[[_x, "_series", "_value"]].copy()
    else:
        long_df = None
        if series_col and series_col in df.columns:
            y_candidate = None
            if y_cols:
                for yc in y_cols:
                    auto_y = _auto_col(df, yc, "y", "value", "amount", "score")
                    if auto_y in df.columns:
                        y_candidate = auto_y
                        break
            if y_candidate is None:
                nums = _get_numeric_cols(df, exclude={_x})
                if nums:
                    y_candidate = nums[0]
            if y_candidate is None:
                return ChartResult(warnings=["找不到必填字段 [y]"])

            tmp = df[[_x, series_col, y_candidate]].copy()
            tmp[y_candidate] = pd.to_numeric(tmp[y_candidate], errors="coerce")
            tmp = tmp.dropna(subset=[_x, series_col, y_candidate])
            long_df = tmp.rename(columns={series_col: "_series", y_candidate: "_value"})
        else:
            _y_cols = []
            if not y_cols:
                _y_cols = _get_numeric_cols(df, exclude={_x})[:8]
            else:
                for yc in y_cols:
                    auto_y = _auto_col(df, yc, "y", "value", "amount", "score")
                    if auto_y in df.columns:
                        _y_cols.append(auto_y)
            _y_cols = [c for c in dict.fromkeys(_y_cols) if c in df.columns]
            if not _y_cols:
                return ChartResult(warnings=["找不到必填字段 [y]"])

            long_df = df.melt(
                id_vars=[_x],
                value_vars=_y_cols,
                var_name="_series",
                value_name="_value"
            )
            long_df["_value"] = pd.to_numeric(long_df["_value"], errors="coerce")
            long_df = long_df.dropna(subset=[_x, "_series", "_value"])

    if long_df is None or long_df.empty:
        return ChartResult(warnings=["数据为空或无法构建地平线图"])

    long_df = long_df.sort_values(["_series", _x])
    series_list = list(long_df["_series"].dropna().unique())

    vals = pd.to_numeric(long_df["_value"], errors="coerce").dropna()
    if vals.empty:
        return ChartResult(warnings=["数值列为空，无法计算地平线图"])

    max_abs = float(max(vals.max(), -vals.min()))
    if max_abs == 0:
        max_abs = 1.0
        warnings.append("数值全为0，已使用默认尺度")

    # 标准 horizon：统一线性 band 宽度（不是分位数）
    band_size = max_abs / bands if bands > 0 else max_abs
    if band_size <= 0:
        band_size = 1.0

    def hex_to_rgb(h):
        h = h.lstrip("#")
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)

    # 配色方案：优先用 options.color_scheme
    _scheme = get_color_scheme(options.get("color_scheme", "mckinsey"))
    _pos_hex = _scheme.get("primary", "#003D7A")  # 正值色
    _neg_hex = _scheme.get("negative", "#8B0000")  # 负值色
    pr, pg, pb = hex_to_rgb(_pos_hex)
    nr, ng, nb = hex_to_rgb(_neg_hex)
    opacities = [0.85] if bands == 1 else np.linspace(0.30, 0.92, bands).tolist()

    fig = make_subplots(
        rows=len(series_list),
        cols=1,
        shared_xaxes=False,
        vertical_spacing=0.02,
        subplot_titles=[str(s) for s in series_list]
    )
    n_rows = len(series_list)

    for r, s in enumerate(series_list, start=1):
        dfi = long_df[long_df["_series"] == s].sort_values(_x).copy()
        v = dfi["_value"].astype(float)

        # 正负分离
        pos = v.clip(lower=0)
        neg = (-v).clip(lower=0)

        # 颜色
        pr, pg, pb = hex_to_rgb(_pos_hex)  # 正值蓝
        nr, ng, nb = hex_to_rgb(_neg_hex)  # 负值红

        # 低层浅，高层深（alpha递增）
        opacities = [0.85] if bands == 1 else np.linspace(0.25, 0.90, bands).tolist()

        # -------------------------
        # 正值：各增量层（每层独立 tozeroy 到 0）
        # -------------------------
        for k in range(bands):
            low = k * band_size
            high = (k + 1) * band_size

            # 第k层厚度（0~band_size）
            layer = pos.clip(lower=low, upper=high) - low

            if layer.max() > 0:
                fig.add_trace(
                    go.Scatter(
                        x=dfi[_x],
                        y=layer,
                        mode="lines",
                        line=dict(width=0),
                        fill="tozeroy",
                        fillcolor=f"rgba({pr},{pg},{pb},{opacities[k]})",
                        hoverinfo="skip",
                        showlegend=False
                    ),
                    row=r, col=1
                )

        # -------------------------
        # 负值：各增量层（每层独立 tozeroy 到 0）
        # -------------------------
        for k in range(bands):
            low = k * band_size
            high = (k + 1) * band_size

            # 第k层厚度（0~band_size）
            layer = neg.clip(lower=low, upper=high) - low

            if layer.max() > 0:
                fig.add_trace(
                    go.Scatter(
                        x=dfi[_x],
                        y=-layer,  # 向下
                        mode="lines",
                        line=dict(width=0),
                        fill="tozeroy",
                        fillcolor=f"rgba({nr},{ng},{nb},{opacities[k]})",
                        hoverinfo="skip",
                        showlegend=False
                    ),
                    row=r, col=1
                )

        # 单条透明主线：用于 hover（避免分层重复提示）
        fig.add_trace(
            go.Scatter(
                x=dfi[_x], y=v,
                mode="lines",
                line=dict(width=0, color="rgba(0,0,0,0)"),
                showlegend=False,
                customdata=v,
                hovertemplate=f"<b>{s}</b><br>%{{x}}<br>值: %{{customdata:.3f}}<extra></extra>",
            ),
            row=r, col=1
        )

        # y轴：固定一个band高度（地平线图语义）
        fig.update_yaxes(
            row=r, col=1,
            range=[-band_size * 1.05, band_size * 1.05],
            showgrid=False,
            zeroline=False,
            showticklabels=False,
            title=""
        )

        # 只保留最底部x轴标签
        fig.update_xaxes(
            row=r, col=1,
            showgrid=True,
            gridcolor="rgba(0,0,0,0.08)",
            zeroline=False,
            title="",
            showticklabels=(r == n_rows)
        )

    legend_min, legend_max = -float(max_abs), float(max_abs)
    fig.add_trace(
        go.Scatter(
            x=[None], y=[None], mode="markers",
            marker=dict(
                size=0.1,
                color=[legend_min, legend_max],
                coloraxis="coloraxis",
                showscale=True,
                colorbar=dict(
                    title="原始数值范围",
                    x=1.02, y=0.5, len=0.9, thickness=14,
                    tickmode="array",
                    tickvals=[legend_min, 0, legend_max],
                    ticktext=[f"{legend_min:.2f}", "0", f"{legend_max:.2f}"]
                )
            ),
            hoverinfo="skip",
            showlegend=False
        ),
        row=1, col=1
    )

    fig.update_layout(
        title=dict(text=title, font=dict(size=16)),
        font_family="Heiti SC, Microsoft YaHei, sans-serif",
        plot_bgcolor="white",
        paper_bgcolor="white",
        margin=dict(l=80, r=90, t=70, b=40),
        hovermode="x unified",
        height=max(320, 110 * len(series_list)),
        coloraxis=dict(
            cmin=legend_min, cmax=legend_max,
            colorscale=[
                [0.0, _neg_hex],
                [0.5, "#F7F7F7"],
                [1.0, _pos_hex],
            ]
        )
    )

    chart_html = pio.to_html(fig, full_html=False, include_plotlyjs=False)
    html = _build_html(title, "horizon_chart_standard_fold", "plotly", _DATA_FMT, _DESC, chart_html)

    meta = {
        "chart_id": "horizon_chart_standard_fold",
        "n_rows": len(long_df),
        "x_col": _x,
        "series_count": len(series_list),
        "bands": bands,
        "band_size": float(band_size),
        "fold_mode": "standard_translate_fold",
        "is_wide_format": is_wide,
        "multi_xaxis": True
    }

    return ChartResult(html=html, spec={}, warnings=warnings, meta=meta)