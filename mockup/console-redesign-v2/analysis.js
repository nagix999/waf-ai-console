(function () {
  "use strict";
  const W = window.WAF;
  const H = value => W.h(value == null ? "" : String(value));
  const q = key => W.query.get(key) || "";
  const button = (...args) => W.button(...args);
  const categories = [
    ["insufficient_evidence", "근거 부족"], ["evidence_mismatch", "근거 불일치"],
    ["explanation_error", "설명 오류"], ["exaggerated_impact", "영향 과장"],
    ["inappropriate_severity", "심각도 부적절"], ["other", "기타"]
  ];
  const categoryName = value => categories.find(item => item[0] === value)?.[1] || value;
  const analyses = () => W.data.analyses || [];
  const allFeedback = () => W.data.feedback || (W.data.feedback = []);
  const selected = new Set();
  const testScrollPositions = new Map();
  let filterTimer;
  const get = id => analyses().find(item => String(item.id) === String(id));
  const current = () => q("id") ? get(q("id")) : analyses()[0];
  const label = value => ({ true_positive: "정탐", false_positive: "오탐", inconclusive: "판정 보류" })[value] || "판정 전";
  const purposeName = item => item.purpose === "test" ? "Test" : "Production";
  const timeValues = item => W.timing.values(item);
  const duration = (item, key = "processing") => W.timing.format(timeValues(item)[key]);
  const elapsedLabel = item => item.status === "completed" ? "접수→완료" : timeValues(item).terminal ? "접수→종료" : "접수→현재";
  const timingInline = item => `<span class="analysis-timings" aria-label="분석 시간"><span>${elapsedLabel(item)} <strong>${H(duration(item, "total"))}</strong></span><span>큐 대기 <strong>${H(duration(item, "queue"))}</strong></span><span>분석 소요 <strong>${H(duration(item))}</strong></span></span>`;
  const support = item => ({ true_positive: "attack_request", false_positive: "normal_request" })[item.supports] || item.supports || "context";
  const evidence = item => Array.isArray(item.evidence) ? item.evidence : [];
  const interpretation = item => item.interpretation || item.interpretation_ko || "해석 미기록";
  const rawPayload = item => String(item.input?.payload ?? item.payload ?? "원문 미기록");
  const recordFeedback = item => allFeedback().filter(row => String(row.analysis_id || row.analysisId) === String(item.id));
  const withParams = changes => {
    const params = Object.fromEntries(W.query.entries());
    Object.entries(changes).forEach(([key, value]) => value == null || value === "" ? delete params[key] : params[key] = value);
    return params;
  };
  const nav = (route, changes) => W.go(route, withParams(changes));
  const option = (value, title, currentValue) => `<option value="${H(value)}"${value === currentValue ? " selected" : ""}>${H(title)}</option>`;
  const select = (name, title, values) => `<label class="field"><span>${H(title)}</span><select data-filter="${H(name)}" aria-label="${H(title)}">${values.map(item => option(item[0], item[1], q(name))).join("")}</select></label>`;
  const entityButton = (text, action, id, kind = "button-ghost") => `<button class="button ${kind}" type="button" data-action="${H(action)}" data-id="${H(id)}">${H(text)}${W.icon("arrowUpRight")}</button>`;
  const filterControls = route => {
    W.root.querySelectorAll("[data-filter]").forEach(el => {
      const update = () => { selected.clear(); if (document.activeElement === el) W.analysisFilterFocus = { route, key: el.dataset.filter, start: el.selectionStart, end: el.selectionEnd }; W.go(route, withParams({ [el.dataset.filter]: el.value, page: null, preview: null })); };
      if (el.tagName === "INPUT") el.addEventListener("input", () => { clearTimeout(filterTimer); filterTimer = setTimeout(update, 350); });
      else el.addEventListener("change", update);
    });
    const pending = W.analysisFilterFocus;
    if (pending?.route === route) {
      W.analysisFilterFocus = null;
      requestAnimationFrame(() => { const el = [...W.root.querySelectorAll("[data-filter]")].find(node => node.dataset.filter === pending.key); el?.focus({ preventScroll: true }); if (el?.tagName === "INPUT" && pending.start != null) el.setSelectionRange(pending.start, pending.end); });
    }
  };
  const search = (placeholder, name = "q") => `<label class="search-field">${W.icon("search")}<input data-filter="${name}" value="${H(q(name))}" placeholder="${H(placeholder)}" aria-label="${H(placeholder)}"></label>`;

  function operationRows() {
    return analyses().filter(item => item.purpose !== "test").filter(item => {
      const term = q("q").toLocaleLowerCase();
      return (!term || [item.company, item.signature, item.event_id, item.source_ip, item.summary].some(value => String(value || "").toLocaleLowerCase().includes(term))) &&
        (!q("status") || item.status === q("status")) && (!q("verdict") || item.verdict === q("verdict")) && (!q("key") || item.key === q("key"));
    });
  }

  function evidenceGroups(item, compact = false) {
    const items = evidence(item);
    const seen = new Map();
    const testLinked = item.purpose === "test" && !compact;
    return `<div class="evidence-groups">${[
      ["attack_request", "공격 요청 근거", "attack"], ["normal_request", "정상 요청 근거", "normal"], ["context", "참고 내용", "context"]
    ].map(([key, title, tone]) => {
      const group = items.filter(row => support(row) === key);
      return `<section class="evidence-group ${tone}"><div class="evidence-group-title"><span class="support-dot"></span><h3>${title}</h3><span class="count-badge">${group.length}</span></div>${group.length ? group.map(row => {
        const index = items.indexOf(row) + 1;
        const sourceKey = JSON.stringify([row.field || "payload", row.excerpt, row.source_spans || row.span || row.source_id || `unrecorded-${index}`]);
        const previous = seen.get(sourceKey);
        if (!previous) seen.set(sourceKey, index);
        return `<article class="evidence-card${testLinked && Number(q("evidence")) === index ? " test-evidence-selected" : ""}"${testLinked ? ` data-test-evidence="${index}"` : ""}><div class="evidence-card-top"><span class="evidence-number">${index}</span><span class="small muted">${H(row.field || "payload")}</span>${compact ? "" : `<button class="button button-ghost evidence-source-button" data-action="analysis-source" data-id="${H(item.id)}" data-evidence="${index}"${testLinked ? ` aria-controls="test-payload" aria-pressed="${Number(q("evidence")) === index}"` : ""}>원문에서 확인 ${W.icon("arrowUpRight")}</button>`}</div><p class="evidence-interpretation">${H(interpretation(row))}</p>${previous ? `<div class="small muted evidence-shared">근거 ${previous}과 같은 원문을 사용합니다.</div>` : `<pre class="evidence-source">${H(row.excerpt || "발췌 미기록")}</pre>`}<details class="evidence-origin"><summary>작성 출처</summary><p class="small muted">${H((row.origins || ["Primary"]).map(origin => typeof origin === "string" ? origin : origin.role || "미기록").join(" / "))} · 표시할 대표 근거를 사용합니다. 전체 해석은 실행 이력에 보존됩니다.</p></details></article>`;
      }).join("") : `<p class="evidence-empty small muted">${key === "normal_request" ? "구체적으로 정상 사용을 뒷받침하는 근거는 없습니다." : key === "attack_request" ? "공격 요청을 뒷받침하는 근거는 없습니다." : "추가 참고 내용이 없습니다."}</p>`}</section>`;
    }).join("")}</div>`;
  }

  function contextPanel(item) {
    return `<aside class="result-context stack">${W.card("판정 맥락", `<dl class="context-grid"><div class="context-item"><dt>회사</dt><dd>${H(item.company)}</dd></div><div class="context-item"><dt>탐지명</dt><dd>${H(item.signature)}</dd></div><div class="context-item"><dt>장비 조치</dt><dd>${W.badge(item.action === "D" ? "차단" : "허용", item.action === "D" ? "neutral" : "amber")}</dd></div><div class="context-item"><dt>출발지 IP</dt><dd class="mono">${H(item.source_ip)}</dd></div><div class="context-item"><dt>분석 용도</dt><dd>${W.badge(purposeName(item), item.purpose === "test" ? "violet" : "blue")}</dd></div><div class="context-item"><dt>${elapsedLabel(item)}</dt><dd>${H(duration(item, "total"))}</dd></div><div class="context-item"><dt>큐 대기</dt><dd>${H(duration(item, "queue"))}</dd></div><div class="context-item"><dt>분석 소요</dt><dd>${H(duration(item))}</dd></div></dl><p class="small muted">WAF의 탐지와 차단은 관측값이며, 정답을 의미하지 않습니다.</p>`)}${W.card("참고 판정", `<div class="row">${item.reference ? W.verdict(item.reference) : `<span class="muted">미등록</span>`}${item.reference && item.verdict ? W.badge(item.reference === item.verdict ? "일치" : "불일치", item.reference === item.verdict ? "green" : "amber") : ""}</div><p class="small muted">모델 입력과 분리해 비교에만 사용합니다.</p>${entityButton(item.reference ? "참고 판정 수정" : "참고 판정 등록", "analysis-reference", item.id)}`)}${W.card("분석 피드백", `<p class="muted small">근거나 설명에서 개선할 점을 남겨 주세요. 참고 판정과는 별도로 보존됩니다.</p><div class="row">${button("피드백 남기기", "analysis-feedback-new", "secondary", "message")}${entityButton(`이력 ${recordFeedback(item).length}건`, "analysis-feedback-history", item.id)}</div>`)}<details class="panel technical-meta"><summary>이벤트 정보</summary><dl class="context-grid"><div><dt>이벤트 ID</dt><dd class="mono">${H(item.event_id)}</dd></div><div><dt>분석 ID</dt><dd class="mono">${H(item.id)}</dd></div><div><dt>WAF 벤더</dt><dd>${H(item.vendor || "예시 벤더")}</dd></div><div><dt>API 키 별칭</dt><dd>${H(item.key || "—")}</dd></div></dl></details></aside>`;
  }

  function resultContent(item) {
    if (item.status !== "completed") return `<div class="detail-grid">${W.card(item.status === "failed" ? "분석을 완료하지 못했습니다" : "분석 중입니다", `<div class="callout ${item.status === "failed" ? "callout-warning" : ""}"><p>${item.status === "failed" ? "구조화된 응답을 확인하는 단계에서 작업이 종료됐습니다. 실행 기록을 확인하거나 실패 당시 설정으로 재실행할 수 있습니다." : "결과가 준비되면 판정과 근거를 표시합니다. 접수된 작업은 브라우저를 닫아도 계속됩니다."}</p>${button("실행 기록 보기", "analysis-execution", "secondary", "clock")}</div>`)}${contextPanel(item)}</div>`;
    return `<div class="detail-grid"><div class="stack">${W.card("판정 근거", evidenceGroups(item), `<span class="small muted">API v3 대표 근거 ${evidence(item).length}개</span>`)}${item.verdict === "inconclusive" ? W.card("확정에 필요한 조건", `<div class="callout callout-warning"><strong>이 정보에 따라 판정이 달라질 수 있습니다.</strong><p>${H(item.hold_reason || "요청 대상이 공개된 자료인지 내부 전용 자료인지 현재 입력만으로 구분하기 어렵습니다. 해당 경로의 실제 서비스 용도를 확인해 주세요.")}</p></div>`) : ""}<details class="panel technical-interpretation"><summary>기술 해석</summary><div class="panel-body"><h3>요청의 동작과 영향</h3><p>${H(item.technical || item.summary)}</p><p class="muted small">관찰된 요청 시도와 실제 침해 성공은 구분합니다. 로그에 없는 응답이나 서버 동작을 추정해 확정하지 않습니다.</p><h3>탐지 내용과 요청의 연관성</h3><p>${H(item.signature)}는 장비에서 기록한 탐지명입니다. 실제 요청의 내용 근거와 분리해 참고합니다.</p></div></details></div>${contextPanel(item)}</div>`;
  }

  function analysisHero(item) {
    const unavailable = item.status !== "completed";
    return `<section class="verdict-hero detail-hero ${H(item.verdict || item.status)}"><div class="hero-kicker">${W.badge(purposeName(item), item.purpose === "test" ? "violet" : "blue")}<span>${H(item.company)}</span></div><div class="hero-verdict">${unavailable ? W.status(item.status) : W.verdict(item.verdict)}${!unavailable ? W.severity(item.severity) : ""}</div><h1>${H(item.analysis_title || item.signature || "요청 분석 결과")}</h1><p class="hero-summary">${H(item.summary || "결과가 준비되면 요청의 핵심 동작과 보안 의미를 표시합니다.")}</p>${timingInline(item)}</section>`;
  }

  function testInputFields(item) {
    const source = item.input && typeof item.input === "object" ? item.input : item;
    const fields = [
      ["event_id", "이벤트 ID", source.event_id], ["company_name", "회사", source.company_name ?? source.company],
      ["waf_vendor", "WAF 벤더", source.waf_vendor ?? source.vendor], ["signature", "시그니처", source.signature],
      ["event_name", "이벤트 이름", source.event_name], ["waf_action", "WAF 조치", source.waf_action ?? source.action],
      ["src_ip", "출발지 IP", source.src_ip ?? source.source_ip], ["src_port", "출발지 포트", source.src_port],
      ["dest_ip", "목적지 IP", source.dest_ip], ["dest_port", "목적지 포트", source.dest_port]
    ];
    if (source.extra_fields && typeof source.extra_fields === "object") for (const [key, value] of Object.entries(source.extra_fields)) {
      fields.push([`extra_fields.${key}`, key, value]);
    }
    if (item.input && typeof item.input === "object") for (const [key, value] of Object.entries(item.input)) {
      if (!["payload", "extra_fields"].includes(key) && !fields.some(field => field[0] === key)) fields.push([key, key, value]);
    }
    return fields.map(([id, name, value]) => ({ id, name, value: value == null ? "미기록" : typeof value === "object" ? JSON.stringify(value) : String(value) }));
  }

  function testSourceSelection(item, ordinal) {
    const row = evidence(item)[Number(ordinal) - 1];
    if (!row) return { ranges: [], message: "왼쪽 근거를 누르면 해당 원문을 여기에서 강조합니다." };
    const field = row.field || "payload";
    if (field !== "payload") return { ranges: [], field, message: `이 근거의 출처는 ${field} 필드입니다. payload 대신 해당 입력 필드를 표시합니다.` };
    const raw = rawPayload(item), excerpt = row.excerpt;
    if (typeof excerpt !== "string" || !excerpt.length) return { ranges: [], message: "발췌문이 기록되지 않아 원문 위치를 표시할 수 없습니다." };
    if (Array.isArray(row.source_spans) && row.source_spans.length) {
      const ranges = row.source_spans.map(span => ({ start: span.start, end: span.end })).sort((a, b) => a.start - b.start);
      const valid = ranges.every((span, i) => Number.isInteger(span.start) && Number.isInteger(span.end) && span.start >= 0 && span.end > span.start && span.end <= raw.length && (!i || span.start >= ranges[i - 1].end));
      const pieces = valid ? ranges.map(span => raw.slice(span.start, span.end)) : [];
      if (valid && (pieces.join("") === excerpt || pieces.join("\n") === excerpt)) return { ranges, message: `근거 ${ordinal}의 발췌문 ${ranges.length > 1 ? `${ranges.length}개 구간을` : "위치를"} 강조했습니다.` };
      return { ranges: [], message: "기록된 위치와 발췌문이 일치하지 않아 임의로 강조하지 않습니다." };
    }
    const start = raw.indexOf(excerpt);
    if (start < 0) return { ranges: [], message: "payload에서 발췌문을 확인할 수 없어 임의로 강조하지 않습니다." };
    if (raw.indexOf(excerpt, start + 1) >= 0) return { ranges: [], message: "같은 발췌문이 여러 위치에 있습니다. 출처 위치가 없어 한 구간을 임의로 선택하지 않습니다." };
    return { ranges: [{ start, end: start + excerpt.length }], message: `근거 ${ordinal}의 발췌문 위치를 강조했습니다.` };
  }

  function populateTestPayload(item, ordinal) {
    const target = document.getElementById("test-payload");
    if (!target) return;
    const raw = rawPayload(item), { ranges } = testSourceSelection(item, ordinal), fragment = document.createDocumentFragment();
    let end = 0;
    ranges.forEach((span, i) => {
      fragment.append(document.createTextNode(raw.slice(end, span.start)));
      const mark = document.createElement("mark");
      if (!i) mark.id = "source-highlight";
      mark.dataset.sourceHighlight = ""; mark.tabIndex = -1; mark.setAttribute("aria-label", "선택한 근거의 원문"); mark.textContent = raw.slice(span.start, span.end);
      fragment.append(mark); end = span.end;
    });
    fragment.append(document.createTextNode(raw.slice(end))); target.replaceChildren(fragment);
  }

  function testDecisionExtras(item) {
    return `<div class="test-decision-extras">${W.card("참고 판정", `<div class="row">${item.reference ? W.verdict(item.reference) : '<span class="muted">미등록</span>'}${item.reference && item.verdict ? W.badge(item.reference === item.verdict ? "일치" : "불일치", item.reference === item.verdict ? "green" : "amber") : ""}${entityButton("참고 판정 수정", "analysis-reference", item.id)}</div><p class="small muted">비교에만 사용하며 모델 입력으로 전달하지 않습니다.</p>`)}${W.card("분석 피드백", `<div class="row">${button("피드백 남기기", "analysis-feedback-new", "secondary", "message")}${entityButton(`이력 ${recordFeedback(item).length}건`, "analysis-feedback-history", item.id)}</div><p class="small muted">근거와 설명에 대한 의견을 별도로 보존합니다.</p>`)}</div>`;
  }

  function testAnalysisWorkspace(item) {
    const ordinal = Number(q("evidence")) || 0, selection = testSourceSelection(item, ordinal), decoding = Array.isArray(item.decoded) ? item.decoded : [];
    const decision = item.status === "completed" ? `${W.card("판정 근거", evidenceGroups(item), `<span class="small muted">대표 근거 ${evidence(item).length}개</span>`)}${item.verdict === "inconclusive" ? W.card("확정에 필요한 조건", `<div class="callout callout-warning">${H(item.hold_reason || "현재 입력에서 확인하지 못한 조건에 따라 판정이 달라질 수 있습니다.")}</div>`) : ""}<details class="panel technical-interpretation"><summary>기술 해석</summary><div class="panel-body"><h3>요청의 동작과 영향</h3><p>${H(item.technical || item.summary)}</p><p class="small muted">관측한 요청 시도와 실제 침해 성공은 구분합니다. 시그니처와 WAF 조치는 요청 근거와 분리해 참고합니다.</p></div></details>` : W.card(item.status === "failed" ? "분석 실행을 확인해 주세요" : "분석 중입니다", `<p class="muted small">${item.status === "failed" ? "실행 기록에서 실패 원인을 확인하고 실패 당시 설정으로 재실행할 수 있습니다." : "완료되면 이 영역에 판정과 근거를 표시합니다."}</p><div class="row">${button("실행 기록 보기", "analysis-execution", "secondary", "clock")}${item.status === "failed" ? button("재실행", "analysis-retry", "primary", "refresh") : ""}</div>`);
    return `<div class="test-analysis-workspace" id="test-analysis-workspace" role="group" aria-label="테스트 분석 결과와 입력 대조"><section class="test-analysis-pane test-results-pane" id="test-results-scroll" tabindex="0" role="region" aria-label="판정과 근거"><header class="test-pane-heading"><h2>${W.icon("shield", 16)} 판정과 근거</h2><span class="small muted">근거를 눌러 원문 확인</span></header><div class="test-pane-content stack">${analysisHero(item)}${decision}${testDecisionExtras(item)}</div></section><section class="test-analysis-pane test-input-pane" id="test-input-scroll" tabindex="0" role="region" aria-label="입력 정보와 payload"><header class="test-pane-heading"><h2>${W.icon("code", 16)} 입력과 원문</h2><span id="test-selected-source" class="badge badge-purple">${ordinal ? `근거 ${ordinal} 선택` : "원문 보존"}</span></header><div class="test-pane-content stack"><section class="panel test-input-fields-panel"><header class="panel-header"><h2>입력 정보</h2><span class="small muted">수신한 필드</span></header><div class="table-shell test-fields"><div id="test-input-fields" aria-label="테스트 입력 필드"></div></div></section><section class="panel test-payload-panel"><header class="panel-header"><div><h2 id="test-payload-title">Payload</h2><p class="small muted">${new TextEncoder().encode(rawPayload(item)).length.toLocaleString()} bytes · 원문 그대로 표시</p></div>${button("복사", "analysis-copy-payload", "ghost", "copy")}</header><p class="test-source-status" id="test-source-status" role="status" aria-live="polite">${H(selection.message)}</p><pre class="code-block raw-payload" id="test-payload" tabindex="0" aria-labelledby="test-payload-title"></pre></section>${decoding.length ? `<section class="panel"><header class="panel-header"><h2>디코딩 결과</h2><span class="small muted">원문과 별도 표시</span></header><div class="panel-body">${decoding.map(row => `<div class="decoding-pair"><div><span class="small muted">원본</span><code>${H(row.original)}</code></div><div><span class="small muted">${H(row.method || "디코딩")}</span><code>${H(row.decoded)}</code></div></div>`).join("")}</div></section>` : ""}<p class="test-input-notice small muted">${W.icon("shield", 14)} 관리자 원문 조회 화면의 예시입니다. 실제 서비스에서는 열람 이력을 남깁니다.</p></div></section></div>`;
  }

  function fitTestWorkspace() {
    const workspace = document.getElementById("test-analysis-workspace");
    if (workspace) workspace.style.setProperty("--test-workspace-top", `${Math.max(0, Math.ceil(workspace.getBoundingClientRect().top + window.scrollY))}px`);
  }
  window.addEventListener("resize", fitTestWorkspace);

  function rememberTestScroll(item) {
    const left = document.getElementById("test-results-scroll"), right = document.getElementById("test-input-scroll");
    if (left && right) testScrollPositions.set(item.id, { left: left.scrollTop, right: right.scrollTop });
  }

  function selectTestEvidence(item, ordinal, updateUrl = true) {
    const right = document.getElementById("test-input-scroll"), payload = document.getElementById("test-payload");
    if (!right || !payload) return;
    const selectedOrdinal = Number(ordinal);
    if (!evidence(item)[selectedOrdinal - 1]) return;
    const selection = testSourceSelection(item, selectedOrdinal);
    populateTestPayload(item, selectedOrdinal);
    document.getElementById("test-source-status").textContent = selection.message;
    document.getElementById("test-selected-source").textContent = `근거 ${selectedOrdinal} 선택`;
    W.root.querySelectorAll("[data-test-evidence]").forEach(card => {
      const active = Number(card.dataset.testEvidence) === selectedOrdinal;
      card.classList.toggle("test-evidence-selected", active);
      card.querySelector("[data-action='analysis-source']")?.setAttribute("aria-pressed", String(active));
    });
    W.root.querySelectorAll(".test-input-field-selected").forEach(row => row.classList.remove("test-input-field-selected"));
    let target = document.getElementById("source-highlight");
    if (selection.field) {
      const fieldRow = W.tables["test-input-fields"]?.getRows().find(row => row.getData().id === selection.field);
      target = fieldRow?.getElement();
      target?.classList.add("test-input-field-selected");
      if (!target) document.getElementById("test-source-status").textContent = `이 근거의 출처는 ${selection.field} 필드지만 입력에 기록되지 않았습니다. payload를 임의로 강조하지 않습니다.`;
    }
    if (!target) target = document.getElementById("test-source-status");
    if (target) {
      const top = target.getBoundingClientRect().top - right.getBoundingClientRect().top + right.scrollTop - 82;
      right.scrollTo({ top: Math.max(0, top), behavior: "auto" });
    }
    if (updateUrl) {
      const params = new URLSearchParams(W.query); params.set("evidence", selectedOrdinal); params.set("tab", "result");
      W.query = params; history.replaceState(history.state, "", `#analysis?${params}`);
    }
    rememberTestScroll(item);
  }

  function mountTestWorkspace(item) {
    populateTestPayload(item, Number(q("evidence")) || 0);
    const table = W.table("test-input-fields", testInputFields(item), [
      { title: "입력 필드", field: "name", width: 145, headerSort: false, variableHeight: true, formatter: cell => `<span class="test-field-label">${H(cell.getValue())}</span><code class="test-field-key">${H(cell.getRow().getData().id)}</code>` },
      { title: "값", field: "value", minWidth: 120, widthGrow: 2, headerSort: false, variableHeight: true, formatter: cell => `<span class="test-field-value">${H(cell.getValue())}</span>` }
    ], { pagination: false, selectableRows: false });
    const restore = () => requestAnimationFrame(() => {
      fitTestWorkspace();
      const saved = testScrollPositions.get(item.id);
      if (saved) { document.getElementById("test-results-scroll").scrollTop = saved.left; document.getElementById("test-input-scroll").scrollTop = saved.right; }
      if (q("evidence")) selectTestEvidence(item, q("evidence"), false);
    });
    table?.on("tableBuilt", restore); restore();
    document.getElementById("test-results-scroll").addEventListener("click", event => {
      const card = event.target.closest("[data-test-evidence]");
      if (card && !event.target.closest("button, a, summary, details, input, select, textarea")) selectTestEvidence(item, card.dataset.testEvidence);
    });
  }

  function highlightedPayload(item) {
    const index = Math.max(0, Number(q("evidence")) - 1);
    const excerpt = evidence(item)[index]?.excerpt;
    const raw = rawPayload(item);
    const start = excerpt ? raw.indexOf(excerpt) : -1;
    return start < 0 ? H(raw) : H(raw.slice(0, start)) + `<mark id="source-highlight" tabindex="-1" aria-label="선택한 근거의 원문">${H(excerpt)}</mark>` + H(raw.slice(start + excerpt.length));
  }

  function evidenceContent(item) {
    const decoding = Array.isArray(item.decoded) ? item.decoded : [];
    return `<div class="callout source-security">${W.icon("shield")}<span>관리자 원문 조회 화면입니다. 실제 서비스에서는 열람 이력이 기록됩니다. 아래는 가상 데이터입니다.</span></div><div class="evidence-layout"><section class="panel source-pane"><div class="panel-header"><div><h2>수신 원문</h2><p class="small muted">원문을 바꾸지 않고 선택한 근거 위치만 강조합니다.</p></div>${button("복사", "analysis-copy-payload", "ghost", "copy")}</div><div class="source-meta">${W.badge("payload", "neutral")}${q("evidence") ? W.badge(`근거 ${q("evidence")} 선택`, "violet") : ""}<span class="muted small">${new TextEncoder().encode(rawPayload(item)).length.toLocaleString()} bytes</span></div><pre class="code-block raw-payload">${highlightedPayload(item)}</pre>${q("evidence") && !rawPayload(item).includes(evidence(item)[Number(q("evidence")) - 1]?.excerpt || "\u0000") ? `<p class="small muted">선택한 발췌의 위치가 기록되지 않아 임의로 강조하지 않습니다.</p>` : ""}${decoding.length ? `<div class="panel-body"><h3>디코딩 결과</h3><p class="muted small">원문과 해석한 문자열을 분리합니다. 디코딩 결과도 안전한 실행 코드가 아닙니다.</p>${decoding.map(row => `<div class="decoding-pair"><div><span class="small muted">원본</span><code>${H(row.original)}</code></div><div><span class="small muted">${H(row.method || "디코딩")}</span><code>${H(row.decoded)}</code></div></div>`).join("")}</div>` : ""}<details class="panel-body"><summary>이벤트 필드</summary><pre class="code-block">${H(JSON.stringify({ event_id: item.event_id, company_name: item.company, waf_vendor: item.vendor, signature: item.signature, src_ip: item.source_ip, waf_action: item.action }, null, 2))}</pre></details></section><section class="source-evidence stack"><div class="source-evidence-heading"><h2>근거와 원문 연결</h2><p class="small muted">근거의 ‘원문에서 확인’을 누르면 왼쪽의 해당 구간으로 이동합니다.</p></div>${evidenceGroups(item)}</section></div>`;
  }

  const jsonTree = (value, key = "결과", depth = 0) => {
    if (value === null || typeof value !== "object") return `<div class="json-leaf"><span class="json-key">${H(key)}</span><span class="json-${value === null ? "null" : typeof value}">${H(JSON.stringify(value))}</span></div>`;
    return `<details class="json-branch"${depth < 1 ? " open" : ""}><summary><span class="json-key">${H(key)}</span> <span class="muted small">${Array.isArray(value) ? `[${value.length}]` : `{${Object.keys(value).length}}`}</span></summary><div class="json-children">${Object.entries(value).map(([child, data]) => jsonTree(data, child, depth + 1)).join("")}</div></details>`;
  };

  const stepStatusName = value => ({ completed: "완료", failed: "실패", processing: "실행 중", skipped: "미실행", pending: "대기" })[value] || "기록 없음";

  function resultExample(item) {
    if (item.status !== "completed") return null;
    return { schema_version: "waf-analysis-v3", verdict: item.verdict, threat_analysis: { severity: item.severity }, summary_ko: item.summary, evidence: evidence(item).map(row => ({ evidence_id: row.id, supports: row.supports, excerpt: row.excerpt, interpretation_ko: interpretation(row), origins: row.origins })) };
  }

  function executionSteps(item) {
    if (Array.isArray(item.execution_steps)) return item.execution_steps;
    const failed = item.status === "failed", complete = item.status === "completed", processing = item.status === "processing";
    const elapsed = timeValues(item).processing;
    const event = Object.fromEntries(testInputFields(item).filter(field => field.value !== "미기록").map(field => [field.id, field.value]));
    event.payload = rawPayload(item);
    const run = W.data.tests.find(row => row.id === item.run_id);
    const config = { primary: run?.primary || "Gemma 검토용", verifier: run?.verifier || "Gemma 검토용", instructions: run?.instructions || "공통 지침 v2.32", input_schema: run?.schema || "표준 입력 v4", fixture_only: true };
    const error = item.error || {};
    const definitions = [
      ["input", "입력 확인", "수신 필드와 고정된 입력 규격 확인", .005, event, { accepted: true, fields: Object.keys(event), configuration_snapshot: config }],
      ["parse", "HTTP 구조와 디코딩", "원문을 보존하고 분석용 구조 추출", .015, { payload: rawPayload(item) }, { request_line: rawPayload(item).split(/\r?\n/)[0], decoded: item.decoded || [], source_preserved: true }],
      ["primary", "1차 분석", "요청 동작과 근거의 보안 의미 분석", .48, { event, instructions: config.instructions, profile: config.primary }, { verdict: item.verdict, summary_ko: item.summary, evidence: evidence(item).filter(row => row.origins?.some(origin => origin.role === "primary")) }],
      ["verifier", "추가 검증", "1차 분석 답변 없이 같은 요청 독립 검토", .38, { event, instructions: config.instructions, profile: config.verifier }, { verdict: item.verdict, evidence: evidence(item).filter(row => row.origins?.some(origin => origin.role === "verifier")) }],
      ["policy", "최종 판정", "검증된 결과를 서버 정책으로 결합", .01, { primary_verdict: item.verdict, verifier_verdict: item.verdict }, { verdict: item.verdict }],
      ["editor", "근거 정리", "같은 논점에서 보여줄 대표 근거 선택", .11, { evidence: evidence(item) }, { representative_evidence_ids: evidence(item).map(row => row.id), verdict_changed: false }]
    ];
    let offset = 0;
    const started = Date.parse(item.started_at);
    return definitions.map(([id, name, note, weight, input, output], index) => {
      const status = complete ? "completed" : failed ? index < 2 ? "completed" : index === 2 ? "failed" : "skipped" : processing ? index < 2 ? "completed" : index === 2 ? "processing" : "pending" : "pending";
      const measured = elapsed != null && ["completed", "failed"].includes(status);
      const ms = measured ? Math.round(elapsed * 1000 * (failed && index === 2 ? .98 : weight)) : null;
      const start = measured && Number.isFinite(started) ? new Date(started + offset).toISOString() : null;
      const end = start ? new Date(started + offset + ms).toISOString() : null;
      if (ms != null) offset += ms;
      return { id, name, note, status, duration_ms: ms, started_at: start, completed_at: end, input: ["skipped", "pending"].includes(status) ? null : input, output: status === "failed" ? { error_code: error.code || "미기록", provider_finish_reason: error.finish_reason || null, attempt_count: error.attempt_count ?? null } : status === "completed" ? output : null, metadata: { ...config, source: "wireframe_example", raw_access: "admin_only" } };
    });
  }

  function executionSelection(item) {
    const steps = executionSteps(item), wanted = q("step");
    const final = { id: "result", name: "최종 결과 JSON", note: "단계별 모델 출력과 구분한 API 결과 구조 예시", status: item.status === "completed" ? "completed" : "pending", output: resultExample(item), metadata: { schema_version: "waf-analysis-v3", fixture_only: true } };
    return wanted === "result" ? final : steps.find(row => String(row.id) === wanted) || steps.find(row => row.status === "failed" || row.status === "processing") || steps.find(row => row.id === "primary") || steps[0] || final;
  }

  function executionDetail(item, step) {
    const view = ["input", "output", "meta"].includes(q("view")) ? q("view") : "output";
    const value = view === "meta" ? { status: step.status, duration_ms: step.duration_ms ?? null, started_at: step.started_at ?? null, completed_at: step.completed_at ?? null, ...(step.metadata || {}) } : step[view];
    const missing = view === "input" ? "이 단계의 입력 기록이 없습니다." : step.status === "skipped" ? "이 단계는 실행하지 않아 출력이 없습니다." : ["processing", "pending"].includes(step.status) ? "아직 완료된 출력이 없습니다." : "이 단계의 출력 기록이 없습니다.";
    return `<header class="execution-detail-heading"><div><span class="eyebrow">${step.id === "result" ? "API 결과" : "선택한 단계"}</span><h2>${H(step.name)}</h2><p class="small muted">${H(step.note || "실행 당시 기록을 확인합니다.")}</p></div><div class="execution-step-state">${W.badge(stepStatusName(step.status), step.status === "failed" ? "red" : step.status === "completed" ? "teal" : "neutral")}<span class="mono small muted">${W.timing.format(step.duration_ms == null ? null : step.duration_ms / 1000)}</span></div></header><nav class="execution-record-tabs" aria-label="단계 기록 종류">${[["output", "출력"], ["input", "입력"], ["meta", "실행 정보"]].map(([key, title]) => `<button class="execution-record-tab${view === key ? " active" : ""}" data-action="analysis-step-view" data-view="${key}" aria-pressed="${view === key}"${step.id === "result" && key === "input" ? ' disabled title="최종 결과 JSON에는 별도 단계 입력이 없습니다"' : ""}>${title}</button>`).join("")}</nav><div class="execution-record-body"><p class="small muted execution-record-note">${step.id === "result" ? "API 결과의 주요 필드만 보여주는 시안입니다. 원래 역할별 출력은 각 단계에서 구분해 확인합니다." : "가상 데이터로 구성한 단계 기록 예시입니다. 실제 모델 응답이나 프롬프트가 아니며 원문 열람에는 관리자 권한과 감사 기록이 필요합니다."}</p>${value == null ? `<div class="execution-no-record">${W.icon("file", 24)}<p>${missing}</p></div>` : `<div class="json-view" id="execution-record-json">${jsonTree(value, view === "input" ? "input" : view === "output" ? "output" : "execution")}</div>`}</div>`;
  }

  function executionContent(item) {
    const steps = executionSteps(item), chosen = executionSelection(item);
    const failed = item.status === "failed", run = W.data.tests.find(row => row.id === item.run_id);
    return `<section class="execution-overview">${W.metrics([{ label: elapsedLabel(item), value: duration(item, "total"), note: `${timeValues(item).terminal ? "접수부터 종료" : "접수부터 현재"} / 큐 대기 포함`, tone: "violet" }, { label: "큐 대기", value: duration(item, "queue"), note: "서버 접수부터 첫 분석 시작", tone: "amber" }, { label: "분석 소요", value: duration(item), note: `${timeValues(item).terminal ? "첫 시작부터 종료" : "첫 시작부터 현재"} / 큐 대기 제외`, tone: "teal" }])}<p class="timing-boundary-note small muted">분석 소요에는 모델 호출, 내부 대기와 재시도가 포함됩니다. 병렬 단계 시간을 더한 값이 아닙니다. HTTP 요청→응답 시간은 별도 지표이며, 이 시안에서는 미측정입니다.</p></section>${failed ? `<div class="execution-failure callout callout-warning"><div><strong>분석을 완료하지 못했습니다.</strong><p class="small">오류가 발생한 단계를 선택해 출력과 실행 정보를 확인하세요.</p></div>${button("실패 당시 설정으로 재실행", "analysis-retry", "secondary", "refresh")}</div>` : ""}<div class="execution-workspace" id="execution-workspace"><section class="execution-navigation panel" id="execution-steps" aria-label="실행 단계"><header class="panel-header"><div><h2>실행 단계</h2><p class="small muted">단계를 선택하면 오른쪽에 기록을 표시합니다.</p></div></header><ol class="execution-step-list">${steps.map((step, index) => `<li><button class="execution-step${String(chosen.id) === String(step.id) ? " active" : ""}" data-action="analysis-step" data-step="${H(step.id)}" aria-current="${String(chosen.id) === String(step.id) ? "step" : "false"}" aria-controls="execution-detail-pane"><span class="execution-step-index ${H(step.status)}">${step.status === "completed" ? W.icon("check", 13) : step.status === "failed" ? W.icon("x", 13) : index + 1}</span><span class="execution-step-text"><strong>${H(step.name)}</strong><small>${H(step.note || "단계 기록")}</small><span class="execution-step-footer"><span>${stepStatusName(step.status)}</span><span class="mono">${W.timing.format(step.duration_ms == null ? null : step.duration_ms / 1000)}</span></span></span>${W.icon("chevronRight", 13)}</button></li>`).join("")}</ol><div class="execution-final-result"><button class="button button-ghost${chosen.id === "result" ? " active" : ""}" data-action="analysis-step" data-step="result" aria-controls="execution-detail-pane" aria-current="${chosen.id === "result" ? "step" : "false"}">${W.icon("code", 15)} 최종 결과 JSON ${W.icon("chevronRight", 13)}</button></div><details class="execution-configuration"><summary>실행 당시 구성</summary><dl class="context-grid"><div><dt>Primary</dt><dd>${H(run?.primary || "Gemma 검토용")}</dd></div><div><dt>Verifier</dt><dd>${H(run?.verifier || "Gemma 검토용")}</dd></div><div><dt>분석 지침</dt><dd>${H(run?.instructions || "공통 지침 v2.32")}</dd></div><div><dt>입력 스키마</dt><dd>${H(run?.schema || "표준 입력 v4")}</dd></div></dl><p class="small muted">시안의 구성 예시이며 현재 운영 설정이 아닙니다.</p></details></section><section class="execution-detail panel" id="execution-detail-pane" tabindex="0" aria-label="선택한 단계의 기록">${executionDetail(item, chosen)}</section></div>`;
  }

  function fitExecutionWorkspace() {
    const workspace = document.getElementById("execution-workspace");
    if (workspace) workspace.style.setProperty("--execution-workspace-top", `${Math.ceil(workspace.getBoundingClientRect().top + window.scrollY)}px`);
  }
  window.addEventListener("resize", fitExecutionWorkspace);

  function selectExecutionStep(item, stepId, view) {
    const pane = document.getElementById("execution-detail-pane");
    if (!pane) return;
    if (stepId === "result" && view === "input") view = "output";
    const restoreViewFocus = pane.contains(document.activeElement) && document.activeElement?.dataset.action === "analysis-step-view";
    const params = new URLSearchParams(W.query); params.set("step", stepId); params.set("view", view); params.set("tab", "execution");
    W.query = params; history.replaceState(history.state, "", `#analysis?${params}`);
    const selectedStep = executionSelection(item);
    pane.innerHTML = executionDetail(item, selectedStep); pane.scrollTop = 0;
    if (restoreViewFocus) pane.querySelector(`[data-action="analysis-step-view"][data-view="${view}"]`)?.focus({ preventScroll: true });
    W.root.querySelectorAll("[data-action='analysis-step']").forEach(el => { const active = String(el.dataset.step) === String(selectedStep.id); el.classList.toggle("active", active); el.setAttribute("aria-current", active ? "step" : "false"); });
  }

  function reportMarkdown(item) {
    return `# WAF 심층 분석 보고서\n\n> 가상 데이터로 구성한 UI 시안이며 실제 분석 결과가 아닙니다.\n\n## 판정\n\n${label(item.verdict)} / ${item.severity || "미기록"}\n\n## 분석 요약\n\n${item.summary || "결과 미기록"}\n\n${[["attack_request", "공격 요청 근거"], ["normal_request", "정상 요청 근거"], ["context", "참고 내용"]].map(([kind, title]) => `## ${title}\n\n${evidence(item).filter(row => support(row) === kind).map(row => `${interpretation(row)}\n\n\`\`\`text\n${row.excerpt}\n\`\`\``).join("\n\n") || "해당 근거 없음"}`).join("\n\n")}\n\n## 이벤트 정보\n\n회사: ${item.company}\n탐지명: ${item.signature}\nWAF 조치: ${item.action === "D" ? "차단" : "허용"}\n`;
  }

  function reportContent(item) {
    const raw = q("view") === "markdown";
    return `<div class="report-toolbar toolbar"><div class="local-tabs compact-tabs" aria-label="보고서 표시 방식"><button class="local-tab${!raw ? " active" : ""}" data-action="analysis-report-preview">보고서</button><button class="local-tab${raw ? " active" : ""}" data-action="analysis-report-raw">Markdown 원본</button></div><div class="row">${button("Markdown 저장", "analysis-report-download", "secondary", "download")}${button("PDF 다운로드", "analysis-pdf", "primary", "download")}</div></div>${raw ? `<section class="panel"><pre class="code-block report-markdown">${H(reportMarkdown(item))}</pre></section>` : `<article class="report-paper"><div class="report-brand">WAF AI CONSOLE <span>ANALYSIS REPORT</span></div><div class="report-eyebrow">요청 보안 분석</div><h1>WAF 심층 분석 보고서</h1><p class="report-description">가상 데이터로 구성한 화면 예시입니다.</p><div class="report-result">${W.verdict(item.verdict)} ${W.severity(item.severity)}<span class="muted">${H(item.company)}</span></div><h2>분석 요약</h2><p>${H(item.summary)}</p>${evidenceGroups(item, true)}<h2>이벤트 정보</h2><dl class="context-grid"><div><dt>탐지명</dt><dd>${H(item.signature)}</dd></div><div><dt>WAF 조치</dt><dd>${item.action === "D" ? "차단" : "허용"}</dd></div><div><dt>이벤트 ID</dt><dd class="mono">${H(item.event_id)}</dd></div></dl><footer>이 보고서는 정책을 자동 변경하지 않습니다. 실제 영향과 조치는 담당자가 확인합니다.</footer></article>`}`;
  }

  function feedbackBody(rows, item) {
    return `<div class="stack"><p class="small muted">피드백은 추가형 이력입니다. AI 입력과 참고 판정에 자동 반영하지 않습니다.</p>${rows.length ? rows.map(row => `<article class="feedback-entry"><div class="row"><strong>${H(row.author || "관제 분석가")}</strong><time class="muted small">${H(row.time || row.created_at || "2026-11-25 10:24")}</time></div><div class="feedback-categories">${(row.categories || []).map(value => W.badge(categoryName(value), "violet")).join("") || W.badge("의견만 등록", "neutral")}</div><p>${H(row.comment || "추가 의견 없음")}</p></article>`).join("") : `<div class="empty-state">${W.icon("message")}<h3>아직 등록된 피드백이 없습니다</h3><p>분석의 근거와 설명에서 개선할 점을 남겨 주세요.</p></div>`}${item ? `<div class="row">${entityButton("이 분석의 피드백 모아보기", "analysis-all-feedback", item.id)}</div>` : ""}</div>`;
  }

  function openFeedbackForm(item) {
    W.dialog({ title: "분석 피드백", body: `<form id="analysis-feedback-form" class="stack"><p class="muted">개선이 필요한 항목과 의견을 선택해서 남겨 주세요. 참고 판정은 바뀌지 않습니다.</p><fieldset class="feedback-fieldset"><legend>피드백 항목 <span class="muted small">선택 사항</span></legend><div class="feedback-choice-grid">${categories.map(([value, title]) => `<label class="checkbox-card"><input type="checkbox" name="categories" value="${value}"><span>${title}</span></label>`).join("")}</div></fieldset><label class="field"><span>의견 <span class="muted small">선택 사항 · 기타 선택 시 작성</span></span><textarea name="comment" rows="4" placeholder="어떤 내용이 부족하거나 잘못됐는지 적어 주세요."></textarea></label><div class="callout small">추가한 피드백은 이력으로 남습니다. 이 시안에서는 메모리에만 저장되며 실제 API를 호출하지 않습니다.</div><p id="feedback-form-error" class="form-error" role="alert"></p></form>`, footer: `<button class="button button-secondary" data-close-dialog>취소</button><button class="button button-primary" data-action="analysis-feedback-save" data-id="${H(item.id)}">피드백 등록</button>` });
  }

  function exportDialog(rows) {
    W.dialog({ title: "분석 목록 다운로드", body: `<div class="stack"><p>검색 조건에 맞는 <strong>${rows.length}건</strong>을 대상으로 합니다. 현재 페이지만 내려받는 것이 아닙니다.</p><fieldset class="feedback-fieldset"><legend>포함할 컬럼</legend><div class="feedback-choice-grid">${[["event_id", "이벤트 ID"], ["company_name", "회사"], ["signature", "탐지명"], ["waf_action", "WAF 조치"], ["verdict", "최종 판정"], ["severity", "심각도"], ["summary_ko", "분석 요약"], ["payload", "HTTP 원문"]].map(([en, ko]) => `<label class="checkbox-card"><input type="checkbox"${en === "payload" ? "" : " checked"}><span><strong class="mono small">${en}</strong><small>${ko}</small></span></label>`).join("")}</div></fieldset><p class="callout small">완성 서비스에서는 분석결과/컬럼안내 시트로 저장합니다. 원문 포함 시 민감한 정보가 파일에 담길 수 있습니다. 이 시안은 실제 파일 생성이나 서버 조회를 하지 않습니다.</p></div>`, footer: `<button class="button button-secondary" data-close-dialog>닫기</button><button class="button button-primary" data-action="ops-export-confirm">Excel 다운로드 예시</button>` });
  }

  W.pages.operations = {
    render() {
      const rows = operationRows();
      const keys = [...new Set(analyses().filter(item => item.purpose !== "test").map(item => item.key).filter(Boolean))];
      const preview = get(q("preview"));
      return `${W.heading("분석 이력", "Production API로 접수한 요청만 표시합니다. 판정과 근거를 확인하고 필요한 항목을 검토하세요.", button("Excel 다운로드", "ops-export", "secondary", "download"))}${W.metrics([{ label: "검색된 요청", value: rows.length, note: "현재 조건에 해당하는 전체 범위", tone: "blue" }, { label: "정탐", value: rows.filter(item => item.verdict === "true_positive").length, note: "공격 요청으로 판정", tone: "red" }, { label: "판정 보류", value: rows.filter(item => item.verdict === "inconclusive").length, note: "확정에 필요한 정보 확인", tone: "amber" }, { label: "실행 실패", value: rows.filter(item => item.status === "failed").length, note: "판정이 아닌 처리 오류", tone: "violet" }])}<section class="panel"><div class="panel-header"><div><h2>분석 요청</h2><p class="small muted">판정과 처리 상태를 나누어 표시합니다.</p></div>${button("새로고침", "ops-refresh", "ghost", "refresh")}</div><div class="filter-row">${search("회사, 탐지명, 이벤트 ID 또는 IP 검색")}${select("verdict", "최종 판정", [["", "모든 판정"], ["true_positive", "정탐"], ["false_positive", "오탐"], ["inconclusive", "판정 보류"]])}${select("status", "처리 상태", [["", "모든 상태"], ["completed", "완료"], ["processing", "처리 중"], ["failed", "실패"]])}${select("key", "API 키 별칭", [["", "모든 API 키"], ...keys.map(value => [value, value])])}${button("초기화", "ops-clear", "ghost")}</div><div id="ops-selection" class="selection-toolbar" hidden><strong><span id="ops-selection-count">0</span>건 선택</strong>${button("참고 판정 입력", "ops-bulk-reference", "secondary", "checkCircle")}${button("정답 데이터에 추가", "ops-add-dataset", "secondary", "plus")}</div><div class="table-inset"><div id="operations-table" aria-label="Production 분석 이력"></div></div></section>${preview ? `<aside class="drawer analysis-preview" aria-label="분석 미리보기"><div class="panel-header"><div><span class="eyebrow">분석 미리보기</span><h2>${H(preview.company)}</h2></div><button class="icon-button" aria-label="미리보기 닫기" data-action="ops-preview-close">${W.icon("x")}</button></div><div class="panel-body stack"><div class="row">${W.verdict(preview.verdict)}${W.severity(preview.severity)}${W.status(preview.status)}</div><p class="preview-summary">${H(preview.summary || "분석 결과가 아직 없습니다.")}</p><dl class="context-grid"><div><dt>탐지명</dt><dd>${H(preview.signature)}</dd></div><div><dt>WAF 조치</dt><dd>${preview.action === "D" ? "차단" : "허용"}</dd></div></dl><h3>핵심 근거</h3>${evidence(preview).slice(0, 2).map(row => `<div class="preview-evidence"><span class="support-label ${support(row)}">${support(row) === "attack_request" ? "공격 요청 근거" : support(row) === "normal_request" ? "정상 요청 근거" : "참고 내용"}</span><p>${H(interpretation(row))}</p></div>`).join("") || `<p class="small muted">표시할 근거가 없습니다.</p>`}${entityButton("분석 상세 열기", "ops-open", preview.id, "button-primary")}</div></aside>` : ""}`;
    },
    mount() {
      filterControls("operations");
      W.table("operations-table", operationRows(), [
        { formatter: "rowSelection", titleFormatter: "rowSelection", hozAlign: "center", headerSort: false, width: 42 },
        { title: "처리", field: "status", minWidth: 94, width: 100, formatter: cell => W.status(cell.getValue()) },
        { title: "최종 판정", field: "verdict", minWidth: 110, width: 116, formatter: cell => W.verdict(cell.getValue()) },
        { title: "심각도", field: "severity", minWidth: 105, width: 110, formatter: cell => W.severity(cell.getValue()) },
        { title: "요청과 분석 요약", field: "summary", minWidth: 310, widthGrow: 3, formatter: cell => { const item = cell.getRow().getData(); return `<button class="table-summary" data-action="ops-preview" data-id="${H(item.id)}"><strong>${H(item.signature)}</strong><span title="${H(item.summary || "분석 전")}">${H(item.summary || "분석 전")}</span></button>`; } },
        { title: "회사", field: "company", minWidth: 110, widthGrow: 1 },
        { title: "API 키", field: "key", minWidth: 110, widthGrow: 1 },
        { title: "접수→완료", field: "total_elapsed_ms", width: 172, hozAlign: "right", formatter: cell => { const item = cell.getRow().getData(); return `<strong>${H(duration(item, "total"))}</strong><span class="time-cell-breakdown">큐 ${H(duration(item, "queue"))} / 분석 ${H(duration(item))}</span>`; } },
        { title: "상세", width: 72, headerSort: false, formatter: cell => `<button class="icon-button" data-action="ops-open" data-id="${H(cell.getRow().getData().id)}" aria-label="분석 상세 열기">${W.icon("arrowUpRight")}</button>` }
      ], { selectableRows: true, rowSelectionChanged(data) { selected.clear(); data.forEach(item => selected.add(String(item.id))); const bar = document.getElementById("ops-selection"); if (bar) bar.hidden = !selected.size; const count = document.getElementById("ops-selection-count"); if (count) count.textContent = selected.size; } });
    },
    action(name, el) {
      const id = el?.dataset.id;
      if (name === "ops-preview") nav("operations", { preview: id });
      if (name === "ops-preview-close") nav("operations", { preview: null });
      if (name === "ops-open") W.go("analysis", { id, tab: "result", from: "operations" });
      if (name === "ops-clear") W.go("operations");
      if (name === "ops-refresh") W.toast("가상 데이터를 다시 표시했습니다. 서버 요청은 보내지 않았습니다.");
      if (name === "ops-export") exportDialog(operationRows());
      if (name === "ops-export-confirm") W.toast("다운로드 구성 예시입니다. 실제 Excel 생성은 서비스 연결 시 제공됩니다.");
      if (name === "ops-bulk-reference") referenceDialog([...selected].map(get).filter(Boolean));
      if (name === "ops-reference-save") saveReference();
      if (name === "ops-add-dataset") {
        const drafts = (W.data.datasets || []).filter(item => item.status === "draft");
        W.dialog({ title: "정답 데이터에 추가", body: `<div class="stack"><p>선택한 ${selected.size}건을 복사합니다. 중복 항목은 제외합니다.</p>${drafts.length ? `<label class="field"><span>추가할 정답 데이터 초안</span><select id="ops-dataset-target">${drafts.map(item => `<option value="${H(item.id)}">${H(item.name)} · 편집 중</option>`).join("")}</select></label>` : `<div class="callout callout-warning">추가할 수 있는 초안이 없습니다. 정답 데이터에서 새 초안을 만든 뒤 다시 선택해 주세요.</div>`}<p class="callout small">복사한 분석 결과가 공식 정답으로 자동 승인되지는 않습니다. 원본과 별개의 사본으로 보존합니다.</p></div>`, footer: `<button class="button button-secondary" data-close-dialog>취소</button>${drafts.length ? `<button class="button button-primary" data-action="ops-dataset-confirm">선택 항목 추가</button>` : `<button class="button button-primary" data-action="ops-datasets-open">정답 데이터로 이동</button>`}` });
      }
      if (name === "ops-dataset-confirm") {
        const id = document.getElementById("ops-dataset-target")?.value;
        const target = (W.data.datasets || []).find(item => String(item.id) === id && item.status === "draft");
        if (!target) { W.toast("추가할 정답 데이터 초안을 선택해 주세요."); return; }
        W.toast(`‘${target.name}’에 추가하는 동선 예시입니다. 실제 데이터는 변경하지 않았습니다.`);
      }
      if (name === "ops-datasets-open") { closeDialog(); W.go("datasets"); }
    }
  };

  let referenceTargets = [];
  function referenceDialog(items) {
    referenceTargets = items;
    W.dialog({ title: items.length > 1 ? "참고 판정 일괄 입력" : "참고 판정 입력", body: `<form id="reference-form" class="stack"><p>${items.length}건의 비교용 참고 판정을 입력합니다. 모델은 다시 호출하지 않습니다.</p><label class="field"><span>참고 판정</span><select name="reference"><option value="true_positive">정탐</option><option value="false_positive">오탐</option><option value="inconclusive">판정 보류</option></select></label><label class="field"><span>메모</span><textarea name="comment" rows="3" placeholder="판정 기준이나 확인한 내용을 적어 주세요."></textarea></label><p class="callout small">참고 판정은 AI 입력에 전달되지 않으며, 공식 Ground Truth 승인과는 별개입니다.</p></form>`, footer: `<button class="button button-secondary" data-close-dialog>취소</button><button class="button button-primary" data-action="ops-reference-save">참고 판정 저장</button>` });
  }
  function closeDialog() { const dialog = document.querySelector("dialog[open]"); if (dialog) dialog.close(); }
  function saveReference() {
    const form = document.getElementById("reference-form");
    if (!form) return;
    const reference = new FormData(form).get("reference");
    referenceTargets.forEach(item => item.reference = reference);
    closeDialog(); W.toast(`${referenceTargets.length}건의 참고 판정을 시안 메모리에 저장했습니다.`);
    W.go(q("id") ? "analysis" : "operations", Object.fromEntries(W.query));
  }

  W.pages.analysis = {
    render() {
      const item = current();
      if (!item) return W.heading("분석을 찾을 수 없습니다", "분석 목록에서 항목을 다시 선택해 주세요.", button("분석 이력으로", "analysis-back", "secondary", "arrowLeft"));
      const requestedTab = q("tab") || "result";
      const isTest = item.purpose === "test";
      const tab = isTest && !["execution", "report"].includes(requestedTab) ? "result" : requestedTab;
      const unavailable = item.status !== "completed";
      const backLabel = q("from") === "feedback" ? "피드백으로 돌아가기" : q("from") === "home" ? "운영 현황으로 돌아가기" : item.purpose === "test" ? "테스트로 돌아가기" : "분석 이력으로 돌아가기";
      const actions = `<div class="detail-actions"><button class="button button-ghost" data-action="analysis-back">${W.icon("arrowLeft")}${H(backLabel)}</button><div class="row">${item.status === "failed" ? button("재실행", "analysis-retry", "primary", "refresh") : ""}${button("PDF 다운로드", "analysis-pdf", "secondary", "download")}${button("Excel 다운로드", "analysis-excel", "secondary", "download")}${button("이벤트 정보", "analysis-metadata", "ghost", "more")}</div></div>`;
      const tabs = `<nav class="local-tabs detail-tabs" aria-label="분석 상세"><button class="local-tab${tab === "result" ? " active" : ""}" data-action="analysis-tab" data-tab="result" aria-current="${tab === "result" ? "page" : "false"}">${isTest ? "분석 결과" : "판정"}</button>${isTest ? "" : `<button class="local-tab${tab === "evidence" ? " active" : ""}" data-action="analysis-tab" data-tab="evidence">근거·입력</button>`}<button class="local-tab${tab === "execution" ? " active" : ""}" data-action="analysis-tab" data-tab="execution">실행</button><button class="local-tab${tab === "report" ? " active" : ""}" data-action="analysis-tab" data-tab="report">보고서</button></nav>`;
      if (isTest && tab === "result") return actions + tabs + testAnalysisWorkspace(item);
      if (tab === "execution") return actions + `<div class="execution-page-heading"><div><h1>실행 기록</h1><p class="small muted">${H(item.company)} / ${H(item.signature)}</p></div>${W.status(item.status)}</div>` + tabs + executionContent(item);
      return actions + analysisHero(item) + tabs + (tab === "evidence" ? evidenceContent(item) : tab === "execution" ? executionContent(item) : tab === "report" ? unavailable ? W.card("보고서 준비 전", `<p class="muted">분석이 완료되면 보고서를 표시합니다.</p>`) : reportContent(item) : resultContent(item));
    },
    mount() {
      if (document.getElementById("execution-workspace")) { requestAnimationFrame(fitExecutionWorkspace); return; }
      if (document.getElementById("test-analysis-workspace")) { mountTestWorkspace(current()); return; }
      if (q("evidence")) requestAnimationFrame(() => { const target = document.getElementById("source-highlight"); target?.focus({ preventScroll: true }); target?.scrollIntoView({ block: "nearest", inline: "nearest" }); });
    },
    action(name, el) {
      const item = current();
      if (item) rememberTestScroll(item);
      if (name === "analysis-back") { if (q("from") === "feedback") W.go("feedback"); else if (q("from") === "home") W.go("home"); else if (q("test")) W.go("test", { id: q("test"), tab: "cases" }); else if (["test", "tests"].includes(q("from")) || item?.purpose === "test") W.go("tests"); else W.go("operations"); return; }
      if (!item) return;
      if (name === "analysis-step") { selectExecutionStep(item, el.dataset.step, ["input", "output", "meta"].includes(q("view")) ? q("view") : "output"); return; }
      if (name === "analysis-step-view") { selectExecutionStep(item, String(executionSelection(item).id), el.dataset.view); return; }
      if (name === "analysis-tab") nav("analysis", { tab: el.dataset.tab, evidence: null, view: null });
      if (name === "analysis-source") {
        if (item.purpose === "test" && document.getElementById("test-analysis-workspace")) selectTestEvidence(item, el.dataset.evidence);
        else nav("analysis", { tab: item.purpose === "test" ? "result" : "evidence", evidence: el.dataset.evidence });
      }
      if (name === "analysis-execution") nav("analysis", { tab: "execution" });
      if (name === "analysis-report-preview") nav("analysis", { view: null });
      if (name === "analysis-report-raw") nav("analysis", { view: "markdown" });
      if (name === "analysis-feedback-new") openFeedbackForm(item);
      if (name === "analysis-feedback-history") W.dialog({ title: "분석 피드백 이력", body: feedbackBody(recordFeedback(item), item), footer: `<button class="button button-secondary" data-close-dialog>닫기</button><button class="button button-primary" data-action="analysis-feedback-new">피드백 추가</button>` });
      if (name === "analysis-all-feedback") { closeDialog(); W.go("feedback", { analysis: item.id }); }
      if (name === "analysis-reference") referenceDialog([item]);
      if (name === "ops-reference-save") saveReference();
      if (name === "analysis-feedback-save") {
        const form = document.getElementById("analysis-feedback-form"); if (!form) return;
        const fd = new FormData(form); const chosen = fd.getAll("categories"); const comment = String(fd.get("comment") || "").trim();
        if (chosen.includes("other") && !comment) { document.getElementById("feedback-form-error").textContent = "기타를 선택한 경우 의견을 적어 주세요."; form.querySelector("textarea").focus(); return; }
        const target = get(el.dataset.id) || item;
        allFeedback().unshift({ id: `feedback-demo-${Date.now()}`, analysis_id: target.id, purpose: target.purpose, company: target.company, signature: target.signature, categories: chosen, comment, author: "데모 분석가", time: "방금 전", created_at: new Date().toISOString(), production_version_id: target.purpose === "production" ? target.production_version_id ?? null : null });
        closeDialog(); W.toast("피드백을 시안 메모리에 추가했습니다. 기존 이력은 변경하지 않았습니다."); nav("analysis", {});
      }
      if (name === "analysis-retry") W.dialog({ title: "실패한 분석 재실행", body: `<div class="stack"><p>기존 실패 이력을 보존하고 새 실행을 연결합니다.</p><dl class="context-grid"><div><dt>모델</dt><dd>실패 당시 프로필</dd></div><div><dt>지침과 스키마</dt><dd>실패 당시 고정 버전</dd></div></dl><div class="callout callout-warning">실제 서비스에서는 모델 호출과 비용이 발생할 수 있습니다. 이 시안에서는 호출하지 않습니다.</div></div>`, footer: `<button class="button button-secondary" data-close-dialog>취소</button><button class="button button-primary" data-action="analysis-retry-confirm">재실행 예시 확인</button>` });
      if (name === "analysis-retry-confirm") { closeDialog(); W.toast("재실행 확인 동선입니다. 모델 호출이나 결과 변경은 하지 않았습니다."); }
      if (name === "analysis-metadata") W.dialog({ title: "이벤트와 실행 정보", body: `<dl class="context-grid"><div><dt>이벤트 ID</dt><dd class="mono">${H(item.event_id)}</dd></div><div><dt>분석 ID</dt><dd class="mono">${H(item.id)}</dd></div><div><dt>출발지</dt><dd class="mono">${H(item.source_ip)}</dd></div><div><dt>용도</dt><dd>${purposeName(item)}</dd></div><div><dt>API 키 별칭</dt><dd>${H(item.key)}</dd></div><div><dt>접수 시각</dt><dd>${H(item.time)}</dd></div></dl><p class="muted small">실제 서비스의 해시와 전체 버전 정의는 관리자 실행 이력에서 확인합니다.</p>`, footer: `<button class="button button-secondary" data-close-dialog>닫기</button>` });
      if (name === "analysis-copy-payload") navigator.clipboard?.writeText(rawPayload(item)).then(() => W.toast("가상 원문을 복사했습니다.")).catch(() => W.toast("브라우저가 복사를 허용하지 않습니다. 원문을 직접 선택해 주세요."));
      if (name === "analysis-pdf") W.dialog({ title: "PDF 보고서 다운로드", body: `<div class="stack"><p>웹 보고서와 같은 대표 근거를 PDF에 담습니다.</p><label class="checkbox-card"><input type="checkbox"><span>HTTP 전체 원문 포함 <small>민감한 정보가 포함될 수 있습니다.</small></span></label><p class="callout small">직접 다운로드 동선의 시안입니다. 실제 서비스에서는 기존 PDF 생성기를 사용하며 Chromium을 추가하지 않습니다.</p></div>`, footer: `<button class="button button-secondary" data-close-dialog>닫기</button><button class="button button-primary" data-action="analysis-download-demo">PDF 다운로드 예시</button>` });
      if (name === "analysis-excel") exportDialog([item]);
      if (name === "ops-export-confirm" || name === "analysis-download-demo") W.toast("다운로드 확인 동선입니다. 실제 PDF/Excel은 생성하지 않습니다.");
      if (name === "analysis-report-download") { const blob = new Blob([reportMarkdown(item)], { type: "text/markdown;charset=utf-8" }); const url = URL.createObjectURL(blob); const a = document.createElement("a"); a.href = url; a.download = `waf-demo-report-${String(item.event_id || "sample").replace(/[^a-zA-Z0-9_-]/g, "_")}.md`; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000); W.toast("가상 데이터의 Markdown 예시를 저장했습니다."); }
    }
  };

  function feedbackDefaults() {
    const end = W.data.feedback_reference_date || new Intl.DateTimeFormat("sv-SE", { timeZone: "Asia/Seoul" }).format(new Date());
    const start = new Date(`${end}T00:00:00Z`); start.setUTCDate(start.getUTCDate() - 29);
    return { start: start.toISOString().slice(0, 10), end };
  }

  function feedbackScope() {
    const defaults = feedbackDefaults(), source = q("analysis") ? get(q("analysis")) : null;
    const requested = q("purpose") || source?.purpose || "production";
    const purpose = ["all", "production", "test"].includes(requested) ? requested : "production";
    return { start: W.query.has("start") ? q("start") : defaults.start, end: W.query.has("end") ? q("end") : defaults.end, purpose, version: purpose === "test" ? "" : q("version"), category: q("category"), analysis: q("analysis"), query: q("q") };
  }

  function feedbackDateError(scope) {
    if (!W.feedbackStats.validDate(scope.start) || !W.feedbackStats.validDate(scope.end)) return "시작일과 종료일을 올바르게 입력해 주세요. 조회 조건이 적용되지 않았습니다.";
    if (scope.start > scope.end) return "시작일은 종료일보다 늦을 수 없습니다. 조회 조건이 적용되지 않았습니다.";
    return "";
  }

  const feedbackData = () => W.feedbackStats.normalize(allFeedback(), analyses());
  const feedbackRows = (scope = feedbackScope()) => feedbackDateError(scope) ? [] : W.feedbackStats.filter(feedbackData(), scope);
  const versionLabel = id => !id || id === "__unrecorded__" ? "버전 미기록" : (W.data.production_versions || []).find(row => row.id === id)?.label || "등록 정보가 없는 버전";

  function feedbackVersionOptions(scope) {
    const versions = new Map((W.data.production_versions || []).map(row => [row.id, row.label]));
    feedbackData().filter(row => row.purpose === "production" && row.production_version_id).forEach(row => { if (!versions.has(row.production_version_id)) versions.set(row.production_version_id, row.production_version_label || "등록 정보가 없는 버전"); });
    if (scope.version && scope.version !== "__unrecorded__" && !versions.has(scope.version)) versions.set(scope.version, "이력에서 찾을 수 없는 버전");
    return option("", scope.purpose === "test" ? "Test / 적용 안 함" : "모든 운영 버전", scope.version) + [...versions].map(([id, title]) => option(id, title, scope.version)).join("") + option("__unrecorded__", "버전 미기록", scope.version);
  }

  function feedbackFilters(scope) {
    return `<section class="panel feedback-filter-panel" aria-label="피드백 조회 조건"><form id="feedback-filters" class="feedback-filters"><label class="field"><span>등록 시작일</span><input type="date" name="start" value="${H(W.feedbackStats.validDate(scope.start) ? scope.start : "")}" required></label><label class="field"><span>등록 종료일</span><input type="date" name="end" value="${H(W.feedbackStats.validDate(scope.end) ? scope.end : "")}" required></label><label class="field"><span>분석 용도</span><select name="purpose">${[["production", "Production"], ["test", "Test"], ["all", "모든 용도"]].map(([id, title]) => option(id, title, scope.purpose)).join("")}</select></label><label class="field"><span>운영 버전</span><select name="version"${scope.purpose === "test" ? ' disabled aria-describedby="feedback-period-note"' : ""}>${feedbackVersionOptions(scope)}</select></label><label class="field feedback-query"><span>검색</span><input name="query" value="${H(scope.query)}" placeholder="회사, 탐지명, 의견 또는 작성자"></label><label class="field"><span>피드백 유형</span><select name="category">${option("", "모든 유형", scope.category)}${categories.map(([id, title]) => option(id, title, scope.category)).join("")}</select></label><div class="feedback-filter-actions"><button type="submit" class="button button-primary">조회</button>${button("초기화", "feedback-clear", "ghost")}</div><div class="feedback-filter-bottom"><p class="small muted" id="feedback-period-note">피드백 등록일 기준, 한국 시간(KST). 시작일과 종료일을 모두 포함합니다.${scope.purpose === "test" ? " 운영 버전은 Test에 적용하지 않습니다." : ""}</p><div class="row"><span class="small muted">최근</span><button type="button" class="button button-ghost" data-action="feedback-range" data-days="7">7일</button><button type="button" class="button button-ghost" data-action="feedback-range" data-days="30">30일</button></div></div><p class="form-error" id="feedback-filter-error" role="alert">${H(feedbackDateError(scope))}</p></form></section>`;
  }

  function feedbackCharts(rows, summary) {
    const counts = W.feedbackStats.categoryCounts(rows), versions = W.feedbackStats.versionCounts(rows);
    const countFor = category => counts.find(row => row.category === category)?.count || 0;
    return `<div class="feedback-charts"><section class="panel"><header class="panel-header"><div><h2>유형별 피드백</h2><p class="small muted">유형을 누르면 해당 피드백만 확인합니다.</p></div>${W.badge("복수 선택", "violet")}</header><div class="panel-body"><div id="feedback-category-chart" class="feedback-category-chart">${categories.map(([id, title], i) => { const count = countFor(id), pct = rows.length ? count / rows.length * 100 : 0; return `<button type="button" class="feedback-category-bar${q("category") === id ? " active" : ""}" data-action="feedback-category" data-category="${id}" aria-pressed="${q("category") === id}" aria-label="${title} ${count}건 필터"><span class="feedback-category-name"><i class="feedback-category-dot tone-${i}"></i>${title}</span><span class="feedback-category-track" aria-hidden="true"><span class="tone-${i}" style="width:${pct}%"></span></span><span class="feedback-category-value"><strong>${count}건</strong><small>${rows.length ? `${pct.toFixed(1)}%` : "—"}</small></span></button>`; }).join("")}</div><p class="feedback-count-note small muted">비율의 분모는 조회된 피드백 ${rows.length}건입니다. 여러 유형을 선택할 수 있어 합계는 100%를 넘을 수 있습니다.${summary.commentOnlyCount ? ` 유형 없이 의견만 등록한 피드백 ${summary.commentOnlyCount}건은 막대에 포함하지 않습니다.` : ""}</p></div></section><section class="panel feedback-version-panel"><header class="panel-header"><div><h2>운영 버전별</h2><p class="small muted">분석 당시 고정된 운영 구성 기준</p></div>${W.icon("layers", 17)}</header><div class="panel-body"><div id="feedback-version-summary" class="feedback-version-summary">${versions.length ? versions.map(row => `<button type="button" class="feedback-version-item${q("version") === row.id ? " active" : ""}" data-action="feedback-version" data-version="${H(row.id)}" aria-pressed="${q("version") === row.id}"><span><strong>${H(versionLabel(row.id))}</strong><small>대상 분석 ${row.analysisCount}건</small></span><span class="feedback-version-count">${row.count}<small>건</small>${W.icon("chevronRight", 13)}</span></button>`).join("") : `<div class="feedback-versions-empty small muted">조회 조건에 해당하는 운영 피드백이 없습니다.</div>`}</div><p class="feedback-count-note small muted">Test는 운영 버전 집계에서 제외합니다. 피드백 건수만으로 버전의 정확도나 오류율을 판단하지 않습니다.</p></div></section></div>`;
  }

  function feedbackApply(form) {
    const fd = new FormData(form), purpose = String(fd.get("purpose") || "production");
    const scope = { start: String(fd.get("start") || ""), end: String(fd.get("end") || "") };
    const error = feedbackDateError(scope);
    if (error) { document.getElementById("feedback-filter-error").textContent = error; form.elements.start.focus(); return; }
    const version = purpose === "test" ? null : fd.get("version");
    W.go("feedback", { ...scope, purpose: version ? "production" : purpose, version, category: fd.get("category"), q: String(fd.get("query") || "").trim(), analysis: q("analysis") || null });
  }
  W.pages.feedback = {
    render() {
      const scope = feedbackScope(), rows = feedbackRows(scope), summary = W.feedbackStats.summarize(rows);
      const missingDates = W.feedbackStats.filter(feedbackData(), { ...scope, start: "", end: "" }).filter(row => !row.date).length;
      return `${W.heading("분석 피드백", "기간과 운영 버전으로 개선할 점을 확인합니다. 참고 판정과 공식 평가 정답에는 반영하지 않습니다.") }<div class="feedback-workspace stack">${feedbackFilters(scope)}${q("analysis") ? `<div class="feedback-analysis-scope"><span>선택한 분석의 피드백만 표시 중입니다.</span>${button("전체 분석으로 확대", "feedback-all-analyses", "ghost", "arrowUpRight")}</div>` : ""}${W.metrics([{ label: "피드백", value: `${summary.feedbackCount}건`, note: "조회 조건에 맞는 등록 이력", tone: "violet", icon: "message" }, { label: "피드백이 달린 분석", value: `${summary.analysisCount}건`, note: "같은 분석의 여러 의견은 한 번 집계", tone: "blue" }, { label: "복수 유형 피드백", value: `${summary.multiCategoryCount}건`, note: "두 가지 이상 유형을 선택한 이력", tone: "amber" }])}${missingDates ? `<p class="small muted feedback-missing-date">등록일 미기록 ${missingDates}건은 기간을 확인할 수 없어 제외했습니다.</p>` : ""}${feedbackCharts(rows, summary)}<section class="panel"><header class="panel-header"><div><h2>피드백 이력</h2><p class="small muted">위 집계와 같은 조회 조건의 전체 이력입니다. 등록 이력은 덮어쓰지 않습니다.</p></div>${W.badge(`${rows.length}건`, "neutral")}</header><div class="table-inset"><div id="feedback-table" aria-label="분석 피드백 이력"></div></div></section><p class="small muted feedback-access-note">${W.icon("shield", 14)} 관리자 조회 화면의 시안입니다. 실제 연결 시 조회 권한과 열람 감사 기록을 적용합니다.</p></div>`;
    },
    mount() {
      const form = document.getElementById("feedback-filters");
      form.addEventListener("submit", event => { event.preventDefault(); feedbackApply(form); });
      form.elements.purpose.addEventListener("change", () => { const testOnly = form.elements.purpose.value === "test"; form.elements.version.disabled = testOnly; if (testOnly) form.elements.version.value = ""; document.getElementById("feedback-period-note").textContent = `피드백 등록일 기준, 한국 시간(KST). 시작일과 종료일을 모두 포함합니다.${testOnly ? " 운영 버전은 Test에 적용하지 않습니다." : ""}`; });
      form.elements.version.addEventListener("change", () => { if (form.elements.version.value) form.elements.purpose.value = "production"; });
      W.table("feedback-table", feedbackRows(), [{ title: "등록 시각 (KST)", field: "created_at", minWidth: 150, width: 160, formatter: cell => { const row = cell.getRow().getData(); return H(row.date ? new Intl.DateTimeFormat("sv-SE", { timeZone: "Asia/Seoul", dateStyle: "short", timeStyle: "short" }).format(new Date(row.created_at)) : "미기록"); } }, { title: "운영 버전", field: "production_version_id", minWidth: 136, width: 145, formatter: cell => { const row = cell.getRow().getData(); return row.purpose === "test" ? W.badge("Test / 적용 안 함", "neutral") : W.badge(versionLabel(cell.getValue()), cell.getValue() ? "violet" : "neutral"); } }, { title: "분석 대상", field: "signature", minWidth: 190, widthGrow: 2, formatter: cell => `<div class="table-two-lines"><strong>${H(cell.getValue())}</strong><span class="small muted">${H(cell.getRow().getData().company)}</span></div>` }, { title: "피드백 유형", field: "categories", minWidth: 230, widthGrow: 2, formatter: cell => `<div class="feedback-categories">${(cell.getValue() || []).map(value => W.badge(categoryName(value), "violet")).join("") || `<span class="muted">의견만 등록</span>`}</div>` }, { title: "의견", field: "comment", minWidth: 220, widthGrow: 3, formatter: cell => `<span class="table-ellipsis" title="${H(cell.getValue() || "추가 의견 없음")}">${H(cell.getValue() || "추가 의견 없음")}</span>` }, { title: "작성자", field: "author", minWidth: 100, width: 110 }, { title: "상세", width: 76, headerSort: false, formatter: cell => `<button class="icon-button" data-action="feedback-detail" data-id="${H(cell.getRow().getData().id)}" aria-label="피드백 상세 보기">${W.icon("arrowUpRight")}</button>` }]);
    },
    action(name, el) {
      if (name === "feedback-clear") W.go("feedback");
      if (name === "feedback-all-analyses") { const scope = feedbackScope(); W.go("feedback", { start: scope.start, end: scope.end, purpose: scope.purpose, version: scope.version, category: scope.category, q: scope.query }); }
      if (name === "feedback-category" || name === "feedback-version") { const scope = feedbackScope(); W.go("feedback", { start: scope.start, end: scope.end, purpose: name === "feedback-version" ? "production" : scope.purpose, version: name === "feedback-version" ? (scope.version === el.dataset.version ? null : el.dataset.version) : scope.version, category: name === "feedback-category" ? (scope.category === el.dataset.category ? null : el.dataset.category) : scope.category, q: scope.query, analysis: scope.analysis }); }
      if (name === "feedback-range") { const form = document.getElementById("feedback-filters"), end = feedbackDefaults().end, start = new Date(`${end}T00:00:00Z`); start.setUTCDate(start.getUTCDate() - Number(el.dataset.days) + 1); form.elements.start.value = start.toISOString().slice(0, 10); form.elements.end.value = end; feedbackApply(form); }
      if (name === "feedback-detail") { const row = feedbackData().find(item => String(item.id) === el.dataset.id); if (!row) return; W.dialog({ title: "피드백 상세", body: `${feedbackBody([row])}<dl class="context-grid"><div><dt>회사</dt><dd>${H(row.company)}</dd></div><div><dt>탐지명</dt><dd>${H(row.signature)}</dd></div><div><dt>운영 버전</dt><dd>${H(row.purpose === "test" ? "Test / 적용 안 함" : versionLabel(row.production_version_id))}</dd></div></dl>`, footer: `<button class="button button-secondary" data-close-dialog>닫기</button><button class="button button-primary" data-action="feedback-open-analysis" data-id="${H(row.analysis_id || row.analysisId)}">원 분석 보기 ${W.icon("arrowUpRight")}</button>` }); }
      if (name === "feedback-open-analysis") { closeDialog(); W.go("analysis", { id: el.dataset.id, tab: "result", from: "feedback" }); }
    }
  };
})();
