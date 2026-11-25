// Mechanical offline bundler: only reads task-owned sources and writes the single-file preview.
import { readFileSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
const root=dirname(fileURLToPath(import.meta.url));
let html=readFileSync(join(root,'index.html'),'utf8');
const safeStyle=text=>text.replace(/<\/style/gi,'<\\/style');
const safeScript=text=>text.replace(/<\/script/gi,'<\\/script');
for(const path of ['vendor/tabulator.min.css','ui.css','analysis.css','evaluation.css','settings.css','test-split.css','execution.css','spacing.css','feedback.css'])html=html.replace(`<link rel="stylesheet" href="${path}">`,`<style>\n${safeStyle(readFileSync(join(root,path),'utf8'))}\n</style>`);
for(const path of ['vendor/tabulator.min.js','data.js','feedback-stats.js','timing.js','app.js','analysis.js','evaluation.js','settings.js'])html=html.replace(`<script src="${path}"></script>`,`<script>\n${safeScript(readFileSync(join(root,path),'utf8'))}\n</script>`);
const license=readFileSync(join(root,'vendor/TABULATOR-LICENSE.txt'),'utf8');
html=html.replace('<!doctype html>',`<!doctype html>\n<!-- Offline UI wireframe. Tabulator 6.3.1: ${license.replace(/-->/g,'-- >')} -->`);
const out=join(root,'..','waf-console-redesign-v2.html');writeFileSync(out,html);console.log(out);
