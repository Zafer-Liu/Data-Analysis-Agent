# -*- coding: utf-8 -*-
"""
配色方案管理模块
支持多种企业配色方案（McKinsey、BCG、Bain、EY）
"""

COLOR_SCHEMES = {
    "mckinsey": {
        "name": "McKinsey Blue",
        "description": "麦肯锡蓝色配色",
        "colors": [
            "#003B71",  # 麦肯锡深蓝 - 品牌主色
            "#005CAB",  # 中蓝 - 标题/强调
            "#0083CA",  # 亮蓝 - 辅助元素
            "#00A3E0",  # 青色 - 点缀/链接
            "#7FBA00",  # 绿色 - 正向/增长
            "#FFC000",  # 金色 - 中性/提示
            "#F7630C",  # 橙色 - 警示
            "#E2231A",  # 红色 - 负向/下降
            "#A4373A",  # 深红 - 严重/强调
            "#6B2C91",  # 紫色 - 特殊/创新
        ],
        "primary": "#003D7A",
        "secondary": "#0084D1",
        "accent": "#00A4EF",
        "positive": "#7FBA00",
        "negative": "#DA3B01",
        "neutral": "#FFB81C",
    },
    "bcg": {
        "name": "BCG Green",
        "description": "波士顿咨询绿色配色",
        "colors": [
            "#006C5B",  # 深绿 - 主色
            "#009879",  # 中绿 - 次色
            "#00B398",  # 亮绿 - 辅色
            "#004A3F",  # 墨绿 - 深锚点
            "#8CC63F",  # 青柠 - 对比
            "#F2A900",  # 琥珀 - 暖色对比
            "#2C7FB8",  # 蓝青 - 冷色对比
            "#3F454A",  # 炭灰 - 中性
        ],
        "primary": "#006C5B",
        "secondary": "#009879",
        "accent": "#00B398",
        "positive": "#00B398",
        "negative": "#A6192E",
        "neutral": "#999999",
    },
    "bain": {
        "name": "Bain Red",
        "description": "贝恩红色配色",
        "colors": [
            "#E41E26",  # 主红 - 主色
            "#FF5C5C",  # 亮红 - 次色
            "#A6192E",  # 暗红 - 强调
            "#6E0E1B",  # 酒红 - 深锚点
            "#E87722",  # 橙色 - 暖色对比
            "#0F8B8C",  # 深青 - 冷色对比
            "#3F454A",  # 炭灰 - 中性
        ],
        "primary": "#E41E26",
        "secondary": "#FF5C5C",
        "accent": "#A6192E",
        "positive": "#00B398",
        "negative": "#E41E26",
        "neutral": "#999999",
    },
    "ey": {
        "name": "EY Yellow",
        "description": "安永黄色配色",
        "colors": [
            "#FFD100",  # 金黄 - 主色
            "#2E2E38",  # 炭黑 - 次色
            "#75787B",  # 中灰 - 辅色
            "#9C6F00",  # 暗金 - 深锚点
            "#00629B",  # 钢蓝 - 冷色对比
            "#E87722",  # 橙色 - 暖色对比
        ],
        "primary": "#FFD100",
        "secondary": "#2E2E38",
        "accent": "#9C6F00",
        "positive": "#7FBA00",
        "negative": "#DA3B01",
        "neutral": "#75787B",
    },
}


def get_color_scheme(scheme_name):
    """
    获取指定配色方案
    
    Args:
        scheme_name (str): 配色方案名称（mckinsey/bcg/bain/ey）
    
    Returns:
        dict: 配色方案字典，包含 colors、primary、secondary 等
    """
    return COLOR_SCHEMES.get(scheme_name, COLOR_SCHEMES["mckinsey"])


def list_color_schemes():
    """
    获取所有可用的配色方案列表
    
    Returns:
        list: 配色方案列表，每项包含 name、description、scheme_id
    """
    return [
        {
            "scheme_id": scheme_id,
            "name": scheme["name"],
            "description": scheme["description"],
            "primary_color": scheme["primary"],
        }
        for scheme_id, scheme in COLOR_SCHEMES.items()
    ]


def get_colors_list(scheme_name, count=None):
    """
    获取指定配色方案的颜色列表
    
    Args:
        scheme_name (str): 配色方案名称
        count (int, optional): 返回的颜色数量，None 表示全部
    
    Returns:
        list: 颜色列表
    """
    scheme = get_color_scheme(scheme_name)
    colors = scheme.get("colors", [])
    
    if count is None:
        return colors
    
    # 循环使用颜色以满足需求数量
    result = []
    for i in range(count):
        result.append(colors[i % len(colors)])
    return result
