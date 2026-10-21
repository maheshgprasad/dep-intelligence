import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
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
