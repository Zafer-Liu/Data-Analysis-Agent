# -*- coding: utf-8 -*-
"""
气泡图 Bubble Chart - ECharts 实现
图表分类: 关系类 RELATIONSHIP
感知排名: ★★★★☆
支持: x/y/size/color 四维气泡图
"""
import logging
log = logging.getLogger(__name__)
import sys
from pathlib import Path
from typing import Dict, Any, List
import pandas as pd
from collections import defaultdict
import json as _json

_PROJECT_ROOT = Path(__file__).parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from charts.base import ChartResult
from charts.color_schemes import get_color_scheme

__all__ = ["generate"]

MCKINSEY_COLORS = [
    "#003D7A", "#0084D1", "#00A4EF", "#7FBA00",
    "#FFB81C", "#DA3B01", "#6B2C91", "#4A90E2",
]
MCK_PRIMARY = "#003D7A"
MCK_GRAY = "#6E7B8B"
MCK_LIGHT = "#B9C2CF"


def _jesc(s: str) -> str:
    return (str(s).replace("\\", "\\\\")
            .replace("'", "\\'")
            .replace("\n", "\\n")
            .replace("\r", "\\r")
            .replace("\t", "\\t"))


def _is_numeric(s: pd.Series) -> bool:
    return pd.api.types.is_numeric_dtype(s)


def _num_col(df: pd.DataFrame, hint: str, exclude: set) -> str:
    if hint and hint in df.columns and hint not in exclude and _is_numeric(df[hint]):
        return hint
    h = (hint or "").lower()
    for c in df.columns:
        if c in exclude or not _is_numeric(df[c]):
            continue
        # 跳过 "Color" 列（保留给颜色映射）
        if str(c).lower() == "color":
            continue
        if h and (h in str(c).lower() or str(c).lower() in h):
            return c
    nums = [c for c in df.columns if c not in exclude and _is_numeric(df[c]) and str(c).lower() != "color"]
    return nums[0] if nums else None


def _str_col(df: pd.DataFrame, hint: str, exclude: set) -> str:
    if hint and hint in df.columns and hint not in exclude and not _is_numeric(df[hint]):
        return hint
    h = (hint or "").lower()
    # 优先查找名为 "Color" 的列（无论是否有 hint）
    for c in df.columns:
        if c not in exclude and not _is_numeric(df[c]) and str(c).lower() == "color":
            return c
    # 如果有 hint，按 hint 匹配
    if h:
        for c in df.columns:
            if c in exclude or _is_numeric(df[c]):
                continue
            if h in str(c).lower() or str(c).lower() in h:
                return c
    # 最后才返回第一个字符串列
    strs = [c for c in df.columns if c not in exclude and not _is_numeric(df[c])]
    return strs[0] if strs else None


