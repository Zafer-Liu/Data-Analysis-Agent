---
name: business-model-canvas
description: 商业模式画布（BMC）分析，用 9 个模块拆解产品的客户、价值、渠道、收入和成本（business model 商业模式）
icon: 🗺
tags: [mode-business, strategy]
allowedTools: [display_diagram, edit_diagram, get_diagram]
---
# 商业模式画布分析

使用 `display_diagram` 的 **content-fill 模式** 生成标准 Osterwalder 9 宫格商业模式画布。

## 何时使用

- 用户要求"商业模式画布"、"BMC 分析"、"商业模式分析"
- 用户想全面拆解一个产品的商业模式

## 工具调用（content-fill 模式）

**必须使用 content-fill 模式，不要传 raw XML。**

调用 `display_diagram` 时传以下参数：
- `template_id`: `"business_model_canvas"`
- `title`: 画布标题（如"宁德时代 商业模式画布"）
- `content`: JSON 对象，9 个 key 对应 9 个模块

### content keys（必须使用这些 key）:
| key | 模块 |
|-----|------|
| `key_partners` | 关键伙伴 |
| `key_activities` | 关键活动 |
| `key_resources` | 关键资源 |
| `value_proposition` | 价值主张 |
| `customer_relationships` | 客户关系 |
| `channels` | 渠道 |
| `customer_segments` | 客户细分 |
| `cost_structure` | 成本结构 |
| `revenue_streams` | 收入来源 |

### 调用示例:
```json
{
  "template_id": "business_model_canvas",
  "title": "宁德时代 商业模式画布",
  "content": {
    "key_partners": "• OEM车企：特斯拉、宝马、奔驰\n• 上游供应商：赣锋锂业、天齐锂业\n• 高校研究机构：中科院、清华",
    "key_activities": "• 电池技术研发（麒麟/神行）\n• 供应链管理与原材料保障\n• 电池回收与梯次利用",
    "key_resources": "• 20,000+研发人员\n• 累计专利超30,000项\n• 全球制造基地",
    "value_proposition": "• 高能量密度麒麟电池\n• 7秒快充技术\n• 超安全不起火不爆炸",
    "customer_relationships": "• 长期战略合作协议\n• 联合研发定制化方案\n• 全生命周期服务",
    "channels": "• 直销给整车厂\n• 全球本地化产能\n• 技术授权",
    "customer_segments": "• 国际整车厂（特斯拉/宝马/奔驰）\n• 国内新能源车企\n• 储能系统客户",
    "cost_structure": "• 原材料（锂/钴/镍）成本\n• 研发投入\n• 全球制造运营",
    "revenue_streams": "• 动力电池系统销售\n• 储能系统解决方案\n• 电池回收与材料再利用"
  }
}
```

### 内容格式规则:
- 用 `\n` 换行
- 用 `• ` 作为列表项前缀
- 每个模块内容不超过 300 字符
- **每个模块内容必须唯一，不能跨模块重复**

## 修正画布

如果画布内容需要更新：
1. `get_diagram` — 获取当前图表
2. `edit_diagram` — 通过 cell ID 局部更新

## 分析要点

生成 BMC 时应覆盖 9 个模块：
1. **客户细分** — 目标用户群体
2. **价值主张** — 为客户解决什么问题
3. **渠道** — 如何触达客户
4. **客户关系** — 如何维系客户
5. **收入来源** — 如何赚钱
6. **关键资源** — 核心资产
7. **关键活动** — 核心业务流程
8. **关键伙伴** — 合作方
9. **成本结构** — 主要成本项
