# charts/base.py
"""
统一图表接口协议
所有图表 generate() 必须遵循此接口
"""
from __future__ import annotations

import html as _html
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class ChartResult:
    """
    统一返回结构
    所有图表 generate() 必须返回此结构
    """
    html: str = ""
    spec: Dict[str, Any] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)
    meta: Dict[str, Any] = field(default_factory=dict)

    def is_valid(self) -> bool:
        return bool(self.html.strip()) and len(self.html) > 500


@dataclass
class FieldMapping:
    """
    字段映射标准名称
    图表使用 mapping.xxx 读取实际列名
    """
    label: Optional[str] = None
    value: Optional[str] = None
    x: Optional[str] = None
    y: Optional[str] = None
    series: Optional[str] = None
    size: Optional[str] = None
    color: Optional[str] = None
    source: Optional[str] = None
    target: Optional[str] = None
    lat: Optional[str] = None
    lon: Optional[str] = None
    geo: Optional[str] = None
    date: Optional[str] = None
    time: Optional[str] = None
    parent: Optional[str] = None
    child: Optional[str] = None
    path: Optional[str] = None
    open_: Optional[str] = field(default=None, metadata={"alias": "open"})
    high: Optional[str] = None
    low: Optional[str] = None
    close: Optional[str] = field(default=None, metadata={"alias": "close"})
    volume: Optional[str] = None
    text: Optional[str] = None
    frequency: Optional[str] = None
    group: Optional[str] = None
    rank: Optional[str] = None
    actual: Optional[str] = None
    target_val: Optional[str] = field(default=None, metadata={"alias": "target"})

    def to_dict(self) -> Dict[str, str]:
        return {k: v for k, v in self.__dict__.items() if v is not None}

    @classmethod
    def from_dict(cls, d: Dict[str, str]) -> "FieldMapping":
        known = {}
        for k, v in d.items():
            if hasattr(cls, k) or k in ("open", "close", "target"):
                key = {"open": "open_", "close": "close_", "target": "target_val"}.get(k, k)
                known[key] = v
        return cls(**known)


# ── 统一 HTML 外壳 ────────────────────────────────────────────
# 所有图表模块各自维护的 _build_html() 高度重复，且大多未对 title 等
# 用户/列名可控文本做转义，存在 XSS 风险。这里提供一个统一实现，模块可
# 逐步迁移到此版本：对 title / chart_name / library / data_fmt / desc
# 进行 HTML 转义（embed 是可信的 plotly/echarts 片段，不转义）。
_DEFAULT_CSS = (
    'body{font-family:"Heiti SC","Microsoft YaHei",sans-serif;'
    "margin:40px;background:#fafafa}"
    ".chart-wrap{background:white;border-radius:12px;"
    "box-shadow:0 2px 12px rgba(0,0,0,.08);padding:24px;margin-bottom:32px}"
    "h1{color:#222;font-size:22px;margin-bottom:6px}"
    ".subtitle{color:#888;font-size:13px;margin-bottom:24px}"
    ".desc{color:#555;font-size:14px;line-height:1.7;margin-top:20px}"
)


def build_chart_html(
    title: str,
    chart_name: str,
    library: str,
    data_fmt: str,
    desc: str,
    embed: str,
) -> str:
    """生成统一外壳的图表 HTML。

    对所有用户/列名可控的文本字段做 HTML 转义，防止注入。embed 是图表
    库产出的可信 HTML 片段，不转义。
    """
    esc = _html.escape
    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8">
<title>{esc(title)}</title>
<style>
{_DEFAULT_CSS}
</style></head>
<body><div class="chart-wrap">
<h1>{esc(title)}</h1><div class="subtitle">{esc(chart_name)} | {esc(library)}</div>
{embed}
</div><div class="desc">
<strong>数据格式：</strong>{esc(data_fmt)}<br>
<strong>说明：</strong>{esc(desc)}
</div></body></html>"""
