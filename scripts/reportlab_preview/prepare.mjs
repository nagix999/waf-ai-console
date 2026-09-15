// Offline design fixtures only. Never loads a database, credentials, or real logs.
// Reuse the web report's allowlist, wording, ordering and Markdown parser.
import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { buildAnalysisReport, decodeReportText } from "../../frontend/src/analysisReport.js";
import { parseApiDocument } from "../../frontend/src/apiDocument.js";

export const sampleNames = ["01-short", "02-long", "03-evidence"];
const evidence = (number, supports, field, excerpt, interpretation_ko) => ({
  evidence_id: `preview-e${number}`, supports, field, excerpt, interpretation_ko,
});

function base() {
  return {
    id: "preview-only-not-an-analysis", event_id: "preview-only", status: "completed",
    analysis_purpose: "test", company_name: "시안용 가상 조직", model_profile: "preview-no-llm",
    src_ip: "192.0.2.10", src_port: 51432, dest_ip: "198.51.100.20", dest_port: 443,
    waf_vendor: "시안용 WAF", waf_action: "D", event_name: "검색 요청 탐지", signature: "SQL injection",
    created_at: "2026-09-15T03:00:00Z", started_at: "2026-09-15T03:00:01Z",
    completed_at: "2026-09-15T03:00:06Z", total_elapsed_ms: 6000, queue_wait_ms: 1000, processing_duration_ms: 5000,
    payload: "PREVIEW-RAW-EXCLUDED", extra_fields: { private: "PREVIEW-EXTRA-EXCLUDED" },
    result: {
      schema_version: "waf-analysis-v2", verdict: "true_positive", input_truncated: false,
      summary_ko: "검색 파라미터에서 SQL 조회 조건을 바꾸려는 구문이 확인됩니다. 공격 시도로 판단하며, 데이터 유출이나 공격 성공 여부는 이 요청만으로 확인할 수 없습니다.",
      threat_analysis: {
        severity: "HIGH", category: "SQL 삽입", target: "payload.query",
        technique_ko: "검색어 뒤에 참이 되는 조건과 SQL 주석을 덧붙여 기존 조회 조건을 우회하려는 형태입니다.",
        potential_impact_ko: "입력값이 SQL 문장에 직접 결합되는 경우 조회 범위가 넓어질 수 있습니다. 매개변수 바인딩 여부와 서버 응답은 기록에 없습니다.", obfuscations: [],
      },
      signature_assessment: { relation: "exact", explanation_ko: "조건 우회와 주석 구문이 SQL 삽입 탐지 내용에 부합합니다." },
      analyst_assessment: { version: "analyst-assessment-v1", evidence: [
        evidence(1, "true_positive", "payload.query", "q=' OR 1=1 --", "검색어를 끝내는 따옴표 뒤에 항상 참인 조건과 주석이 이어집니다. 단순한 특수문자 포함이 아니라 조회 조건 변경을 시도하는 조합입니다."),
        evidence(2, "context", "waf_action", "D", "WAF가 요청을 차단한 관측값입니다. 차단 여부만으로 공격 성공이나 실제 피해를 판단하지 않습니다."),
      ], decision_issues: [] },
      analyst_guidance: { checks: [], limitations: [] },
      agent: { framework: "moduagent" }, primary: { secret: "PREVIEW-PRIMARY-EXCLUDED" },
    },
  };
}

