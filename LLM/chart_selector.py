# -*- coding: utf-8 -*-
"""
Chart selector — registry-backed chart recommendation.

All chart metadata is embedded directly from registry.py so this module
works without any runtime path dependency.  The scoring engine uses the
full desc / data_format / constraints text from each ChartMetadata entry,
plus a keyword boost table derived from the actual registry content.

Public API
----------
select_charts(user_intent, available_columns, top_n) -> list[dict]
format_selection_result(candidates)                  -> str
"""
from __future__ import annotations

import logging
log = logging.getLogger(__name__)

from typing import List, Dict, Any, Optional


# ── Embedded registry snapshot ────────────────────────────────────────────────
# Mirrors charts/registry.py exactly.  Update both files when adding charts.
# Fields kept: chart_id, name, category, min_fields, required_roles,
#              optional_roles, data_format, constraints, desc, supports_time

_CHARTS: List[Dict[str, Any]] = [
    # ── 对比类 COMPARING ─────────────────────────────────────────────────────
    dict(
        chart_id="Marimekko_ABS", name="马里美科_ABS", category="对比类 COMPARING",
        required_roles=["x", "y", "group"], optional_roles=[],
        desc="柱宽表示第一维度占比，柱内高度表示第二维度绝对值。适合对比不同品牌的规模和内部构成",
        data_format="x列(品牌) + y列(销售额) + group列(产品类别)",
        constraints="双维占比；柱内高度为绝对值",
    ),
    dict(
        chart_id="Marimekko_PCT", name="马里美科_PCT", category="对比类 COMPARING",
        required_roles=["x", "y", "group"], optional_roles=[],
        desc="柱宽表示第一维度占比，柱内高度表示第二维度占比。适合展示相对构成关系",
        data_format="x列(品牌) + y列(销售额) + group列(产品类别)",
        constraints="双维占比；柱内高度为百分比",
    ),
    dict(
        chart_id="Bar_Chart", name="柱状图", category="对比类 COMPARING",
        required_roles=["x", "y"], optional_roles=["series", "color"],
        desc="通过矩形高度编码数值，最常用的比较图表",
        data_format="x列(类别) + y列(数值)",
        constraints="数值列≥0，y轴从零开始",
    ),
    dict(
        chart_id="Grouped_Bar_Chart", name="分组柱状图", category="对比类 COMPARING",
        required_roles=["x", "y", "series"], optional_roles=["color", "value_cols"],
        desc="同类别多分组并排显示，便于对比。支持两种数据格式：①长格式（x列+分组列+数值列）；②宽格式（x列+多个数值列，自动转换）。"
             "宽格式时：若要比较多个指标列，请在 field_mapping 中传 value_cols=[\"列A\",\"列B\",...] 明确指定要比较的列（可排除样本数等无关数值列），"
             "此时 y 和 series 可省略；若只比较单个指标则正常传 x 和 y。",
        data_format="长格式: x列(类别) + series列(分组) + y列(数值) | 宽格式: x列(类别) + 多个数值列(各列名即为分组)",
        constraints="分组数≤8；宽格式多指标对比时用 value_cols 排除量纲不同的列（如样本数）",
    ),
    dict(
        chart_id="Stacked_Bar_Chart", name="堆叠柱状图", category="对比类 COMPARING",
        required_roles=["x", "y", "series"], optional_roles=["color", "value_cols"],
        desc="堆叠分段比较，展示部分与整体关系。支持长格式和宽格式。",
        data_format="长格式: x列 + series列 + y列 | 宽格式: x列 + 多个数值列（用 y=[...] 或 value_cols=[...]）",
        constraints="数值≥0",
    ),
    dict(
        chart_id="Diverging_Bar_Chart", name="对比条形图", category="对比类 COMPARING",
        required_roles=["label", "value"], optional_roles=[],
        desc="正负对比展示，支持正负值",
        data_format="标签 + 正负值",
        constraints="支持正负值",
    ),
    dict(
        chart_id="Dot_Plot", name="点图", category="对比类 COMPARING",
        required_roles=["category", "start", "end"], optional_roles=[],
        desc="用圆点与连接线展示两个数据点在各类别间的范围和变化，视觉清爽，适合多类别差异对比。",
        data_format="类别列（Y轴） + 起点列（X轴） + 终点列（X轴）",
        constraints="建议10-50个类别；超过50个会警告；自动删除起点或终点为空的行；仅展示两个时间点，不显示中间波动",
    ),
    dict(
        chart_id="Waffle", name="华夫格", category="对比类 COMPARING",
        required_roles=["category", "value"], optional_roles=[],
        desc="10×10网格占比展示，每个单元格代表1个百分点，适合演讲和演示场景",
        data_format="类别 (category) + 数值 (value)",
        constraints="数值 ≥ 0，内部自动归一化至总和为100",
    ),
    dict(
        chart_id="Bullet_Chart", name="靶心图", category="对比类 COMPARING",
        required_roles=["label", "actual", "target"], optional_roles=["low", "medium", "high"],
        desc="目标达成率展示，KPI完成情况对比",
        data_format="类别 + 实际值 + 目标值 + 可选范围",
        constraints="KPI展示",
    ),
    dict(
        chart_id="Sankey_Chart", name="桑基图", category="对比类 COMPARING",
        required_roles=["source", "target", "value"], optional_roles=[],
        desc="展示流向和流量，适合流程展示",
        data_format="源 + 目标 + 流量",
        constraints="适合流程展示",
    ),
    dict(
        chart_id="Heatmap", name="热力图", category="对比类 COMPARING",
        required_roles=["x", "y", "value"], optional_roles=[],
        desc="通过颜色深浅展示数值大小，适合多维数据矩阵、相关性分析",
        data_format="x列 + y列 + 数值列",
        constraints="支持大量数据点",
    ),
    dict(
        chart_id="Waterfall", name="瀑布图", category="对比类 COMPARING",
        required_roles=["x", "y"], optional_roles=["type"],
        desc="展示从起点到终点的累积变化过程，适合分析各阶段增减贡献",
        data_format="x列(阶段) + y列(数值；首行为起始值，中间为增减值，末行可为总计值) [+ type列(可选：absolute/relative/total)]",
        constraints="支持正负值；至少2行数据；默认首行为absolute、末行为total；中间默认relative",
    ),

    # ── 时间趋势类 TIME ────────────────────────────────────────────────────────
    dict(
        chart_id="Line_Chart", name="折线图", category="时间趋势类 TIME",
        required_roles=["x", "y"], optional_roles=["series"],
        desc="展示数据随时间或其他连续变量的变化趋势",
        data_format="x列(时间/连续) + y列(数值)",
        constraints="适合时间序列",
    ),
    dict(
        chart_id="Circular_Line_Chart", name="圆形折线图", category="时间趋势类 TIME",
        required_roles=["x", "y"], optional_roles=["series"],
        desc="在极坐标系中展示周期性数据的变化趋势，通过将时间轴映射到圆周，使周期首尾相连，强调数据的循环特性。适合季节性、日周期等场景。",
        data_format="宽格式：第一列为周期标签（如月份、周次），其余列为数值列（可有多列，每列一条折线）",
        constraints="适合周期性时间序列（≥3个周期，建议12-52个）；线条建议2-5条；数据需完整；不适合精确数值比较",
    ),
    dict(
        chart_id="Slope_Chart", name="斜率图", category="时间趋势类 TIME",
        required_roles=["group", "start", "end"], optional_roles=[],
        desc="通过连线斜率展示两个时间点间的变化幅度和方向，用颜色编码增长(绿)与下降(红)，自动按变化幅度排序",
        data_format="group列(实体名称) + start列(起始值) + end列(终止值)",
        constraints="实体数≤30；仅支持两个时间点对比",
    ),
    dict(
        chart_id="Sparkline", name="迷你图", category="时间趋势类 TIME",
        required_roles=["x", "y"], optional_roles=[],
        desc="极简的趋势线条图，专为表格嵌入设计。为每一行数据生成紧凑的趋势迷你图，通过颜色编码快速传达数据的整体趋势方向",
        data_format="x列(时间) + y列(数值)",
        constraints="节省空间，适合多指标趋势汇总表",
    ),
    dict(
        chart_id="Bump_Chart", name="凹凸图", category="时间趋势类 TIME",
        required_roles=["x", "y", "group"], optional_roles=["highlight"],
        desc="展示多个实体的排名随时间的变化。通过相对排名而非绝对值来展示数据，适合识别黑马和掉队者。",
        data_format="x列(时间) + y列(排名/分数) + group列(实体名称)",
        constraints="实体数≤15个，自动检测，支持高亮",
    ),
    dict(
        chart_id="Cycle_Chart", name="周期图", category="时间趋势类 TIME",
        required_roles=["time", "value"], optional_roles=[],
        desc="用于展示周期性模式。支持宽格式（首列为周期，如年份；其余列为相位，如月份/类别）和长格式（time + value + group），可自动识别并绘制多条周期线及均值参考线。",
        data_format="宽格式: period列 + 多个phase列；或 长格式: time列 + value列 + group列(可选)",
        constraints="至少1列时间/周期字段与1列数值字段；若为宽格式建议首列可解析为年份/时间；其余列需可数值化",
    ),
    dict(
        chart_id="Area_Chart", name="面积图", category="时间趋势类 TIME",
        required_roles=["x", "y"], optional_roles=["series"],
        desc="折线图的填充版本，适合展示时间序列趋势及整体量级",
        data_format="x列(时间) + y列(数值)",
        constraints="适合时间序列",
    ),
    dict(
        chart_id="Stacked_Area_Chart", name="堆叠面积图", category="时间趋势类 TIME",
        required_roles=["x", "y"], optional_roles=["series"],
        desc=(
            "通过填充区域展示多个序列随时间的累积贡献，适合观察整体趋势及各组成部分占比变化。"
            "支持两种输入形式："
            "1) 宽格式：x列 + 多个数值列；"
            "2) 长格式：x列 + 单个数值列 + series分组列。"
        ),
        data_format="宽格式：x列(时间/类别) + 多个y列(数值列)；长格式：x列(时间/类别) + 单个y列(数值列) + series列(分组)",
        constraints=(
            "适合连续时间序列，x轴应可排序且数据点≥3；"
            "宽格式模式下y列数应为2-5列；"
            "长格式模式下必须为单个y列，series分组数应为2-5个；"
            "同一(x, series)组合应唯一，若存在重复应先聚合；"
            "缺失的(x, series)组合应补0；"
            "默认不适用于包含负值的堆叠场景。"
        ),
    ),
    dict(
        chart_id="Horizon_Chart", name="地平线图", category="时间趋势类 TIME",
        required_roles=["x", "y"], optional_roles=["series"],
        desc="将时间序列按幅度分层并折叠叠加的紧凑趋势图，适合在有限空间比较多条序列",
        data_format="x列(时间/顺序) + y列(数值，支持单列或多列；可选series分组)",
        constraints="需要有序x轴；y需为数值；分层(bands)越多细节越高但识别成本上升",
    ),
    dict(
        chart_id="Connected_Scatter", name="连线散点图", category="时间趋势类 TIME",
        required_roles=["x", "y"], optional_roles=["order", "size"],
        desc="在散点基础上用线段连接各点，展示数据的演变过程或轨迹。适合展示有序路径、时间序列或因果关系。",
        data_format="x列(数值) + y列(数值) + 可选size列(标记大小)",
        constraints="支持自动排序",
    ),

    # ── 分布类 DISTRIBUTION ────────────────────────────────────────────────────
    dict(
        chart_id="Histogram_Pareto_chart", name="直方图与帕累托图", category="分布类 DISTRIBUTION",
        required_roles=["value"],  # or ["x","y"] for pareto mode
        optional_roles=[],
        desc="展示数值分布情况，支持频率分布直方图（单列数值）与帕累托图（双列：类别+数值）",
        data_format="单列数值（频率分布）| 双列（类别+数值，帕累托图）",
        constraints="自动检测列数切换模式；频率分布建议数据点≥30；帕累托图自动按数值降序排列，累积百分比0-100%",
    ),
    dict(
        chart_id="Pyramid_Chart", name="金字塔图", category="分布类 DISTRIBUTION",
        required_roles=["label", "left_value", "right_value"], optional_roles=[],
        desc="对称展示两个群体在各分类上的分布对比，快速识别整体结构特征（如人口年龄性别分布）",
        data_format="标签列 + 左侧数值列 + 右侧数值列",
        constraints="左侧数值自动转负值显示，右侧为正值；标签建议升序排列；类别过多(>30)时合并相邻项；图例置于底部",
    ),
    dict(
        chart_id="Error_Bar_Chart", name="误差条形图", category="分布类 DISTRIBUTION",
        required_roles=["label", "value"], optional_roles=[],
        desc="展示分组数据的中位数与四分位数范围（Q25-Q75），直观比较各组的分布特征与变异性",
        data_format="标签列 + 数值列（原始数据，系统自动分组计算统计量）",
        constraints="系统自动按标签分组并计算Q25/Q50/Q75；误差条表示四分位数范围；每组建议≥10个数据点；悬停显示中位数、Q25、Q75及样本数",
    ),
    dict(
        chart_id="Box-and-Whisker_Plot", name="箱线图", category="分布类 DISTRIBUTION",
        required_roles=["y"], optional_roles=["x"],
        desc="展示数据的四分位数和异常值，适合对比多组分布",
        data_format="数值列 + 可选分组列",
        constraints="适合对比分布",
    ),
    dict(
        chart_id="Violin_Chart", name="小提琴图", category="分布类 DISTRIBUTION",
        required_roles=["y"], optional_roles=["x"],
        desc="结合箱线图与核密度估计，展示数据分布形态（如双峰、偏态），适用于多组对比",
        data_format="数值列(y) + 可选分类列(x)；支持宽格式数据自动转换（首列为分组，其余为数值）",
        constraints="每组数据量建议≥10，总数据量≥20",
    ),
    dict(
        chart_id="Ridgeline_Plot", name="山脊线图", category="分布类 DISTRIBUTION",
        required_roles=["group", "value"], optional_roles=[],
        desc="展示多个分组的分布形态，通过重叠密度曲线进行对比",
        data_format="group列(分类/分组名) + value列(数值/分布值)，或宽格式: 第一列为分组标签，其余列为各样本值",
        constraints="每组数据点≥5，分组数建议3-15，总数据量≥20",
    ),
    dict(
        chart_id="Beeswarm_Plot", name="分簇散点图", category="分布类 DISTRIBUTION",
        required_roles=["y"], optional_roles=["x"],
        desc="通过抖动避免点重叠，展示个体数据点的分布密度与聚集模式，支持分组对比",
        data_format="数值列 + 可选分组列（宽格式自动转换：首列分组，其余数值）",
        constraints="数据量 ≥ 20，每组建议 10–200 点，总点数不宜超过 500–1000",
    ),

    # ── 地理类 GEOSPATIAL ──────────────────────────────────────────────────────
    dict(
        chart_id="Dot_Density_Map", name="点密度地图", category="地理类 GEOSPATIAL",
        required_roles=["label", "value"], optional_roles=["category"],
        desc="用点的数量和密度表示绝对数值分布，每个点代表固定数量单位，点越密集表示总量越大",
        data_format="label + value + (可选 category)",
        constraints="地名需匹配 pyecharts 内置中国城市/区县库（3700+），value 需为绝对数值，数据为长格式（每行一个地点/分组组合）",
    ),
    dict(
        chart_id="Choropleth_Map", name="面量图", category="地理类 GEOSPATIAL",
        required_roles=["label", "value"], optional_roles=[],
        desc="用区域填充颜色的深浅表示相对数值分布，颜色越深表示数值越大，适合展示密度、比率等归一化指标",
        data_format="地区 + 相对数值（密度、比率、百分比等）",
        constraints="地名需匹配 pyecharts 内置中国城市/区县库（3700+），value 需为相对数值（非绝对总量），数据为长格式（每行一个地区）",
    ),

    # ── 关系类 RELATIONSHIP ────────────────────────────────────────────────────
    dict(
        chart_id="Scatter_Plot", name="散点图", category="关系类 RELATIONSHIP",
        required_roles=["x", "y"], optional_roles=["size", "color"],
        desc="展示两个数值变量之间的关系，支持正相关、负相关或无相关检测。可用size表示第三维度（数值），用color区分分组类别。",
        data_format="x(数值), y(数值), size(数值,可选), color(文本/数值,可选)",
        constraints="x和y必须为数值列，至少需要两个有效数据点。缺失值自动删除，大量重叠点建议使用透明度或hexbin热力图。",
    ),
    dict(
        chart_id="Bubble_Plot", name="气泡图", category="关系类 RELATIONSHIP",
        required_roles=["x", "y"], optional_roles=["size", "color", "x_mid", "y_mid"],
        desc="气泡图通过气泡的横纵坐标（x/y）、大小（size）、颜色（color）四个维度联动展示数据，适合多维度关系分析、分组聚类和象限战略定位。可通过 x_mid/y_mid 参数叠加象限分界线，用于矩阵式战略分析",
        data_format="x列(数值) + y列(数值) + [size列(数值)] + [color列(类别)]",
        constraints=(
            "x/y 必须为连续数值，不可为类别列；若未指定 size，默认 40px 中等尺寸；"
            "若未指定 color，统一使用默认主色；color 优先识别类别列，传入数值列时按数值大小着色；"
            "x_mid/y_mid 为可选象限分界线，仅在显式传入时绘制；"
            "x 范围在 0–1 之间时，轴标签自动乘以 100 显示为百分比形式；"
            "气泡数建议 5–30 个，过多时会自动添加轻微随机扰动（jitter）以减轻重叠"
        ),
    ),
    dict(
        chart_id="Chord_Diagram", name="弦图", category="关系类 RELATIONSHIP",
        required_roles=["source", "target", "value"], optional_roles=[],
        desc="展示多个实体之间的多向关系强度，节点沿圆周排列，弧线粗细表示关系强弱",
        data_format="边列表（源, 目标, 值）或邻接矩阵",
        constraints="节点建议5-15个；关系值需为正数；邻接矩阵需行列完整且对角线为0",
    ),
    dict(
        chart_id="Arc_Chart", name="弧图", category="关系类 RELATIONSHIP",
        required_roles=["x", "y", "z"], optional_roles=[],
        desc="弧形展示路径，数据标签半圆展示流出值",
        data_format="流出x + 流入y + 流出值Z",
        constraints="关系图表",
    ),
    dict(
        chart_id="Network_Diagram", name="网络图", category="关系类 RELATIONSHIP",
        required_roles=["source", "target"], optional_roles=["weight"],
        desc="展示节点和连接关系，适合网络分析",
        data_format="源 + 目标 + 可选权重",
        constraints="适合网络分析",
    ),
    dict(
        chart_id="Parallel_Coordinates_Plot", name="平行坐标图", category="关系类 RELATIONSHIP",
        required_roles=["dimensions"], optional_roles=["color"],
        desc="用多条竖直轴表示不同变量，每条线连接各轴上的点，展示多个变量之间的关系。支持标准化轴和独立范围轴。",
        data_format="多个数值列（维度）+ 可选分组列（color）",
        constraints="维度数3-6个；数据行数10-100行；所有维度列必须可转换为数值类型",
    ),

    # ── 占比图 PART-TO-WHOLE ──────────────────────────────────────────────────
    dict(
        chart_id="Treemap", name="矩形树图", category="占比图 PART-TO-WHOLE",
        required_roles=["labels", "values"], optional_roles=["parents"],
        desc="用矩形面积表示占比，支持多层级嵌套展示。适合展示有层级且数量较多的分类数据，比柱状图更节省空间。",
        data_format="可选parents列(父级) + labels列(类别名称) + values列(数值)",
        constraints="数值必须>0；行数建议≤200；支持多层级嵌套",
    ),
    dict(
        chart_id="Sunburst_Diagram", name="旭日图", category="占比图 PART-TO-WHOLE",
        required_roles=["labels", "values"], optional_roles=["parents"],
        desc="多层级占比展示，圆形分层结构展示部分与整体的关系",
        data_format="标签 + 数值（+ 可选父级标签）",
        constraints="支持多层级，parents列值需在labels列中存在；values须为正数；建议层级≤3层，行数≤200",
    ),
    dict(
        chart_id="Nightingale_Chart", name="南丁格尔玫瑰图", category="占比图 PART-TO-WHOLE",
        required_roles=["names", "values"], optional_roles=[],
        desc="极坐标扇形面积图，通过扇形面积编码数值大小。适合展示周期性数据（如12个月份、4个季度）或分类数据的占比关系，视觉冲击力强。",
        data_format="names列(类别/月份) + values列(数值≥0)",
        constraints="类别数≤12；数值≥0；不支持负数；建议数据点4-12个；各扇形面积=数值",
    ),
    dict(
        chart_id="Pie_Chart", name="饼图", category="占比图 PART-TO-WHOLE",
        required_roles=["label", "value"], optional_roles=["color"],
        desc="展示各部分占整体的比例",
        data_format="标签列 + 数值列",
        constraints="类别数≤8，总和=100%",
    ),
]


