// Build the very same report component/CSS for the offline server renderer.
import { build } from "esbuild";
import { fileURLToPath } from "node:url";
await build({
  entryPoints: [fileURLToPath(new URL("../src/reportPdf.jsx", import.meta.url))],
  outdir: fileURLToPath(new URL("../../backend/app/data/report-ui/", import.meta.url)),
  bundle: true, minify: true, format: "iife", platform: "browser", jsx: "automatic",
  define: { "process.env.NODE_ENV": '"production"' }, loader: { ".woff2": "dataurl" },
  logLevel: "info",
});
