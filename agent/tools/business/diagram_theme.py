# -*- coding: utf-8 -*-
"""
概念图（drawio）主题层与质检 —— 与图表侧 charts/theme.py 同一套纪律

图表管道的视觉纪律（语义角色 + 配色方案 + QC 自愈）延伸到概念图通道：
LLM 与 DIAGRAM_TEMPLATES 按 DIAGRAM_RULES 写 drawio 默认调色板的六个"槽位"
（blue/green/yellow/red/purple/grey），本模块在落盘前把这些槽位确定性映射
到会话配色方案（mckinsey/bcg/bain/ey，与图表共用 charts.theme.semantic_roles）。

好处：
- 概念图与数据图表在同一会话里视觉同源（同一方案、同一中性色系）
- LLM 提示零变化 —— 它继续写槽位色，不写品牌色，换肤由渲染侧完成
- 模板 XML 不用改：模板里的槽位色同样被重映射

qc_diagram 提供复杂度预算与结构检查（advisory-only，绝不把能渲染的图
变成 error），warnings 随工具结果回传，LLM 同轮可修。
"""
from __future__ import annotations

import logging
import xml.etree.ElementTree as ET

log = logging.getLogger(__name__)

__all__ = ["apply_diagram_theme", "qc_diagram"]


def _semantic_roles(scheme_name: str) -> dict:
    """复用图表侧语义角色（agent.prompts 已引导 sys.path；这里再兜底一次）。"""
    try:
        from charts.theme import semantic_roles
        return semantic_roles(scheme_name)
    except ImportError:
        import sys
        from pathlib import Path

        _charts_gen = Path(__file__).resolve().parents[3] / "Function" / "Charts_generation"
        _p = str(_charts_gen)
        if _p not in sys.path:
            sys.path.insert(0, _p)
        from charts.theme import semantic_roles
        return semantic_roles(scheme_name)


# drawio 默认调色板槽位 → 方案序列位。LLM/模板只允许写这六个槽位
# （见 DIAGRAM_RULES "Style reference"），渲染侧统一换肤。
_SLOT_ORDER = ["blue", "green", "yellow", "red", "purple"]
_FILL_SLOTS = {"#dae8fc": 0, "#d5e8d4": 1, "#fff2cc": 2, "#f8cecc": 3, "#e1d5e7": 4}
_STROKE_SLOTS = {"#6c8ebf": 0, "#82b366": 1, "#d6b656": 2, "#b85450": 3, "#9673a6": 4}
_FONT_SLOTS = _STROKE_SLOTS  # 模板占位文字色跟随对应描边槽位
_GREY_FILL, _GREY_STROKE = "#f5f5f5", "#666666"

# 槽位填充 = 序列色调淡（pastel），保证 ink 文字可读；描边/文字用序列色原强度
_TINT_FACTOR = 0.25


def _tint(hex_color: str, factor: float = _TINT_FACTOR) -> str:
    """向白色混合：保留 factor 比例的原色（0.25 → 25% 原色 + 75% 白）。"""
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    mix = lambda c: int(round(c * factor + 255 * (1 - factor)))  # noqa: E731
    return "#{:02x}{:02x}{:02x}".format(mix(r), mix(g), mix(b))


def _restyle(style: str, roles: dict) -> str:
    """把 style 串里的槽位色映射为方案色；非槽位色原样保留。"""
    parts = style.split(";")
    out = []
    for part in parts:
        if "=" not in part:
            out.append(part)
            continue
        key, _, value = part.partition("=")
        v = value.strip().lower()
        if key == "fillColor":
            if v in _FILL_SLOTS:
                value = _tint(roles["series"][_FILL_SLOTS[v]])
            elif v == _GREY_FILL:
                value = "#f7f8fa"
        elif key == "strokeColor":
            if v in _STROKE_SLOTS:
                value = roles["series"][_STROKE_SLOTS[v]]
            elif v == _GREY_STROKE:
                value = roles["muted"]
        elif key == "fontColor":
            if v in _FONT_SLOTS:
                value = roles["series"][_FONT_SLOTS[v]]
            elif v == _GREY_STROKE:
                value = roles["muted"]
        out.append(f"{key}={value}")
    return ";".join(out)