# ── Scoring engine ─────────────────────────────────────────────────────────────
#
# Each entry is (trigger_words, chart_ids, score_boost).
# trigger_words: any of these substrings in the lower-cased intent triggers boost.
# chart_ids: the charts that get the boost.
# score_boost: how much to add (default 10 = strong signal).
#
# Derived from the actual desc / data_format / constraints in the registry above.

_BOOST_TABLE: List[tuple] = [
    # — TIME / TREND —
    (["趋势", "trend", "随时间", "变化", "月度", "季度", "年度",
      "time series", "timeseries", "时序"],
     ["Line_Chart", "Area_Chart", "Stacked_Area_Chart", "Cycle_Chart", "Horizon_Chart"], 12),

    (["折线", "line chart", "line_chart"],
     ["Line_Chart"], 20),

    (["面积", "area"],
     ["Area_Chart", "Stacked_Area_Chart"], 14),

    (["堆叠面积", "stacked area"],
     ["Stacked_Area_Chart"], 20),

    (["极坐标", "圆形", "circular", "周期性折线"],
     ["Circular_Line_Chart"], 18),

    (["迷你图", "sparkline", "内嵌趋势"],
     ["Sparkline"], 20),

    (["排名变化", "排名趋势", "bump", "凹凸", "黑马", "掉队"],
     ["Bump_Chart"], 20),

    (["周期", "cycle", "季节性", "循环", "年周期", "月周期"],
     ["Cycle_Chart", "Circular_Line_Chart"], 16),

    (["地平线", "horizon", "多序列紧凑"],
     ["Horizon_Chart"], 20),

    (["连线散点", "connected scatter", "轨迹", "演变"],
     ["Connected_Scatter"], 20),

    (["两个时间点", "斜率", "slope", "变化方向", "增减排序"],
     ["Slope_Chart"], 20),

    # — COMPARISON —
    (["柱状", "柱形", "bar chart", "bar_chart", "条形"],
     ["Bar_Chart"], 20),

    (["分组柱", "grouped bar", "并排"],
     ["Grouped_Bar_Chart"], 20),

    (["堆叠柱", "stacked bar", "堆积"],
     ["Stacked_Bar_Chart"], 18),

    (["对比", "比较", "compare", "对照", "comparison", "差异"],
     ["Bar_Chart", "Grouped_Bar_Chart", "Dot_Plot", "Bullet_Chart", "Slope_Chart"], 8),

    (["正负", "diverging", "双向", "net", "负值", "反对", "赞成与反对",
      "同意与不同意", "情感对比", "正负对比", "好评差评", "支持与反对"],
     ["Diverging_Bar_Chart"], 20),

    (["点图", "dot plot", "范围变化", "起止", "起点终点"],
     ["Dot_Plot"], 18),

    (["华夫格", "waffle", "方格占比", "网格"],
     ["Waffle"], 20),

    (["kpi", "达成率", "目标达成", "靶心", "bullet", "子弹图", "子弹",
      "实际与目标", "目标对比", "实际值与目标", "绩效达成", "完成率",
      "实际vs目标", "指标达成"],
     ["Bullet_Chart"], 22),

    (["桑基", "sankey", "流向", "流量", "转移"],
     ["Sankey_Chart"], 20),

    (["热力", "heatmap", "热图", "矩阵颜色", "颜色深浅"],
     ["Heatmap"], 20),

    (["相关矩阵", "correlation matrix"],
     ["Heatmap"], 18),

    (["瀑布", "waterfall", "累积变化", "增减拆解", "bridge chart"],
     ["Waterfall"], 20),

    (["马里美科", "marimekko", "双维占比"],
     ["Marimekko_ABS", "Marimekko_PCT"], 20),

    # — DISTRIBUTION —
    (["分布", "distribution", "频率", "frequency"],
     ["Histogram_Pareto_chart", "Box-and-Whisker_Plot", "Violin_Chart",
      "Ridgeline_Plot", "Beeswarm_Plot"], 10),

    (["直方图", "histogram", "频率分布"],
     ["Histogram_Pareto_chart"], 20),

    (["帕累托", "pareto", "二八", "80/20"],
     ["Histogram_Pareto_chart"], 20),

    (["箱线", "box plot", "boxplot", "须图", "四分位"],
     ["Box-and-Whisker_Plot"], 20),

    (["小提琴", "violin", "密度分布", "核密度"],
     ["Violin_Chart"], 20),

    (["山脊线", "ridgeline", "ridge plot", "多组密度"],
     ["Ridgeline_Plot"], 20),

    (["分簇散点", "beeswarm", "蜂群", "抖动点"],
     ["Beeswarm_Plot"], 20),

    (["金字塔", "pyramid", "年龄结构", "人口结构", "性别对比"],
     ["Pyramid_Chart"], 20),

    (["误差条", "error bar", "置信区间", "q25", "q75", "四分位范围"],
     ["Error_Bar_Chart"], 20),

    # — GEOSPATIAL —
    (["地图", "map", "地理", "geo", "省份", "城市分布", "区域分布"],
     ["Choropleth_Map", "Dot_Density_Map"], 16),

    (["面量图", "choropleth", "填充地图", "密度比率"],
     ["Choropleth_Map"], 20),

    (["点密度", "dot density", "绝对数量地图"],
     ["Dot_Density_Map"], 20),

    # — RELATIONSHIP —
    (["散点图", "散点", "scatter", "scatter plot", "相关性", "correlation",
      "两变量关系", "变量关系", "变量之间的关系", "的关系", "之间关系",
      "relationship between", "association between"],
     ["Scatter_Plot"], 22),

    (["相关性", "correlation", "两变量关系"],
     ["Bubble_Plot"], 8),

    (["气泡", "bubble", "象限", "quadrant", "矩阵战略", "bcg矩阵", "四象限"],
     ["Bubble_Plot"], 20),

    (["弦图", "chord", "多向关系", "互流"],
     ["Chord_Diagram"], 20),

    (["弧图", "arc chart"],
     ["Arc_Chart"], 20),

    (["网络图", "network", "节点", "关系图谱", "知识图谱"],
     ["Network_Diagram"], 20),

    (["平行坐标", "parallel coordinates", "多维变量", "多特征对比"],
     ["Parallel_Coordinates_Plot"], 20),

    # — PART-TO-WHOLE —
    (["占比", "比例", "proportion", "share", "构成", "part to whole", "整体"],
     ["Pie_Chart", "Treemap", "Sunburst_Diagram", "Nightingale_Chart", "Waffle"], 8),

    (["饼图", "pie chart", "pie_chart"],
     ["Pie_Chart"], 20),

    (["树图", "treemap", "矩形树", "层级占比"],
     ["Treemap"], 20),

    (["旭日图", "sunburst", "多层级占比", "圆形层级"],
     ["Sunburst_Diagram"], 20),

    (["玫瑰图", "nightingale", "南丁格尔", "极坐标面积"],
     ["Nightingale_Chart"], 20),
]

