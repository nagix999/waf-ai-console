import { useEffect, useId, useRef, useState } from "react";
import { api } from "./api.js";
import { Icon } from "./Icon.jsx";
import Dialog from "./Dialog.jsx";
import HelpTooltip from "./HelpTooltip.jsx";
import { formatDate } from "./analysisView.js";
import { compactEvaluation, evaluationOutcomeText, evaluationTone, isMockAnalysis, labelAttachmentError, labelPreviewForm, labelSources, ratioText, referenceSourceText, referenceVerdicts, referenceVisibilityText, validateLabelAttachment } from "./labelEvaluation.js";
import "./referenceLabels.css";
import { AdditionalMetrics, ConfusionMatrix, MetricCards, MetricHelp } from "./EvaluationMetrics.jsx";
import { metricText } from "./evaluationMetrics.js";

export function EvaluationBadge({ evaluation }) {
  return <span className={`evaluation-badge evaluation-${evaluationTone(evaluation?.outcome)}`}>{evaluationOutcomeText(evaluation)}</span>;
}

export function ReferenceLabel({ reference }) {
  if (!reference) return <span className="muted">답안 없음</span>;
  return <div className="reference-label"><strong>{referenceVerdicts[reference.verdict] || "미기록"}</strong><small>{referenceSourceText(reference)}</small></div>;
}

function ReferenceComparisonHelp({ evaluation, comparison = compactEvaluation(evaluation) }) {
  if (comparison.unlabeled || ["match", "false_negative", "false_positive", "pending"].includes(evaluation?.outcome)) return null;
  return <HelpTooltip label={comparison.excluded ? "평가 제외 사유" : "답안 비교"}>{comparison.explanation}</HelpTooltip>;
}

export function CompactReferenceComparison({ evaluation }) {
  const comparison = compactEvaluation(evaluation);
  if (comparison.unlabeled) return <span className="compact-reference-empty" aria-label="참고 답안 없음">—</span>;
  return <div className="compact-reference-comparison">
    <div className="compact-reference-line"><span className="compact-reference-value">기준 <strong>{comparison.referenceText}</strong></span><span className={`evaluation-badge evaluation-${comparison.tone}`}>{comparison.text}</span><ReferenceComparisonHelp evaluation={evaluation} comparison={comparison} /></div>
    <small className="compact-reference-source">{comparison.sourceText}{comparison.direction && <> · {comparison.direction}</>}</small>
  </div>;
}

export function LabelHistoryRows({ history, historyError }) {
  const [selected, setSelected] = useState(null);
  if (historyError) return <p className="error" role="alert">연결 이력을 조회하지 못했습니다. 상단 새로고침으로 다시 확인하세요. 이미 표시된 답안 비교는 마지막 조회 결과입니다.</p>;
  if (history == null) return <p className="muted" role="status">연결 이력을 불러오는 중…</p>;
  if (!Array.isArray(history)) return <p className="muted">연결 이력 정보를 확인할 수 없습니다. 새로고침으로 다시 확인하세요.</p>;
  if (!history.length) return <p className="muted">연결된 답안 이력이 없습니다.</p>;
  return <><div className="table-wrap reference-history-table"><table><thead><tr><th>버전</th><th>참고 판정</th><th>답안 구분</th><th>연결 시각</th><th>상세</th></tr></thead><tbody>{history.map((item) => <tr key={item.id}><td>{item.revision}</td><td>{referenceVerdicts[item.verdict] || "미기록"}</td><td className="table-meta">{referenceSourceText(item)}<small>{referenceVisibilityText(item.ai_visible)}</small></td><td>{formatDate(item.created_at)}</td><td><button type="button" className="secondary" aria-label={`답안 버전 ${item.revision} 상세`} onClick={() => setSelected(item)}>보기</button></td></tr>)}</tbody></table></div><Dialog open={!!selected} title="답안 연결 상세" onClose={() => setSelected(null)}>{selected && <dl className="label-metadata"><dt>답안 버전</dt><dd>{selected.revision}</dd><dt>출처 / 버전 이름</dt><dd>{selected.source_ref}</dd><dt>AI 결과 열람</dt><dd>{referenceVisibilityText(selected.ai_visible)}</dd><dt>연결 시각</dt><dd>{formatDate(selected.created_at)}</dd><dt>등록자</dt><dd>{selected.created_by || "미기록"}</dd><dt>연결 ID</dt><dd>{selected.id}</dd></dl>}</Dialog></>;
}

