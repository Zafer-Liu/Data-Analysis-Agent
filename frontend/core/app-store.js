import { eventBus } from "./event-bus.js";

function createAppStore(initialState) {
  const rawState = initialState;
  const state = new Proxy(rawState, {
    set(target, key, value) {
      const previous = target[key];
      if (Object.is(previous, value)) return true;
      target[key] = value;
      const change = Object.freeze({
        key: String(key),
        value,
        previous,
        state,
      });
      eventBus.emit("state:change", change);
      eventBus.emit(`state:${String(key)}`, change);
      return true;
    },
  });

  function get(key) {
    return state[key];
  }

  function set(key, value) {
    state[key] = value;
    return value;
  }

  function patch(values) {
    if (!values || typeof values !== "object") return state;
    for (const [key, value] of Object.entries(values)) state[key] = value;
    return state;
  }

  function subscribe(key, listener) {
    const eventName = key ? `state:${String(key)}` : "state:change";
    return eventBus.on(eventName, listener);
  }

  function snapshot() {
    return { ...rawState };
  }

  return Object.freeze({ state, get, set, patch, subscribe, snapshot });
}

const baa = globalThis.BAA || {};
if (!baa.state) {
  throw new Error("App Store must load after the legacy state bootstrap");
}

export const appStore = baa.store || createAppStore(baa.state);
export const state = appStore.state;

baa.store = appStore;
baa.state = state;
globalThis.BAA = baa;