# ── Column-shape signals ──────────────────────────────────────────────────────
#
# 列名是比 user_intent 更可靠的图表信号（user_intent 由 LLM 自由组织，措辞不稳定；
# 列名直接来自数据）。每条规则：(列名子串集合, 命中阈值, 图表ID, 加分)。
# 当 available_columns 中匹配到 >= 命中阈值 个不同子串时，给对应图表加分。

_COLUMN_PATTERN_TABLE: List[tuple] = [
    # 实际 + 目标 + 等级基准 → 靶心图（Bullet）的标志性数据形状
    (["实际", "目标", "及格", "良好", "优秀", "actual", "target",
      "low", "medium", "high", "基准", "阈值"], 3, "Bullet_Chart", 25),
    # 列名带"负值"后缀 / 正负成对 → 对比条形图（Diverging）
    (["负值", "正值", "_neg", "_pos"], 2, "Diverging_Bar_Chart", 25),
    # source + target → 桑基图 / 网络图 / 弦图
    (["source", "target", "源", "目标节点", "from", "to"], 2, "Sankey_Chart", 12),
    (["source", "target", "源", "目标节点"], 2, "Network_Diagram", 10),
    # left/right 成对 → 金字塔图
    (["left", "right", "男", "女", "male", "female", "左", "右"], 2, "Pyramid_Chart", 18),
    # start + end → 斜率图 / 点图
    (["start", "end", "起点", "终点", "期初", "期末", "before", "after"], 2, "Slope_Chart", 14),
]


