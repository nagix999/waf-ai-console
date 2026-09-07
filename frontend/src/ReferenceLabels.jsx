import { useEffect, useState } from "react";
import { api } from "./api.js";
import { Icon } from "./Icon.jsx";
import { formatDate } from "./analysisView.js";
import { compactEvaluation, evaluationExplanation, evaluationOutcomeText, evaluationTone, isMockAnalysis, labelAttachmentError, labelPreviewForm, labelSources, ratioText, referenceSourceText, referenceVerdicts, referenceVisibilityText, validateLabelAttachment } from "./labelEvaluation.js";
import "./referenceLabels.css";
import { AdditionalMetrics, ConfusionMatrix, MetricCards, MetricHelp } from "./EvaluationMetrics.jsx";
import { metricText } from "./evaluationMetrics.js";

export function EvaluationBadge({ evaluation }) {
  return <span className={`evaluation-badge evaluation-${evaluationTone(evaluation?.outcome)}`}>{evaluationOutcomeText(evaluation)}</span>;
}

export function ReferenceLabel({ reference }) {
  if (!reference) return <span className="muted">미라벨</span>;
  return <div className="reference-label"><strong>{referenceVerdicts[reference.verdict] || "미기록"}</strong><small>{referenceSourceText(reference)}</small></div>;
}

export function CompactReferenceComparison({ evaluation }) {
  const comparison = compactEvaluation(evaluation);
  if (comparison.unlabeled) return <span className="compact-reference-empty" aria-label="참고 답안 없음">—</span>;
  return <div className="compact-reference-comparison">
    <div className="compact-reference-line"><span className="compact-reference-value">기준 <strong>{comparison.referenceText}</strong></span><span className={`evaluation-badge evaluation-${comparison.tone}`} title={comparison.explanation}>{comparison.text}</span></div>
    <small className="compact-reference-source">{comparison.sourceText}{comparison.direction && <> · {comparison.direction}</>}</small>
    {comparison.excluded && <details className="compact-reference-reason"><summary>제외 사유</summary><p>{comparison.explanation}</p></details>}
  </div>;
}

function LabelHistoryRows({ history, historyError }) {
  if (historyError) return <p className="error" role="alert">연결 이력을 조회하지 못했습니다. 상단 새로고침으로 다시 확인하세요. 이미 표시된 답안 비교는 마지막 조회 결과입니다.</p>;
  if (history == null) return <p className="muted" role="status">연결 이력을 불러오는 중…</p>;
  if (!Array.isArray(history)) return <p className="muted">연결 이력 정보를 확인할 수 없습니다. 새로고침으로 다시 확인하세요.</p>;
  if (!history.length) return <p className="muted">연결된 Label 이력이 없습니다.</p>;
  return <div className="table-wrap"><table><thead><tr><th>Revision</th><th>참고 판정</th><th>출처 / 버전</th><th>AI 결과 열람</th><th>연결 시각 / 등록자</th></tr></thead><tbody>{history.map((item) => <tr key={item.id}><td>{item.revision}</td><td>{referenceVerdicts[item.verdict] || "미기록"}</td><td className="table-meta">{referenceSourceText(item)}<small>{item.source_ref}</small></td><td>{referenceVisibilityText(item.ai_visible)}</td><td className="table-meta">{formatDate(item.created_at)}<small>{item.created_by}</small></td></tr>)}</tbody></table></div>;
}

