// Bundle the shared text formatter for a one-shot Node process (no browser).
import { build } from "esbuild";
import { fileURLToPath } from "node:url";
await build({
  entryPoints: [fileURLToPath(new URL("../src/reportDocumentCli.js", import.meta.url))],
  outfile: fileURLToPath(new URL("../../backend/app/data/report-document.cjs", import.meta.url)),
  bundle: true, minify: true, format: "cjs", platform: "node", target: "node22",
  logLevel: "info",
});
