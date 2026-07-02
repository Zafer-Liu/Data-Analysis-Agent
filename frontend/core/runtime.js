import { $ } from "./dom.js";
import { appStore, state } from "./app-store.js";

export { $ };
export { appStore, state };
export const api = () => globalThis.BAA.api;
export const namespace = () => globalThis.BAA;