export function CompactEvaluationDetail({ detail, history, historyError }) {
  const evaluation = detail?.evaluation;
  const reference = evaluation?.reference_label;
  const comparison = compactEvaluation(evaluation);
  if (comparison.unlabeled && !historyError) return null;
  return <section className="panel compact-evaluation-detail" aria-label="참고 답안 비교">
    <div className="compact-evaluation-heading">
      <span className="compact-evaluation-title">참고 답안</span>
      {!comparison.unlabeled && <><div className="compact-reference-line"><strong>{comparison.referenceText}</strong><span className={`evaluation-badge evaluation-${comparison.tone}`}>{comparison.text}</span></div><small>{comparison.sourceText}{comparison.direction && <> · {comparison.direction}</>}</small></>}
    </div>
    {historyError && <p className="compact-evaluation-history-error" role="alert">연결 이력 조회 실패 · 상단 새로고침으로 다시 확인하세요.</p>}
    <details className="compact-evaluation-provenance">
      <summary>출처·연결 이력{comparison.excluded ? " · 제외 사유" : ""}</summary>
      <p className="evaluation-footnote">{comparison.explanation}</p>
      {reference && <dl className="label-metadata"><dt>답안 출처 / 버전</dt><dd>{reference.source_ref}</dd><dt>AI 결과 열람 여부</dt><dd>{referenceVisibilityText(reference.ai_visible)}</dd><dt>현재 연결 이력</dt><dd>revision {reference.revision} · {formatDate(reference.created_at)}</dd></dl>}
      <p className="evaluation-footnote">합성 기대값·지원 판정은 검증된 운영 정답이나 독립적인 정확도가 아닙니다. 답안은 분석 입력에 추가하지 않으며 변경 전 연결 이력과 AI 최종 판정을 보존합니다.</p>
      <LabelHistoryRows history={history} historyError={historyError} />
    </details>
  </section>;
}

export function EvaluationSummary({ summary, loading, error, scopeLabel, onMatrixCell, selectedCell }) {
  if (loading && !summary) return <div className="panel evaluation-summary loading" role="status">Label 평가 요약을 불러오는 중…</div>;
  if (error || !summary) return <div className="panel evaluation-summary"><p className="muted">{error ? "조회 실패로 Label 평가 요약을 표시하지 않습니다." : "Label 평가 요약이 없습니다."}</p></div>;
  const counts = summary.outcomes || {};
  return <section className="panel evaluation-summary" aria-label="검색 범위 Label 평가 요약">
    <div className="panel-head"><div><h2><Icon name="check" size={17} />판정 평가 지표</h2><small>{scopeLabel || "적용된 모든 검색 조건"}의 전체 {summary.total.toLocaleString()}건 · 현재 페이지 한정 아님</small></div></div>
    {summary.metrics && <><p className="quality-basis">확정 판정 기준 · 평가 가능한 이진 답안 {summary.binary_evaluable ?? 0}건 중 확정 {summary.binary_decided ?? 0}건. 정탐 표본 {summary.support_positive ?? 0}건 / 오탐 표본 {summary.support_negative ?? 0}건(보류 포함). 참고 답안 연결률 {metricText(summary.label_coverage)}<MetricHelp metric="label_coverage" label="참고 답안 연결률" />.</p><p className="evaluation-footnote">연결된 답안 표본의 비교 결과입니다. Production 전체 품질로 일반화하지 마세요. 서로 다른 답안 출처가 섞인 경우 아래 출처별 지표를 확인하세요.</p><MetricCards metrics={summary.metrics} /><ConfusionMatrix matrix={summary.confusion_matrix} onCell={onMatrixCell} selectedCell={selectedCell} /><AdditionalMetrics metrics={summary.metrics} /></>}
    <div className="evaluation-counts">
      {[["Label 연결", summary.labeled], ["비교 가능", summary.evaluable], ["기준 일치", summary.matches], ["기준 불일치", (counts.false_negative || 0) + (counts.false_positive || 0) + (counts.expected_abstention_mismatch || 0)], ["모델 보류", counts.abstained || 0], ["미라벨", counts.unlabeled || 0]].map(([name, value]) => <div key={name}><span>{name}</span><strong>{value.toLocaleString()}<small>건</small></strong></div>)}
    </div>
    <p className="evaluation-footnote">Label 연결 항목 중 진행 중 {counts.pending || 0} · 실행 실패 {counts.failed || 0} · 모의 실행 {counts.stub || 0} · 실행 출처 미확인 {counts.unknown_provenance || 0} · 정답 포함 입력 {counts.input_contaminated || 0}건은 일치율 분모에서 제외합니다. 미라벨도 제외하며, 모델 보류는 미탐·과탐과 구분합니다.</p>
    {!!summary.source_groups?.length && <details className="quality-source-details"><summary>답안 출처·AI 열람 여부별 지표 · {summary.source_groups.length}개 집단</summary><div className="evaluation-groups">{summary.source_groups.map((group) => <article key={`${group.source_kind}-${group.ai_visible}`}>
      <div className="evaluation-group-title"><strong>{referenceSourceText(group)}</strong><span>{referenceVisibilityText(group.ai_visible)}</span></div>
      <dl><dt>{group.source_kind === "synthetic_expected" ? "기대 판정 일치율" : "기준 판정 일치율"}<MetricHelp metric="agreement" label="기준 판정 일치율" /></dt><dd>{ratioText(group.matches, group.evaluable)}</dd><dt>확정 판정 coverage<MetricHelp metric="coverage" label="확정 판정 coverage" /></dt><dd>{ratioText(group.binary_decided, group.binary_evaluable)}</dd></dl>
      {group.metrics && <><p className="evaluation-footnote">이진 답안 {group.binary_evaluable}건 · 확정 판정 {group.binary_decided}건 · 참고 정탐 {group.support_positive} / 오탐 {group.support_negative}건</p><MetricCards metrics={group.metrics} compact /><ConfusionMatrix matrix={group.confusion_matrix} /><AdditionalMetrics metrics={group.metrics} /></>}
      <p>미탐 방향 {group.false_negatives} · 과탐 방향 {group.false_positives} · 모델 보류 {group.abstained}건</p>
      {group.source_kind === "synthetic_expected" && <p>기대 보류 일치 {group.expected_abstention_matches} · 불일치 {group.expected_abstention_mismatches}건</p>}
    </article>)}</div></details>}
    <p className="evaluation-footnote">기대 답안이 보류인 문항: 기대 보류 일치 {counts.expected_abstention_match || 0}건 · 불일치 {counts.expected_abstention_mismatch || 0}건. 이 문항은 이진 Confusion Matrix와 Accuracy·Precision·Recall·F1에 포함하지 않습니다.</p>
    <p className="evaluation-footnote">일치율 = 기준과 같은 최종 판정 / 비교 가능한 실제 완료 분석. Coverage = 정탐·오탐으로 확정한 분석 / Label이 정탐·오탐인 비교 가능 분석. 합성 기대값·지원 판정은 검증된 정답이나 운영 정확도가 아닙니다.</p>
  </section>;
}

