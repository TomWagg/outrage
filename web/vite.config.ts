import { defineConfig } from "vite";
import { resolve } from "node:path";

// A multi-page build: the game itself plus the three static information pages.
// Each needs naming here or Rollup only ever emits index.html and the info
// pages 404 in production while working fine under `vite dev`.
export default defineConfig({
  server: {
    port: 5173,
    proxy: {
      "/ws": { target: "ws://localhost:8000", ws: true },
      "/api": { target: "http://localhost:8000", changeOrigin: true },
    },
  },
  build: {
    outDir: "dist",
    rollupOptions: {
      input: {
        main: resolve(__dirname, "index.html"),
        about: resolve(__dirname, "about.html"),
        rules: resolve(__dirname, "rules.html"),
        developers: resolve(__dirname, "developers.html"),
      },
    },
  },
});