def generate(
    df: pd.DataFrame = None,
    mapping: Dict[str, str] = None,
    options: Dict[str, Any] = None,
    excel_path: str = None,
    x: str = None,
    y: str = None,
    size: str = None,
    color: str = None,
    title: str = "气泡图",
    **kwargs
) -> ChartResult:
    """
    Parameters
    ----------
    df : pd.DataFrame
    mapping : dict  e.g. {"x": "消费倾向", "y": "渗透率", "size": "城区人口", "color": "盈利"}
    options : dict  e.g. {"title": "自定义标题", "x_mid": 77.5, "y_mid": 38.3}
    excel_path : str  备选数据源
    x, y, size, color : str  列名提示
    title : str

    Returns
    -------
    ChartResult with .html attribute
    """
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
            return ChartResult(warnings=["需要 df 或 excel_path"])

    if df.empty:
        return ChartResult(warnings=["数据为空"])

    # 确保列名全部为字符串
    df.columns = df.columns.astype(str)

    xh = mapping.get("x")
    xh = xh if xh is not None else x
    yh = mapping.get("y")
    yh = yh if yh is not None else y
    sh = mapping.get("size")
    sh = sh if sh is not None else size
    ch = mapping.get("color")
    ch = ch if ch is not None else color
    title = options.get("title", title)
    color_scheme_name = options.get("color_scheme", "mckinsey")
    
    # 获取配色方案
    try:
        color_scheme = get_color_scheme(color_scheme_name)
        scheme_colors = color_scheme.get("colors", MCKINSEY_COLORS)
        primary_color = color_scheme.get("primary", MCK_PRIMARY)
        secondary_color = color_scheme.get("secondary", "#0084D1")
        positive_color = color_scheme.get("positive", "#7FBA00")
        negative_color = color_scheme.get("negative", "#DA3B01")
    except Exception as e:
        log.warning("[chart] 图表生成异常: %s", e)
        warnings.append(f"配色方案加载失败: {e}，使用默认配色")
        scheme_colors = MCKINSEY_COLORS
        primary_color = MCK_PRIMARY
        secondary_color = "#0084D1"
        positive_color = "#7FBA00"
        negative_color = "#DA3B01"

    used: set = set()
    _x = _num_col(df, xh, used)
    if _x: used.add(_x)
    _y = _num_col(df, yh, used)
    if _y: used.add(_y)
    # 自动检测 size 列（数值列，优先级：明确指定 > 自动检测）
    _sz = _num_col(df, sh, used)
    if _sz: used.add(_sz)
    # 自动检测 color 列：优先查找名为 "Color" 的列（无论字符串还是数值）
    _c = None
    # 第一优先级：明确指定的 color hint
    if ch:
        if ch in df.columns and ch not in used:
            _c = ch
    # 第二优先级：查找名为 "Color" 的列
    if not _c:
        for col in df.columns:
            if col not in used and str(col).lower() == "color":
                _c = col
                break
    # 第三优先级：自动检测（先字符串，后数值）
    if not _c:
        _c = _str_col(df, ch, used)
    if not _c:
        _c = _num_col(df, ch, used)
    if _c: used.add(_c)

    if not _x:
        return ChartResult(warnings=["找不到 x 列"])
    if not _y:
        return ChartResult(warnings=["找不到 y 列"])

    dfx = pd.to_numeric(df[_x], errors="coerce")
    dfy = pd.to_numeric(df[_y], errors="coerce")
    valid_xy = dfx.notna() & dfy.notna()
    if valid_xy.sum() == 0:
        return ChartResult(warnings=["x/y 列无有效数值"])

    xn = dfx[valid_xy]
    yn = dfy[valid_xy]
    xlo, xhi = float(xn.min()), float(xn.max())
    ylo, yhi = float(yn.min()), float(yn.max())

    xp = (xhi - xlo) * 0.06
    yp = (yhi - ylo) * 0.06
    xlo, xhi = (xlo - xp, xhi + xp) if xp > 0 else (xlo - 0.1, xhi + 0.1)
    ylo, yhi = (ylo - yp, yhi + yp) if yp > 0 else (ylo - 0.1, yhi + 0.1)

    # size 映射：气泡直径 px = 18 + (val - min) / (max - min) * 62
    slo = shi = None
    if _sz and _sz in df.columns:
        sv = pd.to_numeric(df[_sz], errors="coerce").dropna()
        if not sv.empty:
            slo, shi = float(sv.min()), float(sv.max())

    # color 类型判断
    color_is_category = False
    color_is_numeric = False
    cat_map = None
    cmin = cmax = None

    if _c and _c in df.columns:
        if _is_numeric(df[_c]):
            color_is_numeric = True
            cv = pd.to_numeric(df[_c], errors="coerce").dropna()
            if not cv.empty:
                cmin, cmax = float(cv.min()), float(cv.max())
        else:
            color_is_category = True
            cats = sorted(set(str(v) for v in df[_c].dropna()))
            cat_map = {k: scheme_colors[i % len(scheme_colors)] for i, k in enumerate(cats)}

    # 构建每行数据：value = [x, y, size(可选)]，name = 行标签（用于label显示）
    # 检查是否有可作为标签的列（第一个非数值列，不需要在 used 中）
    label_col = None
    for col in df.columns:
        if not _is_numeric(df[col]):
            label_col = col
            break
    
    rows = []
    for idx, r in df.iterrows():
        try:
            xv = float(r[_x]); yv = float(r[_y])
        except Exception as e:
            log.warning("[chart] 图表生成异常: %s", e)
            continue

        # 计算气泡像素直径
        px = 40
        if slo is not None and shi is not None and shi > slo:
            try:
                raw = float(r[_sz])
                px = int(18 + (raw - slo) / (shi - slo) * 62)
            except Exception as e:
                log.warning("[chart] 图表生成异常: %s", e)
                px = 40

        # 颜色处理
        item_color = primary_color
        color_value = None  # 用于 visualMap 的数值
        
        if _c and _c in df.columns:
            if color_is_category:
                cv = str(r[_c]) if pd.notna(r[_c]) else None
                item_color = cat_map.get(cv, MCK_GRAY) if cat_map else MCK_GRAY
            elif color_is_numeric:
                try:
                    cnum = float(r[_c])
                    color_value = cnum  # 存储数值，让 visualMap 处理颜色映射
                except Exception as e:
                    log.warning("[chart] 图表生成异常: %s", e)
                    color_value = (cmin + cmax) / 2 if cmin is not None and cmax is not None else 0

        # tooltip 内容：名字 → x → y → size
        tip_parts = []
        if label_col and label_col in df.columns and pd.notna(r[label_col]):
            tip_parts.append(f"{_jesc(label_col)}: {_jesc(str(r[label_col]))}")
        tip_parts.append(f"{_jesc(_x)}: {round(xv, 3)}")
        tip_parts.append(f"{_jesc(_y)}: {round(yv, 3)}")
        if _sz:
            tip_parts.append(f"{_jesc(_sz)}: {round(float(r[_sz]), 3)}")
        if _c and _c in df.columns:
            if color_is_category:
                tip_parts.append(f"{_jesc(_c)}: {_jesc(str(r[_c]))}")
            elif color_is_numeric:
                tip_parts.append(f"{_jesc(_c)}: {round(float(r[_c]), 3)}")

        # 行标签 = 使用 label_col（如城市名），或者行索引
        # value = [x, y, size] 或 [x, y, size, color_value]
        # 对于数值颜色，第4元素由 visualMap 读取
        label_name = str(r[label_col]) if label_col and label_col in df.columns and pd.notna(r[label_col]) else str(idx)
        
        if color_is_numeric and color_value is not None:
            value = [xv, yv, px, color_value]  # 第4元素 = 颜色数值
        else:
            value = [xv, yv, px]  # 第3元素 = 气泡直径 px
        
        point = {
            "name": label_name,
            "value": value,
            "itemStyle": {"opacity": 0.85, "borderColor": "#fff", "borderWidth": 2},  # 不设置 color，让 visualMap 控制
            "tip": "<br>".join(tip_parts),
        }
        rows.append(point)

    if not rows:
        return ChartResult(warnings=["无有效数据点"])

    # ── 象限线（x_mid / y_mid）─────────────────────────
    # markLine 画在 xAxis/yAxis 层（不是 series 层），这样可以跨所有系列
    x_mid_raw = options.get("x_mid")
    y_mid_raw = options.get("y_mid")
    
    # 如果 options 中没有，尝试从数据列中提取
    if x_mid_raw is None:
        for col in df.columns:
            col_lower = str(col).lower()
            if ("x" in col_lower and ("mid" in col_lower or "中线" in col_lower)) or col_lower == "x_mid":
                vals = pd.to_numeric(df[col], errors="coerce").dropna()
                if not vals.empty:
                    x_mid_raw = vals.iloc[0]
                    break
    
    if y_mid_raw is None:
        for col in df.columns:
            col_lower = str(col).lower()
            if ("y" in col_lower and ("mid" in col_lower or "中线" in col_lower)) or col_lower == "y_mid":
                vals = pd.to_numeric(df[col], errors="coerce").dropna()
                if not vals.empty:
                    y_mid_raw = vals.iloc[0]
                    break
    
    x_mid = float(x_mid_raw) if x_mid_raw is not None else None
    y_mid = float(y_mid_raw) if y_mid_raw is not None else None

    # series 层的 markLine：每 series 各自画虚线（可以叠加到series层）
    # 如果 x_mid/y_mid 任一存在，在 series[0] 上画 markLine
    def make_markline():
        data = []
        if x_mid is not None:
            data.append({"xAxis": x_mid})
        if y_mid is not None:
            data.append({"yAxis": y_mid})
        if not data:
            return None
        return {
            "silent": True,
            "symbol": "none",
            "lineStyle": {"type": "dashed", "width": 1.5, "color": MCK_GRAY},
            "label": {"formatter": "", "show": False},
            "data": data,
        }

    markline = make_markline()

    # ── series 构建 ────────────────────────────────────
    # 单系列（所有点在一组）
    show_label = len(rows) <= 20  # 气泡数 > 20 时隐藏标签避免重叠
    series_list = [{
        "type": "scatter",
        "name": "数据点",
        "data": rows,
        # symbolSize 可以是函数，d[2] 就是 value[2]（气泡直径 px）
        "symbolSize": "function(d){return d[2] || 40;}",
        # label 显示行索引，hover 时可见
        "label": {
            "show": True,
            "position": "top",
            "formatter": "function(p){return p.data&&p.data.name?p.data.name:'';}" ,
            "fontSize": 11,
            "color": primary_color,
            "fontWeight": "bold",
        },
        "itemStyle": {"opacity": 0.85, "borderColor": "#fff", "borderWidth": 2},
        "tooltip": {"trigger": "item", "formatter": "function(p){return (p.data&&p.data.tip)?p.data.tip:'';}"},
    }]
    legend_data = []

    if color_is_category and cat_map:
        # 多系列：按 category 分组
        groups: Dict[str, List] = defaultdict(list)
        row_idx = 0
        for idx, r in df.iterrows():
            try:
                float(r[_x]); float(r[_y])
            except Exception as e:
                log.warning("[chart] 图表生成异常: %s", e)
                continue
            if row_idx >= len(rows):
                break
            cat_val = str(r[_c]) if pd.notna(r[_c]) else "NA"
            groups[cat_val].append(rows[row_idx])
            row_idx += 1
        # 重建 series
        series_list = []
        legend_data = []
        for cat, cat_rows in groups.items():
            if not cat_rows:
                continue
            hc = cat_map.get(cat, MCK_GRAY)
            show_cat_label = len(cat_rows) <= 20  # 每个类别的气泡数 > 20 时隐藏标签
            s = {
                "type": "scatter",
                "name": cat,
                "data": cat_rows,
                "symbolSize": "function(d){return d[2] || 40;}",
                "label": {
                    "show": True,
                    "position": "top",
                    "formatter": "function(p){return p.data&&p.data.name?p.data.name:'';}" ,
                    "fontSize": 11,
                    "color": primary_color,
                    "fontWeight": "bold",
                },
                "itemStyle": {"color": hc, "opacity": 0.85, "borderColor": "#fff", "borderWidth": 2},
                "tooltip": {"trigger": "item", "formatter": "function(p){return (p.data&&p.data.tip)?p.data.tip:'';}"},
            }
            if markline:
                s["markLine"] = markline
            series_list.append(s)
            legend_data.append(cat)
    else:
        # 单系列且无 category 分组时，添加 markLine
        if markline:
            series_list[0]["markLine"] = markline

    # ── 是否为百分比轴（0-1 范围视为百分比）──────────
    x_is_pct = bool(xn.min() >= 0 and xn.max() <= 1)
    y_is_pct = bool(yn.min() >= 0 and yn.max() <= 1)
    x_f = "(v*100).toFixed(1)+'%'" if x_is_pct else "v.toFixed(1)"
    y_f = "(v*100).toFixed(1)+'%'" if y_is_pct else "v.toFixed(1)"

    # ── visualMap（数值颜色映射）────────────────────────
    visual_map = None
    if color_is_numeric and cmin is not None and cmax is not None:
        # 使用配色方案中的颜色源
        visual_colors = [positive_color, secondary_color, negative_color]
        visual_map = {
            "type": "continuous",
            "min": float(cmin),
            "max": float(cmax),
            "right": 20,
            "top": "middle",
            "calculable": True,
            "inRange": {"color": visual_colors},
            "text": [str(round(cmax, 1)), str(round(cmin, 1))],
            "seriesIndex": 0,  # 应用到第一个 series
        }

    # ── ECharts option ──────────────────────────────────
    echarts_opt = {
        "animation": True,
        "title": {
            "text": title,
            "left": "center", "top": "1%",
            "textStyle": {"color": primary_color, "fontSize": 16, "fontWeight": "bold",
                          "fontFamily": "Heiti SC, Microsoft YaHei"},
        },
        "tooltip": {
            "trigger": "item",
            "axisPointer": {"type": "cross"},
            "backgroundColor": "rgba(255,255,255,0.95)",
            "borderColor": MCK_LIGHT, "borderWidth": 1,
            "textStyle": {"color": "#333", "fontSize": 12},
        },
        "legend": {"show": bool(legend_data) and len(legend_data) <= 10, "top": "6%", "data": legend_data},
        "xAxis": {
            "type": "value",
            "name": _x,
            "nameLocation": "middle", "nameGap": 42,
            "min": xlo, "max": xhi,
            "axisLabel": {"fontSize": 12, "color": "#333"},
            "axisLine": {"lineStyle": {"color": "#333", "width": 1.5}},
            "splitLine": {"show": False},
            "nameTextStyle": {"color": "#333", "fontSize": 13},
        },
        "yAxis": {
            "type": "value",
            "name": _y,
            "nameLocation": "middle", "nameGap": 45,
            "min": ylo, "max": yhi,
            "axisLabel": {"fontSize": 12, "color": "#333"},
            "axisLine": {"lineStyle": {"color": "#333", "width": 1.5}},
            "splitLine": {"show": False},
            "nameTextStyle": {"color": "#333", "fontSize": 13},
        },
        "series": series_list,
    }
    if visual_map:
        echarts_opt["visualMap"] = visual_map
        # 数值颜色映射时，series 的 itemStyle 不设置 color，由 visualMap 控制
        # 但保留 opacity 和 border
        for s in series_list:
            s["itemStyle"] = {"opacity": 0.85, "borderColor": "#fff", "borderWidth": 2}

    opt_json = _json.dumps(echarts_opt, ensure_ascii=False)
    # 嵌入 <script> 时转义 `</`，防止数据/列名含 `</script>` 破壳注入
    opt_json = opt_json.replace("</", "<\\/")

    # ── 子标题 ─────────────────────────────────────────
    sub = f"{_x} | {_y}"
    if _sz:
        sub += f" | size={_sz}"
    if _c:
        sub += f" | color={_c}"

    # title/sub 插入 HTML 上下文（<title>/<h1>），需 HTML 转义防注入
    import html as _html
    esc_title = _html.escape(title)
    esc_sub = _html.escape(sub)

    # ── 完整 HTML ──────────────────────────────────────
    html = (
        "<!DOCTYPE html><html lang='zh-CN'><head><meta charset='UTF-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1.0'>"
        f"<title>{esc_title}</title>"
        "<style>*{margin:0;padding:0;box-sizing:border-box}"
        "html,body{width:100%;height:100%;font-family:Heiti SC,Microsoft YaHei,sans-serif;background:#fff}"
        f"#header{{padding:12px 24px;background:#fff;border-bottom:2px solid #e8ecf0;box-shadow:0 1px 4px rgba(0,0,0,.06)}}"
        f"#header h1{{font-size:16px;color:{MCK_PRIMARY};font-weight:700}}"
        f"#header .sub{{font-size:11px;color:{MCK_GRAY};margin-top:4px}}"
        "#chart{width:100%;height:calc(100vh - 60px)}</style></head><body>"
        f"<div id='header'><h1>{esc_title}</h1><div class='sub'>{esc_sub}</div></div>"
        "<div id='chart'></div>"
        "<script src='/static/vendor/echarts.min.js'></script>"
        "<script>(function(){"
        "if(!window.echarts){document.getElementById('chart').innerHTML='ECharts加载失败';return;}"
        "var chart=echarts.init(document.getElementById('chart'),'white',{renderer:'canvas',locale:'ZH'});"
        f"var option={opt_json};"
        f"option.xAxis.axisLabel.formatter=function(v){{return {x_f};}};"
        f"option.yAxis.axisLabel.formatter=function(v){{return {y_f};}};"
        # tooltip formatter 和 label formatter 都是字符串，转成真正的函数
        "for(var i=0;i<option.series.length;i++){"
        "  var s=option.series[i];"
        "  if(s.tooltip&&typeof s.tooltip.formatter==='string'){"
        "    try{s.tooltip.formatter=eval('('+s.tooltip.formatter+')');}catch(e){}"
        "  }"
        "  if(s.label&&typeof s.label.formatter==='string'){"
        "    try{s.label.formatter=eval('('+s.label.formatter+')');}catch(e){}"
        "  }"
        "  if(s.symbolSize&&typeof s.symbolSize==='string'){"
        "    try{s.symbolSize=eval('('+s.symbolSize+')');}catch(e){}"
        "  }"
        "}"
        "chart.setOption(option);"
        "window.addEventListener('resize',function(){chart.resize();});"
        "})();</script></body></html>"
    )

    meta = {
        "chart_id": "Bubble_Plot",
        "n_rows": int(len(df)),
        "n_points": int(len(rows)),
        "x_col": _x, "y_col": _y,
        "size_col": _sz, "color_col": _c,
        "label_col": label_col,
        "color_is_category": bool(color_is_category),
        "color_is_numeric": bool(color_is_numeric),
        "x_is_pct": bool(x_is_pct),
        "y_is_pct": bool(y_is_pct),
        "x_mid": x_mid,
        "y_mid": y_mid,
        "show_label": show_label if not color_is_category else "per_category",
    }
    return ChartResult(html=html, spec={}, warnings=warnings, meta=meta)