# Build a fast lookup: chart_id -> chart dict
_CHART_INDEX: Dict[str, Dict[str, Any]] = {c["chart_id"]: c for c in _CHARTS}

_MINIMUM_VALID_SCORE = 1


def _score_chart(chart: Dict[str, Any], intent_lower: str, col_lower: List[str]) -> int:
    score = 0
    cid = chart["chart_id"]

    # 1. Keyword boost table (intent text)
    for trigger_words, chart_ids, boost in _BOOST_TABLE:
        if cid in chart_ids:
            if any(w in intent_lower for w in trigger_words):
                score += boost

    # 1b. Column-shape signals (more reliable than free-text intent)
    cols_joined = " ".join(col_lower)
    for substrs, threshold, target_cid, boost in _COLUMN_PATTERN_TABLE:
        if cid != target_cid:
            continue
        hits = sum(1 for s in substrs if s in cols_joined)
        if hits >= threshold:
            score += boost

    # 2. Full-text match against desc + data_format + constraints
    haystack = " ".join([
        chart["name"],
        chart["desc"],
        chart["data_format"],
        chart["constraints"],
    ]).lower()
    for token in intent_lower.split():
        if len(token) >= 2 and token in haystack:
            score += 4

    # 3. Column name affinity: does any column hint match a required role?
    roles_flat: List[str] = []
    rr = chart["required_roles"]
    if rr and isinstance(rr[0], list):
        for sub in rr:
            roles_flat.extend(sub)
    else:
        roles_flat = list(rr)

    for role in roles_flat:
        role_l = role.lower()
        for col in col_lower:
            if role_l in col or col in role_l:
                score += 3

    return score


