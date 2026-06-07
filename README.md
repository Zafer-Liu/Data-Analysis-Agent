# 智能商业分析 Agent

<p align="center">
  <img src="./Images/Banner.png" alt="智能商业分析 Agent Banner" width="100%" />
</p>

<p align="right"><a href="./README_EN.md">English</a></p>

![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg)
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


<p align="center">
  <a href="#features">✨ 项目亮点</a> ·
  <a href="#install">⚙️ 快速安装</a> ·
  <a href="#examples">📈 使用示例</a> ·
  <a href="#llm-config">🤖 LLM配置说明</a> ·
  <a href="#faq">❓ FAQ</a>
</p>

<details>
<summary><strong>📚 完整目录</strong></summary>

<br>

- [赞助商](#sponsors)
- [项目亮点](#features)
- [核心能力](#capabilities)
- [安装方式](#install)
- [斜杠命令](#commands)
- [使用示例](#examples)
- [LLM 配置说明](#llm-config)
- [项目里程碑](#roadmap)
- [FAQ](#faq)
- [Contributor](#contributor)
- [License](#license)
- [项目目标](#goal)

</details>

---

<a id="sponsors"></a>

# 🙏 赞助商

感谢以下赞助商对本项目的支持！

<table>
<tr>
<td width="50%" align="center" valign="top">
<a href="https://doloffer.com/">
<img src="./Images/DolOffer.png" alt="DolOffer Logo" height="70">
</a>
<br>
<br>
<a href="https://doloffer.com/"><strong>DolOffer</strong></a>
<br>
<br>
<p align="left">
感谢 DolOffer 对本项目的支持！DolOffer 是一个专注于数字产品推荐与优惠分享的平台，帮助用户快速发现值得关注的工具、服务和限时福利。平台提供 YouTube Premium、Claude、ChatGPT Plus、Spotify、Apple Music 等多种热门订阅服务，价格低至官方价的 3 折甚至更低，正版稳定，售后无忧。现在通过我们的专属链接注册，并在充值时输入优惠码 <strong>AI8888</strong>，即可额外享受 9 折优惠。
</p>
<a href="https://github.com/Doloffer-g/guide">了解更多 →</a>
</td>
<td width="50%" align="center" valign="top">
<a href="https://www.atlascloud.ai/?utm_source=github&utm_medium=link&utm_campaign=data-analysis-agent">
<img src="./Images/ATLAS%20CLOUD.png" alt="Atlas Cloud Logo" height="70">
</a>
<br>
<br>
<a href="https://www.atlascloud.ai/?utm_source=github&utm_medium=link&utm_campaign=data-analysis-agent"><strong>Atlas Cloud</strong></a>
<br>
<br>
<p align="left">
感谢 Atlas Cloud 对本项目的支持！Atlas Cloud 是一个全模态 AI 推理平台，为开发者提供统一的 AI API 接口，涵盖视频生成、图像生成和大语言模型 API。您无需分别集成多个供应商，只需一次连接即可统一访问 300 多个精选的全模态模型。快来查看 Atlas Cloud 新推出的编程套餐推广活动，获取更经济实惠的 API 访问。
</p>
<a href="https://www.atlascloud.ai/?utm_source=github&utm_medium=link&utm_campaign=data-analysis-agent">了解更多 →</a>
</td>
</tr>
</table>

---


<a id="features"></a>

# ✨ 项目亮点

智析Agent是一个对话式商业数据分析智能体，目标是让非技术用户也能像“聊天”一样完成数据分析。

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

<a id="capabilities"></a>

# 🧠 核心能力

## 1️⃣ 自然语言数据分析

无需编写 SQL,只需输入自然语言:

```text
今年每个月的订单量趋势
```

系统即可自动完成:

- SQL 生成
- 数据查询
- 图表推荐
- 分析总结

![Data Query](Images/Data_query.png)


## 2️⃣ 多数据源支持

支持上传和连接多种数据源:

- **文件**:Excel / CSV
- **数据库**:SQLite、MySQL、PostgreSQL、SQL Server
- **未来计划**:DuckDB、Spark

![Data Preview](Images/Data_preview.png)


## 3️⃣ 智能图表系统

系统会根据查询结果,从以下 6 大类图表中自动推荐最合适的一种:

| 分类 | 图表类型 |
|---|---|
| **对比类** COMPARING | Marimekko_ABS（马里美科-绝对值）、Marimekko_PCT（马里美科-百分比）、Bar_Chart（柱状图）、Grouped_Bar_Chart（分组柱状图）、Stacked_Bar_Chart（堆叠柱状图）、Diverging_Bar_Chart（对比条形图）、Dot_Plot（点图）、Waffle（华夫格）、Bullet_Chart（靶心图）、Sankey_Chart（桑基图）、Heatmap（热力图）、Waterfall（瀑布图） |
| **时间趋势类** TIME | Line_Chart（折线图）、Circular_Line_Chart（圆形折线图）、Slope_Chart（斜率图）、Sparkline（迷你图）、Bump_Chart（凹凸图）、Cycle_Chart（周期图）、Area_Chart（面积图）、Stacked_Area_Chart（堆叠面积图）、Horizon_Chart（地平线图）、Connected_Scatter（连线散点图） |
| **分布类** DISTRIBUTION | Histogram_Pareto_chart（直方图与帕累托图）、Pyramid_Chart（金字塔图）、Error_Bar_Chart（误差条形图）、Box-and-Whisker_Plot（箱线图）、Violin_Chart（小提琴图）、Ridgeline_Plot（山脊线图）、Beeswarm_Plot（分簇散点图）、stem_leaf（茎叶图） |
| **地理类** GEOSPATIAL | Flow_Map（动态流向图）、Dot_Density_Map（点密度地图）、Choropleth_Map（面量图） |
| **关系类** RELATIONSHIP | Scatter_Plot（散点图）、Bubble_Plot（气泡图）、Radar_Charts（雷达图）、Chord_Diagram（弦图）、Arc_Chart（弧图）、Network_Diagram（网络图）、Parallel_Coordinates_Plot（平行坐标图） |
| **占比类** PART-TO-WHOLE | Treemap（矩形树图）、Sunburst_Diagram（旭日图）、Nightingale_Chart（南丁格尔玫瑰图）、Pie_Chart（饼图） |

![Auto Generated](Images/Auto_generated_image.png)


## 4️⃣ SSE 流式分析体验

分析过程实时可见:

```text
[1/4] 正在读取数据结构...
[2/4] 正在生成 SQL...
[3/4] 正在执行查询...
[4/4] 正在生成图表与洞察...
```

相比传统 BI 工具,更透明、更具交互感。


## 5️⃣ 多模型兼容

支持以下模型服务:

- DeepSeek
- OpenAI
- AtlasCloud
- 任意 OpenAI SDK Compatible API

并支持自定义 `base_url`、`model`、`api_key`。默认配置如下:

| Provider | Default Model |
|---|---|
| DeepSeek | `deepseek-v4-flash` |
| OpenAI | `gpt-4o-mini` |
| AtlasCloud | `deepseek-v4-pro` |


## 6️⃣ 数据分析

目前支持的数据分析功能包括:

- 异常值处理(截尾、缩尾处理)
- 十分位分组分析
- K-Means 聚类分析
- 决策树建模
- ……

![Analyze](Images/Analyze.png)


## 7️⃣ 报告生成

支持导出:

- 整理后的 Excel 表格
- docx 格式报告
- 内置风格 PPT

![Output](Images/Output.png)


## 8️⃣ MCP 拓展

支持连接本地或远程 MCP,拓展 Agent 技能。

![MCP](Images/MCP1.png)

- 教程:[MCP_tutorial](Information/MCP_tutorial.md)


## 9️⃣ 知识库输入

支持上传业务知识,让 Agent 更加了解你的数据。

![repository](Images/repository2.png)

- 教程:[repository_tutorial](Information/repository_tutorial.md)


---


<a id="install"></a>

# ⚙️ 安装方式

---

### 🖥️ 方式 1：Windows 安装包（最简单，推荐）

从 [Releases](https://github.com/Zafer-Liu/Data-Analysis-Agent/Releases) 下载最新版本：

| 平台 | 文件 |
|------|------|
| Windows | `BusinessAnalyticsAgent.exe` |

双击安装包，按提示安装即可。
---

### 方式 2：下载压缩包（推荐新手，跨平台）

> **前置要求：Python 3.10+**  
> 还没装？[点此下载](https://www.python.org/downloads/)（Windows 安装时请勾选 **”Add Python to PATH”**）

**第一步：下载并解压**

![Download installation package](Images/package.png)

**第二步：双击启动**

<table>
<tr>
<td><b>Windows</b></td>
<td>双击 <code>start.bat</code></td>
</tr>
<tr>
<td><b>macOS</b></td>
<td>

① 打开终端（Command + 空格 → 输入 Terminal → 回车）  
② 在终端中运行（把路径替换为实际解压位置）：
```bash
chmod +x ~/Downloads/Data-Analysis-Agent/start.command
xattr -d com.apple.quarantine ~/Downloads/Data-Analysis-Agent/start.command
```
③ 双击 `start.command` 即可

</td>
</tr>
</table>

> **首次启动**会自动创建虚拟环境并安装依赖，约需 3–5 分钟，请耐心等待。**之后启动无需等待**。

**第三步：启动后浏览器自动打开** `http://localhost:5001`

![Download installation package2](Images/package2.png)

**第四步：配置 API Key**

![Configure the API3](Images/Deepseek3.png)

**第五步：后续更新**

![Update](Images/Update.png)

---

### 方式 3：一键在线安装

**Windows（在 PowerShell 中运行）：**

```powershell
iwr -useb https://raw.githubusercontent.com/Zafer-Liu/Data-Analysis-Agent/main/install.ps1 | iex
```

安装完成后双击桌面的 `data-analysis-agent.bat` 启动，或：
```powershell
cd $env:USERPROFILE\.data-analysis-agent\Data-Analysis-Agent
.\.venv\Scripts\activate
python app.py
```

**macOS / Linux（在终端中运行）：**

```bash
curl -fsSL https://raw.githubusercontent.com/Zafer-Liu/Data-Analysis-Agent/main/install.sh | sh
```

安装完成后运行：
```bash
data-analysis-agent
```

如提示 `command not found`，将以下内容添加到 `~/.zshrc` 或 `~/.bashrc`，然后重启终端：
```bash
export PATH=”$HOME/.local/bin:$PATH”
```

---

### 方式 4：通过 GitHub Clone

```bash
git clone https://github.com/Zafer-Liu/Data-Analysis-Agent.git
cd Data-Analysis-Agent
pip install -r requirements.txt
python app.py
```

浏览器打开 `http://localhost:5001`，然后配置 API Key（同方式 1）。


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

<a id="examples"></a>
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

<a id="llm-config"></a>

# 🤖 LLM配置说明

## LLM 配置

在侧边栏 ⚙ 中填写：

```text
API Key
Base URL
Model
```

即可切换模型。

---

<a id="roadmap"></a>

# 🗺️ 项目里程碑

> **当前版本 `v5.0`** · 2026 年 6 月 4 日

本次为一次大版本迭代,涵盖 **多数据源**、**智能交互**、**稳定性修复** 与 **安全加固** 四大方向。

---

## 📌 更新概述

1. 多数据源支持
2. SQL 数据库连接改善
3. 数据预览升级
4. AI 主动提问
5. 对话自动保存
6. MCP 工具接入体验优化
7. 防止 AI 编造数据
8. 知识库触发修复
9. 其他体验修复

---

## 📖 详细更新日志

- [Version Update Log（中文）](Information/Version_Update_Log.md)
- [Version Update Log (English)](Information/Version_Update_Log_EN.md)

---

<a id="faq"></a>

# ❓ FAQ

<details>
<summary><b>📦 安装与启动</b></summary>

<br>

<details>
<summary><b>安装依赖时网络超时？</b></summary>

脚本会自动切换清华源重试。

若仍失败，请手动执行：

```bash
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

</details>

<details>
<summary><b>pip install 报错 / 安装依赖失败？</b></summary>

脚本会自动切换国内镜像（清华源）重试。

若仍失败，请手动指定镜像：

```bash
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

同时请确保磁盘至少保留 **2 GB** 可用空间。

</details>

<details>
<summary><b>Python 版本不对（需要 3.10+）？</b></summary>

查看当前版本：

```bash
python --version
```

如果版本低于 3.10，请前往：

https://www.python.org/downloads/

下载并安装最新版本。

</details>

<details>
<summary><b>start.bat 双击没反应或一闪而过？</b></summary>

Python 未正确加入系统 PATH。

重新安装 Python 时勾选 **"Add Python to PATH"**，然后重启电脑再试。

</details>

<details>
<summary><b>macOS 运行 start.command 被系统阻止？</b></summary>

在终端执行以下命令解除限制：

```bash
xattr -d com.apple.quarantine /你的路径/start.command
```

如果提示：

> “无法打开，因为无法验证开发者”

可以：

1. 右键点击 `start.command`
2. 选择“打开”
3. 再次点击“打开”

</details>

</details>

---

<details>
<summary><b>🔑 API 配置</b></summary>

<br>

<details>
<summary><b>提示未配置 LLM？</b></summary>

在侧栏 ⚙ 中填写 API Key 并保存。

</details>

<details>
<summary><b>如何获取 API Key？</b></summary>

这里以 DeepSeek 为例：

![Configure the API1](Images/Deepseek1.png)

![Configure the API2](Images/Deepseek2.png)

![Configure the API3](Images/Deepseek3.png)

</details>

</details>

---

<details>
<summary><b>🗄️ 数据库连接</b></summary>

<br>

<details>
<summary><b>如何连接 SQL 数据库？</b></summary>

请使用以下格式连接：

```text
mysql+pymysql://用户名:密码@主机:端口/数据库名
```

示例：

❌ 错误写法：

```text
mysql://user:pass@host:3306/dbname
```

✅ 正确写法：

```text
mysql+pymysql://user:pass@host:3306/dbname
```

</details>

</details>

---

<details>
<summary><b>📊 图表与文件</b></summary>

<br>

<details>
<summary><b>图表链接重启后失效？</b></summary>

生成的图表保存在本地目录：

```text
outputs/charts
```

可直接使用浏览器打开对应的 HTML 文件。

</details>

</details>

---

<a id="contributor"></a>

# 🚀 寻找一起改变世界的 Contributor

一个好的开源项目，从来不是一个人的独角戏。  
我们正在打造一个**能真正应对复杂业务场景**的数据工具——它需要在海量数据中极速穿梭，在多表逻辑间游刃有余，在可视化看板上洞察先机。  
而现在，我们遇到了几个极富挑战、也极能体现技术价值的问题。如果你热爱解决“硬核”问题，这里正需要你：

---

### 急需你一起来攻克这些难题：
- **多 Sheets 场景下的表间逻辑判断优化** —— 如何智能梳理几十张表之间的依赖与计算？
- **可视化看板的交互与性能优化** —— 让数据故事讲得更流畅、更直观、更震撼。
- **特殊业务场景下的模型能力提升** —— 那些通用工具搞不定的边缘业务。
- **远程服务器调用** —— 搭建远程GPU调用框架。
---

### 为什么值得你加入？

- 你将直面**真实、有深度、非玩具级**的技术挑战
- 你的代码会直接影响**一线业务用户**的工作效率
- 自由贡献，灵活协作——提 PR 或直接沟通，完全由你
- 优秀 Contributor 将有机会成为项目 Committer

---

### 如何加入？

- 直接 **Pull Request**，我们会在 24 小时内 review
- 或联系邮箱：`rusboldtshanti34@gmail.com`（请备注“Contributor+擅长方向”）
---



<a id="license"></a>

# 📄 License

Apache License 2.0

---

<a id="goal"></a>

# ⭐ 项目目标

把过程交给“智析”，把时间留给思考。



