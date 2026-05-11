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

# 🧠 核心能力

## 1️⃣ 自然语言数据分析

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

## 2️⃣ 多数据源支持

支持上传和连接多种数据源：

- 文件：Excel / CSV
- 数据库：SQLite、MySQL、PostgreSQL、SQL Server
- 未来计划：DuckDB、Spark

---

## 3️⃣ 智能图表系统

| 分类 | 图表类型 |
|---|---|
| **对比类** COMPARING | Marimekko_ABS（马里美科-绝对值）、Marimekko_PCT（马里美科-百分比）、Bar_Chart（柱状图）、Grouped_Bar_Chart（分组柱状图）、Stacked_Bar_Chart（堆叠柱状图）、Diverging_Bar_Chart（对比条形图）、Dot_Plot（点图）、Waffle（华夫格）、Bullet_Chart（靶心图）、Sankey_Chart（桑基图）、Heatmap（热力图）、Waterfall（瀑布图） |
| **时间趋势类** TIME | Line_Chart（折线图）、Circular_Line_Chart（圆形折线图）、Slope_Chart（斜率图）、Sparkline（迷你图）、Bump_Chart（凹凸图）、Cycle_Chart（周期图）、Area_Chart（面积图）、Stacked_Area_Chart（堆叠面积图）、Horizon_Chart（地平线图）、Connected_Scatter（连线散点图） |
| **分布类** DISTRIBUTION | Histogram_Pareto_chart（直方图与帕累托图）、Pyramid_Chart（金字塔图）、Error_Bar_Chart（误差条形图）、Box-and-Whisker_Plot（箱线图）、Violin_Chart（小提琴图）、Ridgeline_Plot（山脊线图）、Beeswarm_Plot（分簇散点图）、stem_leaf（茎叶图） |
| **地理类** GEOSPATIAL | Flow_Map（动态流向图）、Dot_Density_Map（点密度地图）、Choropleth_Map（面量图） |
| **关系类** RELATIONSHIP | Scatter_Plot（散点图）、Bubble_Plot（气泡图）、Radar_Charts（雷达图）、Chord_Diagram（弦图）、Arc_Chart（弧图）、Network_Diagram（网络图）、Parallel_Coordinates_Plot（平行坐标图） |
| **占比类** PART-TO-WHOLE | Treemap（矩形树图）、Sunburst_Diagram（旭日图）、Nightingale_Chart（南丁格尔玫瑰图）、Pie_Chart（饼图） |

系统会根据查询结果自动推荐最合适的图表。

---


## 4️⃣ SSE 流式分析体验

分析过程实时可见：

```text
[1/4] 正在读取数据结构...
[2/4] 正在生成 SQL...
[3/4] 正在执行查询...
[4/4] 正在生成图表与洞察...
```

相比传统 BI 工具，更透明、更具交互感。

---

## 5️⃣ 多模型兼容

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

## ⚙️安装方式



### 方式 1：安装包下载（推荐）

#### 1) 下载安装包 

![Download installation package](Images/package.png)

#### 2) 解压缩，在项目目录下双击直接运行：
**Windows 用户**

```bat
start.bat
```

> 注：首次启动 `start.bat` 会自动配置运行环境，时间可能较长，后续再次运行就无需等待。

**Mac 用户**

① 需要使用脚本 `start.command`

② 在终端里赋予执行权限：
   ```bash
   chmod +x start.command
   ```

③ 双击 `start.command` 即可运行
> 注：首次运行可能会被 macOS 安全策略阻止，解决方法： 右键点击 start.command → 选择“打开” → 再次确认“打开” 或在终端执行：xattr -d com.apple.quarantine start.command


#### 2) 解压缩，命令行运行（备份方法）
**① Windows：**

进入项目目录（也可以直接在项目目录按住Shift右键打开Powershell）
```bash
cd \Data-Analysis-Agent（替换为你的真实路径）
```

安装依赖

```bash
pip install -r requirements.txt
```

启动服务

```bash
python app.py
```

**② Mac**

进入项目目录
```bash
cd Data-Analysis-Agent
```

安装依赖
```bash
pip3 install -r requirements.txt
```

启动服务
```bash
python3 app.py
```


#### 3) 浏览器打开`http://localhost:5001`

注：此地址为本机地址，不会泄露信息，请放心使用

![Download installation package2](Images/package2.png)

#### 4) 配置API key

![Configure the API3](Images/Deepseek3.png)

#### 5) 后续更新

![Update](Images/Update.png)

注：更新前请先刷新页面

---
### 方式 2：一键安装 + 启动（还在测试，不稳定）

#### 1) Windows（PowerShell）

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

#### 1) macOS / Linux（Shell）

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

#### 2) 浏览器打开（同方式 1）

#### 3) 配置API key（同方式 1）

#### 4) 后续更新（同方式 1）


---
### 方式 3：通过 GitHub 安装（命令行）

#### 1) 克隆仓库

```bash
git clone https://github.com/Zafer-Liu/Data-Analysis-Agent.git
```

#### 2) 进入项目目录

```bash
cd Data-Analysis-Agent
```

#### 3）安装依赖

```bash
pip install -r requirements.txt
```

#### 4）启动服务

```bash
python app.py
```

#### 5) 浏览器打开（同方式 1）

#### 6) 配置API key（同方式 1）

#### 7) 后续更新（同方式 1）

---

# 🛠 斜杠命令 

| Command | Status | Description |
|---|---|---|
| `/chart` | ✅ | 强制优先生成图表 |
| `/sql` | ✅ | 直接执行 SQL |
| `/analyze` | ✅ | 深度统计分析 |
| `/tree` | ✅ | 决策树分析 |
| `/kmeans` | ✅ | K-Means 聚类分析 |
| `/data` | ✅ | 数据探查与预览 |
| `/inset` | ✅ | 缺失值插补处理 |
| `/winsorize` | ✅ | 缩尾处理（极值替换） |
| `/trimming` | ✅ | 截尾处理（极值剔除） |
| `/export` | ✅ | 导出数据文件 |
| `/report` | ✅ | 导出 Word/PDF 报告 |
| `/ppt` | ✅ | 导出 PPT 演示文稿 |
| `/status` | ✅ | 查看任务状态 |

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

# 🗺️ Roadmap

## Version Development Log
- [Version_Update_Log](Version_Update_Log.md)
- [Version_Update_Log_EN](Version_Update_Log_EN.md)

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

A: 在侧栏 ⚙ 中填写 API Key 并保存。


## Q：图表链接重启后失效？

A: 生成的图表储存在本地内容*\outputs\charts目录下

## Q：如何获取API Key？
A: 这里以Deepseek为例，步骤如下：

![Configure the API1](Images/Deepseek1.png)

![Configure the API2](Images/Deepseek2.png)

![Configure the API3](Images/Deepseek3.png)

---

# 📄 License

Apache License 2.0

---

# ⭐ 项目目标

让商业分析像聊天一样简单。
