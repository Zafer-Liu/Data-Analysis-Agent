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
  <a href="../README.md">English</a> | <a href="./README.zh-CN.md">中文</a> | <a href="./README.ja.md">日本語</a> | 한국어 | <a href="./README.es.md">Español</a> | <a href="./README.fr.md">Français</a>
</p>

<p align="center">
  <a href="https://www.atlascloud.ai">Atlas Cloud</a>의 MCP(Model Context Protocol) 서버 — 이미지 생성, 비디오 생성, LLM을 제공하는 AI API 통합 플랫폼.
</p>

---

## 기능

- **모델 탐색** — 300개 이상의 AI 모델을 가격 및 기능과 함께 목록 표시
- **이미지 생성** — Seedream, Qwen-Image, Flux, Imagen 등의 모델로 이미지 생성
- **비디오 생성** — Kling, Vidu, Seedance, Wan, Hailuo, Veo 등의 모델로 비디오 생성
- **LLM 채팅** — DeepSeek, Qwen, GLM, MiniMax 등 LLM과 대화 (OpenAI 호환 형식)
- **미디어 업로드** — 로컬 이미지/미디어 파일을 업로드하여 이미지 편집 및 이미지→비디오 모델에 사용
- **빠른 생성** — 원스텝으로 모델 검색부터 파라미터 구성까지 자동 실행
- **문서 검색** — IDE에서 Atlas Cloud 문서, 모델, API 참조를 직접 검색
- **동적 스키마** — 각 모델의 파라미터 스키마를 자동 가져오기

## 빠른 시작

### 사전 요구사항

- Node.js >= 18
- Atlas Cloud API Key — [무료 발급](https://www.atlascloud.ai/console/api-keys)

### IDE 및 에디터 (JSON 설정)

MCP 설정 파일에 다음을 추가하세요 — MCP를 지원하는 모든 IDE와 에디터에서 동작합니다:

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

| 클라이언트 | 설정 위치 |
|-----------|----------|
| [Cursor](https://cursor.com) | Settings → MCP → Add Server |
| [Windsurf](https://codeium.com/windsurf) | Settings → MCP → Add Server |
| [VS Code (Copilot)](https://code.visualstudio.com) | `.vscode/mcp.json` 또는 Settings → MCP |
| [Trae](https://trae.ai) | Settings → MCP → Add Server |
| [Zed](https://zed.dev) | Settings → MCP |
| [JetBrains IDEs](https://www.jetbrains.com) | Settings → Tools → AI Assistant → MCP |
| [Claude Desktop](https://claude.ai/download) | `claude_desktop_config.json` |
| [ChatGPT Desktop](https://openai.com/chatgpt/desktop) | Settings → MCP |
| [Amazon Q Developer](https://aws.amazon.com/q/developer/) | MCP Configuration |

### VS Code 확장 프로그램

다음 VS Code 확장 프로그램도 동일한 JSON 설정 형식으로 MCP를 지원합니다:

| 확장 프로그램 | 설치 방법 |
|-------------|----------|
| [Cline](https://github.com/cline/cline) | MCP Marketplace → Add Server |
| [Roo Code](https://github.com/RooCodeInc/Roo-Code) | Settings → MCP → Add Server |
| [Continue](https://continue.dev) | `config.yaml` → MCP |

### CLI 도구

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

> CLI 도구를 사용할 때는 셸에서 `ATLASCLOUD_API_KEY` 환경 변수를 설정해야 합니다.

### Skills 버전 (Claude Code)

MCP 대신 Skills를 사용하고 싶다면, [Atlas Cloud Skills](https://github.com/AtlasCloudAI/atlas-cloud-skills) 패키지도 제공합니다. Claude Code 및 기타 Skills 지원 에이전트에서 사용할 수 있습니다.

## 사용 가능한 도구

| 도구 | 설명 |
|------|------|
| `atlas_search_docs` | 키워드로 문서 및 모델 검색 |
| `atlas_list_models` | 전체 모델 목록 (유형 필터 가능: Text/Image/Video) |
| `atlas_get_model_info` | 모델 상세 정보 (API 스키마, 파라미터, 사용 예제) |
| `atlas_generate_image` | 이미지 생성 |
| `atlas_generate_video` | 비디오 생성 |
| `atlas_quick_generate` | 원스텝 생성 — 키워드로 모델 자동 검색, 파라미터 구성, 작업 제출 |
| `atlas_upload_media` | 로컬 파일 업로드하여 URL 획득 (이미지 편집/이미지→비디오 모델용) |
| `atlas_chat` | LLM 채팅 (OpenAI 호환) |
| `atlas_get_prediction` | 생성 작업 상태 및 결과 확인 |

> **참고**: 업로드된 파일은 Atlas Cloud 생성 작업에서의 임시 사용만을 위한 것입니다. 파일은 주기적으로 정리될 수 있습니다. 영구적인 파일 호스팅으로 사용하지 마세요. 남용 시 API 키가 정지될 수 있습니다.

## 개발

```bash
npm install
npm run build
npm run dev
```

## 라이선스

MIT
