# 智能商业分析 Agent v1.0.0 LTS

[中文](#中文) · [English](#english)

<a id="中文"></a>

> **发布日期：** 2026 年 6 月 20 日  
> **类型：** 长期支持版本（Long-Term Support）

---

## ✨ v1.0.0 LTS 更新内容

### 🚀 安装与启动

- Windows 安装向导始终显示安装目录选择页，首次安装和覆盖升级均可调整路径
- 首次启动自动创建 `.venv` 并安装项目依赖，后续启动直接复用虚拟环境
- 修复技能模块启动时报错 `ModuleNotFoundError: No module named 'yaml'` 的问题
- 改进依赖安装失败处理，安装不完整时不再错误重启应用

### 🔐 配置安全

安装包明确排除以下用户私有配置：

- `LLM/llm_config.json`：LLM API Key 与自定义模型配置
- `LLM/mcp_config.json`：远程 MCP 地址、环境变量与认证信息
- `data/datasource_config.json`：数据库连接串及外部数据源凭据

每位用户安装后需自行配置 API Key、MCP 服务和数据源。覆盖安装不会替换已有用户配置。

### 🤖 多模型支持

| 提供商 | 默认模型 |
|--------|----------|
| DeepSeek | `deepseek-chat` |
| OpenAI | `gpt-4o-mini` |
| AtlasCloud | `moonshotai/kimi-k2.6` |
| 自定义 | 任意 OpenAI SDK 兼容 API |

### 📊 核心能力

- 使用自然语言查询 Excel、CSV、SQLite、MySQL、PostgreSQL 和 SQL Server 数据
- 六大类别、43 种图表自动推荐
- SSE 流式展示分析过程
- 支持异常值处理、十分位分析、K-Means、决策树、回归和时间序列分析
- 导出 Excel、Word 和 PowerPoint 报告
- 支持知识库、MCP 扩展和多数据源联合分析

---

## 🔧 系统要求

| 项目 | 要求 |
|------|------|
| 操作系统 | Windows 10 / 11（64 位） |
| Python | 3.10 或更高版本，并已加入 PATH |
| 内存 | 建议 8 GB 以上 |
| 磁盘空间 | 建议预留 1 GB，用于程序、虚拟环境和运行输出 |
| 网络 | 首次安装依赖及调用在线 LLM API 时需要网络连接 |

---

## 🛠 安装步骤

1. 安装 [Python 3.10+](https://www.python.org/downloads/)；Windows 安装 Python 时勾选 **Add Python to PATH**
2. 下载 `BusinessAnalyticsAgent_v1.0.0_LTS.exe`
3. 双击安装包，选择安装目录并完成安装
4. 从桌面或开始菜单启动 **Business Analytics Agent**
5. 等待首次依赖安装完成，然后在设置面板中填写自己的 API Key
6. 上传数据文件或连接数据源，开始分析

---

## 🔄 从旧版升级

运行新版安装包并选择原安装目录即可覆盖升级。安装器不会打包或覆盖 API Key、MCP、数据源等用户私有配置。

---

## 🤝 反馈与交流

- **Bug 报告 / 功能建议：** [GitHub Issues](https://github.com/Zafer-Liu/Data-Analysis-Agent/issues)
- **QQ 群：** `991636855`
- **Telegram 群：** [加入官方交流群](https://t.me/+cdRNfS68u9BlYjJl)
- **代码贡献：** 欢迎提交 Pull Request

---

*[查看完整中文文档 →](../README.md) · [English Documentation →](../README_EN.md)*

---

<a id="english"></a>

# Business Analytics Agent v1.0.0 LTS

> **Release date:** June 20, 2026  
> **Release type:** Long-Term Support

---

## 📦 Download

| Platform | File | Size | Description |
|---|---|---|---|
| Windows (x64) | [`BusinessAnalyticsAgent_v1.0.0_LTS.exe`](./BusinessAnalyticsAgent_v1.0.0_LTS.exe) | 12.2 MB | Graphical installer with a selectable destination folder |
| macOS / Linux | *Installer not currently available* | — | Use the [ZIP or command-line installation](../README_EN.md#install) |

**SHA256**

```text
5202D1BD33A811A716279F13096D9EC8DAB442B8A5CA514C62AF49F4D2976430
```

> **Windows security notice:** If Windows displays “Windows protected your PC” or “Unknown publisher,” select “More info” → “Run anyway.” The installer is not currently signed with a Microsoft code-signing certificate.

---

## ✨ What’s New in v1.0.0 LTS

### 🚀 Installation and Startup

- The Windows installer always displays the destination folder page, both for new installations and upgrades
- The first launch creates a `.venv` and installs project dependencies; subsequent launches reuse that environment
- Fixed `ModuleNotFoundError: No module named 'yaml'` during skill-module startup
- Improved dependency failure handling so an incomplete installation no longer triggers an incorrect restart

### 🔐 Configuration Security

The installer explicitly excludes these private user configuration files:

- `LLM/llm_config.json`: LLM API keys and custom model settings
- `LLM/mcp_config.json`: remote MCP addresses, environment variables, and authentication details
- `data/datasource_config.json`: database connection strings and external data-source credentials

Each user must configure their own API keys, MCP servers, and data sources after installation. An in-place upgrade does not replace existing user configuration.

### 🤖 Model Support

| Provider | Default Model |
|---|---|
| DeepSeek | `deepseek-chat` |
| OpenAI | `gpt-4o-mini` |
| AtlasCloud | `moonshotai/kimi-k2.6` |
| Custom | Any OpenAI SDK-compatible API |

### 📊 Core Capabilities

- Query Excel, CSV, SQLite, MySQL, PostgreSQL, and SQL Server data in natural language
- Automatic chart recommendation across 6 categories and 43 chart types
- SSE streaming of the analysis process
- Outlier handling, decile analysis, K-Means, decision trees, regression, and time-series analysis
- Export Excel, Word, and PowerPoint reports
- Knowledge base support, MCP extensions, and multi-source analysis

---

## 🔧 System Requirements

| Item | Requirement |
|---|---|
| Operating system | 64-bit Windows 10 / 11 |
| Python | Python 3.10 or later, added to PATH |
| Memory | 8 GB or more recommended |
| Disk space | Reserve at least 1 GB for the application, virtual environment, and generated output |
| Network | Required for first-time dependency installation and online LLM APIs |

---

## 🛠 Installation

1. Install [Python 3.10+](https://www.python.org/downloads/); on Windows, select **Add Python to PATH**
2. Download `BusinessAnalyticsAgent_v1.0.0_LTS.exe`
3. Run the installer, choose a destination folder, and complete setup
4. Launch **Business Analytics Agent** from the desktop or Start Menu
5. Wait for the first-time dependency installation, then enter your own API key in Settings
6. Upload a data file or connect a data source to begin analyzing

---

## 🔄 Upgrading from an Earlier Version

Run the new installer and select the existing installation directory. The installer neither packages nor overwrites private API key, MCP, or data-source configuration files.

---

## 🤝 Feedback and Community

- **Bug reports / feature requests:** [GitHub Issues](https://github.com/Zafer-Liu/Data-Analysis-Agent/issues)
- **QQ Group:** `991636855`
- **Telegram Group:** [Join the official community](https://t.me/+cdRNfS68u9BlYjJl)
- **Contributions:** Pull Requests are welcome

---

*[完整中文文档 →](../README.md) · [Full English Documentation →](../README_EN.md)*
