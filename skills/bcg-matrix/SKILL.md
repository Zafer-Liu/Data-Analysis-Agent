---
name: bcg-matrix
description: 波士顿矩阵（BCG Matrix）分析，按市场增长率和相对市场份额将业务单元分为明星、问题、现金牛、瘦狗四类（portfolio 产品组合）
icon: 📊
tags: [mode-business, strategy, visualization]
allowedTools: [display_diagram, edit_diagram, get_diagram]
---
# 波士顿矩阵分析

使用 `display_diagram` 的 **content-fill 模式** 生成标准 2x2 BCG 矩阵。

## 何时使用

- 用户要求"BCG 矩阵"、"波士顿矩阵"、"BCG 分析"
- 用户想按市场增长率和市场份额对业务/产品进行分类

## 工具调用（content-fill 模式）

**必须使用 content-fill 模式，不要传 raw XML。**

调用 `display_diagram` 时传以下参数：
- `template_id`: `"bcg_matrix"`
- `title`: 矩阵标题
- `content`: JSON 对象，4 个 key 对应 4 个象限

### content keys:
| key | 象限 |
|-----|------|
| `stars` | 明星业务（高增长+高份额） |
| `question_marks` | 问题业务（高增长+低份额） |
| `cash_cows` | 现金牛业务（低增长+高份额） |
| `dogs` | 瘦狗业务（低增长+低份额） |

### 调用示例:
```json
{
  "template_id": "bcg_matrix",
  "title": "某公司 BCG 矩阵",
  "content": {
    "stars": "• 产品A（增长20%，份额35%）\n• 产品B（增长18%，份额28%）",
    "question_marks": "• 产品C（增长25%，份额8%）\n• 产品D（增长15%，份额5%）",
    "cash_cows": "• 产品E（增长3%，份额45%）\n• 产品F（增长2%，份额40%）",
    "dogs": "• 产品G（增长-2%，份额6%）\n• 产品H（增长-1%，份额4%）"
  }
}
```

### 内容格式规则:
- 用 `\n` 换行
- 用 `• ` 作为列表项前缀
- 每个象限内容不超过 300 字符
- 每个象限内容必须唯一

## 修正矩阵

如果内容需要更新：
1. `get_diagram` — 获取当前图表
2. `edit_diagram` — 通过 cell ID 局部更新

## 分析要点

1. **明星业务** — 高增长高份额，需要持续投资
2. **问题业务** — 高增长低份额，需判断是否值得投资
3. **现金牛业务** — 低增长高份额，稳定现金流来源
4. **瘦狗业务** — 低增长低份额，考虑退出或剥离