export function CompactEvaluationDetail({ detail, history, historyError }) {
  const [historyOpen, setHistoryOpen] = useState(false);
  const evaluation = detail?.evaluation;
  const reference = evaluation?.reference_label;
  const comparison = compactEvaluation(evaluation);
  if (comparison.unlabeled && !historyError) return null;
  return <section className="panel compact-evaluation-detail" aria-label="참고 답안 비교">
    <div className="compact-evaluation-heading">
      <span className="compact-evaluation-title">참고 답안</span>
      {!comparison.unlabeled && <><div className="compact-reference-line"><strong>{comparison.referenceText}</strong><span className={`evaluation-badge evaluation-${comparison.tone}`}>{comparison.text}</span></div><small>{comparison.sourceText}{comparison.direction && <> · {comparison.direction}</>}</small></>}
      <ReferenceComparisonHelp evaluation={evaluation} comparison={comparison} />
      <button type="button" className="text-button" onClick={() => setHistoryOpen(true)}>연결 이력</button>
    </div>
    {historyError && <p className="compact-evaluation-history-error" role="alert">연결 이력 조회 실패 · 상단 새로고침으로 다시 확인하세요.</p>}
    <Dialog open={historyOpen} title="참고 답안 이력" onClose={() => setHistoryOpen(false)}>
      {reference && <dl className="label-metadata"><dt>현재 출처 / 버전</dt><dd>{reference.source_ref}</dd><dt>AI 결과 열람</dt><dd>{referenceVisibilityText(reference.ai_visible)}</dd><dt>현재 답안 버전</dt><dd>{reference.revision} · {formatDate(reference.created_at)}</dd></dl>}
      <p className="reference-inline-note">이전 답안과 AI 판정은 보존됩니다. 답안 변경은 모델 입력에 반영하지 않습니다.</p>
      <LabelHistoryRows history={history} historyError={historyError} />
    </Dialog>
  </section>;
}

