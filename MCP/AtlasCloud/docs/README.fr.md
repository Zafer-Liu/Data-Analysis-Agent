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
  <a href="../README.md">English</a> | <a href="./README.zh-CN.md">中文</a> | <a href="./README.ja.md">日本語</a> | <a href="./README.ko.md">한국어</a> | <a href="./README.es.md">Español</a> | Français
</p>

<p align="center">
  Serveur MCP (Model Context Protocol) pour <a href="https://www.atlascloud.ai">Atlas Cloud</a> — plateforme d'agrégation d'APIs IA offrant la génération d'images, la génération de vidéos et des modèles LLM.
</p>

---

## Fonctionnalités

- **Découverte de modèles** — Parcourez plus de 300 modèles IA avec prix et capacités
- **Génération d'images** — Générez des images avec Seedream, Qwen-Image, Flux, Imagen, etc.
- **Génération de vidéos** — Générez des vidéos avec Kling, Vidu, Seedance, Wan, Hailuo, Veo, etc.
- **Chat LLM** — Discutez avec des modèles LLM (format compatible OpenAI) : DeepSeek, Qwen, GLM, MiniMax, etc.
- **Téléchargement de médias** — Téléchargez des images/fichiers locaux pour les utiliser avec les modèles d'édition d'image et image-vers-vidéo
- **Génération rapide** — Génération en une étape avec recherche automatique de modèles et construction de paramètres
- **Recherche de documentation** — Recherchez la documentation, les modèles et les références API d'Atlas Cloud directement depuis votre IDE
- **Schéma dynamique** — Récupère automatiquement le schéma de paramètres de chaque modèle

## Démarrage rapide

### Prérequis

- Node.js >= 18
- Clé API Atlas Cloud — [Obtenir gratuitement](https://www.atlascloud.ai/console/api-keys)

### IDEs et éditeurs (configuration JSON)

Ajoutez ce qui suit à votre fichier de configuration MCP — fonctionne avec tous les IDEs et éditeurs compatibles MCP :

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

| Client | Emplacement de configuration |
|--------|------------------------------|
| [Cursor](https://cursor.com) | Settings → MCP → Add Server |
| [Windsurf](https://codeium.com/windsurf) | Settings → MCP → Add Server |
| [VS Code (Copilot)](https://code.visualstudio.com) | `.vscode/mcp.json` ou Settings → MCP |
| [Trae](https://trae.ai) | Settings → MCP → Add Server |
| [Zed](https://zed.dev) | Settings → MCP |
| [JetBrains IDEs](https://www.jetbrains.com) | Settings → Tools → AI Assistant → MCP |
| [Claude Desktop](https://claude.ai/download) | `claude_desktop_config.json` |
| [ChatGPT Desktop](https://openai.com/chatgpt/desktop) | Settings → MCP |
| [Amazon Q Developer](https://aws.amazon.com/q/developer/) | MCP Configuration |

### Extensions VS Code

Ces extensions VS Code prennent également en charge MCP avec le même format de configuration JSON :

| Extension | Installation |
|-----------|-------------|
| [Cline](https://github.com/cline/cline) | MCP Marketplace → Add Server |
| [Roo Code](https://github.com/RooCodeInc/Roo-Code) | Settings → MCP → Add Server |
| [Continue](https://continue.dev) | `config.yaml` → MCP |

### Outils CLI

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

> Pour les outils CLI, assurez-vous de définir la variable d'environnement `ATLASCLOUD_API_KEY` dans votre shell.

### Version Skills (Claude Code)

Si vous préférez utiliser Skills plutôt que MCP, nous proposons également le package [Atlas Cloud Skills](https://github.com/AtlasCloudAI/atlas-cloud-skills) pour Claude Code et autres agents compatibles Skills.

## Outils disponibles

| Outil | Description |
|-------|-------------|
| `atlas_search_docs` | Rechercher documentation et modèles par mot-clé |
| `atlas_list_models` | Lister tous les modèles (filtrer par type : Text/Image/Video) |
| `atlas_get_model_info` | Obtenir les détails d'un modèle (schéma API, paramètres, exemples) |
| `atlas_generate_image` | Générer des images |
| `atlas_generate_video` | Générer des vidéos |
| `atlas_quick_generate` | Génération en une étape — recherche auto par mot-clé, construit les paramètres et soumet |
| `atlas_upload_media` | Télécharger des fichiers locaux pour obtenir une URL (pour modèles d'édition/image-vers-vidéo) |
| `atlas_chat` | Chat avec LLM (compatible OpenAI) |
| `atlas_get_prediction` | Vérifier le statut et le résultat des tâches de génération |

> **Note** : Les fichiers téléchargés sont uniquement destinés à un usage temporaire avec les tâches de génération Atlas Cloud. Les fichiers peuvent être nettoyés périodiquement. Ne pas utiliser comme hébergement permanent — tout abus peut entraîner la suspension de la clé API.

## Développement

```bash
npm install
npm run build
npm run dev
```

## Licence

MIT
