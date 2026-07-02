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
  <a href="../README.md">English</a> | <a href="./README.zh-CN.md">中文</a> | <a href="./README.ja.md">日本語</a> | <a href="./README.ko.md">한국어</a> | Español | <a href="./README.fr.md">Français</a>
</p>

<p align="center">
  Servidor MCP (Model Context Protocol) para <a href="https://www.atlascloud.ai">Atlas Cloud</a> — plataforma de agregación de APIs de IA que ofrece generación de imágenes, generación de vídeos y modelos LLM.
</p>

---

## Características

- **Descubrimiento de modelos** — Explora más de 300 modelos de IA con precios y capacidades
- **Generación de imágenes** — Genera imágenes con modelos como Seedream, Qwen-Image, Flux, Imagen, etc.
- **Generación de vídeos** — Genera vídeos con modelos como Kling, Vidu, Seedance, Wan, Hailuo, Veo, etc.
- **Chat LLM** — Chatea con modelos LLM (formato compatible con OpenAI) incluyendo DeepSeek, Qwen, GLM, MiniMax, etc.
- **Carga de medios** — Sube imágenes/archivos locales para usar con modelos de edición de imagen e imagen-a-vídeo
- **Generación rápida** — Generación en un paso con búsqueda automática de modelos y construcción de parámetros
- **Búsqueda de documentación** — Busca documentos, modelos y referencias API de Atlas Cloud directamente desde tu IDE
- **Schema dinámico** — Obtiene automáticamente el schema de parámetros de cada modelo

## Inicio rápido

### Requisitos previos

- Node.js >= 18
- API Key de Atlas Cloud — [Obtener gratis](https://www.atlascloud.ai/console/api-keys)

### IDEs y editores (configuración JSON)

Añade lo siguiente a tu archivo de configuración MCP — funciona con todos los IDEs y editores compatibles con MCP:

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

| Cliente | Ubicación de configuración |
|---------|---------------------------|
| [Cursor](https://cursor.com) | Settings → MCP → Add Server |
| [Windsurf](https://codeium.com/windsurf) | Settings → MCP → Add Server |
| [VS Code (Copilot)](https://code.visualstudio.com) | `.vscode/mcp.json` o Settings → MCP |
| [Trae](https://trae.ai) | Settings → MCP → Add Server |
| [Zed](https://zed.dev) | Settings → MCP |
| [JetBrains IDEs](https://www.jetbrains.com) | Settings → Tools → AI Assistant → MCP |
| [Claude Desktop](https://claude.ai/download) | `claude_desktop_config.json` |
| [ChatGPT Desktop](https://openai.com/chatgpt/desktop) | Settings → MCP |
| [Amazon Q Developer](https://aws.amazon.com/q/developer/) | MCP Configuration |

### Extensiones de VS Code

Estas extensiones de VS Code también soportan MCP con el mismo formato de configuración JSON:

| Extensión | Instalación |
|-----------|-------------|
| [Cline](https://github.com/cline/cline) | MCP Marketplace → Add Server |
| [Roo Code](https://github.com/RooCodeInc/Roo-Code) | Settings → MCP → Add Server |
| [Continue](https://continue.dev) | `config.yaml` → MCP |

### Herramientas CLI

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

> Para herramientas CLI, asegúrate de configurar la variable de entorno `ATLASCLOUD_API_KEY` en tu shell.

### Versión Skills (Claude Code)

Si prefieres usar Skills en lugar de MCP, también ofrecemos el paquete [Atlas Cloud Skills](https://github.com/AtlasCloudAI/atlas-cloud-skills) para Claude Code y otros agentes compatibles con Skills.

## Herramientas disponibles

| Herramienta | Descripción |
|-------------|-------------|
| `atlas_search_docs` | Buscar documentación y modelos por palabra clave |
| `atlas_list_models` | Listar todos los modelos (filtrar por tipo: Text/Image/Video) |
| `atlas_get_model_info` | Obtener información detallada del modelo (schema API, parámetros, ejemplos) |
| `atlas_generate_image` | Generar imágenes |
| `atlas_generate_video` | Generar vídeos |
| `atlas_quick_generate` | Generación en un paso — busca modelo por palabra clave, construye parámetros y envía |
| `atlas_upload_media` | Subir archivos locales para obtener URL (para modelos de edición/imagen-a-vídeo) |
| `atlas_chat` | Chat con LLM (compatible con OpenAI) |
| `atlas_get_prediction` | Consultar estado y resultado de tareas de generación |

> **Nota**: Los archivos subidos son solo para uso temporal con tareas de generación de Atlas Cloud. Los archivos pueden limpiarse periódicamente. No utilice esto como alojamiento permanente — el abuso puede resultar en la suspensión de la clave API.

## Desarrollo

```bash
npm install
npm run build
npm run dev
```

## Licencia

MIT
