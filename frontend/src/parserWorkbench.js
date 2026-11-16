// Pure presentation helpers. Never execute an LLM proposal or a regular expression.
export const WORKBENCH_FIELDS = ["method", "uri", "protocol", "headers", "query", "body"];
export const parserSessionBusy = session => (session?.jobs || []).some(job => ["pending", "running", "processing"].includes(job.status));

export function codepointSegments(raw, spans = []) {
  const points = Array.from(typeof raw === "string" ? raw : "");
  const ranges = spans.filter(span => Number.isInteger(span?.start) && Number.isInteger(span?.end)
    && span.start >= 0 && span.end > span.start && span.end <= points.length)
    .map(span => ({ start: span.start, end: span.end })).sort((a, b) => a.start - b.start);
  const merged = [];
  for (const span of ranges) {
    const last = merged.at(-1);
    if (last && span.start <= last.end) last.end = Math.max(last.end, span.end);
    else merged.push({ ...span });
  }
  const result = []; let cursor = 0;
  for (const span of merged) {
    if (cursor < span.start) result.push({ text: points.slice(cursor, span.start).join(""), highlighted: false });
    result.push({ text: points.slice(span.start, span.end).join(""), highlighted: true }); cursor = span.end;
  }
  if (cursor < points.length) result.push({ text: points.slice(cursor).join(""), highlighted: false });
  return result;
}

export function expectationDraft(record = {}) {
  return Object.fromEntries(WORKBENCH_FIELDS.map(field => {
    const value = record[field];
    return [field, { state: ["present", "absent", "unknown"].includes(value?.state) ? value.state : "unknown",
      text: value?.state === "present" ? typeof value.value === "string" ? value.value : JSON.stringify(value.value, null, 2) : "" }];
  }));
}

export function parserFieldValue(parsed, field) {
  const value = parsed?.[field];
  if (!["headers", "query"].includes(field)) return value;
  if (value === undefined || value === null) return value;
  if (Array.isArray(value)) return value.map(pair => Array.isArray(pair) ? { name: String(pair[0]), value: String(pair[1]) } : pair);
  if (typeof value === "object") return Object.entries(value).flatMap(([name, values]) => (Array.isArray(values) ? values : [values]).map(item => ({ name, value: String(item) })));
  return value;
}

export function parserValueText(value, field) {
  if (value === undefined) return "—";
  if (["headers", "query"].includes(field) && Array.isArray(value)) return value.map(pair => `${pair.name}: ${pair.value}`).join("\n");
  return typeof value === "string" ? value : JSON.stringify(value);
}

export function verifiedParserSpans(item, field) {
  const comparison = item?.comparisons?.find(value => value.field === field);
  if (comparison) return comparison.source_spans || [];
  if (item?.valid_source_spans !== true) return [];
  const key = field === "uri" ? "request_target" : field;
  const spans = item?.candidate?.raw_source_spans?.[key];
  if (!Array.isArray(spans)) return [];
  return spans.map(span => Array.isArray(span) ? { start: span[0], end: span[1] } : span);
}

export function expectationPayload(draft) {
  return Object.fromEntries(WORKBENCH_FIELDS.map(field => {
    const input = draft[field];
    if (!["present", "absent", "unknown"].includes(input?.state)) throw new Error("invalid_parser_expectation");
    if (input.state !== "present") return [field, { state: input.state }];
    let value = input.text;
    if (field === "headers" || field === "query") {
      try { value = JSON.parse(input.text); } catch { throw new Error("invalid_parser_pair_list"); }
      if (!Array.isArray(value) || value.some(pair => !pair || typeof pair !== "object"
        || Array.isArray(pair) || typeof pair.name !== "string" || typeof pair.value !== "string"
        || Object.keys(pair).some(key => !["name", "value"].includes(key)))) throw new Error("invalid_parser_pair_list");
    }
    return [field, { state: "present", value }];
  }));
}

export function casePriority(item) {
  if (item?.selection_status === "ambiguous" || item?.candidate?.selection?.status === "ambiguous") return 0;
  if (item?.candidate?.parse_status === "failed" || item?.parse_status === "failed") return 1;
  if (item?.changed_fields?.length || item?.changed) return 2;
  if (item?.representative) return 3;
  return 4;
}

export function parserWorkbenchError(error, english = false) {
  const code = error?.message || "";
  const messages = {
    invalid_parser_pair_list: ["헤더와 쿼리는 name/value 배열로 입력하세요.", "Enter headers and query as arrays of name/value pairs."],
    parser_workbench_groups_unconfirmed: ["로그 형식을 확인한 뒤 규칙을 생성하세요.", "Confirm the format groups before generating rules."],
    parser_workbench_validation_required: ["현재 규칙으로 파싱 검증을 실행하세요.", "Validate the current rules first."],
    parser_split_requires_distinct_families: ["제안용과 검증용으로 나눌 서로 다른 요청 사례가 부족합니다. 중복되지 않는 문항을 추가하세요.", "Not enough distinct request families for authoring and validation. Add cases that are not duplicates."],
    parser_source_membership_changed: ["선택한 테스트 문항이 변경되었습니다. 표본 목록을 다시 불러오세요.", "Selected test cases have changed. Reload the case list."],
    parser_rule_target_invalid: ["기준 버전의 같은 벤더 규칙을 선택하세요. 한 규칙은 한 번만 선택할 수 있습니다.", "Select a rule for the same vendor in the baseline version. Each rule can be selected only once."],
    parser_workbench_job_in_progress: ["모델 작업이 진행 중입니다. 완료한 뒤 변경하세요.", "A model job is in progress. Wait before making changes."],
    parser_workbench_revision_conflict: ["다른 작업으로 내용이 바뀌었습니다. 다시 조회한 뒤 변경하세요.", "The work changed elsewhere. Refresh before editing."],
    parser_holdout_rules_locked: ["검증용 입력을 확인해 규칙이 고정됐습니다. 규칙을 바꾸려면 새 입력으로 작업을 만드세요.", "Rules are locked after opening validation inputs. Use fresh inputs to change rules."],
  };
  // Fixed UI messages only: never display an upstream body or arbitrary exception.
  if (messages[code]) return messages[code][english ? 1 : 0];
  if (error?.status === 401) return english ? "Your session expired. Sign in again." : "로그인이 만료되었습니다. 다시 로그인하세요.";
  if (error?.status === 409) return english ? "The source or configuration changed. Refresh and review it again." : "입력이나 설정이 바뀌었습니다. 다시 조회하고 확인하세요.";
  return english ? "The request could not be completed. Refresh and check the selected inputs and model." : "작업을 처리하지 못했습니다. 다시 조회하고 선택한 입력과 모델을 확인하세요.";
}