export function EvaluationSummary({ summary, loading, error, scopeLabel, onMatrixCell, selectedCell }) {
  const [sourcesOpen, setSourcesOpen] = useState(false);
  const [excludedOpen, setExcludedOpen] = useState(false);
  if (loading && !summary) return <div className="panel evaluation-summary loading" role="status">평가 요약을 불러오는 중…</div>;
  if (error || !summary) return <div className="panel evaluation-summary"><p className="muted">{error ? "조회 실패로 평가 요약을 표시하지 않습니다." : "평가 요약이 없습니다."}</p></div>;
  const counts = summary.outcomes || {};
  return <section className="panel evaluation-summary" aria-label="검색 범위 평가 요약">
    <div className="panel-head"><div><h2><Icon name="check" size={17} />판정 평가 지표</h2><small>{scopeLabel || "적용된 모든 검색 조건"}의 전체 {summary.total.toLocaleString()}건 · 현재 페이지 한정 아님</small></div></div>
    {summary.metrics && <><p className="quality-basis">확정 판정 기준 · 평가 가능한 이진 답안 {summary.binary_evaluable ?? 0}건 중 확정 {summary.binary_decided ?? 0}건. 정탐 표본 {summary.support_positive ?? 0}건 / 오탐 표본 {summary.support_negative ?? 0}건(보류 포함). 참고 답안 연결률 {metricText(summary.label_coverage)}<MetricHelp metric="label_coverage" label="참고 답안 연결률" />.</p><MetricCards metrics={summary.metrics} /><ConfusionMatrix matrix={summary.confusion_matrix} onCell={onMatrixCell} selectedCell={selectedCell} /><AdditionalMetrics metrics={summary.metrics} /></>}
    <div className="evaluation-counts">
      {[["답안 연결", summary.labeled], ["비교 가능", summary.evaluable], ["기준 일치", summary.matches], ["기준 불일치", (counts.false_negative || 0) + (counts.false_positive || 0) + (counts.expected_abstention_mismatch || 0)], ["모델 보류", counts.abstained || 0], ["답안 없음", counts.unlabeled || 0]].map(([name, value]) => <div key={name}><span>{name}</span><strong>{value.toLocaleString()}<small>건</small></strong></div>)}
    </div>
    <div className="reference-summary-actions"><button type="button" className="secondary" onClick={() => setExcludedOpen(true)}>제외·보류 내역</button></div><Dialog open={excludedOpen} title="평가 제외·보류 내역" onClose={() => setExcludedOpen(false)}><p className="evaluation-footnote">답안 연결 항목 중 진행 중 {counts.pending || 0} · 실행 실패 {counts.failed || 0} · 모의 실행 {counts.stub || 0} · 실행 출처 미확인 {counts.unknown_provenance || 0} · 정답 포함 입력 {counts.input_contaminated || 0}건은 일치율 분모에서 제외합니다. 답안 없음도 제외하며, 모델 보류는 미탐·과탐과 구분합니다.</p><p>기대 보류 일치 {counts.expected_abstention_match || 0}건 · 불일치 {counts.expected_abstention_mismatch || 0}건</p><p className="reference-inline-note">참고 답안이 보류인 문항은 정탐·오탐 기준의 오차 행렬과 Accuracy·Precision·Recall·F1 계산에서 제외합니다.</p></Dialog>
    {!!summary.source_groups?.length && <><div className="reference-summary-actions"><button type="button" className="secondary" onClick={() => setSourcesOpen(true)}>출처별 지표 · {summary.source_groups.length}개</button></div><Dialog open={sourcesOpen} title="답안 출처별 지표" onClose={() => setSourcesOpen(false)}><p className="reference-inline-note">답안 출처·AI 열람 여부별 표본입니다. 서로 다른 그룹을 하나의 독립 정확도로 해석하지 마세요.</p><div className="evaluation-groups reference-source-groups">{summary.source_groups.map((group) => <article key={`${group.source_kind}-${group.ai_visible}`}>
      <div className="evaluation-group-title"><strong>{referenceSourceText(group)}</strong><span>{referenceVisibilityText(group.ai_visible)}</span></div>
      <dl><dt>{group.source_kind === "synthetic_expected" ? "기대 판정 일치율" : "기준 판정 일치율"}<MetricHelp metric="agreement" label="기준 판정 일치율" /></dt><dd>{ratioText(group.matches, group.evaluable)}</dd><dt>확정 판정 비율<MetricHelp metric="coverage" label="확정 판정 비율" /></dt><dd>{ratioText(group.binary_decided, group.binary_evaluable)}</dd></dl>
      {group.metrics && <><p className="evaluation-footnote">이진 답안 {group.binary_evaluable}건 · 확정 판정 {group.binary_decided}건 · 참고 정탐 {group.support_positive} / 오탐 {group.support_negative}건</p><MetricCards metrics={group.metrics} compact /><ConfusionMatrix matrix={group.confusion_matrix} /><AdditionalMetrics metrics={group.metrics} /></>}
      <p>미탐 방향 {group.false_negatives} · 과탐 방향 {group.false_positives} · 모델 보류 {group.abstained}건</p>
      {group.source_kind === "synthetic_expected" && <p>기대 보류 일치 {group.expected_abstention_matches} · 불일치 {group.expected_abstention_mismatches}건</p>}
    </article>)}</div></Dialog></>}
    <p className="reference-inline-note">답안이 있는 표본의 평가입니다.<HelpTooltip label="평가 기준">일치율 = 기준과 같은 최종 판정 / 비교 가능한 실제 완료 분석. 확정 판정 비율 = 정탐·오탐으로 확정한 분석 / 답안이 정탐·오탐인 비교 가능 분석. 기대 답안·지원 판정은 검증된 정답이나 운영 정확도가 아닙니다. 프로덕션 전체 품질로 일반화하지 마세요. 서로 다른 답안 출처가 섞인 경우 출처별 지표를 확인하세요.</HelpTooltip></p>
  </section>;
}

export function initialLabelAttachmentForm(sourceSystem) {
  // Suggest only an exact server-owned scope supplied by the parent. Never
  // infer a source from a test name, filename, event ID or rows on screen.
  return { source_system: typeof sourceSystem === "string" ? sourceSystem : "", source_kind: "synthetic_expected", source_ref: "", ai_visible: "" };
}

