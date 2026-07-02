import { chatStream } from "../features/chat-stream.js";

const baa = globalThis.BAA || {};
baa.chatStream = chatStream;
globalThis.BAA = baa;
