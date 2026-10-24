import { resolve } from "node:path";
import { defineConfig } from "vite";

const root = resolve(import.meta.dirname, "../..");

export default defineConfig({
  root,
  // base must match the public URL path where chat-app.js is served so that
  // Vite's __vite__mapDeps preload links and dynamic import() paths resolve
  // to /static/dist/chunks/... instead of /chunks/...
  base: "/static/dist/",
  build: {
    outDir: "static/dist",
    emptyOutDir: false,
    sourcemap: false,
    rollupOptions: {
      input: resolve(root, "frontend/entries/chat-app.js"),
      output: {
        entryFileNames: "chat-app.js",
        chunkFileNames: "chunks/[name]-[hash].js",
        assetFileNames: "assets/[name]-[hash][extname]",
        manualChunks(id) {
          const normalized = id.replace(/\\/g, "/");
          if (
            normalized.endsWith("/frontend/core/ui-registry.js") ||
            normalized.endsWith("/frontend/core/event-bus.js")
          ) {
            return "ui-registry";
          }
        },
      },
    },
  },
});
