// Local review artifacts only. No service API, model or live data is accessed.
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("../../test-results/", import.meta.url));
const output = `${root}wireframe-alignment/`;
const source = JSON.parse(readFileSync(`${root}wireframe-comparison/comparison.json`, "utf8"));
const audit = JSON.parse(readFileSync(`${output}audit.json`, "utf8"));
const escape = text => String(text).replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll('"', "&quot;");
const rows = source.rows.filter(row => existsSync(`${output}Light-KR-1440-${row.name}.png`));
const image = (path, label) => `<figure><figcaption>${label}</figcaption><a href="${path}"><img src="${path}" alt="${label}" loading="lazy"></a></figure>`;
writeFileSync(`${output}index.html`, `<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>시안 반영 화면 검토</title><style>
*{box-sizing:border-box}body{margin:0;background:#f5f7fa;color:#243047;font:14px/1.6 system-ui,sans-serif}header{padding:24px 32px;background:#fff;border-bottom:1px solid #e8ebf2}h1{margin:0 0 8px}p{margin:0;color:#68748b}nav{display:flex;flex-wrap:wrap;gap:8px;padding:18px 32px}nav a{padding:5px 9px;background:#fff;border:1px solid #e8ebf2;border-radius:7px;color:#5b48c9;text-decoration:none}section{padding:20px 24px;border-top:1px solid #d8deea}h2{font-size:18px;margin:0 0 14px}.compare{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px;align-items:start}figure{margin:0;min-width:0;border:1px solid #d8deea;background:white;border-radius:9px;overflow:hidden}figcaption{padding:10px 12px;color:#5b48c9;font-weight:650}img{display:block;width:100%;height:auto}details{padding:20px 32px}li{margin:5px 0}a{color:#5b48c9}@media(max-width:900px){.compare{grid-template-columns:1fr}}
</style><header><h1>시안과 수정 전후 비교</h1><p>시안 / 수정 전 / 수정 후 모두 1440px Light KR 화면입니다. 데이터는 별도 합성 자료이며 메뉴와 권한 및 실제 계약을 우선합니다. 이미지 너비는 동일하게 표시하고 전체 높이는 그대로 유지합니다. 클릭하면 원본 크기로 확인할 수 있습니다.</p></header>
<nav>${rows.map(row => `<a href="#${escape(row.name)}">${escape(row.title)}</a>`).join("")}</nav>
${rows.map(row => `<section id="${escape(row.name)}"><h2>${escape(row.title)} / ${escape(row.name)}</h2><div class="compare">${image(`../wireframe-comparison/reference-${row.name}.png`, "시안")}${image(`../design-transition/Light-KR-1440-${row.name}.png`, "수정 전")}${image(`Light-KR-1440-${row.name}.png`, "수정 후")}</div></section>`).join("")}
<details><summary>테마, 언어, 모바일 및 편집 화면 ${audit.results.length}장</summary><ul>${audit.results.map(row => `<li><a href="${escape(row.name)}.png">${escape(row.name)}</a></li>`).join("")}</ul></details></html>`);
console.log(`Review gallery: ${rows.length} comparisons, ${audit.results.length} current screenshots.`);
