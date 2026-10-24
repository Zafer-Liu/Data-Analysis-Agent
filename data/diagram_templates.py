"""Pre-built draw.io XML templates for business analysis frameworks.

Each template is a complete <mxfile> XML string that renders the framework
layout when loaded into the draw.io embed iframe. Page sizes vary per
template (declared in each mxGraphModel); content stays within them.

Cell IDs start from 2 (0 and 1 are root sentinels used by draw.io).
`content_cells` maps agent-facing section keys to the mxCell id that
`display_diagram`'s content-fill mode writes into — keep them in sync with
agent/prompts.py, agent/tools/schemas.py and skills/*/SKILL.md.
"""

from __future__ import annotations

_BLANK_DIAGRAM_XML = (
    '<mxfile><diagram name="Blank" id="blank">'
    '<mxGraphModel pageWidth="820" pageHeight="660" pageFormat="custom"><root>'
    '<mxCell id="0"/>'
    '<mxCell id="1" parent="0"/>'
    '</root></mxGraphModel></diagram></mxfile>'
)

DIAGRAM_TEMPLATES: dict[str, dict[str, str]] = {
    "blank_canvas": {
        "name": "空白画布",
        "description": "Empty draw.io canvas",
        "content_cells": {},
        "xml": _BLANK_DIAGRAM_XML,
    },
    "bcg_matrix": {
        "name": "BCG 矩阵",
        "description": "2×2 quadrant: market growth vs relative market share",
        "content_cells": {
            "stars": "3",
            "question_marks": "5",
            "cash_cows": "7",
            "dogs": "9",
        },
        "xml": (
            '<mxfile><diagram name="BCG Matrix" id="bcg">'
            '<mxGraphModel pageWidth="820" pageHeight="660" pageFormat="custom"><root>'
            '<mxCell id="0"/>'
            '<mxCell id="1" parent="0"/>'

            # ── quadrant containers ──

            # Stars (top-left): high growth, high share
            '<mxCell id="2" value="明星业务&#xa;(Stars)" '
            'style="swimlane;startSize=30;fillColor=#dae8fc;strokeColor=#6c8ebf;fontStyle=1;fontSize=14;html=1;" '
            'vertex="1" parent="1">'
            '<mxGeometry x="20" y="60" width="370" height="260" as="geometry"/>'
            "</mxCell>"
            '<mxCell id="3" value="点击编辑明星业务内容" '
            'style="text;html=1;align=center;verticalAlign=middle;whiteSpace=wrap;fontSize=11;fontColor=#6c8ebf;" '
            'vertex="1" parent="2">'
            '<mxGeometry x="40" y="50" width="290" height="190" as="geometry"/>'
            "</mxCell>"

            # Question Marks (top-right): high growth, low share
            '<mxCell id="4" value="问题业务&#xa;(Question Marks)" '
            'style="swimlane;startSize=30;fillColor=#fff2cc;strokeColor=#d6b656;fontStyle=1;fontSize=14;html=1;" '
            'vertex="1" parent="1">'
            '<mxGeometry x="410" y="60" width="370" height="260" as="geometry"/>'
            "</mxCell>"
            '<mxCell id="5" value="点击编辑问题业务内容" '
            'style="text;html=1;align=center;verticalAlign=middle;whiteSpace=wrap;fontSize=11;fontColor=#d6b656;" '
            'vertex="1" parent="4">'
            '<mxGeometry x="40" y="50" width="290" height="190" as="geometry"/>'
            "</mxCell>"

            # Cash Cows (bottom-left): low growth, high share
            '<mxCell id="6" value="现金牛业务&#xa;(Cash Cows)" '
            'style="swimlane;startSize=30;fillColor=#d5e8d4;strokeColor=#82b366;fontStyle=1;fontSize=14;html=1;" '
            'vertex="1" parent="1">'
            '<mxGeometry x="20" y="340" width="370" height="260" as="geometry"/>'
            "</mxCell>"
            '<mxCell id="7" value="点击编辑现金牛业务内容" '
            'style="text;html=1;align=center;verticalAlign=middle;whiteSpace=wrap;fontSize=11;fontColor=#82b366;" '
            'vertex="1" parent="6">'
            '<mxGeometry x="40" y="50" width="290" height="190" as="geometry"/>'
            "</mxCell>"

            # Dogs (bottom-right): low growth, low share
            '<mxCell id="8" value="瘦狗业务&#xa;(Dogs)" '
            'style="swimlane;startSize=30;fillColor=#f8cecc;strokeColor=#b85450;fontStyle=1;fontSize=14;html=1;" '
            'vertex="1" parent="1">'
            '<mxGeometry x="410" y="340" width="370" height="260" as="geometry"/>'
            "</mxCell>"
            '<mxCell id="9" value="点击编辑瘦狗业务内容" '
            'style="text;html=1;align=center;verticalAlign=middle;whiteSpace=wrap;fontSize=11;fontColor=#b85450;" '
            'vertex="1" parent="8">'
            '<mxGeometry x="40" y="50" width="290" height="190" as="geometry"/>'
            "</mxCell>"

            # ── axis labels ──

            # Y-axis: market growth (high at top)
            '<mxCell id="10" value="高市场增长率" '
            'style="text;html=1;align=center;verticalAlign=middle;whiteSpace=wrap;fontSize=13;fontStyle=1;" '
            'vertex="1" parent="1">'
            '<mxGeometry x="20" y="20" width="370" height="30" as="geometry"/>'
            "</mxCell>"
            '<mxCell id="11" value="低市场增长率" '
            'style="text;html=1;align=center;verticalAlign=middle;whiteSpace=wrap;fontSize=13;fontStyle=1;" '
            'vertex="1" parent="1">'
            '<mxGeometry x="20" y="310" width="370" height="30" as="geometry"/>'
            "</mxCell>"

            # X-axis: relative market share (high on left)
            '<mxCell id="12" value="高相对市场份额" '
            'style="text;html=1;align=center;verticalAlign=middle;whiteSpace=wrap;fontSize=13;fontStyle=1;" '
            'vertex="1" parent="1">'
            '<mxGeometry x="180" y="600" width="220" height="30" as="geometry"/>'
            "</mxCell>"
            '<mxCell id="13" value="低相对市场份额" '
            'style="text;html=1;align=center;verticalAlign=middle;whiteSpace=wrap;fontSize=13;fontStyle=1;" '
            'vertex="1" parent="1">'
            '<mxGeometry x="570" y="600" width="220" height="30" as="geometry"/>'
            "</mxCell>"

            "</root></mxGraphModel></diagram></mxfile>"
        ),
    },

    "swot_analysis": {
        "name": "SWOT 分析",
        "description": "4 quadrant: Strengths, Weaknesses, Opportunities, Threats",
        "content_cells": {
            "strengths": "3",
            "weaknesses": "5",
            "opportunities": "7",
            "threats": "9",
        },
        "xml": (
            '<mxfile><diagram name="SWOT Analysis" id="swot">'
            '<mxGraphModel pageWidth="820" pageHeight="660" pageFormat="custom"><root>'
            '<mxCell id="0"/>'
            '<mxCell id="1" parent="0"/>'

            # Strengths (top-left) — green
            '<mxCell id="2" value="优势&#xa;(Strengths)" '
            'style="swimlane;startSize=30;fillColor=#d5e8d4;strokeColor=#82b366;fontStyle=1;fontSize=14;html=1;" '
            'vertex="1" parent="1">'
            '<mxGeometry x="20" y="60" width="370" height="260" as="geometry"/>'
            "</mxCell>"
            '<mxCell id="3" value="列出内部优势…" '
            'style="text;html=1;align=center;verticalAlign=middle;whiteSpace=wrap;fontSize=11;fontColor=#82b366;" '
            'vertex="1" parent="2">'
            '<mxGeometry x="40" y="50" width="290" height="190" as="geometry"/>'
            "</mxCell>"

            # Weaknesses (top-right) — red
            '<mxCell id="4" value="劣势&#xa;(Weaknesses)" '
            'style="swimlane;startSize=30;fillColor=#f8cecc;strokeColor=#b85450;fontStyle=1;fontSize=14;html=1;" '
            'vertex="1" parent="1">'
            '<mxGeometry x="410" y="60" width="370" height="260" as="geometry"/>'
            "</mxCell>"
            '<mxCell id="5" value="列出内部劣势…" '
            'style="text;html=1;align=center;verticalAlign=middle;whiteSpace=wrap;fontSize=11;fontColor=#b85450;" '
            'vertex="1" parent="4">'
            '<mxGeometry x="40" y="50" width="290" height="190" as="geometry"/>'
            "</mxCell>"

            # Opportunities (bottom-left) — blue
            '<mxCell id="6" value="机会&#xa;(Opportunities)" '
            'style="swimlane;startSize=30;fillColor=#dae8fc;strokeColor=#6c8ebf;fontStyle=1;fontSize=14;html=1;" '
            'vertex="1" parent="1">'
            '<mxGeometry x="20" y="340" width="370" height="260" as="geometry"/>'
            "</mxCell>"
            '<mxCell id="7" value="列出外部机会…" '
            'style="text;html=1;align=center;verticalAlign=middle;whiteSpace=wrap;fontSize=11;fontColor=#6c8ebf;" '
            'vertex="1" parent="6">'
            '<mxGeometry x="40" y="50" width="290" height="190" as="geometry"/>'
            "</mxCell>"

            # Threats (bottom-right) — orange
            '<mxCell id="8" value="威胁&#xa;(Threats)" '
            'style="swimlane;startSize=30;fillColor=#fff2cc;strokeColor=#d6b656;fontStyle=1;fontSize=14;html=1;" '
            'vertex="1" parent="1">'
            '<mxGeometry x="410" y="340" width="370" height="260" as="geometry"/>'
            "</mxCell>"
            '<mxCell id="9" value="列出外部威胁…" '
            'style="text;html=1;align=center;verticalAlign=middle;whiteSpace=wrap;fontSize=11;fontColor=#d6b656;" '
            'vertex="1" parent="8">'
            '<mxGeometry x="40" y="50" width="290" height="190" as="geometry"/>'
            "</mxCell>"

            # ── axis labels ──

            # Y-axis: internal (top) vs external (bottom)
            '<mxCell id="10" value="内部因素" '
            'style="text;html=1;align=center;verticalAlign=middle;whiteSpace=wrap;fontSize=13;fontStyle=1;" '
            'vertex="1" parent="1">'
            '<mxGeometry x="20" y="20" width="370" height="30" as="geometry"/>'
            "</mxCell>"
            '<mxCell id="11" value="外部因素" '
            'style="text;html=1;align=center;verticalAlign=middle;whiteSpace=wrap;fontSize=13;fontStyle=1;" '
            'vertex="1" parent="1">'
            '<mxGeometry x="20" y="310" width="370" height="30" as="geometry"/>'
            "</mxCell>"

            # X-axis: positive (left) vs negative (right)
            '<mxCell id="12" value="积极因素" '
            'style="text;html=1;align=center;verticalAlign=middle;whiteSpace=wrap;fontSize=13;fontStyle=1;" '
            'vertex="1" parent="1">'
            '<mxGeometry x="180" y="600" width="220" height="30" as="geometry"/>'
            "</mxCell>"
            '<mxCell id="13" value="消极因素" '
            'style="text;html=1;align=center;verticalAlign=middle;whiteSpace=wrap;fontSize=13;fontStyle=1;" '
            'vertex="1" parent="1">'
            '<mxGeometry x="570" y="600" width="220" height="30" as="geometry"/>'
            "</mxCell>"

            "</root></mxGraphModel></diagram></mxfile>"
        ),
    },


    "value_proposition": {
        "name": "Ad-Lib 价值主张速写",
        "description": "6-cell grid with Ad-Lib sentence template",
        "content_cells": {
            "product_service": "5",
            "customer_segments": "7",
            "customer_jobs": "9",
            "pain_relievers": "11",
            "gain_creators": "13",
            "competitors": "15",
        },
        "xml": (
            '<mxfile><diagram name="Value Proposition" id="vp">'
            '<mxGraphModel pageWidth="820" pageHeight="440" pageFormat="custom"><root>'
            '<mxCell id="0"/>'
            '<mxCell id="1" parent="0"/>'

            # title bar
            '<mxCell id="2" value="💡 价值主张速写（Ad-Lib）" '
            'style="swimlane;startSize=30;fillColor=#1a1a2e;strokeColor=#1a1a2e;fontColor=#ffffff;fontStyle=1;fontSize=14;html=1;" '
            'vertex="1" parent="1">'
            '<mxGeometry x="20" y="20" width="780" height="30" as="geometry"/>'
            "</mxCell>"

            # Ad-Lib sentence
            '<mxCell id="3" value="我们的 [产品/服务] 帮助 [目标客户]，他们想要完成 [客户任务]。&#xa;我们通过 [痛点缓解]，并 [收益创造] 来创造价值，&#xa;不同于 [竞争替代方案]。" '
            'style="text;html=1;align=center;verticalAlign=middle;whiteSpace=wrap;fillColor=#16213e;strokeColor=#16213e;fontColor=#ffffff;fontSize=11;fontStyle=2;" '
            'vertex="1" parent="1">'
            '<mxGeometry x="20" y="55" width="780" height="45" as="geometry"/>'
            "</mxCell>"

            # Row 1: 01 | 02 | 03  (y=110, h=150)

            # 01 (blue)
            '<mxCell id="4" value="01 产品与服务" '
            'style="swimlane;startSize=26;fillColor=#dae8fc;strokeColor=#6c8ebf;fontStyle=1;fontSize=11;html=1;" '
            'vertex="1" parent="1">'
            '<mxGeometry x="20" y="110" width="250" height="150" as="geometry"/>'
            "</mxCell>"
            '<mxCell id="5" value="客户真正会购买、使用或接触到的提供物" '
            'style="text;html=1;align=center;verticalAlign=middle;whiteSpace=wrap;fontSize=10;fontColor=#6c8ebf;" '
            'vertex="1" parent="4">'
            '<mxGeometry x="10" y="36" width="230" height="104" as="geometry"/>'
            "</mxCell>"

            # 02 (yellow)
            '<mxCell id="6" value="02 客户细分" '
            'style="swimlane;startSize=26;fillColor=#fff2cc;strokeColor=#d6b656;fontStyle=1;fontSize=11;html=1;" '
            'vertex="1" parent="1">'
            '<mxGeometry x="280" y="110" width="250" height="150" as="geometry"/>'
            "</mxCell>"
            '<mxCell id="7" value="只服务一个具体人群，不服务「所有人」" '
            'style="text;html=1;align=center;verticalAlign=middle;whiteSpace=wrap;fontSize=10;fontColor=#d6b656;" '
            'vertex="1" parent="6">'
            '<mxGeometry x="10" y="36" width="230" height="104" as="geometry"/>'
            "</mxCell>"

            # 03 (green)
            '<mxCell id="8" value="03 客户任务" '
            'style="swimlane;startSize=26;fillColor=#d5e8d4;strokeColor=#82b366;fontStyle=1;fontSize=11;html=1;" '
            'vertex="1" parent="1">'
            '<mxGeometry x="540" y="110" width="260" height="150" as="geometry"/>'
            "</mxCell>"
            '<mxCell id="9" value="客户想达成的结果，不是「需要工具」" '
            'style="text;html=1;align=center;verticalAlign=middle;whiteSpace=wrap;fontSize=10;fontColor=#82b366;" '
            'vertex="1" parent="8">'
            '<mxGeometry x="10" y="36" width="240" height="104" as="geometry"/>'
            "</mxCell>"

            # Row 2: 04 | 05 | 06  (y=270, h=150)

            # 04 (red)
            '<mxCell id="10" value="04 痛点缓解" '
            'style="swimlane;startSize=26;fillColor=#f8cecc;strokeColor=#b85450;fontStyle=1;fontSize=11;html=1;" '
            'vertex="1" parent="1">'
            '<mxGeometry x="20" y="270" width="250" height="150" as="geometry"/>'
            "</mxCell>"
            '<mxCell id="11" value="减少 / 消除 / 避免 + 客户最在意的痛点" '
            'style="text;html=1;align=center;verticalAlign=middle;whiteSpace=wrap;fontSize=10;fontColor=#b85450;" '
            'vertex="1" parent="10">'
            '<mxGeometry x="10" y="36" width="230" height="104" as="geometry"/>'
            "</mxCell>"

            # 05 (purple)
            '<mxCell id="12" value="05 收益创造" '
            'style="swimlane;startSize=26;fillColor=#e1d5e7;strokeColor=#9673a6;fontStyle=1;fontSize=11;html=1;" '
            'vertex="1" parent="1">'
            '<mxGeometry x="280" y="270" width="250" height="150" as="geometry"/>'
            "</mxCell>"
            '<mxCell id="13" value="增加 / 实现 / 加速 + 客户最想要的收益" '
            'style="text;html=1;align=center;verticalAlign=middle;whiteSpace=wrap;fontSize=10;fontColor=#9673a6;" '
            'vertex="1" parent="12">'
            '<mxGeometry x="10" y="36" width="230" height="104" as="geometry"/>'
            "</mxCell>"

            # 06 (grey)
            '<mxCell id="14" value="06 竞争价值主张" '
            'style="swimlane;startSize=26;fillColor=#f5f5f5;strokeColor=#666666;fontStyle=1;fontSize=11;html=1;" '
            'vertex="1" parent="1">'
            '<mxGeometry x="540" y="270" width="260" height="150" as="geometry"/>'
            "</mxCell>"
            '<mxCell id="15" value="客户今天在用的替代方案：竞品、手工变通、内部流程或什么都不做" '
            'style="text;html=1;align=center;verticalAlign=middle;whiteSpace=wrap;fontSize=10;fontColor=#666666;" '
            'vertex="1" parent="14">'
            '<mxGeometry x="10" y="36" width="240" height="104" as="geometry"/>'
            "</mxCell>"

            "</root></mxGraphModel></diagram></mxfile>"
        ),
    },

    "business_model_canvas": {
        "name": "商业模式画布（图表版）",
        "description": "9-cell grid layout rendered as draw.io diagram",
        "content_cells": {
            "key_partners": "3",
            "key_activities": "5",
            "key_resources": "7",
            "value_proposition": "9",
            "customer_relationships": "11",
            "channels": "13",
            "customer_segments": "15",
            "cost_structure": "17",
            "revenue_streams": "19",
        },
        "xml": (
            '<mxfile><diagram name="Business Model Canvas" id="bmc">'
            '<mxGraphModel pageWidth="820" pageHeight="460" pageFormat="custom"><root>'
            '<mxCell id="0"/>'
            '<mxCell id="1" parent="0"/>'

            # ── Row 1: Key Partners | Key Activities | Value Proposition | Customer Relationships | Customer Segments ──

            # Key Partners (col 1, row 1)
            '<mxCell id="2" value="关键伙伴&#xa;(Key Partners)" '
            'style="swimlane;startSize=30;fillColor=#f5f5f5;strokeColor=#666666;fontStyle=1;fontSize=11;html=1;" '
            'vertex="1" parent="1">'
            '<mxGeometry x="20" y="20" width="150" height="260" as="geometry"/>'
            "</mxCell>"
            '<mxCell id="3" value="" '
            'style="text;html=1;whiteSpace=wrap;fontSize=10;" '
            'vertex="1" parent="2">'
            '<mxGeometry x="10" y="40" width="130" height="210" as="geometry"/>'
            "</mxCell>"

            # Key Activities (col 2, row 1)
            '<mxCell id="4" value="关键活动&#xa;(Key Activities)" '
            'style="swimlane;startSize=30;fillColor=#dae8fc;strokeColor=#6c8ebf;fontStyle=1;fontSize=11;html=1;" '
            'vertex="1" parent="1">'
            '<mxGeometry x="180" y="20" width="150" height="130" as="geometry"/>'
            "</mxCell>"
            '<mxCell id="5" value="" '
            'style="text;html=1;whiteSpace=wrap;fontSize=10;" '
            'vertex="1" parent="4">'
            '<mxGeometry x="10" y="40" width="130" height="80" as="geometry"/>'
            "</mxCell>"

            # Key Resources (col 2, row 2)
            '<mxCell id="6" value="关键资源&#xa;(Key Resources)" '
            'style="swimlane;startSize=30;fillColor=#dae8fc;strokeColor=#6c8ebf;fontStyle=1;fontSize=11;html=1;" '
            'vertex="1" parent="1">'
            '<mxGeometry x="180" y="160" width="150" height="120" as="geometry"/>'
            "</mxCell>"
            '<mxCell id="7" value="" '
            'style="text;html=1;whiteSpace=wrap;fontSize=10;" '
            'vertex="1" parent="6">'
            '<mxGeometry x="10" y="40" width="130" height="70" as="geometry"/>'
            "</mxCell>"

            # Value Proposition (col 3, tall)
            '<mxCell id="8" value="价值主张&#xa;(Value Proposition)" '
            'style="swimlane;startSize=30;fillColor=#d5e8d4;strokeColor=#82b366;fontStyle=1;fontSize=12;html=1;" '
            'vertex="1" parent="1">'
            '<mxGeometry x="340" y="20" width="160" height="260" as="geometry"/>'
            "</mxCell>"
            '<mxCell id="9" value="" '
            'style="text;html=1;whiteSpace=wrap;fontSize=10;" '
            'vertex="1" parent="8">'
            '<mxGeometry x="10" y="40" width="140" height="210" as="geometry"/>'
            "</mxCell>"

            # Customer Relationships (col 4, row 1)
            '<mxCell id="10" value="客户关系&#xa;(Customer Relationships)" '
            'style="swimlane;startSize=30;fillColor=#e1d5e7;strokeColor=#9673a6;fontStyle=1;fontSize=11;html=1;" '
            'vertex="1" parent="1">'
            '<mxGeometry x="510" y="20" width="140" height="130" as="geometry"/>'
            "</mxCell>"
            '<mxCell id="11" value="" '
            'style="text;html=1;whiteSpace=wrap;fontSize=10;" '
            'vertex="1" parent="10">'
            '<mxGeometry x="10" y="40" width="120" height="80" as="geometry"/>'
            "</mxCell>"

            # Channels (col 4, row 2)
            '<mxCell id="12" value="渠道&#xa;(Channels)" '
            'style="swimlane;startSize=30;fillColor=#e1d5e7;strokeColor=#9673a6;fontStyle=1;fontSize=11;html=1;" '
            'vertex="1" parent="1">'
            '<mxGeometry x="510" y="160" width="140" height="120" as="geometry"/>'
            "</mxCell>"
            '<mxCell id="13" value="" '
            'style="text;html=1;whiteSpace=wrap;fontSize=10;" '
            'vertex="1" parent="12">'
            '<mxGeometry x="10" y="40" width="120" height="70" as="geometry"/>'
            "</mxCell>"

            # Customer Segments (col 5)
            '<mxCell id="14" value="客户细分&#xa;(Customer Segments)" '
            'style="swimlane;startSize=30;fillColor=#fff2cc;strokeColor=#d6b656;fontStyle=1;fontSize=11;html=1;" '
            'vertex="1" parent="1">'
            '<mxGeometry x="660" y="20" width="140" height="260" as="geometry"/>'
            "</mxCell>"
            '<mxCell id="15" value="" '
            'style="text;html=1;whiteSpace=wrap;fontSize=10;" '
            'vertex="1" parent="14">'
            '<mxGeometry x="10" y="40" width="120" height="210" as="geometry"/>'
            "</mxCell>"

            # ── Row 2: Cost Structure (bottom-left) | Revenue Streams (bottom-right) ──

            # Cost Structure
            '<mxCell id="16" value="成本结构&#xa;(Cost Structure)" '
            'style="swimlane;startSize=30;fillColor=#f8cecc;strokeColor=#b85450;fontStyle=1;fontSize=11;html=1;" '
            'vertex="1" parent="1">'
            '<mxGeometry x="20" y="290" width="320" height="150" as="geometry"/>'
            "</mxCell>"
            '<mxCell id="17" value="" '
            'style="text;html=1;whiteSpace=wrap;fontSize=10;" '
            'vertex="1" parent="16">'
            '<mxGeometry x="10" y="40" width="300" height="100" as="geometry"/>'
            "</mxCell>"

            # Revenue Streams
            '<mxCell id="18" value="收入来源&#xa;(Revenue Streams)" '
            'style="swimlane;startSize=30;fillColor=#d5e8d4;strokeColor=#82b366;fontStyle=1;fontSize=11;html=1;" '
            'vertex="1" parent="1">'
            '<mxGeometry x="350" y="290" width="450" height="150" as="geometry"/>'
            "</mxCell>"
            '<mxCell id="19" value="" '
            'style="text;html=1;whiteSpace=wrap;fontSize=10;" '
            'vertex="1" parent="18">'
            '<mxGeometry x="10" y="40" width="430" height="100" as="geometry"/>'
            "</mxCell>"

            "</root></mxGraphModel></diagram></mxfile>"
        ),
    },

}
