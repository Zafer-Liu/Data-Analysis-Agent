<p align="center">
  <img src="https://www.atlascloud.ai/logo.svg" alt="Atlas Cloud" width="80" />
</p>

<h1 align="center">Atlas Cloud MCP Server</h1>

<p align="center">
  <a href="https://www.npmjs.com/package/atlascloud-mcp"><img src="https://img.shields.io/npm/v/atlascloud-mcp.svg?style=flat&colorA=18181B&colorB=28CF8D" alt="npm version" /></a>
  <a href="https://www.npmjs.com/package/atlascloud-mcp"><img src="https://img.shields.io/npm/dm/atlascloud-mcp.svg?style=flat&colorA=18181B&colorB=28CF8D" alt="npm downloads" /></a>
  <a href="https://github.com/AtlasCloudAI/mcp-server"><img src="https://img.shields.io/github/license/AtlasCloudAI/mcp-server?style=flat&colorA=18181B&colorB=28CF8D" alt="license" /></a>
</p>

<p align="center">
  <a href="../README.md">English</a> | <a href="./README.zh-CN.md">中文</a> | 日本語 | <a href="./README.ko.md">한국어</a> | <a href="./README.es.md">Español</a> | <a href="./README.fr.md">Français</a>
</p>

<p align="center">
  <a href="https://www.atlascloud.ai">Atlas Cloud</a> の MCP（Model Context Protocol）サーバー — 画像生成、動画生成、LLM を提供する AI API 統合プラットフォーム。
</p>

---

## 機能

- **モデル探索** — 300以上の AI モデルを価格や機能と共に一覧表示
- **画像生成** — Seedream、Qwen-Image、Flux、Imagen などのモデルで画像を生成
- **動画生成** — Kling、Vidu、Seedance、Wan、Hailuo、Veo などのモデルで動画を生成
- **LLM チャット** — DeepSeek、Qwen、GLM、MiniMax などの LLM と対話（OpenAI 互換形式）
- **メディアアップロード** — ローカル画像/メディアファイルをアップロードし、画像編集や画像から動画生成に使用
- **クイック生成** — ワンステップでモデル検索からパラメータ構築まで自動実行
- **ドキュメント検索** — IDE から Atlas Cloud のドキュメント、モデル、API リファレンスを直接検索
- **動的スキーマ** — 各モデルのパラメータスキーマを自動取得

## クイックスタート

### 前提条件

- Node.js >= 18
- Atlas Cloud API Key — [無料で取得](https://www.atlascloud.ai/console/api-keys)

### IDE・エディタ（JSON 設定）

MCP 設定ファイルに以下を追加 — MCP 対応のすべての IDE・エディタで動作します：

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

| クライアント | 設定場所 |
|-------------|---------|
| [Cursor](https://cursor.com) | Settings → MCP → Add Server |
| [Windsurf](https://codeium.com/windsurf) | Settings → MCP → Add Server |
| [VS Code (Copilot)](https://code.visualstudio.com) | `.vscode/mcp.json` または Settings → MCP |
| [Trae](https://trae.ai) | Settings → MCP → Add Server |
| [Zed](https://zed.dev) | Settings → MCP |
| [JetBrains IDEs](https://www.jetbrains.com) | Settings → Tools → AI Assistant → MCP |
| [Claude Desktop](https://claude.ai/download) | `claude_desktop_config.json` |
| [ChatGPT Desktop](https://openai.com/chatgpt/desktop) | Settings → MCP |
| [Amazon Q Developer](https://aws.amazon.com/q/developer/) | MCP Configuration |

### VS Code 拡張機能

以下の VS Code 拡張機能も同じ JSON 設定形式で MCP をサポートしています：

| 拡張機能 | インストール方法 |
|---------|----------------|
| [Cline](https://github.com/cline/cline) | MCP Marketplace → Add Server |
| [Roo Code](https://github.com/RooCodeInc/Roo-Code) | Settings → MCP → Add Server |
| [Continue](https://continue.dev) | `config.yaml` → MCP |

### CLI ツール

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

> CLI ツールを使用する場合は、シェルで `ATLASCLOUD_API_KEY` 環境変数を設定してください。

### Skills 版（Claude Code）

MCP の代わりに Skills を使用したい場合は、[Atlas Cloud Skills](https://github.com/AtlasCloudAI/atlas-cloud-skills) パッケージも提供しています。Claude Code やその他の Skills 対応エージェントで利用できます。

## 利用可能なツール

| ツール | 説明 |
|--------|------|
| `atlas_search_docs` | キーワードでドキュメントとモデルを検索 |
| `atlas_list_models` | 全モデル一覧（タイプでフィルタ可能：Text/Image/Video） |
| `atlas_get_model_info` | モデル詳細情報（API スキーマ、パラメータ、使用例） |
| `atlas_generate_image` | 画像生成 |
| `atlas_generate_video` | 動画生成 |
| `atlas_quick_generate` | ワンステップ生成 — キーワードでモデルを自動検索、パラメータ構築、タスク送信 |
| `atlas_upload_media` | ローカルファイルをアップロードしてURL取得（画像編集/画像→動画モデル用） |
| `atlas_chat` | LLM チャット（OpenAI 互換） |
| `atlas_get_prediction` | 生成タスクのステータスと結果を確認 |

> **注意**: アップロードされたファイルは Atlas Cloud 生成タスクでの一時的な使用のみを目的としています。ファイルは定期的にクリーンアップされる場合があります。恒久的なファイルホスティングとして使用しないでください。乱用は API キーの停止につながる可能性があります。

## 開発

```bash
npm install
npm run build
npm run dev
```

## ライセンス

MIT
