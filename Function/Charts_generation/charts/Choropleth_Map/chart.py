"""
面量图 Choropleth Map - 地理
图表分类: 地理 Geographic
感知排名: ★★★★☆

统一接口:
    generate(df, mapping, options) -> ChartResult

面量图用区域填充颜色的深浅表示数值大小。
白色底图，给定地区数据则改变该地区的颜色，颜色深浅代表数据大小。
"""
import logging
log = logging.getLogger(__name__)
import html as _html
import sys
from pathlib import Path
from typing import Dict, Any, Optional

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
            "#B9D6E8", "#7FB5D5", "#0084D1", "#003D7A", "#001F3F",
        ]}

__all__ = ["generate"]

_DATA_FMT = "label列(地区名) + value列(数值)"
_DESC = "白色底图，按地区数据改变颜色，颜色深浅代表数值大小。"


def _fuzzy_match_region(name: str) -> str:
    """模糊匹配地区名。如果用户输入 "北京"，自动扩展为 "北京市"。"""
    abbr_map = {
        '北京': '北京市',
        '上海': '上海市',
        '天津': '天津市',
        '重庆': '重庆市',
        '广州': '广州市',
        '深圳': '深圳市',
        '杭州': '杭州市',
        '南京': '南京市',
        '武汉': '武汉市',
        '成都': '成都市',
        '西安': '西安市',
        '苏州': '苏州市',
        '郑州': '郑州市',
        '长沙': '长沙市',
        '沈阳': '沈阳市',
        '青岛': '青岛市',
        '大连': '大连市',
        '宁波': '宁波市',
        '厦门': '厦门市',
        '福州': '福州市',
        '济南': '济南市',
        '哈尔滨': '哈尔滨市',
        '长春': '长春市',
        '太原': '太原市',
        '石家庄': '石家庄市',
        '贵阳': '贵阳市',
        '昆明': '昆明市',
        '南昌': '南昌市',
        '合肥': '合肥市',
        '兰州': '兰州市',
        '银川': '银川市',
        '西宁': '西宁市',
        '乌鲁木齐': '乌鲁木齐市',
        '浙江': '浙江省',
        '江苏': '江苏省',
        '广东': '广东省',
        '海南': '海南省',
        '山东': '山东省',
        '四川': '四川省',
        '湖北': '湖北省',
        '湖南': '湖南省',
        '河南': '河南省',
        '河北': '河北省',
        '山西': '山西省',
        '陕西': '陕西省',
        '安徽': '安徽省',
        '江西': '江西省',
        '福建': '福建省',
        '云南': '云南省',
        '贵州': '贵州省',
        '青海': '青海省',
        '甘肃': '甘肃省',
        '黑龙江': '黑龙江省',
        '吉林': '吉林省',
        '辽宁': '辽宁省',
        '内蒙古': '内蒙古自治区',
        '广西': '广西壮族自治区',
        '西藏': '西藏自治区',
        '新疆': '新疆维吾尔自治区',
        '宁夏': '宁夏回族自治区',
        '香港': '香港特别行政区',
        '澳门': '澳门特别行政区',
        '台湾': '台湾省',
    }
    return abbr_map.get(name, name)


def _is_string_col(s: pd.Series) -> bool:
    """判断列是否为字符串类型（兼容 object/string/str dtype）。"""
    if pd.api.types.is_numeric_dtype(s):
        return False
    if pd.api.types.is_datetime64_any_dtype(s):
        return False
    return True


def _detect_label_col(df: pd.DataFrame, exclude: set) -> Optional[str]:
    """自动识别地区名列（首个字符串列）。"""
    for c in df.columns:
        if c not in exclude and _is_string_col(df[c]):
            return c
    return None


def _detect_value_col(df: pd.DataFrame, exclude: set, hint: str = None) -> Optional[str]:
    """识别数值列。"""
    if hint:
        for c in df.columns:
            if c not in exclude and str(c).lower() == str(hint).lower():
                return c
    for c in df.columns:
        if c not in exclude and pd.api.types.is_numeric_dtype(df[c]):
            return c
    return None