export function LabelAttachment({ onAttached, controlledOpen, onOpenChange, sourceSystem }) {
  const [localOpen, setLocalOpen] = useState(false);
  const [file, setFile] = useState(null);
  const [form, setForm] = useState(() => initialLabelAttachmentForm(sourceSystem));
  const [preview, setPreview] = useState(null);
  const [previewOpen, setPreviewOpen] = useState(false);
  const [selectedRow, setSelectedRow] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [approved, setApproved] = useState(false);
  const [expired, setExpired] = useState(false);
  const requestPending = useRef(false);
  const inputId = useId();
  const controlled = typeof controlledOpen === "boolean";
  const open = controlled ? controlledOpen : localOpen;
  const setOpen = controlled ? onOpenChange : setLocalOpen;
  useEffect(() => {
    setExpired(false);
    if (!preview?.expires_at) return undefined;
    const remaining = new Date(preview.expires_at).getTime() - Date.now();
    if (!Number.isFinite(remaining) || remaining <= 0) { setExpired(true); setApproved(false); return undefined; }
    const timer = setTimeout(() => { setExpired(true); setApproved(false); }, remaining);
    return () => clearTimeout(timer);
  }, [preview]);
  function invalidate() { setPreview(null); setPreviewOpen(false); setSelectedRow(null); setApproved(false); setError(""); setMessage(""); }
  const update = (event) => { invalidate(); setForm({ ...form, [event.target.name]: event.target.value }); };
  function close() { if (requestPending.current) return; setPreviewOpen(false); setSelectedRow(null); setOpen?.(false); }
  async function inspect(event) {
    event.preventDefault();
    if (requestPending.current) return;
    invalidate();
    const validation = validateLabelAttachment(file, form);
    if (validation) { setError(validation); return; }
    requestPending.current = true; setBusy(true);
    try { setPreview(await api.previewEvaluationLabels(labelPreviewForm(file, form))); setPreviewOpen(true); }
    catch (err) { setError(labelAttachmentError(err.message)); }
    finally { requestPending.current = false; setBusy(false); }
  }
  async function confirm() {
    if (!preview?.can_confirm || !preview.preview_token || !approved || requestPending.current) return;
    if (expired || new Date(preview.expires_at).getTime() <= Date.now()) { setExpired(true); setError("미리보기가 만료되었습니다. 다시 미리보기하세요."); return; }
    requestPending.current = true; setBusy(true); setError("");
    try {
      const result = await api.confirmEvaluationLabels(preview.preview_token);
      setMessage("답안 연결 완료 · 새 이력 " + result.applied_count + "건 · 변경 없음 " + result.unchanged_count + "건" + (result.duplicate ? " · 동일 요청 재전송" : "") + ". 분석을 다시 실행하지 않았습니다.");
      setPreview(null); setPreviewOpen(false); setApproved(false); onAttached?.();
    } catch (err) { setError(labelAttachmentError(err.message) + " · 다시 미리보기하세요."); setPreview(null); setPreviewOpen(false); setApproved(false); }
    finally { requestPending.current = false; setBusy(false); }
  }
  return <>{!controlled && <button type="button" className="secondary" onClick={() => setLocalOpen(true)}><Icon name="upload" size={16} />참고 답안 연결</button>}
    <Dialog open={open} title="참고 답안 연결" onClose={close} className="label-attachment-dialog"><div className="label-attachment label-attachment-body">
      <p className="reference-inline-note">기존 분석에 답안만 연결합니다. 모델 호출 없음.<HelpTooltip label="답안 파일 형식">분석 로그가 아닌 답안 JSON 배열을 선택하세요. 최대 2 MiB / 500행이며 각 행의 event_id와 expected_verdict를 사용합니다. 난이도·해설·원문 발췌는 모델 입력이나 평가 점수에 사용하지 않습니다.</HelpTooltip></p>
      <form onSubmit={inspect}><fieldset disabled={busy}><div className="label-attachment-fields">
        <label>참고 답안 JSON<input type="file" accept=".json,application/json" onChange={(event) => { invalidate(); setFile(event.target.files?.[0] || null); }} /></label>
        <div className="reference-form-field"><div className="reference-field-label"><label htmlFor={inputId + "-source"}>분석 출처</label><HelpTooltip label="분석 출처">분석 상세 또는 테스트 실행의 source_system과 정확히 일치해야 합니다. 이름이나 파일명으로 찾지 않습니다. 새 테스트는 실행별 출처를 사용하며 이전 웹 테스트만 admin-ui일 수 있습니다. 입력한 출처 + 파일의 이벤트 ID로 연결합니다.</HelpTooltip></div><input id={inputId + "-source"} name="source_system" value={form.source_system} onChange={update} maxLength={120} placeholder="분석의 source_system 값" required />{typeof sourceSystem === "string" && sourceSystem && sourceSystem !== form.source_system && <button type="button" className="text-button" onClick={() => { invalidate(); setForm({ ...form, source_system: sourceSystem }); }}>현재 범위의 출처 사용</button>}</div>
        <div className="reference-form-field"><div className="reference-field-label"><label htmlFor={inputId + "-kind"}>답안 구분</label><HelpTooltip label="답안 구분">기대 답안은 테스트 시나리오에서 정한 값이며 검증된 운영 정답이 아닙니다. 참고 답안에는 정탐·오탐만 허용합니다. 보류가 포함된 테스트 답안은 ‘기대 답안’으로 연결하세요.</HelpTooltip></div><select id={inputId + "-kind"} name="source_kind" value={form.source_kind} onChange={update}>{Object.entries(labelSources).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></div>
        <label>답안 출처 / 버전 이름<input name="source_ref" value={form.source_ref} onChange={update} maxLength={120} placeholder="예: 보안팀 검토_1차" required /></label>
        <div className="reference-form-field"><label htmlFor={inputId + "-visible"}>답안 작성 시 AI 결과 열람</label><select id={inputId + "-visible"} name="ai_visible" value={form.ai_visible} onChange={update} required><option value="" disabled>직접 선택하세요</option><option value="unknown">알 수 없음</option><option value="false">보지 않았음 (입력자 신고)</option><option value="true">본 뒤 작성함 (지원 판정)</option></select></div>
      </div>
      <p className="reference-scope-warning">목록 검색 조건과 무관하게 파일 전체를 연결합니다.<HelpTooltip label="답안 연결 범위">입력한 정확한 source_system + event_id로 연결합니다. 같은 출처의 현재 목록에 보이지 않는 분석도 대상이 될 수 있습니다. 사후 답안 연결은 분석 목록·상세의 최신 비교에 반영되며 테스트 실행의 접수 당시 고정 답안을 바꾸지 않습니다.</HelpTooltip></p>
      <div className="reference-summary-actions"><button className="primary" type="submit" disabled={busy || !file}>{busy ? "처리 중…" : "연결 미리보기"}</button>{preview && <button className="secondary" type="button" onClick={() => setPreviewOpen(true)}>미리보기 다시 열기</button>}</div>
      </fieldset></form>
      {error && <div className="error" role="alert">{error}</div>}{message && <div className="notice" role="status">{message}</div>}
    </div>
    <Dialog open={open && previewOpen && !!preview} title="답안 연결 미리보기" onClose={() => { if (!requestPending.current) { setSelectedRow(null); setPreviewOpen(false); } }} className="reference-preview-dialog">{preview && <section className="label-preview" aria-label="답안 연결 미리보기">
      <p>{referenceSourceText(preview)} · {preview.source_ref} · {referenceVisibilityText(preview.ai_visible)}</p>
      <div className="reference-preview-counts"><span>전체 <strong>{preview.total_rows}</strong>행</span><span>연결 대상 <strong>{preview.matched_count}</strong>건</span><span>새 이력 <strong>{preview.change_count}</strong>건</span><span>변경 없음 <strong>{preview.unchanged_count}</strong>건</span><span>오류 <strong>{preview.errors?.length || 0}</strong>건</span></div>
      <p className="reference-scope-warning">대상 행을 확인한 뒤 연결을 확정하세요.<HelpTooltip label="미리보기 대상 범위">분석 출처: {preview.source_system}. 현재 목록 검색과 무관하게 파일 전체를 이 출처에서 찾았습니다. 행별 ‘보기’에서 이벤트 ID와 분석 ID를 확인할 수 있습니다. AI 판정과 기존 답안 이력은 변경하지 않습니다. 확인 전 다른 관리자가 답안을 변경했거나 미리보기가 만료되면 다시 검토해야 합니다.</HelpTooltip></p>
      <div className="table-wrap"><table><thead><tr><th>파일 행</th><th>현재 답안</th><th>연결할 답안</th><th>처리</th><th>상세</th></tr></thead><tbody>{preview.rows.map((row) => <tr key={row.row_number}><td>{row.row_number}</td><td>{referenceVerdicts[row.current_label] || "답안 없음"}</td><td>{referenceVerdicts[row.proposed_label] || row.proposed_label}</td><td>{row.change ? "새 이력 추가" : "변경 없음"}</td><td><button type="button" className="secondary" aria-label={row.row_number + "행 연결 정보"} onClick={() => setSelectedRow(row)}>보기</button></td></tr>)}</tbody></table></div>
      {!!preview.errors?.length && <div className="upload-errors"><h3>연결할 수 없는 행</h3><div className="table-wrap"><table><thead><tr><th>파일 행</th><th>수정할 내용</th></tr></thead><tbody>{preview.errors.map((item, index) => <tr key={index}><td>{item.row_number}</td><td>{labelAttachmentError(item.code)}<HelpTooltip label={item.row_number + "행 오류 정보"}>필드: {item.field || "미기록"} · 오류 코드: {item.code}</HelpTooltip></td></tr>)}</tbody></table></div><p className="error">오류를 수정한 뒤 다시 미리보기하세요. 일부 행만 자동 연결하지 않습니다.</p></div>}
      {preview.can_confirm && <><p className="reference-inline-note">미리보기 만료: {formatDate(preview.expires_at)}</p>{expired && <p className="error" role="alert">미리보기가 만료되었습니다. 다시 미리보기하세요.</p>}<label className="checkbox-row label-confirm"><input type="checkbox" checked={approved} disabled={busy || expired} onChange={(event) => setApproved(event.target.checked)} /><span>연결 범위와 새 이력 {preview.change_count}건을 확인했습니다. 기존 답안 이력과 AI 판정은 보존됩니다.</span></label><button className="primary" type="button" disabled={!approved || busy || expired} onClick={confirm}>{busy ? "연결 중…" : "답안 연결 확정"}</button></>}
    </section>}
    <Dialog open={open && previewOpen && !!selectedRow} title="연결 대상 상세" onClose={() => setSelectedRow(null)}>{selectedRow && <dl className="label-metadata"><dt>파일 행</dt><dd>{selectedRow.row_number}</dd><dt>분석 출처</dt><dd>{preview?.source_system}</dd><dt>이벤트 ID</dt><dd>{selectedRow.event_id}</dd><dt>분석 ID</dt><dd>{selectedRow.analysis_id || "연결 대상 없음"}</dd><dt>현재 답안 버전</dt><dd>{selectedRow.current_revision ?? 0}</dd><dt>현재 답안</dt><dd>{referenceVerdicts[selectedRow.current_label] || "답안 없음"}</dd><dt>연결할 답안</dt><dd>{referenceVerdicts[selectedRow.proposed_label] || selectedRow.proposed_label}</dd></dl>}</Dialog>
    </Dialog></Dialog>
  </>;
}

export function EvaluationDetail({ detail, history, historyError }) {
  const [historyOpen, setHistoryOpen] = useState(false);
  const evaluation = detail.evaluation;
  const reference = evaluation?.reference_label;
  return <section className="panel evaluation-detail" aria-label="참고 답안 평가">
    <div className="panel-head"><h2>참고 답안 평가</h2><EvaluationBadge evaluation={evaluation} /><ReferenceComparisonHelp evaluation={evaluation} /></div>
    <div className="evaluation-detail-body">
      <div className="label-comparison"><div><span>참고 답안</span><ReferenceLabel reference={reference} /></div><span className="comparison-arrow" aria-hidden="true">↔</span><div><span>{isMockAnalysis(detail) ? "모의 판정 (LLM 아님)" : "AI 최종 판정"}</span><strong>{detail.status === "completed" ? referenceVerdicts[detail.verdict] || "미기록" : "아직 확정되지 않음"}</strong></div></div>
      <button type="button" className="secondary" onClick={() => setHistoryOpen(true)}>연결 이력</button>
      <Dialog open={historyOpen} title="참고 답안 이력" onClose={() => setHistoryOpen(false)}>{reference && <dl className="label-metadata"><dt>현재 출처 / 버전</dt><dd>{reference.source_ref}</dd><dt>AI 결과 열람</dt><dd>{referenceVisibilityText(reference.ai_visible)}</dd><dt>현재 답안 버전</dt><dd>{reference.revision} · {formatDate(reference.created_at)}</dd></dl>}<LabelHistoryRows history={history} historyError={historyError} /></Dialog>
    </div>
  </section>;
}
