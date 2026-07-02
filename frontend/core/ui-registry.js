import { eventBus } from "./event-bus.js";

const baa = globalThis.BAA || {};
const ui = baa.ui || {};

baa.ui = ui;
globalThis.BAA = baa;

export const uiRegistry = ui;

export function registerUiIsland(name, api) {
  const previous = uiRegistry[name] || null;
  uiRegistry[name] = api || null;
  eventBus.emit(
    api ? "ui:registered" : "ui:unregistered",
    Object.freeze({
      name,
      api: uiRegistry[name],
      previous,
    }),
  );
  return uiRegistry[name];
}

export function getUiIsland(name) {
  return uiRegistry[name] || null;
}
