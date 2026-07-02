// Audited compatibility Slash Command handler registry.
const handlers = new Map();

export function register(action, handler) {
  const key = String(action || "").trim();
  if (!key || typeof handler !== "function") {
    throw new TypeError("Command handler requires an action and function.");
  }
  if (handlers.has(key)) {
    throw new Error(`Duplicate Command handler: ${key}`);
  }
  handlers.set(key, handler);
}

export function has(action) {
  return handlers.has(String(action || "").trim());
}

export async function execute(command, argumentsText, context = {}) {
  const action = String(command?.clientAction || "").trim();
  const handler = handlers.get(action);
  if (!handler) {
    throw new Error(`Command handler unavailable: ${action || command?.cmd || "unknown"}`);
  }
  return await handler({
    ...context,
    command,
    arguments: String(argumentsText || "").trim(),
  });
}

export const commandHandlers = Object.freeze({ register, has, execute });