export function LabelAttachment({ onAttached, controlledOpen, onOpenChange }) {
  const [file, setFile] = useState(null);
  const [form, setForm] = useState({ source_system: "", source_kind: "synthetic_expected", source_ref: "", ai_visible: "" });
  const [preview, setPreview] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [approved, setApproved] = useState(false);
  const [expired, setExpired] = useState(false);
  useEffect(() => {
    setExpired(false);
    if (!preview?.expires_at) return undefined;
    const remaining = new Date(preview.expires_at).getTime() - Date.now();
    if (!Number.isFinite(remaining) || remaining <= 0) { setExpired(true); return undefined; }
    const timer = setTimeout(() => { setExpired(true); setApproved(false); }, remaining);
    return () => clearTimeout(timer);
  }, [preview]);
  function invalidate() { setPreview(null); setApproved(false); setError(""); setMessage(""); }
  const update = (event) => { invalidate(); setForm({ ...form, [event.target.name]: event.target.value }); };
  async function inspect(event) {
    event.preventDefault();
    invalidate();
    const validation = validateLabelAttachment(file, form);
    if (validation) { setError(validation); return; }
    setBusy(true);
    try { setPreview(await api.previewEvaluationLabels(labelPreviewForm(file, form))); }
    catch (err) { setError(labelAttachmentError(err.message)); }
    finally { setBusy(false); }
  }
  async function confirm() {
    if (!preview?.can_confirm || !preview.preview_token || !approved || busy) return;
    if (expired || new Date(preview.expires_at).getTime() <= Date.now()) { setExpired(true); setError("미리보기가 만료되었습니다. 다시 미리보기하세요."); return; }
    setBusy(true); setError("");
    try {
      const result = await api.confirmEvaluationLabels(preview.preview_token);
      setMessage(`Label 연결 완료 · 새 이력 ${result.applied_count}건 · 변경 없음 ${result.unchanged_count}건${result.duplicate ? " · 동일 확인 요청 재전송" : ""}. 모델을 호출하거나 분석을 다시 실행하지 않았습니다.`);
      setPreview(null); setApproved(false); onAttached();
    } catch (err) { setError(`${labelAttachmentError(err.message)} · 미리보기를 다시 확인하세요.`); setPreview(null); setApproved(false); }
    finally { setBusy(false); }
  }
  const controlled = typeof controlledOpen === "boolean";
  return <details className="panel label-attachment" open={controlled ? controlledOpen : undefined} hidden={controlled && !controlledOpen}>
    <summary onClick={controlled ? (event) => { event.preventDefault(); onOpenChange?.(!controlledOpen); } : undefined}><span><Icon name="upload" size={18} />참고 답안 연결</span><small>기존 분석에 Label만 연결 · LLM 호출 없음</small></summary>
    <div className="label-attachment-body">
      <p className="muted">분석 로그가 아닌 답안 JSON 배열을 선택하세요. 최대 2 MiB / 500행이며 각 행의 <code>event_id</code>와 <code>expected_verdict</code>를 사용합니다. 난이도·해설·원문 발췌는 모델 입력이나 평가 점수에 사용하지 않습니다.</p>
      <form onSubmit={inspect}>
        <fieldset disabled={busy}>
          <div className="label-attachment-fields">
            <label>참고 답안 JSON<input type="file" accept=".json,application/json" onChange={(event) => { invalidate(); setFile(event.target.files?.[0] || null); }} /></label>
            <label>Source System (정확히)<input name="source_system" value={form.source_system} onChange={update} maxLength={120} placeholder="분석 상세 또는 테스트 실행의 Source System" required /></label>
            <label>Label 출처<select name="source_kind" value={form.source_kind} onChange={update}>{Object.entries(labelSources).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label>
            <label>답안 출처 / 버전<input name="source_ref" value={form.source_ref} onChange={update} maxLength={120} placeholder="예: waf-dummy-v1" required /></label>
            <label>답안 작성 시 AI 결과 열람<select name="ai_visible" value={form.ai_visible} onChange={update} required><option value="" disabled>명시적으로 선택하세요</option><option value="unknown">알 수 없음</option><option value="false">보지 않았음 (입력자 신고)</option><option value="true">본 뒤 작성함 (지원 판정)</option></select></label>
          </div>
          <p className="evaluation-footnote">새 테스트는 실행별 Source System을 사용합니다. 테스트 실행 요약이나 분석 상세의 값을 확인하세요. 이전 웹 테스트만 <code>admin-ui</code>일 수 있습니다. 목록 검색 조건과 무관하게 위의 정확한 Source System + Event ID로 연결합니다. 사후 답안 연결은 분석 목록·상세의 최신 비교에 반영되며 테스트 실행의 접수 당시 고정 답안을 바꾸지 않습니다.</p>
          <p className="evaluation-footnote">합성 답안은 검증된 운영 정답이 아닙니다. 참조 Label에는 정탐/오탐만 허용하며, 기대 보류가 포함된 샘플은 ‘합성 기대값’으로 연결하세요.</p>
          <button className="secondary" type="submit" disabled={busy || !file}>{busy ? "처리 중…" : "연결 미리보기"}</button>
        </fieldset>
      </form>
      {error && <div className="error" role="alert">{error}</div>}
      {message && <div className="notice" role="status">{message}</div>}
      {preview && <section className="label-preview" aria-label="답안 연결 미리보기">
        <h3>연결 미리보기</h3>
        <p><strong>{preview.source_system}</strong> · {referenceSourceText(preview)} · {preview.source_ref} · {referenceVisibilityText(preview.ai_visible)}</p>
        <div className="notice">전체 {preview.total_rows}행 · 발견 {preview.matched_count}건 · 변경 없음 {preview.unchanged_count}건 · 새 이력 {preview.change_count}건 · 오류 {preview.errors?.length || 0}건</div>
        <div className="table-wrap"><table><thead><tr><th>행</th><th>Event ID / Analysis ID</th><th>현재 Label / revision</th><th>연결할 Label</th><th>처리</th></tr></thead><tbody>{preview.rows.map((row) => <tr key={row.row_number}><td>{row.row_number}</td><td className="table-meta">{row.event_id}<small>{row.analysis_id || "연결 대상 없음"}</small></td><td>{referenceVerdicts[row.current_label] || "미라벨"}<small className="label-revision">revision {row.current_revision ?? 0}</small></td><td>{referenceVerdicts[row.proposed_label] || row.proposed_label}</td><td>{row.change ? "새 이력 추가" : "변경 없음"}</td></tr>)}</tbody></table></div>
        {!!preview.errors?.length && <div className="upload-errors"><h3>연결할 수 없는 행</h3><div className="table-wrap"><table><thead><tr><th>행</th><th>오류 필드 / 코드</th></tr></thead><tbody>{preview.errors.map((item, index) => <tr key={index}><td>{item.row_number}</td><td>{item.field ? `${item.field}: ` : ""}<code>{labelAttachmentError(item.code)}</code></td></tr>)}</tbody></table></div><p className="error">오류를 수정한 뒤 다시 미리보기하세요. 일부 행만 자동 연결하지 않습니다.</p></div>}
        {preview.can_confirm && <><p className="evaluation-footnote">미리보기 만료: {formatDate(preview.expires_at)} · 확인 전 기존 Label이 바뀌면 다시 검토해야 합니다.</p>{expired && <p className="error" role="alert">미리보기가 만료되었습니다. 다시 미리보기하세요.</p>}<label className="checkbox-row label-confirm"><input type="checkbox" checked={approved} disabled={busy || expired} onChange={(event) => setApproved(event.target.checked)} /><span>연결 범위와 새 이력 {preview.change_count}건을 확인했습니다. 기존 Label 이력은 보존하며 AI 판정은 변경하지 않습니다.</span></label><button className="primary" type="button" disabled={!approved || busy || expired} onClick={confirm}>{busy ? "연결 중…" : "Label 연결 확정"}</button></>}
      </section>}
    </div>
  </details>;
}

export function EvaluationDetail({ detail, history, historyError }) {
  const evaluation = detail.evaluation;
  const reference = evaluation?.reference_label;
  return <section className="panel evaluation-detail" aria-label="참조 Label 평가">
    <div className="panel-head"><h2>참조 Label 평가</h2><EvaluationBadge evaluation={evaluation} /></div>
    <div className="evaluation-detail-body">
      <div className="label-comparison"><div><span>참조 Label</span><ReferenceLabel reference={reference} /></div><span className="comparison-arrow" aria-hidden="true">↔</span><div><span>{isMockAnalysis(detail) ? "모의 판정 (LLM 아님)" : "AI 최종 판정"}</span><strong>{detail.status === "completed" ? referenceVerdicts[detail.verdict] || "미기록" : "아직 확정되지 않음"}</strong></div></div>
      <p>{evaluationExplanation(evaluation)}</p>
      {reference && <dl className="label-metadata"><dt>답안 출처 / 버전</dt><dd>{reference.source_ref}</dd><dt>AI 결과 열람 여부</dt><dd>{referenceVisibilityText(reference.ai_visible)}</dd><dt>연결 이력</dt><dd>revision {reference.revision} · {formatDate(reference.created_at)}</dd></dl>}
      <details className="label-history"><summary>Label 연결 이력 · 추가형 저장</summary>{historyError ? <div className="error" role="alert">{historyError}</div> : history === null ? <p className="muted">이력을 불러오는 중…</p> : !history.length ? <p className="muted">연결된 Label 이력이 없습니다.</p> : <div className="table-wrap"><table><thead><tr><th>Revision</th><th>Label</th><th>출처 / 버전</th><th>AI 결과 열람</th><th>연결 시각 / 등록자</th></tr></thead><tbody>{history.map((item) => <tr key={item.id}><td>{item.revision}</td><td>{referenceVerdicts[item.verdict]}</td><td className="table-meta">{referenceSourceText(item)}<small>{item.source_ref}</small></td><td>{referenceVisibilityText(item.ai_visible)}</td><td className="table-meta">{formatDate(item.created_at)}<small>{item.created_by}</small></td></tr>)}</tbody></table></div>}</details>
    </div>
  </section>;
}
