import { resolve } from "node:path";
import { defineConfig } from "vite";

export default defineConfig({
  build: {
    outDir: "static/dist",
    emptyOutDir: false,
    sourcemap: false,
    rollupOptions: {
      input: resolve(import.meta.dirname, "frontend/entries/chat-app.js"),
      output: {
        entryFileNames: "chat-app.js",
        chunkFileNames: "chunks/[name]-[hash].js",
        assetFileNames: "assets/[name]-[hash][extname]",
      },
    },
  },
});