export function fixtures() {
  const short = base();
  const long = base();
  long.event_name = "문서 미리보기 요청 탐지";
  long.signature = "Script in request body";
  long.result.verdict = "inconclusive";
  long.result.summary_ko = "문서 본문에 스크립트가 포함되어 있지만, 미리보기 화면에서 실행되는지 일반 텍스트로 표시되는지 확인되지 않아 판단을 보류합니다. 문서 업로드 기능 자체는 정상 기능일 수 있으므로 저장 후 표시 방식을 확인해야 합니다.";
  long.result.threat_analysis = {
    severity: "UNKNOWN", category: "스크립트 삽입 가능성", target: "payload.body",
    technique_ko: "업로드된 문서 본문에는 HTML 태그와 스크립트 예제가 함께 있습니다. 스크립트를 실행 가능한 문서로 표시하는 경우와 예제 코드를 텍스트로 보여주는 경우를 구분해야 합니다. "
      + "요청에는 응답 본문, 콘텐츠 유형, 미리보기 화면의 처리 방식이 포함되어 있지 않습니다. 경로 이름만으로 안전한 처리를 단정할 수 없습니다. ".repeat(9),
    potential_impact_ko: "다른 사용자가 문서를 열 때 스크립트가 실행된다면 브라우저 내 정보 접근이나 사용자 동작 유도가 가능할 수 있습니다. 현재 자료에는 실행 결과가 없으므로 실제 피해를 확정하지 않습니다.",
    obfuscations: [],
  };
  long.result.signature_assessment = { relation: "partial", explanation_ko: "스크립트 표기는 탐지 조건과 관련되지만, 애플리케이션의 출력 처리에 따라 공격 성립 여부가 달라집니다." };
  const longExcerpt = "PREVIEW-LONG-START\n<script>window.previewOnly = '가상 예제';</script>\n"
    + "long_token=" + "Ab09".repeat(650) + "\n"
    + Array.from({ length: 42 }, (_, i) => `문서 ${String(i + 1).padStart(2, "0")}행: 긴 본문이 페이지를 넘어가도 순서와 내용이 보존되는지 확인하는 가상 문장입니다.`).join("\n")
    + "\nliteral=&lt;not-a-tag&gt;  <b>일반 문자열</b>\nPREVIEW-LONG-END";
  long.result.analyst_assessment = { version: "analyst-assessment-v1", evidence: [
    evidence(1, "true_positive", "payload.body", longExcerpt, "문서 본문에 실행 가능한 스크립트 문법이 들어 있습니다. 브라우저에서 HTML로 해석될 경우를 확인해야 합니다. 길게 이어지는 값은 페이지 나눔 검증용이며 그 길이 자체를 공격 근거로 삼지 않습니다."),
    evidence(2, "false_positive", "payload.path", "/docs/preview", "문서 미리보기 기능을 요청한 경로입니다. 코드 예제를 제출하는 정상 사용일 가능성이 있지만 경로만으로 안전한 처리를 보장하지는 않습니다."),
    evidence(3, "context", "payload.headers.content-type", "text/plain; charset=utf-8", "클라이언트가 본문을 일반 텍스트로 표시한 값입니다. 서버의 응답 콘텐츠 유형이나 저장 후 표시 방식과 같다고 단정할 수 없습니다."),
  ], decision_issues: [{ point_ko: "문서의 스크립트가 사용자 브라우저에서 실행되는가", evidence_ids: ["preview-e1", "preview-e2"], missing_condition_ko: "미리보기 응답의 콘텐츠 유형과 HTML 이스케이프 처리" }] };
  long.result.analyst_guidance = { checks: [{ source_ko: "문서 미리보기 응답과 출력 처리 코드", check_ko: "문서 본문이 텍스트로 이스케이프되어 표시되는지, HTML로 해석되는지 확인해 주세요.", why_ko: "스크립트가 실행되는 입력인지 문서 예제인지 구분하기 위해 필요합니다." }], limitations: ["가상 요청만으로 구성한 디자인 시안입니다. 응답이나 서버 내부 동작은 확인하지 않았습니다."] };

  const many = base();
  many.event_name = "보안 교육 문서 등록 요청 탐지";
  many.signature = "Multiple attack examples";
  many.result.verdict = "inconclusive";
  many.result.summary_ko = "등록 문서에는 여러 공격 예제가 포함되어 있고, 교육 자료임을 나타내는 내용도 있습니다. 문서가 실행되지 않는 텍스트로만 표시되는지 확인하기 전에는 정탐·오탐을 확정하기 어렵습니다. 근거 개수는 판정 점수가 아닙니다.";
  many.result.threat_analysis = { severity: "UNKNOWN", category: "문서 내 공격 구문", target: "payload.body", technique_ko: "SQL, HTML, 경로 이동 예제가 한 문서에 들어 있습니다. 각 예제의 문법과 문서 등록이라는 사용 맥락을 함께 살펴야 합니다.", potential_impact_ko: "본문이 명령이나 HTML로 실행되는 경우 영향을 줄 수 있습니다. 단순한 텍스트 보관이면 같은 문구라도 실행 효과는 없습니다.", obfuscations: [] };
  many.result.signature_assessment = { relation: "partial", explanation_ko: "공격 문법은 존재하지만 교육 예제인지 실제 실행 입력인지 추가 확인이 필요합니다." };
  many.result.analyst_assessment = { version: "analyst-assessment-v1", evidence: [
    evidence(1, "true_positive", "payload.body", "example_sql: ' OR 1=1 --", "SQL 조건 우회 문법이 포함되어 있습니다. 데이터베이스 실행부에 전달되는지 여부는 별도 확인이 필요합니다."),
    evidence(2, "true_positive", "payload.body", "example_html: <img src=x onerror=previewOnly()>", "이벤트 속성으로 함수를 호출하는 HTML 예제가 있습니다. HTML 해석 여부가 중요합니다."),
    evidence(3, "true_positive", "payload.body", "example_path: ../../private/preview.txt", "상위 경로 이동 표현이 있습니다. 실제 파일 경로로 사용되는지 확인되지 않았습니다."),
    evidence(4, "true_positive", "payload.body", "example_cmd: ; printf preview-only", "명령 구분자 예제가 들어 있습니다. 서버에서 셸 명령으로 실행되었다는 뜻은 아닙니다."),
    evidence(5, "false_positive", "payload.path", "/training/articles", "교육 문서 등록 경로로 보여 문서 예제 제출이라는 정상 사용 맥락을 뒷받침합니다."),
    evidence(6, "false_positive", "payload.body", "title: 입력 검증 교육 예제", "문서 제목이 입력 검증 교육임을 명시합니다. 제목은 사용자가 정할 수 있으므로 이것만으로 정상 판정을 확정하지 않습니다."),
    evidence(7, "false_positive", "payload.body", "notice: 실행하지 말고 문자열로 표시하세요.", "본문에 텍스트 예제로 다루라는 설명이 있습니다. 실제 출력 처리와 일치하는지 확인해야 합니다."),
    evidence(8, "context", "payload.headers.content-type", "application/json", "클라이언트의 전송 형식입니다. 문서가 이후 어떤 형태로 출력되는지는 알려주지 않습니다."),
    evidence(9, "context", "payload.method", "POST", "본문을 전송한 메서드입니다. 메서드 자체는 공격 여부를 결정하지 않습니다."),
    evidence(10, "context", "waf_action", "D", "WAF가 차단했다는 관측만 확인됩니다. 공격 성공 증거로 사용하지 않습니다."),
  ], decision_issues: [{ point_ko: "교육 예제가 저장·출력 과정에서 실행될 수 있는가", evidence_ids: ["preview-e1", "preview-e2", "preview-e5"], missing_condition_ko: "문서 본문을 실행부와 분리하고 텍스트로 표시하는지 여부" }] };
  many.result.analyst_guidance = { checks: [{ source_ko: "교육 문서 저장·조회 기능", check_ko: "본문이 SQL·셸 명령에 직접 결합되지 않고 HTML 출력 시 이스케이프되는지 확인해 주세요.", why_ko: "교육 예제 문자열과 실행 가능한 공격 입력을 구분하기 위해 필요합니다." }], limitations: [] };
  return [short, long, many];
}