def apply_diagram_theme(xml: str, scheme_name: str = "mckinsey") -> tuple[str, list[str]]:
    """把 drawio XML 里的调色板槽位重映射为会话配色方案。

    返回 ``(xml, warnings)``。任何内部异常都回退为原 XML —— 主题绝不把
    能渲染的图变成 error。
    """
    if not xml or "<mxCell" not in xml:
        return xml, []
    try:
        roles = _semantic_roles(scheme_name)
        if not roles.get("series"):
            return xml, []
        root = ET.fromstring(xml)
        themed = 0
        for cell in root.iter("mxCell"):
            style = cell.get("style") or ""
            if not any(
                k in style for k in ("fillColor=", "strokeColor=", "fontColor=")
            ):
                continue
            new_style = _restyle(style, roles)
            if new_style != style:
                cell.set("style", new_style)
                themed += 1
        if not themed:
            return xml, []
        return ET.tostring(root, encoding="unicode"), []
    except Exception as exc:  # pragma: no cover —— 主题兜底
        log.debug("diagram theme skipped: %s", exc)
        return xml, []


# ── 概念图质检（复杂度预算 + 结构检查） ──────────────────────────────────────

# 预算对齐图表侧 chart_qc 的量级：概念图讲"一图一讯息"，超过即提示拆分
_VERTEX_BUDGET = 60
_EDGE_BUDGET = 80
_FONT_MIN, _FONT_MAX = 8, 24


def qc_diagram(xml: str) -> list[str]:
    """对 drawio XML 做建议级检查，返回中文 warnings（可能为空）。

    检查项：节点/连线超预算、悬空连线（缺 source 或 target）、孤立节点
    （仅在图里已有连线时才提示，避免把 BMC 等无连线框架误报）、字号越界。
    """
    warnings: list[str] = []
    try:
        if not xml or "<mxCell" not in xml:
            return warnings
        root = ET.fromstring(xml)
        vertices, edges = [], []
        for cell in root.iter("mxCell"):
            style = cell.get("style") or ""
            if cell.get("vertex") == "1":
                vertices.append(cell)
            elif cell.get("edge") == "1":
                edges.append(cell)

        if len(vertices) > _VERTEX_BUDGET:
            warnings.append(
                f"节点数 {len(vertices)} 超过概念图预算 {_VERTEX_BUDGET}，"
                "建议聚合次要节点或拆分为多张图"
            )
        if len(edges) > _EDGE_BUDGET:
            warnings.append(
                f"连线数 {len(edges)} 超过预算 {_EDGE_BUDGET}，建议精简关系或拆分"
            )

        floating = sum(1 for e in edges if not e.get("source") or not e.get("target"))
        if floating:
            warnings.append(f"存在 {floating} 条悬空连线（缺 source/target），请在 edit_diagram 中补全")

        # 孤立节点：顶层、非容器、有文字、且没有任何连线触及。
        # 只在有连线的图里检查 —— BMC/SWOT 这类分块框架本来就没有连线。
        if edges:
            endpoints = set()
            for e in edges:
                endpoints.add(e.get("source") or "")
                endpoints.add(e.get("target") or "")
            isolated = [
                c for c in vertices
                if c.get("parent") == "1"
                and c.get("id") not in endpoints
                and (c.get("value") or "").strip()
                and "swimlane" not in (c.get("style") or "")
                and "container" not in (c.get("style") or "")
            ]
            if isolated:
                warnings.append(
                    f"存在 {len(isolated)} 个孤立节点（无任何连线），"
                    "若它们不属于本图讯息请删除，否则请连线表达关系"
                )

        bad_fonts = 0
        for c in vertices + edges:
            style = c.get("style") or ""
            for part in style.split(";"):
                key, _, value = part.partition("=")
                if key == "fontSize":
                    try:
                        if not (_FONT_MIN <= int(float(value)) <= _FONT_MAX):
                            bad_fonts += 1
                    except ValueError:
                        pass
        if bad_fonts:
            warnings.append(
                f"存在 {bad_fonts} 处字号越界（建议 {_FONT_MIN}-{_FONT_MAX}，标题可到 20），"
                "请统一字号层级"
            )
    except Exception as exc:  # pragma: no cover —— QC 兜底
        log.debug("diagram QC skipped: %s", exc)
    return warnings
