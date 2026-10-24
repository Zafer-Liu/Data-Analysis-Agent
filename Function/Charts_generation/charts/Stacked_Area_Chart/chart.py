"""
堆叠面积图 Stacked Area Chart - 趋势图表
图表分类: 趋势 Trend
感知排名: ★★★★☆

统一接口:
    generate(df, mapping, options) -> ChartResult

使用示例:
    from charts.Stacked_Area_Chart.chart import generate
    from charts import ChartResult

    result = generate(
        df=df,
        mapping={"x": "月份", "y": ["销售额", "成本"]},
        options={"title": "累积趋势"}
    )

长格式示例:
    result = generate(
        df=df,
        mapping={"x": "week", "y": "flow", "series": "period"},
        options={"title": "分时段堆叠趋势"}
    )
"""
import logging
log = logging.getLogger(__name__)
import pandas as pd
import plotly.graph_objects as go
import plotly.io as pio
from typing import List, Tuple

try:
    from charts.base import ChartResult, build_chart_html
except ImportError as e:
    log.debug("[chart] 图表生成异常: %s", e)
    class ChartResult:
        def __init__(self, html: str = "", spec: dict = None, warnings: list = None, meta: dict = None):
            self.html = html
            self.spec = spec or {}
            self.warnings = warnings or []
            self.meta = meta or {}
        def is_valid(self):
            return bool(self.html.strip()) and len(self.html) > 500

    import html as _html
    def build_chart_html(title, chart_name, library, data_fmt, desc, embed):
        esc = _html.escape
        return (f'<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8">'
                f'<title>{esc(title)}</title></head><body>'
                f'<h1>{esc(title)}</h1>{embed}'
                f'<div>{esc(data_fmt)} {esc(desc)}</div></body></html>')

try:
    from charts.color_schemes import get_color_scheme
except ImportError as e:
    log.debug("[chart] 图表生成异常: %s", e)
    def get_color_scheme(name="mckinsey"):
        return {"colors": ["#003D7A", "#0084D1", "#00A4EF", "#7FBA00", "#FFB81C",
                           "#F7630C", "#DA3B01", "#A4373A", "#6B2C91", "#00B4EF"]}


def _get_colors(scheme_name: str, count: int) -> List[str]:
    scheme = get_color_scheme(scheme_name)
    palette = scheme.get("colors", [])
    if not palette:
        palette = ["#003D7A", "#0084D1", "#00A4EF", "#7FBA00", "#FFB81C"]
    return [palette[i % len(palette)] for i in range(count)]


def _hex_to_rgba(hex_color: str, alpha: float = 0.4) -> str:
    """将 #RRGGBB 转为 rgba(r,g,b,a) 格式，兼容所有 Plotly 版本"""
    hex_color = hex_color.lstrip("#")
    r, g, b = int(hex_color[0:2], 16), int(hex_color[2:4], 16), int(hex_color[4:6], 16)
    return f"rgba({r},{g},{b},{alpha})"


def _auto_col_x(df: pd.DataFrame) -> str:
    hints = ["date", "time", "month", "year", "week", "day", "period", "Week_Num", "时间", "日期", "月份", "年份", "周", "周数"]
    col_lower = {str(c).lower(): c for c in df.columns}
    for hint in hints:
        if str(hint).lower() in col_lower:
            return col_lower[str(hint).lower()]
    for c in df.columns:
        if df[c].dtype == object or pd.api.types.is_string_dtype(df[c]):
            return c
    for c in df.columns:
        if pd.api.types.is_numeric_dtype(df[c]):
            return c
    raise ValueError("找不到有效的 x 列")


def _auto_cols_y(df: pd.DataFrame, x_col: str) -> List[str]:
    y_cols = [c for c in df.columns if c != x_col and pd.api.types.is_numeric_dtype(df[c])]
    if not y_cols:
        for c in df.columns:
            if c != x_col:
                s = pd.to_numeric(df[c].astype(str).str.replace(',', '', regex=False), errors='coerce')
                if s.notna().any():
                    y_cols.append(c)
    return y_cols


def _try_parse_datetime_order(values: List) -> Tuple[bool, List]:
    s = pd.Series(values)
    dt = pd.to_datetime(s, errors="coerce")
    if dt.notna().all():
        order_df = pd.DataFrame({"raw": values, "dt": dt}).sort_values("dt")
        ordered = order_df["raw"].tolist()
        # 去重保持排序后顺序
        ordered = list(pd.Index(ordered).unique())
        return True, ordered
    return False, list(pd.Index(values).unique())


