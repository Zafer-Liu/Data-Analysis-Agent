# Intelligent Business Analysis Agent

<p align="right"><a href="./README_EN.md">English</a></p>

<p align="center">
  <img src="./Information/assets/Banner.png" alt="智能商业分析 Agent Banner" width="100%" />
</p>

<div align="center">

[![Trendshift](https://trendshift.io/api/badge/trendshift/repositories/29216/daily?language=Python)](https://trendshift.io/repositories/29216?utm_source=trendshift-badge&utm_medium=badge&utm_campaign=badge-trendshift-29216)

![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg)
![Flask](https://img.shields.io/badge/Backend-Flask-black.svg)
![Plotly](https://img.shields.io/badge/Visualization-Plotly-3F4F75.svg)
![LLM](https://img.shields.io/badge/LLM-OpenAI%20Compatible-green.svg)
![Charts](https://img.shields.io/badge/Charts-41_Types-orange.svg)
![License](https://img.shields.io/badge/License-CC%20BY--NC%204.0-blue.svg)

</div>

> An AI Agent built for business analysis scenarios.
> After connecting a data source, users simply ask questions in natural language — the system automatically handles:
>
> - Data schema discovery
> - SQL generation & execution
> - Chart generation
> - Business insight analysis

> © 2026 Zafer-Liu · Chinese software copyright registration applied (Acceptance No. 2026R11S0817877, Software: Natural-Language Interactive Business Data Analysis Software V1.0) · Licensed under CC BY-NC 4.0. **Unauthorized commercial use is prohibited; contact the author for commercial licensing.**

> 💬 **Official Community:** QQ Group `991636855` · [Telegram Group](https://t.me/+cdRNfS68u9BlYjJl) · [Discord](https://discord.gg/EEG4Sw7tde)

<p align="center">
  <a href="#changelog">📝 Changelog</a> ·
  <a href="#features">✨ Highlights</a> ·
  <a href="#agent-manager">🧩 Agent Manager</a> ·
  <a href="#install">⚙️ Installation</a> ·
  <a href="#examples">📈 Examples</a> ·
  <a href="#llm-config">🤖 LLM Setup</a> ·
  <a href="#faq">❓ FAQ</a>
</p>

<details>
<summary><strong>📚 Full Table of Contents</strong></summary>

<br>

- [📝 Changelog](#changelog)
- [🙏 Sponsors](#sponsors)
- [✨ Project Highlights](#features)
- [🧠 Core Capabilities](#capabilities)
- [⚙️ Installation](#install)
- [🛠 Slash Commands](#commands)
- [📈 Usage Examples](#examples)
- [🤖 LLM Configuration](#llm-config)
- [❓ FAQ](#faq)
- [🤝 Contribution Guide](#contributing)
- [📄 License](#license)
- [⭐ Project Goal](#goal)

</details>

---

<a id="changelog"></a>

# 📝 Changelog

> **Current version [`v1.2.0 LTS`](https://github.com/Zafer-Liu/Data-Analysis-Agent/releases/latest)** · July 17, 2026 · [📦 Download Installer](https://github.com/Zafer-Liu/Data-Analysis-Agent/releases/latest)

v1.2.0 LTS focuses on a smoother, more reliable data-analysis experience:

1. **AI Team Collaboration**: Break complex work into tasks for specialist Agents, with progress and results clearly visible.
2. **Business Canvas and Skills**: Map business relationships, reuse proven analysis methods, and create dashboards and visual results faster.
3. **More Reliable Data Processing**: Long-running tasks and local-data workflows are more dependable and reassuring.
4. **Local Data Cleanup**: Review storage and cleanup candidates in **Settings → Storage**; files go to the Recycle Bin first and important data remains protected.
5. **Refined Everyday Experience**: Chat, workspace, and frequent actions are clearer and easier to use.

## 📖 Detailed Changelog

- [Version Update Log (中文)](./Information/Version_Update_Log.md)
- [Version Update Log (English)](./Information/Version_Update_Log_EN.md)

---

<a id="sponsors"></a>

# 🙏 Sponsors

Thank you to the following sponsors for supporting this project!

## ☁️ Sponsored by Bloome

<a href="https://bloome.im/app?ref=ZaferLiu&utm_medium=github&utm_source=Zafer-Liu-Data-Analysis-Agent-ivor-202607"><img src="./Information/assets/bloome.png" alt="Bloome banner" /></a>

Thank you to Bloome for sponsoring this project! Bloome lets multiple AI agents (Claude, ChatGPT, DeepSeek, etc.) collaborate in a single conversation — zero setup, cloud-based, works on web and mobile, and you can share your configured agents with your team.

👉 **[Try Bloome](https://bloome.im/app?ref=ZaferLiu&utm_medium=github&utm_source=Zafer-Liu-Data-Analysis-Agent-ivor-202607)**

---

## ☁️ Sponsored by DolOffer

<p align="center"><a href="https://doloffer.com/"><img src="./Information/assets/DolOffer.png" alt="DolOffer logo" height="80" /></a></p>

Thank you to DolOffer for supporting this project! DolOffer is a platform focused on digital product recommendations and deal sharing, helping users quickly discover useful tools, services, and limited-time offers. The platform provides popular subscription services such as YouTube Premium, Claude, ChatGPT Plus, Spotify, Apple Music, and more, with prices as low as 30% of the official price or even lower. All services are genuine, stable, and backed by reliable after-sales support. Register through our exclusive link and use the coupon code **AI8888** when topping up to enjoy an additional 10% discount.

👉 **[Learn more](https://github.com/Doloffer-g/guide)**

---

## ☁️ Sponsored by Atlas Cloud

<div align="center"><a href="https://www.atlascloud.ai/?utm_source=github&utm_medium=link&utm_campaign=data-analysis-agent"><img src="./Information/assets/ATLAS%20CLOUD.png" alt="Atlas Cloud logo" height="80" /></a></div>

Thank you to Atlas Cloud for supporting this project! Atlas Cloud is an all-modal AI inference platform that provides developers with a unified AI API interface, covering video generation, image generation, and large language model APIs. Instead of integrating multiple providers separately, you can connect once and access more than 300 carefully selected all-modal models through a single interface. Check out Atlas Cloud’s newly launched coding plan promotion to get more cost-effective API access.

👉 **[Learn more](https://www.atlascloud.ai/?utm_source=github&utm_medium=link&utm_campaign=data-analysis-agent)**

---

<a id="features"></a>

# ✨ Project Highlights

Business Analyst Agent is a conversational business data analysis system, designed to let non-technical users perform data analysis just by chatting.

After uploading an Excel/CSV file or connecting a database, users can ask questions directly:

```text
What is the sales trend for the last three months?
Which region has the highest profit?
Generate a user growth chart for me
```

The system will automatically:

1. Understand the intent of the question
2. Analyze the data structure (Schema)
3. Auto-generate SQL
4. Execute the query
5. Recommend a chart type
6. Output business insights

All delivered via **SSE (Server-Sent Events) streaming**, showing the analysis process in real time.

---

<a id="demo"></a>

## 🎬 Product Demo

> One demonstration is worth a thousand screenshots.

<div align="center">

https://github.com/user-attachments/assets/4cc6f9d7-52d9-42c5-b3e7-059019f67275

</div>

<p align="center">
<em>Product demo — Chinese</em>
</p>

---

<a id="agent-manager"></a>

# 🧩 Recommended Management: Agent Manager

Business Analytics Agent can run independently or with **Agent Manager** for a more convenient desktop management experience.

<details>
<summary><strong>Learn how Agent Manager manages this project</strong></summary>

<br>

**Agent Manager** is a unified control center for local AI Agent projects. After adding Business Analytics Agent, you can:

* Start or stop the Agent with one click
* View logs and port status in real time
* Open the web analysis interface inside the desktop app
* Ask the Manager Agent to start or open this project in natural language
* Create a temporary public sharing link for demos

```text
Example:
Start Business Analytics Agent and open its interface.
```

This is especially useful when you frequently demonstrate, debug, or run multiple Agent projects.

👉 Project: [Agent Manager](https://github.com/Zafer-Liu/Agent_Manager)

</details>

---

<a id="capabilities"></a>

# 🧠 Core Capabilities

## 1️⃣ Natural Language Data Analysis

No SQL required — just type in plain language:

```text
Monthly order volume trend for this year
```

The system will automatically:

- Generate SQL
- Query the data
- Recommend a chart
- Summarize the analysis

![Data Query](./Information/assets/Data_query.png)

## 2️⃣ Multi-Source Data Support

Supports uploading and connecting to multiple data sources:

- **Files**: Excel / CSV
- **Databases**: SQLite, MySQL, PostgreSQL, SQL Server
- **Planned**: DuckDB, Spark

![Data Preview](./Information/assets/Data_preview.png)

## 3️⃣ Intelligent Chart System

Based on your query results, the system automatically recommends the most appropriate chart from 6 major categories:

| Category | Chart Types |
|---|---|
| **COMPARING** | Marimekko_ABS, Marimekko_PCT, Bar_Chart, Grouped_Bar_Chart, Stacked_Bar_Chart, Diverging_Bar_Chart, Dot_Plot, Waffle, Bullet_Chart, Sankey_Chart, Heatmap, Waterfall |
| **TIME** | Line_Chart, Circular_Line_Chart, Slope_Chart, Sparkline, Bump_Chart, Cycle_Chart, Area_Chart, Stacked_Area_Chart, Horizon_Chart, Connected_Scatter |
| **DISTRIBUTION** | Histogram_Pareto_chart, Pyramid_Chart, Error_Bar_Chart, Box-and-Whisker_Plot, Violin_Chart, Ridgeline_Plot, Beeswarm_Plot, stem_leaf |
| **GEOSPATIAL** | Flow_Map, Dot_Density_Map, Choropleth_Map |
| **RELATIONSHIP** | Scatter_Plot, Bubble_Plot, Radar_Charts, Chord_Diagram, Arc_Chart, Network_Diagram, Parallel_Coordinates_Plot |
| **PART-TO-WHOLE** | Treemap, Sunburst_Diagram, Nightingale_Chart, Pie_Chart |

![Auto Generated](./Information/assets/Auto_generated_image.png)

## 4️⃣ SSE Streaming Analysis Experience

The analysis process is visible in real time:

```text
[1/4] Reading data structure...
[2/4] Generating SQL...
[3/4] Executing query...
[4/4] Generating chart and insights...
```

More transparent and interactive than traditional BI tools.

## 5️⃣ Multi-Model Compatibility

Supports the following model providers:

- DeepSeek
- OpenAI
- AtlasCloud
- Any OpenAI SDK-compatible API

Supports custom `base_url`, `model`, and `api_key`. Default configurations:

| Provider | Default Model |
|---|---|
| DeepSeek | `deepseek-v4-flash` |
| OpenAI | `gpt-4o-mini` |
| AtlasCloud | `deepseek-v4-pro` |

## 6️⃣ Data Analysis

Currently supported analysis features:

- Outlier handling (trimming and winsorizing)
- Decile grouping analysis
- K-Means clustering
- Decision tree modeling
- And more...

## 7️⃣ Report Generation

Supports exporting:

- Formatted Excel spreadsheets
- Reports in `.docx` format
- Built-in styled PowerPoint presentations

## 8️⃣ MCP Extensions

Supports connecting to local or remote MCP servers to expand the Agent's capabilities.

![MCP](./Information/assets/MCP1.png)

- Tutorial: [MCP_tutorial](./Information/MCP_tutorial.md)

## 9️⃣ Knowledge Base Input

Supports uploading business knowledge to help the Agent better understand your data.

![repository](./Information/assets/repository2.png)

- Tutorial: [repository_tutorial](./Information/repository_tutorial.md)

---

<a id="install"></a>

# ⚙️ Installation

### 🖥️ Option 1: Windows Installer (Easiest — Recommended)

Download the latest version from [GitHub Releases](https://github.com/Zafer-Liu/Data-Analysis-Agent/tree/main/Releases):

| Platform | File |
|---|---|
| Windows | `BusinessAnalyticsAgent_v1.0.0_LTS.exe` |

> **Prerequisites: Python 3.10+ and 64-bit Windows 10 / 11.**

Run the installer and follow the setup wizard. After installation, launch **Business Analytics Agent** from the desktop or Start Menu.

---

### Option 2: Download the ZIP (Recommended for Beginners, Cross-Platform)

> **Prerequisite: Python 3.10+**
> Don't have it? [Download here](https://www.python.org/downloads/) (Windows: check **"Add Python to PATH"** during install)

**Step 1: Download and extract**

![Download installation package](Information/assets/package.png)

**Step 2: Double-click to launch**

| OS | Action |
|---|---|
| **Windows** | Double-click `start.bat` |
| **macOS** | ① Open Terminal (Command + Space → type Terminal → Enter) ② Run the following (replace the path with your actual extraction location): `chmod +x ~/Downloads/Data-Analysis-Agent/start.command` then `xattr -d com.apple.quarantine ~/Downloads/Data-Analysis-Agent/start.command` ③ Double-click `start.command` |

> **First launch** will automatically create a virtual environment and install dependencies — this takes about 3–5 minutes. **Subsequent launches are instant.**

**Step 3: Browser auto-opens** at `http://localhost:5001`

![Download installation package2](./Information/assets/package2.png)

**Step 4: Configure your API Key**

![Configure the API3](./Information/assets/Deepseek3.png)

**Step 5: Future updates**

![Update](./Information/assets/Update.png)

---

### Option 3: One-Click Online Install

**Windows (run in PowerShell):**

```powershell
iwr -useb https://raw.githubusercontent.com/Zafer-Liu/Data-Analysis-Agent/main/install.ps1 | iex
```

After installation, double-click `data-analysis-agent.bat` on your desktop, or run:
```powershell
cd $env:USERPROFILE\.data-analysis-agent\Data-Analysis-Agent
.\.venv\Scripts\activate
python app.py
```

**macOS / Linux (run in Terminal):**

```bash
curl -fsSL https://raw.githubusercontent.com/Zafer-Liu/Data-Analysis-Agent/main/install.sh | sh
```

After installation, run:
```bash
data-analysis-agent
```

If you see `command not found`, add the following to `~/.zshrc` or `~/.bashrc`, then restart Terminal:
```bash
export PATH="$HOME/.local/bin:$PATH"
```

---

### Option 4: Clone from GitHub

```bash
git clone https://github.com/Zafer-Liu/Data-Analysis-Agent.git
cd Data-Analysis-Agent
pip install -r requirements.txt
python app.py
```

Open `http://localhost:5001` in your browser and configure your API Key.

---

<a id="commands"></a>

# 🛠 Slash Commands

| Command | Status | Description |
|---|---|---|
| `/chart` | ✅ | Force chart generation as the priority output |
| `/sql` | ✅ | Execute SQL directly |
| `/analyze` | ✅ | Deep statistical analysis |
| `/tree` | ✅ | Decision tree analysis |
| `/kmeans` | ✅ | K-Means clustering analysis |
| `/data` | ✅ | Data exploration and preview |
| `/inset` | ✅ | Missing value imputation |
| `/winsorize` | ✅ | Winsorizing (replace extreme values) |
| `/trimming` | ✅ | Trimming (remove extreme values) |
| `/export` | ✅ | Export data file |
| `/report` | ✅ | Export Word/PDF report |
| `/ppt` | ✅ | Export PowerPoint presentation |
| `/status` | ✅ | View task status |

---

<a id="examples"></a>

# 📈 Usage Examples

## Example 1: Trend Analysis

User input:

```text
Sales trend for the last 12 months
```

System output:

- SQL query
- Trend line chart
- Sales growth analysis

---

## Example 2: Regional Analysis

User input:

```text
Which region has the highest profit?
```

System output:

- Regional profit rankings
- Bar chart
- Regional business insights

---

## Example 3: Chart-First Mode

User input:

```text
/chart User growth overview
```

The system will prioritize generating a visualization.

---

<a id="llm-config"></a>

# 🤖 LLM Configuration

In the sidebar ⚙, fill in:

```text
API Key
Base URL
Model
```

to switch between models.

---

<a id="faq"></a>

# ❓ FAQ

<details>
<summary><b>📦 Installation & Startup</b></summary>

<details>
<summary><b>Network timeout while installing dependencies?</b></summary>

The script will automatically switch to the Tsinghua mirror and retry.

If it still fails, run manually:

```bash
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

</details>

<details>
<summary><b>pip install error / dependency installation failed?</b></summary>

The script automatically retries using a domestic mirror (Tsinghua).

If it still fails, specify the mirror manually:

```bash
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

Also ensure at least **2 GB** of free disk space is available.

</details>

<details>
<summary><b>Wrong Python version (requires 3.10+)?</b></summary>

Check your current version:

```bash
python --version
```

If below 3.10, download the latest version at:

https://www.python.org/downloads/

</details>

<details>
<summary><b>Double-clicking start.bat does nothing or flashes briefly?</b></summary>

Python is not correctly added to the system PATH.

Reinstall Python and check **"Add Python to PATH"**, then restart your computer and try again.

</details>

<details>
<summary><b>macOS blocks start.command from running?</b></summary>

Run this in Terminal to remove the restriction:

```bash
xattr -d com.apple.quarantine /your/path/to/start.command
```

If you see:

> "Cannot be opened because the developer cannot be verified"

You can:

1. Right-click `start.command`
2. Select "Open"
3. Click "Open" again

</details>

</details>

---

<details>
<summary><b>🔑 API Configuration</b></summary>

<details>
<summary><b>Prompted that LLM is not configured?</b></summary>

Enter your API Key in the sidebar ⚙ and save.

</details>

<details>
<summary><b>How do I get an API Key?</b></summary>

Using DeepSeek as an example:

![Configure the API1](./Information/assets/Deepseek1.png)

![Configure the API2](./Information/assets/Deepseek2.png)

![Configure the API3](./Information/assets/Deepseek3.png)

</details>

</details>

---

<details>
<summary><b>🗄️ Database Connection</b></summary>

<details>
<summary><b>How do I connect to a SQL database?</b></summary>

Use the following connection format:

```text
mysql+pymysql://username:password@host:port/database_name
```

Example:

❌ Wrong:

```text
mysql://user:pass@host:3306/dbname
```

✅ Correct:

```text
mysql+pymysql://user:pass@host:3306/dbname
```

</details>

</details>

---

<details>
<summary><b>📊 Charts & Files</b></summary>

<details>
<summary><b>Chart links broken after restart?</b></summary>

Generated charts are saved locally at:

```text
outputs/charts
```

You can open the corresponding HTML files directly in your browser.

</details>

</details>

---

<a id="contributing"></a>

# 🤝 Contribution Guide

We welcome every kind of contribution, whether you are fixing a bug, improving documentation, or adding a feature.

### Contribution Workflow

1. **Fork this repository**
2. **Create a feature branch**
   ```bash
   git checkout -b feature/your-feature-name
   ```
3. **Commit your changes**
   ```bash
   git add .
   git commit -m "feat: add your feature"
   ```
   We recommend following the [Conventional Commits](https://www.conventionalcommits.org/) specification.
4. **Push your branch**
   ```bash
   git push origin feature/your-feature-name
   ```
5. **Open a Pull Request**
   - Describe your changes
   - Include screenshots or a recording for new UI features
   - Link related issues, if any

### Code Standards

- Follow [PEP 8](https://pep8.org/) for Python code
- Run `flake8` before submitting changes
- Add relevant tests for new features
- Keep commit messages clear and concise

### Reporting Bugs

Report bugs through [GitHub Issues](https://github.com/Zafer-Liu/Data-Analysis-Agent/issues) and include:

- 📋 Steps to reproduce
- 🎯 Expected behavior
- ⚠️ Actual behavior
- 📸 Screenshots, when applicable
- 💻 Environment details, including OS and Python version

---

## 🚀 Looking for Contributors

A great open-source project is never a solo act.
We're building a **data tool that can genuinely handle complex business scenarios** — one that races through massive datasets, navigates multi-table logic with ease, and surfaces insights on visual dashboards.
We've hit a few deeply challenging, high-value problems. If you love solving hard technical problems, we need you:

---

### Key challenges we'd love your help with:

- **Multi-sheet inter-table logic optimization** — How do you intelligently map dependencies and calculations across dozens of sheets?
- **Visualization dashboard interactivity & performance** — Making data stories flow more smoothly, intuitively, and powerfully.
- **Model capability enhancement for specialized business scenarios** — The edge cases that general-purpose tools can't handle.
- **Remote server invocation** — Building a framework for remote GPU calls.

---

### Why is it worth joining?

- You'll tackle **real, deep, non-toy technical challenges**
- Your code will directly impact the productivity of **front-line business users**
- Contribute freely, collaborate flexibly — submit a PR or reach out directly, entirely up to you
- Outstanding contributors may be invited to become project Committers

---

### How to join?

- Open a **Pull Request** directly — we review within 24 hours
- Or contact us at: `rusboldtshanti34@gmail.com` (please note "Contributor + area of expertise")
- Join the official community: QQ Group `991636855` or [Telegram Group](https://t.me/+cdRNfS68u9BlYjJl) or [Discord](https://discord.gg/EEG4Sw7tde)

---

<a id="license"></a>

# 📄 License

This software is licensed under **CC BY-NC 4.0** and a Chinese software copyright registration has been applied for (Acceptance No. 2026R11S0817877, Software: Natural-Language Interactive Business Data Analysis Software V1.0).  
See the [LICENSE](./LICENSE) file for the full terms.

> Unauthorized commercial use is prohibited; contact the author for a separate commercial license.

---

<a id="goal"></a>

# ⭐ Project Goal

Leave the process to Business Analytics Agent, and save your time for thinking.

---


<div align="center">

⭐️ If this project helps you, please give it a star!

Made with ❤️ by [Zafer-Liu](https://github.com/Zafer-Liu)

</div>
� by [Zafer-Liu](https://github.com/Zafer-Liu)

</div>
