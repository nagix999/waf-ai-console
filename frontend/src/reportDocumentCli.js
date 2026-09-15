// Private, one-shot stdin/stdout adapter. Never print report errors or content.
import { buildReportDocument } from "./reportDocument.js";

async function main() {
  const chunks = [];
  let size = 0;
  for await (const chunk of process.stdin) {
    size += chunk.length;
    if (size > 2 * 1024 * 1024) process.exit(2);
    chunks.push(chunk);
  }
  const document = buildReportDocument(JSON.parse(Buffer.concat(chunks).toString("utf8")));
  const output = Buffer.from(JSON.stringify(document));
  if (output.length > 512_000) process.exit(2);
  process.stdout.write(output);
}
main().catch(error => {
  process.exit(error?.message === "report_too_large" ? 2 : 1);
});
