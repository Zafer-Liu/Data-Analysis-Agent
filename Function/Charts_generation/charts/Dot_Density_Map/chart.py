"""
点密度地图 Dot Density Map - 地理
图表分类: 地理 Geographic
感知排名: ★★★★☆

统一接口:
    generate(df, mapping, options) -> ChartResult

逻辑：
- 自动识别市列，调取市地图
- 第一个数值列 → 散点着色
- 第二个数值列（可选）→ 散点大小
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

_DATA_FMT = "市列 + 地区名列 + 数值列1 [+ 数值列2(可选)]"
_DESC = "散点图：第一列数值着色，第二列数值控制点大小。"


def _fuzzy_match_region(name: str) -> str:
    """模糊匹配地区名。"""
    abbr_map = {
        '北京': '北京市', '上海': '上海市', '天津': '天津市', '重庆': '重庆市',
        '广州': '广州市', '深圳': '深圳市', '杭州': '杭州市', '南京': '南京市',
        '武汉': '武汉市', '成都': '成都市', '西安': '西安市', '苏州': '苏州市',
        '郑州': '郑州市', '长沙': '长沙市', '沈阳': '沈阳市', '青岛': '青岛市',
        '大连': '大连市', '宁波': '宁波市', '厦门': '厦门市', '福州': '福州市',
        '济南': '济南市', '哈尔滨': '哈尔滨市', '长春': '长春市', '太原': '太原市',
        '石家庄': '石家庄市', '贵阳': '贵阳市', '昆明': '昆明市', '南昌': '南昌市',
        '合肥': '合肥市', '兰州': '兰州市', '银川': '银川市', '西宁': '西宁市',
        '乌鲁木齐': '乌鲁木齐市',
        '浙江': '浙江省', '江苏': '江苏省', '山东': '山东省', '四川': '四川省',
        '湖北': '湖北省', '湖南': '湖南省', '河南': '河南省', '河北': '河北省',
        '山西': '山西省', '陕西': '陕西省', '安徽': '安徽省', '江西': '江西省',
        '福建': '福建省', '云南': '云南省', '贵州': '贵州省', '青海': '青海省',
        '甘肃': '甘肃省', '黑龙江': '黑龙江省', '吉林': '吉林省', '辽宁': '辽宁省',
        '内蒙古': '内蒙古自治区', '广西': '广西壮族自治区', '西藏': '西藏自治区',
        '新疆': '新疆维吾尔自治区', '宁夏': '宁夏回族自治区',
        '香港': '香港特别行政区', '澳门': '澳门特别行政区', '台湾': '台湾省',
    }
    return abbr_map.get(name, name)


def _is_string_col(s: pd.Series) -> bool:
    """判断列是否为字符串类型。"""
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


# 预定义各区县中心坐标 [lng, lat]
_REGION_COORDS = {
    # 宜昌市
    '夷陵区': [111.32, 30.77], '西陵区': [111.27, 30.70],
    '伍家岗区': [111.35, 30.65], '点军区': [111.00, 30.72],
    '猇亭区': [111.42, 30.53], '枝江市': [111.77, 30.43],
    '当阳市': [111.78, 30.82], '宜都市': [111.45, 30.40],
    '远安县': [111.63, 31.07], '兴山县': [110.75, 31.35],
    '秭归县': [110.98, 30.83], '长阳土家族自治县': [111.18, 30.47],
    '五峰土家族自治县': [110.67, 30.20],
    # 武汉
    '江岸区': [114.30, 30.60], '江汉区': [114.27, 30.59],
    '硚口区': [114.27, 30.57], '汉阳区': [114.23, 30.55],
    '武昌区': [114.31, 30.56], '青山区': [114.39, 30.63],
    '洪山区': [114.31, 30.50], '黄陂区': [114.30, 30.79],
    '新洲区': [114.79, 30.84], '江夏区': [114.31, 30.35],
    '蔡甸区': [113.96, 30.58], '汉南区': [113.97, 30.31],
}


def generate(
    df: pd.DataFrame = None,
    mapping: Dict[str, str] = None,
    options: Dict[str, Any] = None,
    excel_path: str = None,
    city: str = None,
    label: str = None,
    value: str = None,
    title: str = "点密度地图",
    **kwargs,
) -> ChartResult:
    """
    参数说明：
        city   : 市列名
        label  : 地区名列名
        value  : 数值列名（第一列用于着色）
    """
    from pyecharts.charts import Geo
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

    city_hint = mapping.get("city")
    city_hint = city_hint if city_hint is not None else city
    lbl_hint = mapping.get("label")
    lbl_hint = lbl_hint if lbl_hint is not None else label
    val_hint = mapping.get("value")
    val_hint = val_hint if val_hint is not None else value
    title = options.get("title", title)

    # ── 列识别 ────────────────────────────────────────────
    used: set = set()

    # 识别市列（模糊匹配，支持 city/城市/城市名/市/省市 等变体）
    _city_keywords = ['市', 'city', '城市', '省市', '城市名', '所在市', 'prefecture']
    _city = None
    if city_hint and city_hint in df.columns:
        _city = city_hint
    else:
        for c in df.columns:
            c_lower = str(c).lower()
            if any(kw in c_lower for kw in _city_keywords):
                _city = c
                break

    # 若仍未找到，city 列可选——取第一个字符串列作为地区名即可，city 可为 None
    if _city:
        used.add(_city)

    # 识别地区名列
    _label = lbl_hint if (lbl_hint and lbl_hint in df.columns) else _detect_label_col(df, used)
    if _label is None and _city:
        # 只有一个字符串列（city列），用它同时作为地区名列（全国地图散点）
        _label = _city
    if _label:
        used.add(_label)
    else:
        return ChartResult(warnings=["找不到地区名列 [label]"])

    # 识别数值列（可能有多个）
    _value1 = _detect_value_col(df, used, val_hint)
    if _value1:
        used.add(_value1)
    else:
        return ChartResult(warnings=["找不到数值列 [value]"])

    _value2 = _detect_value_col(df, used)  # 第二个数值列（可选）

    # ── 提取市名作为 maptype ──────────────────────────────
    if _city and _city != _label:
        # city列 与 label列 不同：city列是地图范围（如"武汉"→武汉市地图）
        city_val = str(df[_city].iloc[0]).strip()
        city_val = _fuzzy_match_region(city_val)
        maptype = city_val[:-1] if city_val.endswith('市') else city_val
    else:
        # 没有独立city列，或city列即label列：退化为全国地图
        maptype = "china"

    # ── 构建散点数据（使用 pyecharts 内置坐标库 COORDINATES）──────
    from pyecharts.datasets import COORDINATES

    scatter_data: list = []   # [(name, val1), ...]
    extra_coords: list = []  # [(name, lng, lat), ...] 需要手动注册坐标的点
    val1_list: list = []
    missing_regions: list = []

    for _, row in df.iterrows():
        try:
            raw_name = str(row[_label]).strip()
            name = _fuzzy_match_region(raw_name)
            val1 = round(float(row[_value1]), 2)
        except Exception as e:
            log.warning("[chart] 图表生成异常: %s", e)
            warnings_.append(f"行解析失败: {e}")
            continue

        # 查询 pyecharts 内置坐标库（COORDINATES 是 FuzzyDict，支持模糊匹配）
        coord = COORDINATES.get(name)
        if coord is None:
            # 尝试去掉"区"/"县"/"市"后缀再查一次
            for suffix in ['区', '县', '市', '镇']:
                if name.endswith(suffix):
                    alt = COORDINATES.get(name[:-1])
                    if alt is not None:
                        coord = alt
                        # 用原始名字保留（不改名），注册坐标给 Geo
                        extra_coords.append((name, coord[0], coord[1]))
                        break
        if coord is None:
            missing_regions.append(name)
            continue

        scatter_data.append((name, val1))
        val1_list.append(val1)

    if missing_regions:
        warnings_.append(f"以下地区未找到坐标（已跳过）: {', '.join(missing_regions[:10])}"
                         + (f" 等{len(missing_regions)}处" if len(missing_regions) > 10 else ""))

    if not scatter_data:
        return ChartResult(warnings=["没有有效数据点，请检查地区名是否在 pyecharts 坐标库中"])

    vmin1, vmax1 = min(val1_list), max(val1_list)

    # ── 构建 Geo + Scatter ────────────────────────────────
    # 配色方案：优先用 options.color_scheme
    _scheme = get_color_scheme(options.get("color_scheme", "mckinsey"))
    _palette = _scheme.get("colors") or ["#B9D6E8", "#7FB5D5", "#0084D1", "#003D7A", "#001F3F"]

    geo = Geo()
    geo.add_schema(maptype=maptype)

    # 注册 COORDINATES 中无法直接查到的坐标（如带后缀的区县名）
    for ec_name, ec_lng, ec_lat in extra_coords:
        geo.add_coordinate(ec_name, ec_lng, ec_lat)

    geo.add(
        series_name=_value1,
        data_pair=scatter_data,   # 已是 [(name, val1), ...] 格式
        type_="effectScatter",
        symbol_size=20,
        itemstyle_opts=opts.ItemStyleOpts(opacity=0.9),
    )

    geo.set_global_opts(
        title_opts=opts.TitleOpts(
            title=title,
            title_textstyle_opts=opts.TextStyleOpts(
                color="#003D7A", font_size=18,
                font_family="Heiti SC, Microsoft YaHei, sans-serif",
                font_weight="bold",
            ),
        ),
        visualmap_opts=opts.VisualMapOpts(
            min_=vmin1,
            max_=vmax1,
            is_piecewise=False,
            range_color=_palette,
            textstyle_opts=opts.TextStyleOpts(color="#333"),
            pos_left="left",
            pos_bottom="20",
        ),
        tooltip_opts=opts.TooltipOpts(trigger="item", formatter="{b}"),
    )

    sub_parts = [f"共 {len(scatter_data)} 个地区（maptype: {maptype}）"]
    sub_parts.append(f"字段: {_value1} · 范围: {vmin1:.2f} ~ {vmax1:.2f}")
    if _value2:
        sub_parts.append(f"附加: {_value2}")

    raw_html = geo.render_embed()

    # title/列名 用户/LLM 可控，HTML 转义防注入
    esc_title = _html.escape(str(title))
    esc_sub = _html.escape(" · ".join(sub_parts))

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
        "chart_id": "dot_density_map",
        "n_rows": len(df),
        "n_points": len(scatter_data),
        "city_col": _city,
        "label_col": _label,
        "value_col": _value1,
        "value2_col": _value2,
        "maptype": maptype,
        "value_min": vmin1,
        "value_max": vmax1,
    }

    return ChartResult(html=html, spec={}, warnings=warnings_, meta=meta)
