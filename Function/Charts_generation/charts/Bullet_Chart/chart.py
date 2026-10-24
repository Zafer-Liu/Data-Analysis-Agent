"""
靶心图 Bullet Chart - 比较图表
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
            "#003D7A", "#0084D1", "#00A4EF", "#7FBA00",
            "#FFB81C", "#F7630C", "#DA3B01", "#A4373A",
            "#6B2C91", "#00B4EF",
        ]}

__all__ = ["generate"]

_DATA_FMT = "label列 + actual列 + target列 + [low/mid/high列]"
_DESC = "用实际值与目标值对比，背景范围表示绩效等级，适合KPI/绩效追踪。"


def _auto_col(df: pd.DataFrame, *hints: str) -> Optional[str]:
    """根据提示自动查找匹配的列名。"""
    if df is None or df.empty or not df.columns.size:
        return None

    hints = [h for h in hints if h]
    if not hints:
        return None

    col_lower_map = {str(c).lower(): c for c in df.columns}

    # 1) 精确匹配
    for h in hints:
        h_lower = str(h).lower()
        if h_lower in col_lower_map:
            return col_lower_map[h_lower]

    # 2) 模糊匹配
    for h in hints:
        h_lower = str(h).lower()
        for col in df.columns:
            col_name = str(col).lower()
            if h_lower in col_name or col_name in h_lower:
                return col

    # 3) 类型启发
    hint = str(hints[0]).lower()
    str_cols = [c for c in df.columns if df[c].dtype == object or pd.api.types.is_string_dtype(df[c])]
    num_cols = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]

    if any(kw in hint for kw in ["label", "name", "category", "title", "指标", "名称", "类目"]):
        return str_cols[0] if str_cols else (num_cols[0] if num_cols else None)

    if any(kw in hint for kw in ["actual", "value", "real", "实际", "达成", "完成"]):
        return num_cols[0] if num_cols else (str_cols[0] if str_cols else None)

    if any(kw in hint for kw in ["target", "goal", "目标", "指标值"]):
        return num_cols[1] if len(num_cols) > 1 else (num_cols[0] if num_cols else (str_cols[0] if str_cols else None))

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


def _validate_bullet_data(df: pd.DataFrame, label_col: str, actual_col: str, target_col: str) -> List[str]:
    """验证靶心图数据有效性。"""
    warnings: List[str] = []

    if df.empty:
        warnings.append("数据为空")
        return warnings

    # 检查空值
    empty_rows = df[df[[label_col, actual_col, target_col]].isnull().any(axis=1)]
    if len(empty_rows) > 0:
        warnings.append(f"检测到 {len(empty_rows)} 行空值，已过滤")

    # 检查负值
    invalid_actual = df[df[actual_col] < 0]
    if len(invalid_actual) > 0:
        warnings.append(f"检测到 {len(invalid_actual)} 行负值实际值，已过滤")

    invalid_target = df[df[target_col] < 0]
    if len(invalid_target) > 0:
        warnings.append(f"检测到 {len(invalid_target)} 行负值目标值，已过滤")

    return warnings


def generate(
    df: pd.DataFrame = None,
    mapping: Dict[str, str] = None,
    options: Dict[str, Any] = None,
    # 向后兼容旧接口
    excel_path: str = None,
    label: str = "label",
    actual: str = "actual",
    target: str = "target",
    low: Optional[str] = None,
    mid: Optional[str] = None,
    high: Optional[str] = None,
    title: str = "靶心图",
    **kwargs
) -> ChartResult:
    warnings: List[str] = []
    options = options or {}
    mapping = mapping or {}

    # 1) 读取数据
    if df is None:
        if excel_path:
            try:
                df = pd.read_excel(excel_path)
            except Exception as e:
                log.warning("[chart] 图表生成异常: %s", e)
                return ChartResult(warnings=[f"读取Excel失败: {e}"], meta={"error_stage": "read_excel"})
        else:
            return ChartResult(warnings=["请提供 df 或 excel_path"], meta={"error_stage": "input"})

    if not isinstance(df, pd.DataFrame) or df.empty:
        return ChartResult(warnings=["输入数据为空或格式错误"], meta={"error_stage": "input"})

    # 2) 参数优先级：mapping > 函数参数默认值
    # 注意 chart_selector 声明可选角色为 low/medium/high，而旧版模块读 "mid"。
    # 这里统一以 selector 规范 medium 为准，并兼容旧别名 mid/middle。
    lbl_hint = mapping.get("label")
    lbl_hint = lbl_hint if lbl_hint is not None else label
    act_hint = mapping.get("actual")
    act_hint = act_hint if act_hint is not None else actual
    tgt_hint = mapping.get("target")
    tgt_hint = tgt_hint if tgt_hint is not None else target
    low_hint = mapping.get("low")
    low_hint = low_hint if low_hint is not None else low
    mid_hint = mapping.get("medium")
    mid_hint = mid_hint if mid_hint is not None else (mapping.get("mid") if mapping.get("mid") is not None else (mapping.get("middle") if mapping.get("middle") is not None else mid))
    high_hint = mapping.get("high")
    high_hint = high_hint if high_hint is not None else high
    title = options.get("title", title)

    # 3) 自动识别列
    _lbl = _auto_col(df, lbl_hint, "label", "name", "category", "指标", "名称", "类目")
    _act = _auto_col(df, act_hint, "actual", "value", "real", "实际", "达成", "完成")
    _tgt = _auto_col(df, tgt_hint, "target", "goal", "目标", "指标值")
    _low = _auto_col(df, *([low_hint] if low_hint else []), "low", "poor", "差", "低", "及格")
    _mid = _auto_col(df, *([mid_hint] if mid_hint else []), "medium", "mid", "middle", "中", "良好")
    _high = _auto_col(df, *([high_hint] if high_hint else []), "high", "good", "优", "高", "优秀")

    detected_mapping = {
        "label": _lbl,
        "actual": _act,
        "target": _tgt,
        "low": _low,
        "medium": _mid,
        "high": _high
    }

    # 4) 必填校验
    required_missing = []
    for role, col in [("label", _lbl), ("actual", _act), ("target", _tgt)]:
        if col is None or col not in df.columns:
            required_missing.append(role)
            warnings.append(f"找不到必填字段 [{role}]")

    if required_missing:
        return ChartResult(
            warnings=warnings,
            meta={
                "error_stage": "detect_columns",
                "required_missing": required_missing,
                "detected_mapping": detected_mapping,
                "input_columns": [str(c) for c in df.columns]
            }
        )

    # 5) 组装并清理数据
    use_cols = [_lbl, _act, _tgt]
    for c in [_low, _mid, _high]:
        if c and c in df.columns and c not in use_cols:
            use_cols.append(c)

    d = df[use_cols].copy()
    d = d.dropna(subset=[_lbl, _act, _tgt])

    d[_act] = pd.to_numeric(d[_act], errors="coerce")
    d[_tgt] = pd.to_numeric(d[_tgt], errors="coerce")
    if _low and _low in d.columns:
        d[_low] = pd.to_numeric(d[_low], errors="coerce")
    if _mid and _mid in d.columns:
        d[_mid] = pd.to_numeric(d[_mid], errors="coerce")
    if _high and _high in d.columns:
        d[_high] = pd.to_numeric(d[_high], errors="coerce")

    d = d.dropna(subset=[_act, _tgt])

    validation_warnings = _validate_bullet_data(d, _lbl, _act, _tgt)
    warnings.extend(validation_warnings)

    # 过滤负值
    d = d[(d[_act] >= 0) & (d[_tgt] >= 0)]

    if d.empty:
        return ChartResult(
            warnings=warnings + ["处理后无有效数据"],
            meta={
                "error_stage": "clean_data",
                "detected_mapping": detected_mapping,
                "input_columns": [str(c) for c in df.columns]
            }
        )

    # 6) 画图
    # 配色方案：优先用 options.color_scheme
    _scheme = get_color_scheme(options.get("color_scheme", "mckinsey"))
    _palette = _scheme.get("colors") or [
        "#003D7A", "#0084D1", "#00A4EF", "#7FBA00",
        "#FFB81C", "#F7630C", "#DA3B01", "#A4373A",
        "#6B2C91", "#00B4EF",
    ]
    _actual_color = _scheme.get("primary", "rgba(0,91,154,1.0)")
    _target_color = _scheme.get("negative", "#E74C3C")
    _low_color = _scheme.get("negative", "rgba(244,67,54,0.35)")
    _mid_color = _scheme.get("neutral", "rgba(255,193,7,0.35)")
    _high_color = _scheme.get("positive", "rgba(76,175,80,0.35)")

    fig = go.Figure()

    # 动态区间标签：根据输入数据标签改变
    low_label = str(mapping.get("low") or _low or "及格")
    mid_label = str(mapping.get("mid") or _mid or "良好")
    high_label = str(mapping.get("high") or _high or "优秀")

    # 背景范围 low -> mid -> high
    for idx, (_, row) in enumerate(d.iterrows()):
        lbl = str(row[_lbl])

        low_val = float(row[_low]) if (_low and _low in d.columns and pd.notna(row[_low])) else None
        mid_val = float(row[_mid]) if (_mid and _mid in d.columns and pd.notna(row[_mid])) else None
        high_val = float(row[_high]) if (_high and _high in d.columns and pd.notna(row[_high])) else None

        if low_val is not None and low_val > 0:
            fig.add_trace(go.Bar(
                y=[lbl], x=[low_val], orientation='h',
                marker=dict(color=_low_color),
                name=low_label, showlegend=(idx == 0),
                hovertemplate=f'<b>{lbl}</b><br>{low_label}区间: 0 - {low_val:.2f}<extra></extra>'
            ))

        if low_val is not None and mid_val is not None and mid_val > low_val:
            fig.add_trace(go.Bar(
                y=[lbl], x=[mid_val - low_val], orientation='h', base=low_val,
                marker=dict(color=_mid_color),
                name=mid_label, showlegend=(idx == 0),
                hovertemplate=f'<b>{lbl}</b><br>{mid_label}区间: {low_val:.2f} - {mid_val:.2f}<extra></extra>'
            ))

        if mid_val is not None and high_val is not None and high_val > mid_val:
            fig.add_trace(go.Bar(
                y=[lbl], x=[high_val - mid_val], orientation='h', base=mid_val,
                marker=dict(color=_high_color),
                name=high_label, showlegend=(idx == 0),
                hovertemplate=f'<b>{lbl}</b><br>{high_label}区间: {mid_val:.2f} - {high_val:.2f}<extra></extra>'
            ))

    # 实际值
    fig.add_trace(go.Bar(
        y=d[_lbl].astype(str),
        x=d[_act],
        orientation='h',
        marker=dict(color=_actual_color),
        name='实际值',
        text=d[_act].round(1),
        textposition='inside',
        hovertemplate='<b>%{y}</b><br>实际: %{x:.2f}<extra></extra>'
    ))

    # 目标值
    fig.add_trace(go.Scatter(
        y=d[_lbl].astype(str),
        x=d[_tgt],
        mode='markers',
        marker=dict(symbol='line-ns', size=22, color=_target_color, line=dict(width=3)),
        name='目标值',
        hovertemplate='<b>%{y}</b><br>目标: %{x:.2f}<extra></extra>'
    ))

    fig.update_layout(
        barmode='overlay',
        title=title,
        xaxis_title='数值',
        yaxis_title='指标',
        height=max(300, len(d) * 50),
        margin=dict(l=150, r=40, t=60, b=40),
        showlegend=True,
        hovermode='closest',
        plot_bgcolor='white',
        paper_bgcolor='white',
        font_family="Heiti SC, Microsoft YaHei, sans-serif"
    )

    # 7) 产出 html + spec
    chart_html = pio.to_html(fig, full_html=False, include_plotlyjs=False)
    html = _build_html(title, "bullet_chart", "plotly", _DATA_FMT, _DESC, chart_html)


    # 返回你要的“输入数据标签”与“识别到的列”
    input_labels = d[_lbl].astype(str).tolist()

    meta = {
        "chart_id": "bullet_chart",
        "n_items": len(d),
        "detected_mapping": detected_mapping,   # 识别到的列
        "input_labels": input_labels,           # 输入数据标签
        "input_columns": [str(c) for c in df.columns]
    }

    # 关键：spec 不要空，避免某些 is_valid 校验失败
    return ChartResult(html=html, spec=fig.to_plotly_json(), warnings=warnings, meta=meta)
