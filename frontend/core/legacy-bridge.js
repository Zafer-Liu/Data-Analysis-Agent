import { ApiError, apiClient } from "./api-client.js";

export function installLegacyBridge(appName) {
  const baa = globalThis.BAA || {};
  baa.api = baa.api || apiClient;
  baa.ApiError = baa.ApiError || ApiError;
  baa.frontend = Object.freeze({
    ...(baa.frontend || {}),
    build: "vite",
    entry: appName,
  });
  globalThis.BAA = baa;
  return baa;
}
