---
name: swot-analysis
description: SWOT 分析，从内部优势/劣势和外部机会/威胁四个维度评估企业或产品的战略态势（strategy 战略分析）
icon: 🔲
tags: [mode-business, strategy, visualization]
allowedTools: [display_diagram, edit_diagram, get_diagram]
---
# SWOT 分析

使用 `display_diagram` 的 **content-fill 模式** 生成标准 2x2 SWOT 四象限图。

## 何时使用

- 用户要求"SWOT 分析"、"优势劣势分析"、"SWOT"
- 用户想从内外部、正负面四个维度评估战略

## 工具调用（content-fill 模式）

**必须使用 content-fill 模式，不要传 raw XML。**

调用 `display_diagram` 时传以下参数：
- `template_id`: `"swot_analysis"`
- `title`: 分析标题
- `content`: JSON 对象，4 个 key 对应 4 个象限

### content keys:
| key | 象限 |
|-----|------|
| `strengths` | 优势（内部正面） |
| `weaknesses` | 劣势（内部负面） |
| `opportunities` | 机会（外部正面） |
| `threats` | 威胁（外部负面） |

### 调用示例:
```json
{
  "template_id": "swot_analysis",
  "title": "某公司 SWOT 分析",
  "content": {
    "strengths": "• 品牌知名度高\n• 技术领先\n• 团队经验丰富",
    "weaknesses": "• 资金链紧张\n• 渠道覆盖不足\n• 依赖单一产品",
    "opportunities": "• 政策利好\n• 新兴市场增长\n• 技术变革带来新需求",
    "threats": "• 竞争加剧\n• 原材料涨价\n• 替代品出现"
  }
}
```

### 内容格式规则:
- 用 `\n` 换行
- 用 `• ` 作为列表项前缀
- 每个象限内容不超过 300 字符
- 每个象限内容必须唯一

## 分析要点

1. **Strengths（优势）** — 内部正面因素：品牌、技术、团队、资源
2. **Weaknesses（劣势）** — 内部负面因素：短板、不足、瓶颈
3. **Opportunities（机会）** — 外部正面因素：市场趋势、政策、技术变革
4. **Threats（威胁）** — 外部负面因素：竞争、替代品、监管风险

## 战略组合分析

生成 SWOT 后可进一步推导：
- **SO 战略**（优势+机会）— 利用优势抓住机会
- **WO 战略**（劣势+机会）— 克服劣势利用机会
- **ST 战略**（优势+威胁）— 利用优势规避威胁
- **WT 战略**（劣势+威胁）— 减少劣势规避威胁