function normalize(block) {
  if (block.type === "code") return { ...block, text: block.text.endsWith("\n") ? block.text.slice(0, -1) : block.text };
  const output = { ...block };
  if (typeof block.text === "string") output.text = decodeReportText(block.text);
  if (block.items) output.items = block.items.map(decodeReportText);
  if (block.headers) output.headers = block.headers.map(decodeReportText);
  if (block.rows) output.rows = block.rows.map(row => row.map(decodeReportText));
  return output;
}

const output = resolve(process.argv[2] || "/tmp/waf-reportlab-preview");
await mkdir(output, { recursive: true });
for (const [index, detail] of fixtures().entries()) {
  const before = JSON.stringify(detail);
  const markdown = buildAnalysisReport(detail);
  const report = parseApiDocument(markdown);
  const document = {
    title: decodeReportText(report.title), intro: report.intro.map(normalize),
    sections: report.sections.map(section => ({ ...section, title: decodeReportText(section.title), blocks: section.blocks.map(normalize) })),
  };
  assert.equal(JSON.stringify(detail), before);
  for (const excluded of ["PREVIEW-RAW-EXCLUDED", "PREVIEW-EXTRA-EXCLUDED", "PREVIEW-PRIMARY-EXCLUDED"]) assert(!markdown.includes(excluded));
  const name = sampleNames[index];
  // Exclusive writes: an existing preview is never silently replaced.
  await writeFile(resolve(output, `${name}.json`), JSON.stringify(document, null, 2), { flag: "wx" });
  await writeFile(resolve(output, `${name}.md`), markdown, { flag: "wx" });
  await writeFile(resolve(output, `${name}.web.json`), JSON.stringify({ detail, decoding: null, includeAppendix: false, theme: "light" }), { flag: "wx" });
}
console.log("Prepared 3 fictional reports using the web report formatter; no LLM calls.");