# ── Public API ─────────────────────────────────────────────────────────────────

def select_charts(
    user_intent: str,
    available_columns: Optional[List[str]] = None,
    top_n: int = 3,
) -> List[Dict[str, Any]]:
    """Return up to top_n best-matching chart candidates, ranked by relevance.

    Each result dict contains all information the LLM needs to call
    generate_chart correctly:
      chart_id, name, category, required_roles, optional_roles,
      data_format, constraints, desc
    """
    intent_lower = user_intent.lower()
    col_lower = [c.lower() for c in (available_columns or [])]

    scored = []
    for chart in _CHARTS:
        score = _score_chart(chart, intent_lower, col_lower)
        if score >= _MINIMUM_VALID_SCORE:
            scored.append((chart, score))
    scored.sort(key=lambda t: -t[1])

    results = []
    for chart, sc in scored[:top_n]:
        # Flatten nested required_roles for display
        rr = chart["required_roles"]
        if rr and isinstance(rr[0], list):
            roles_display = [" OR ".join(r) for r in rr]
        else:
            roles_display = list(rr)

        results.append({
            "chart_id":      chart["chart_id"],
            "name":          chart["name"],
            "category":      chart["category"],
            "required_roles": roles_display,
            "optional_roles": list(chart["optional_roles"]),
            "data_format":   chart["data_format"],
            "constraints":   chart["constraints"],
            "desc":          chart["desc"],
            "_score":        sc,   # included for debugging, ignored by LLM
        })
    return results


