<p align="center">
  <img src="https://www.atlascloud.ai/logo.svg" alt="Atlas Cloud" width="80" />
</p>

<h1 align="center">Atlas Cloud MCP Server</h1>

<p align="center">
  <a href="https://www.npmjs.com/package/atlascloud-mcp"><img src="https://img.shields.io/npm/v/atlascloud-mcp.svg?style=flat&colorA=18181B&colorB=28CF8D" alt="npm version" /></a>
  <a href="https://www.npmjs.com/package/atlascloud-mcp"><img src="https://img.shields.io/npm/dm/atlascloud-mcp.svg?style=flat&colorA=18181B&colorB=28CF8D" alt="npm downloads" /></a>
  <a href="https://github.com/AtlasCloudAI/mcp-server"><img src="https://img.shields.io/github/license/AtlasCloudAI/mcp-server?style=flat&colorA=18181B&colorB=28CF8D" alt="license" /></a>
  <a href="https://github.com/AtlasCloudAI/mcp-server"><img src="https://img.shields.io/github/stars/AtlasCloudAI/mcp-server?style=flat&colorA=18181B&colorB=28CF8D" alt="github stars" /></a>
</p>

<p align="center">
  <a href="../README.md">English</a> | 中文 | <a href="./README.ja.md">日本語</a> | <a href="./README.ko.md">한국어</a> | <a href="./README.es.md">Español</a> | <a href="./README.fr.md">Français</a>
</p>

<p align="center">
  <a href="https://www.atlascloud.ai">Atlas Cloud</a> 的 MCP（模型上下文协议）服务器 —— 一站式 AI API 聚合平台，提供图片生成、视频生成和大语言模型服务。
</p>

---

## 功能特性

- **模型发现** — 浏览 300+ 可用 AI 模型，包含价格和能力信息
- **图片生成** — 使用 Seedream、Qwen-Image、Flux、Imagen 等模型生成图片
- **视频生成** — 使用 Kling、Vidu、Seedance、Wan、Hailuo、Veo 等模型生成视频
- **LLM 对话** — 与 DeepSeek、Qwen、GLM、MiniMax 等大语言模型对话（兼容 OpenAI 格式）
- **媒体上传** — 上传本地图片/媒体文件，用于图片编辑和图生视频等场景
- **快速生成** — 一步到位，自动搜索模型并构建参数
- **文档搜索** — 在 IDE 中直接搜索 Atlas Cloud 文档、模型和 API 参考
- **动态 Schema** — 自动获取每个模型的参数定义，确保 API 调用准确

## 快速开始

### 前提条件

- Node.js >= 18
- Atlas Cloud API Key — [免费获取](https://www.atlascloud.ai/console/api-keys)

### IDE 和编辑器（JSON 配置）

在你的 MCP 配置文件中添加以下内容，适用于所有支持 MCP 的 IDE 和编辑器：

```json
{
  "mcpServers": {
    "atlascloud": {
      "command": "npx",
      "args": ["-y", "atlascloud-mcp"],
      "env": {
        "ATLASCLOUD_API_KEY": "your-api-key-here"
      }
    }
  }
}
```

| 客户端 | 配置位置 |
|--------|---------|
| [Cursor](https://cursor.com) | Settings → MCP → Add Server |
| [Windsurf](https://codeium.com/windsurf) | Settings → MCP → Add Server |
| [VS Code (Copilot)](https://code.visualstudio.com) | `.vscode/mcp.json` 或 Settings → MCP |
| [Trae](https://trae.ai) | Settings → MCP → Add Server |
| [Zed](https://zed.dev) | Settings → MCP |
| [JetBrains IDEs](https://www.jetbrains.com) | Settings → Tools → AI Assistant → MCP |
| [Claude Desktop](https://claude.ai/download) | `claude_desktop_config.json` |
| [ChatGPT Desktop](https://openai.com/chatgpt/desktop) | Settings → MCP |
| [Amazon Q Developer](https://aws.amazon.com/q/developer/) | MCP Configuration |

### VS Code 扩展

以下 VS Code 扩展同样支持 MCP，使用相同的 JSON 配置格式：

| 扩展 | 安装方式 |
|------|---------|
| [Cline](https://github.com/cline/cline) | MCP Marketplace → Add Server |
| [Roo Code](https://github.com/RooCodeInc/Roo-Code) | Settings → MCP → Add Server |
| [Continue](https://continue.dev) | `config.yaml` → MCP |

### 命令行工具

```bash
# Claude Code
claude mcp add atlascloud -- npx -y atlascloud-mcp

# Gemini CLI
gemini mcp add atlascloud -- npx -y atlascloud-mcp

# OpenAI Codex CLI
codex mcp add atlascloud -- npx -y atlascloud-mcp

# Goose CLI
goose mcp add atlascloud -- npx -y atlascloud-mcp
```

> 使用命令行工具时，请确保在 shell 中设置 `ATLASCLOUD_API_KEY` 环境变量。

### Skills 版本（Claude Code）

如果你更喜欢使用 Skills 而非 MCP，我们也提供了 [Atlas Cloud Skills](https://github.com/AtlasCloudAI/atlas-cloud-skills) 包，适用于 Claude Code 及其他支持 Skills 的 AI 代理。

## 可用工具

| 工具 | 描述 |
|------|------|
| `atlas_search_docs` | 按关键词搜索 Atlas Cloud 文档和模型 |
| `atlas_list_models` | 列出所有可用模型，可按类型过滤（Text/Image/Video） |
| `atlas_get_model_info` | 获取模型详情，包括 API Schema、参数说明和使用示例 |
| `atlas_generate_image` | 使用任意支持的图片模型生成图片 |
| `atlas_generate_video` | 使用任意支持的视频模型生成视频 |
| `atlas_quick_generate` | 一步生成 — 自动按关键词搜索模型、构建参数并提交任务 |
| `atlas_upload_media` | 上传本地文件获取 URL，用于图片编辑/图生视频等模型 |
| `atlas_chat` | 与大语言模型对话（兼容 OpenAI 格式） |
| `atlas_get_prediction` | 查询图片/视频生成任务的状态和结果 |

## 使用示例

### 搜索模型

> "搜索 Atlas Cloud 上的视频生成模型"

### 生成图片

> "用 Seedream 生成一张太空猫的图片"

AI 助手会：
1. 用 `atlas_list_models` 查找 Seedream 图片模型
2. 用 `atlas_get_model_info` 获取模型参数
3. 用 `atlas_generate_image` 配合正确参数生成图片

### 生成视频

> "用 Kling v3 创建一段火箭发射的视频"

### 上传本地图片进行编辑或生成视频

> "帮我把这张图 /Users/me/photos/cat.jpg 加个帽子"

AI 助手会：
1. 用 `atlas_upload_media` 上传本地文件获取 URL
2. 查找图片编辑模型
3. 用 `atlas_generate_image` 配合上传的 URL 进行编辑

> **注意**：上传的文件仅供 Atlas Cloud 生成任务临时使用，文件可能会被定期清理。请勿将此功能用作长期文件存储，滥用可能导致 API Key 被封禁。

### LLM 对话

> "让 Qwen 解释量子计算"

## 开发

```bash
npm install
npm run build
npm run dev
```

## 许可证

MIT