def _sort_x_values(values: List) -> List:
    """
    x 排序策略：
    1. 若全可转 datetime，则按时间排序
    2. 若为数值，则按数值排序
    3. 否则保持原始出现顺序（避免 W1, W10, W2 这种字符串排序错误）
    """
    uniq = list(pd.Index([v for v in values if pd.notna(v)]).unique())
    if not uniq:
        return uniq

    is_dt, ordered = _try_parse_datetime_order(uniq)
    if is_dt:
        return ordered

    ser = pd.Series(uniq)
    num = pd.to_numeric(ser, errors="coerce")
    if num.notna().all():
        tmp = pd.DataFrame({"raw": uniq, "num": num}).sort_values("num")
        return tmp["raw"].tolist()

    return uniq


def _clean_numeric_series(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series.astype(str).str.replace(',', '', regex=False), errors='coerce')


def generate(df: pd.DataFrame, mapping: dict = None, options: dict = None) -> ChartResult:
    warnings = []
    meta = {}
    options = options or {}

    if df is None or df.empty:
        return ChartResult("<p>数据为空</p>", warnings=["数据为空，无法绘制堆叠面积图"])

    df = df.copy()

    # 读取 mapping
    x_col = None
    y_cols = []
    series_col = None

    if mapping:
        if mapping.get("x"):
            x_col = mapping["x"]
        if mapping.get("y"):
            y_val = mapping["y"]
            y_cols = y_val if isinstance(y_val, list) else [y_val]
        if mapping.get("series"):
            series_col = mapping["series"]

    # 自动识别 x
    if not x_col:
        try:
            x_col = _auto_col_x(df)
        except ValueError as e:
            log.debug("[chart] 图表生成异常: %s", e)
            warnings.append(str(e))
            return ChartResult("<p>找不到有效的 x 列</p>", warnings=warnings, meta=meta)

    if x_col not in df.columns:
        return ChartResult(f"<p>x列不存在: {x_col}</p>", warnings=[f"x列不存在: {x_col}"], meta=meta)

    # 自动识别 y
    if not y_cols:
        y_cols = _auto_cols_y(df, x_col)

    if not y_cols:
        warnings.append("找不到有效的数值列")
        return ChartResult("<p>找不到有效的数值列</p>", warnings=warnings, meta=meta)

    for y in y_cols:
        if y not in df.columns:
            return ChartResult(f"<p>y列不存在: {y}</p>", warnings=[f"y列不存在: {y}"], meta=meta)

    if series_col and series_col not in df.columns:
        return ChartResult(f"<p>series列不存在: {series_col}</p>", warnings=[f"series列不存在: {series_col}"], meta=meta)

    title = options.get("title", "堆叠面积图")
    color_scheme_name = options.get("color_scheme", "mckinsey")

    # x 点数量检查
    if df[x_col].dropna().nunique() < 3:
        warnings.append("x轴有效数据点少于3个，堆叠面积图效果可能不佳")

    fig = go.Figure()

    # =========================
    # 模式1：长格式 x + 单y + series
    # =========================
    if series_col:
        if len(y_cols) != 1:
            return ChartResult(
                html="<p>series 模式下只支持单个 y 列</p>",
                warnings=["series 模式下，堆叠面积图只支持单个 y 列"],
                meta=meta
            )

        y_col = y_cols[0]

        df_plot = df[[x_col, y_col, series_col]].copy()
        df_plot[y_col] = _clean_numeric_series(df_plot[y_col])
        df_plot = df_plot.dropna(subset=[x_col, y_col, series_col])

        if df_plot.empty:
            return ChartResult("<p>清洗后数据为空</p>", warnings=["清洗后数据为空，无法绘制堆叠面积图"], meta=meta)

        # series 个数检查
        n_series = df_plot[series_col].nunique()
        if n_series < 2:
            return ChartResult(
                html="<p>series 分组不足</p>",
                warnings=["series 分组数少于2，无法形成有效堆叠面积图"],
                meta=meta
            )
        if n_series > 5:
            warnings.append(f"series 分组数过多({n_series}个)，建议控制在 2-5 个以内")

        # 负值检查
        if (df_plot[y_col] < 0).any():
            warnings.append("检测到负值，堆叠面积图可能无法正确表达累积关系")

        # 重复组合聚合
        if df_plot.duplicated(subset=[x_col, series_col]).any():
            warnings.append("检测到重复的 (x, series) 组合，已自动按求和聚合")

        df_plot = df_plot.groupby([x_col, series_col], as_index=False)[y_col].sum()

        # x 顺序：时间 > 数值 > 原始顺序
        x_order = _sort_x_values(df_plot[x_col].tolist())

        # series 顺序：按总量从大到小，增强可读性
        series_order_df = df_plot.groupby(series_col, as_index=False)[y_col].sum().sort_values(y_col, ascending=False)
        series_order = series_order_df[series_col].tolist()

        # 补齐缺失 (x, series) 组合
        full_index = pd.MultiIndex.from_product(
            [x_order, series_order],
            names=[x_col, series_col]
        )

        df_plot = (
            df_plot.set_index([x_col, series_col])
            .reindex(full_index, fill_value=0)
            .reset_index()
        )

        # 转成分类顺序，确保绘图顺序稳定
        df_plot[x_col] = pd.Categorical(df_plot[x_col], categories=x_order, ordered=True)
        df_plot[series_col] = pd.Categorical(df_plot[series_col], categories=series_order, ordered=True)
        df_plot = df_plot.sort_values([x_col, series_col])

        colors = _get_colors(color_scheme_name, len(series_order))

        for i, s_name in enumerate(series_order):
            sub = df_plot[df_plot[series_col] == s_name]
            fig.add_trace(go.Scatter(
                x=sub[x_col].astype(str),
                y=sub[y_col],
                mode='lines',
                name=str(s_name),
                line=dict(color=colors[i], width=0.8),
                stackgroup='one',
                groupnorm=options.get("groupnorm", None),  # 可选："percent"
                fillcolor=_hex_to_rgba(colors[i], 0.4),
                hovertemplate=f"{series_col}: {s_name}<br>{x_col}: %{{x}}<br>{y_col}: %{{y}}<extra></extra>"
            ))

        meta["mode"] = "long"
        meta["x_col"] = x_col
        meta["y_col"] = y_col
        meta["series_col"] = series_col
        meta["series_count"] = n_series

    # =========================
    # 模式2：宽格式 x + 多y
    # =========================
    else:
        if len(y_cols) < 2:
            return ChartResult(
                html="<p>非 series 模式下至少需要2个 y 列</p>",
                warnings=["非 series 模式下，堆叠面积图至少需要 2 个 y 列"],
                meta=meta
            )

        if len(y_cols) > 5:
            warnings.append(f"y列过多({len(y_cols)}列)，建议控制在 2-5 列以内")

        df_plot = df[[x_col] + y_cols].copy()
        df_plot = df_plot.dropna(subset=[x_col])

        if df_plot.empty:
            return ChartResult("<p>清洗后数据为空</p>", warnings=["清洗后数据为空，无法绘制堆叠面积图"], meta=meta)

        for col in y_cols:
            df_plot[col] = _clean_numeric_series(df_plot[col]).fillna(0)
            if (df_plot[col] < 0).any():
                warnings.append(f"列 {col} 检测到负值，堆叠面积图可能无法正确表达累积关系")

        x_order = _sort_x_values(df_plot[x_col].tolist())
        df_plot[x_col] = pd.Categorical(df_plot[x_col], categories=x_order, ordered=True)
        df_plot = df_plot.sort_values(x_col)

        # 按总量从大到小排序，增强可读性
        y_cols_sorted = sorted(y_cols, key=lambda c: df_plot[c].sum(), reverse=True)
        colors = _get_colors(color_scheme_name, len(y_cols_sorted))

        for i, col in enumerate(y_cols_sorted):
            fig.add_trace(go.Scatter(
                x=df_plot[x_col].astype(str),
                y=df_plot[col],
                mode='lines',
                name=str(col),
                line=dict(color=colors[i], width=0.8),
                stackgroup='one',
                groupnorm=options.get("groupnorm", None),  # 可选："percent"
                fillcolor=_hex_to_rgba(colors[i], 0.4),
                hovertemplate=f"{x_col}: %{{x}}<br>{col}: %{{y}}<extra></extra>"
            ))

        meta["mode"] = "wide"
        meta["x_col"] = x_col
        meta["y_cols"] = y_cols_sorted

    fig.update_layout(
        title=title,
        font_family="Heiti SC, Microsoft YaHei, sans-serif",
        plot_bgcolor="white",
        paper_bgcolor="white",
        margin=dict(l=50, r=50, t=70, b=50),
        hovermode="x unified",
        xaxis_title=x_col,
        yaxis_title="占比" if options.get("groupnorm") == "percent" else "数值",
        showlegend=True,
        legend=dict(orientation="v", yanchor="top", y=0.99, xanchor="left", x=0.01)
    )

    chart_html = pio.to_html(fig, full_html=False, include_plotlyjs=False)
    html = build_chart_html(title, "stacked_area_chart", "plotly",
                            "x列 + y列(多列) 或 x列 + y列 + series列",
                            "堆叠面积图，展示各部分随时间的累积趋势。", chart_html)

    return ChartResult(html=html, warnings=warnings, meta=meta)