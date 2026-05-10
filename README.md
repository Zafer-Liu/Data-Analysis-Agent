# 智能商业分析 Agent

<p align="center">
  <img src="./Images/Banner.png" alt="智能商业分析 Agent Banner" width="100%" />
</p>

<p align="right"><a href="./README_EN.md">English</a></p>

![Python](https://img.shields.io/badge/Python-3.8%2B-blue.svg)
![Flask](https://img.shields.io/badge/Backend-Flask-black.svg)
![Plotly](https://img.shields.io/badge/Visualization-Plotly-3F4F75.svg)
![LLM](https://img.shields.io/badge/LLM-OpenAI%20Compatible-green.svg)
![Charts](https://img.shields.io/badge/Charts-43_Types-orange.svg)
![License](https://img.shields.io/badge/License-Apache%202.0-yellow.svg)

> 一个面向商业分析场景的 AI Agent。  
> 连接数据源后，用户只需使用自然语言提问，系统即可自动完成：
>
> - 数据结构识别
> - SQL 生成与执行
> - 图表生成
> - 业务洞察分析

---


# ✨ 项目亮点

Business Analyst Agent 是一个对话式商业数据分析系统，目标是让非技术用户也能像“聊天”一样完成数据分析。

上传 Excel / CSV，或连接数据库后，用户可以直接提问：

```text
最近三个月销售额趋势如何？
哪个地区利润最高？
帮我生成用户增长图
```

系统会自动：

1. 理解问题意图
2. 分析数据结构（Schema）
3. 自动生成 SQL
4. 执行查询
5. 自动推荐图表
6. 输出业务洞察

并通过 **SSE（Server-Sent Events）流式输出**，实时展示分析过程。

---

# 🚀 核心能力

## 🧠 自然语言数据分析

无需编写 SQL。

用户只需输入自然语言：

```text
今年每个月的订单量趋势
```

系统自动完成：

- SQL 生成
- 数据查询
- 图表推荐
- 分析总结

---

## 🔌 多数据源支持

支持：

- Excel
- CSV
- SQLite
- MySQL
- PostgreSQL
- SQL Server

未来计划：

- DuckDB
- Spark

---

## 📊 智能图表系统

当前内置 **43 种图表类型**，覆盖：

| 分类 | 示例 |
|---|---|
| 对比分析 | 柱状图、条形图 |
| 时间趋势 | 折线图、面积图 |
| 分布分析 | 直方图、箱线图 |
| 关系分析 | 散点图、气泡图 |
| 占比分析 | 饼图、环形图 |
| 地理分析 | 地图类图表 |

系统会根据查询结果自动推荐最合适的图表。

---

## ⚡ SSE 流式分析体验

分析过程实时可见：

```text
[1/4] 正在读取数据结构...
[2/4] 正在生成 SQL...
[3/4] 正在执行查询...
[4/4] 正在生成图表与洞察...
```

相比传统 BI 工具，更透明、更具交互感。

---

## 🤖 多模型兼容

支持：

- DeepSeek
- OpenAI
- Claude
- 任意 OpenAI SDK Compatible API

支持自定义：

- `base_url`
- `model`
- `api_key`

默认配置：

| Provider | Default Model |
|---|---|
| DeepSeek | `deepseek-chat` |
| OpenAI | `gpt-4o-mini` |
| Anthropic | `claude-3-5-haiku-20241022` |

---

# 🖼️ 界面预览

### Data Preview
![Data Preview](Images/Data_preview.png)


### Data Query
![Data Query](Images/Data_query.png)

---

### Auto Generated Chart
![Auto Generated](Images/Auto_generated_image.png)

---

## 安装方式

### 方式 1：一键安装 + 启动（推荐）

#### Windows（PowerShell）

```powershell
iwr -useb https://raw.githubusercontent.com/Zafer-Liu/Data-Analysis-Agent/main/install.ps1 | iex
```

安装完成后可用以下方式启动：

- 双击运行（Windows）：
  ```bat
  %USERPROFILE%\data-analysis-agent.bat
  ```
- 或进入目录手动启动：
  ```powershell
  cd $env:USERPROFILE\.data-analysis-agent\Data-Analysis-Agent
  .\.venv\Scripts\activate
  python app.py
  ```

#### macOS / Linux（Shell）

```bash
curl -fsSL https://raw.githubusercontent.com/Zafer-Liu/Data-Analysis-Agent/main/install.sh | sh
```

安装完成后启动：

```bash
data-analysis-agent
```

如果提示 `command not found`，请先把 `~/.local/bin` 加入 PATH（写入 `~/.bashrc` 或 `~/.zshrc`）：

```bash
export PATH="$HOME/.local/bin:$PATH"
```

---

### 方式 2：Windows 一键启动（仅已安装依赖时适用）

在项目目录下直接运行：

```bat
start.bat
```

> 说明：该方式依赖你本机已配置好 Python 环境并安装好依赖（或 `start.bat` 内部已处理依赖安装）。

---

### 方式 3：手动启动（通用）

#### 1）安装依赖

```bash
pip install -r requirements.txt
```

#### 2）启动服务

```bash
python app.py
```

---

## 访问地址

```text
http://localhost:5001
```

---

# 🛠 Slash Commands

| Command | Status | Description |
|---|---|---|
| `/chart` | ✅ | 强制优先生成图表 |
| `/sql` | ✅ | 直接执行 SQL |
| `/analyze` | 🔲 | 深度统计分析（开发中） |
| `/report` | 🔲 | 导出 Word/PDF 报告（开发中） |

---

# 📈 使用示例

## 示例 1：趋势分析

用户输入：

```text
最近 12 个月销售趋势
```

系统输出：

- SQL 查询
- 趋势折线图
- 销售增长分析

---

## 示例 2：区域分析

用户输入：

```text
哪个地区利润最高？
```

系统输出：

- 地区利润排行
- 柱状图
- 区域经营洞察

---

## 示例 3：图表优先模式

用户输入：

```text
/chart 用户增长情况
```

系统会优先生成可视化图表。

---

# ⚙️ 配置说明

## LLM 配置

在侧边栏 ⚙ 中填写：

```text
API Key
Base URL
Model
```

即可切换模型。

---

## 新增图表类型

在：

```text
Function/Charts_generation/charts/
```

新增图表目录，并在：

```python
registry.py
```

中注册即可。

---

# 🗺️ Roadmap

## Version Development Log
- [View changelog](Version_Update_Log.md)
- [View changelog](Version_Update_Log_EN.md)

## Phase 1（当前）

- ✅ 对话式数据分析
- ✅ SQL 自动生成
- ✅ 多数据源支持
- ✅ 43 种图表
- ✅ SSE 流式输出

---

## Phase 2

- 🔲 可拖拽 Dashboard
- 🔲 多图表联动
- 🔲 可视化看板保存

---

## Phase 3

- ✅ `/report` 自动分析报告
- ✅ Word/PDF 导出
- ✅ 自动业务总结

---

## Phase 4

- 🔲 DuckDB 支持
- 🔲 Spark 支持
- 🔲 大规模数据分析优化

---

# ❓ FAQ

## Q：提示未配置 LLM？

在侧栏 ⚙ 中填写 API Key 并保存。

---

## Q：图表链接重启后失效？

当前图表 HTML 存储于内存中，服务重启后需要重新生成。  
后续会加入持久化存储。

---

## Q：如何新增图表类型？

在：

```text
Function/Charts_generation/charts/
```

新增图表实现，并在：

```python
registry.py
```

中注册即可。

---

# 📄 License

Apache License 2.0

---

# ⭐ 项目目标

让商业分析像聊天一样简单。
