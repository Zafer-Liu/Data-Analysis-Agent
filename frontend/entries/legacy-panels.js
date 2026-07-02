import { installMcpPanel, mcp } from "../features/mcp.js";
import { installKnowledgePanel, knowledge } from "../features/knowledge.js";

const baa = globalThis.BAA || {};
baa.mcp = mcp;
baa.knowledge = knowledge;
globalThis.BAA = baa;

installMcpPanel();
installKnowledgePanel();
