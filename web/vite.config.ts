import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

const carbonEsm = fileURLToPath(new URL("./node_modules/@carbon/react/es/index.js", import.meta.url));

export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    setupFiles: "./src/test/setup.ts",
    css: false,
    alias: {
      "@carbon/react": carbonEsm,
    },
  },
  css: {
    preprocessorOptions: {
      scss: {
        api: "modern",
        quietDeps: true,
        silenceDeprecations: ["legacy-js-api", "if-function"],
      },
    },
  },
  server: {
    port: 3002,
    proxy: {
      "/api": "http://127.0.0.1:8010",
    },
  },
});