def generate(
    df: pd.DataFrame = None,
    mapping: Dict[str, str] = None,
    options: Dict[str, Any] = None,
    excel_path: str = None,
    label: str = None,
    value: str = "value",
    title: str = "面量图",
    maptype: str = "china",
    **kwargs,
) -> ChartResult:
    """
    参数说明：
        label      : 地区名列名
        value      : 数值列名
        maptype    : 'china' | 省名 | 市名
    """
    from pyecharts.charts import Map
    from pyecharts import options as opts

    warnings_: list = []
    options = options or {}
    mapping = mapping or {}

    # ── 读取数据 ──────────────────────────────────────────
    if df is None:
        if excel_path:
            try:
                df = pd.read_excel(excel_path)
            except Exception as e:
                log.warning("[chart] 图表生成异常: %s", e)
                return ChartResult(warnings=[f"读取Excel失败: {e}"])
        else:
            return ChartResult(warnings=["请提供 df 或 excel_path"])

    # ── 参数解析 ──────────────────────────────────────────
    lbl_hint = mapping.get("label")
    lbl_hint = lbl_hint if lbl_hint is not None else label
    val_hint = mapping.get("value")
    val_hint = val_hint if val_hint is not None else value
    title = options.get("title", title)
    maptype = options.get("maptype", maptype)

    # ── 列识别 ────────────────────────────────────────────
    used: set = set()
    
    # 先检测省市列（用于 maptype）
    _city = None
    for c in df.columns:
        c_lower = str(c).lower()
        if _is_string_col(df[c]) and c_lower in ['省市', '城市', '市', 'city', 'province']:
            _city = c
            used.add(_city)
            break
    
    # 再检测地区名列（label）
    _label = lbl_hint if (lbl_hint and lbl_hint in df.columns) else _detect_label_col(df, used)
    if _label:
        used.add(_label)

    # 自动提取市名作为 maptype
    if _city:
        city_val = str(df[_city].iloc[0]).strip()
        city_val = _fuzzy_match_region(city_val)
        # 移除"市"后缀用于 maptype
        if city_val.endswith('市'):
            maptype = city_val[:-1]
        else:
            maptype = city_val

    _value = _detect_value_col(df, used, val_hint)
    if _value:
        used.add(_value)

    # ── 验证必要字段 ──────────────────────────────────────
    if _label is None:
        warnings_.append("找不到地区名列 [label]")
        return ChartResult(warnings=warnings_)
    if _value is None:
        warnings_.append("找不到数值列 [value]")
        return ChartResult(warnings=warnings_)

    # ── 构建数据 ──────────────────────────────────────────
    pairs: list = []  # (地名, 数值)

    for _, row in df.iterrows():
        try:
            name = str(row[_label]).strip()
            # 模糊匹配：缩写 -> 完整名
            name = _fuzzy_match_region(name)
            val = float(row[_value])
            # 四舍五入到两位小数
            val = round(val, 2)
        except Exception as e:
            log.warning("[chart] 图表生成异常: %s", e)
            warnings_.append(f"行数据解析失败: {e}")
            continue

        pairs.append((name, val))

    if not pairs:
        warnings_.append("没有有效数据点")
        return ChartResult(warnings=warnings_)

    # ── 值域 ──────────────────────────────────────────────
    vals_only = [v for _, v in pairs]
    vmin, vmax = min(vals_only), max(vals_only)

    # ── 构建 Map（面量图）────────────────────────────────
    # 配色方案：优先用 options.color_scheme
    _scheme = get_color_scheme(options.get("color_scheme", "mckinsey"))
    _palette = _scheme.get("colors") or ["#B9D6E8", "#7FB5D5", "#0084D1", "#003D7A", "#001F3F"]

    map_chart = Map()
    map_chart.add(
        series_name=_value,
        data_pair=pairs,
        is_map_symbol_show=False,
        maptype=maptype,
    )

    map_chart.set_global_opts(
        title_opts=opts.TitleOpts(
            title=title,
            title_textstyle_opts=opts.TextStyleOpts(
                color="#003D7A", font_size=18,
                font_family="Heiti SC, Microsoft YaHei, sans-serif",
                font_weight="bold",
            ),
        ),
        visualmap_opts=opts.VisualMapOpts(
            min_=vmin,
            max_=vmax,
            is_piecewise=False,
            range_color=_palette,
            textstyle_opts=opts.TextStyleOpts(color="#333"),
            pos_left="left",
            pos_bottom="20",
        ),
        tooltip_opts=opts.TooltipOpts(
            trigger="item",
            formatter=f"{{b}}<br/>{_value}: {{c}}",
        ),
    )

    # ── 导出 HTML ─────────────────────────────────────────
    raw_html = map_chart.render_embed()

    # title/列名 用户/LLM 可控，HTML 转义防注入
    esc_title = _html.escape(str(title))
    esc_sub = _html.escape(f"共 {len(pairs)} 个地区 · 字段: {_value} · 范围: {vmin:.2f} ~ {vmax:.2f}")

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{esc_title}</title>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
html, body {{ width: 100%; height: 100%;
  font-family: "Heiti SC", "Microsoft YaHei", sans-serif;
  background: #fff; }}
#header {{
  padding: 14px 24px; background: #fff;
  border-bottom: 1px solid #e8ecf0;
  box-shadow: 0 1px 4px rgba(0,0,0,.05);
}}
#header h1 {{ font-size: 18px; color: #003D7A; font-weight: 700; }}
#header .sub {{ font-size: 12px; color: #999; margin-top: 3px; }}
#chart {{ width: 100%; height: calc(100vh - 60px); }}
</style>
</head>
<body>
<div id="header">
  <h1>{esc_title}</h1>
  <div class="sub">{esc_sub}</div>
</div>
<div id="chart">
{raw_html}
</div>
</body>
</html>"""

    meta = {
        "chart_id": "choropleth_map",
        "n_rows": len(df),
        "n_regions": len(pairs),
        "label_col": _label,
        "value_col": _value,
        "maptype": maptype,
        "value_min": vmin,
        "value_max": vmax,
    }

    return ChartResult(html=html, spec={}, warnings=warnings_, meta=meta)
