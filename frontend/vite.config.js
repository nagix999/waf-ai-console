import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";
import { readFileSync } from "node:fs";

export default defineConfig({
  plugins: [react(), {
    name: "report-font-license",
    generateBundle() {
      this.emitFile({ type: "asset", fileName: "assets/OFL-WafReport.txt", source: readFileSync(new URL("../backend/app/assets/fonts/OFL-Nanum.txt", import.meta.url), "utf8") });
    }
  }],
  server: {
    port: 5173,
    fs: {
      allow: [
        fileURLToPath(new URL(".", import.meta.url)),
        fileURLToPath(new URL("../backend/app/assets/fonts", import.meta.url)),
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