def format_selection_result(candidates: List[Dict[str, Any]]) -> str:
    """Format candidates as structured text for the LLM tool response.

    Tells the LLM:
    - which chart_id to use
    - the exact field_mapping keys (required_roles)
    - the expected data shape (data_format)
    - any constraints to watch out for
    """
    if not candidates:
        return (
            "No matching charts found in the registry. "
            "Use `ask_user` to confirm the visualization goal "
            "before selecting a chart."
        )

    lines: List[str] = [
        "根据用户需求，从图表注册表中匹配到以下候选图表（按相关度排序）。",
        "请选择最合适的一个，并**严格使用 `required_roles` 中列出的 key 构造 `field_mapping`**。",
        "",
    ]

    for i, c in enumerate(candidates, 1):
        score_hint = f"  *(score={c['_score']})*" if c.get("_score", 0) > 0 else ""
        lines.append(f"### 选项 {i}：{c['name']}  (`{c['chart_id']}`){score_hint}")
        lines.append(f"- **分类**：{c['category']}")
        lines.append(f"- **用途**：{c['desc']}")
        lines.append(f"- **required_roles → field_mapping keys**：`{c['required_roles']}`")
        if c["optional_roles"]:
            lines.append(f"- **optional_roles**：`{c['optional_roles']}`")
        lines.append(f"- **数据格式**：{c['data_format']}")
        if c["constraints"]:
            lines.append(f"- **约束**：{c['constraints']}")
        lines.append("")

    lines += [
        "---",
        "**下一步操作**：",
        "1. 选定 `chart_id`",
        "2. 用 `query_data` 确认 SQL 结果列名与 `required_roles` 一一对应",
        "3. 调用 `generate_chart`，`field_mapping` 的 key 必须完全来自上面的 `required_roles`",
    ]
    return "\n".join(lines)
