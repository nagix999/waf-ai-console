import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    fs: {
      allow: [
        fileURLToPath(new URL(".", import.meta.url)),
        fileURLToPath(new URL("../docs/Production_API_v0.1.md", import.meta.url))
      ]
    },
    proxy: {
      "/api": "http://localhost:8000",
      "/health": "http://localhost:8000",
      "^/docs(?:/oauth2-redirect)?/?(?:\\?|$)": "http://localhost:8000",
      "^/openapi\\.json(?:\\?|$)": "http://localhost:8000"
    }
  }
});
