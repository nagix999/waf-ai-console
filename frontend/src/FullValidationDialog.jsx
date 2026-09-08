import { useEffect, useRef, useState } from "react";
import { EvaluationSummary } from "./ReferenceLabels.jsx";
import { datasetEvaluationStatus, datasetListState } from "./modelValidation.js";
import { providerLabel } from "./llmProfiles.js";
import HelpTooltip from "./HelpTooltip.jsx";
import Dialog from "./Dialog.jsx";
import "./fullValidation.css";

export function FullValidationContent({ profile, name = "", onNameChange, busy, error, needsReconfirm, onCancel, onSubmit, cancelRef }) {
  return <><div className="validation-dialog-heading"><h2 id="full-validation-title">전체 검증 범위 선택</h2><p id="full-validation-description">선택한 프로필의 연결·기능을 검증하고, 선택에 따라 테스트 WAF 150건의 판정도 평가합니다.</p></div>
    <dl className="validation-profile"><dt>테스트 대상</dt><dd>{profile.name}</dd><dt>공급자 / 모델</dt><dd>{providerLabel(profile)} · {profile.model_name}</dd></dl>
    <label className="test-name-field">테스트명 · 선택<input maxLength={120} value={name} disabled={busy} onChange={event => onNameChange?.(event.target.value)} placeholder="비워두면 자동 ID를 사용합니다" /></label>
    <p>Test 지정 여부와 관계없이 선택한 프로필로 검증하며 Production·Test 지정을 바꾸지 않습니다. 공통 활성 프롬프트를 사용합니다.</p>
    <div className="validation-options"><section><h3>연결·기능 검증만</h3><p>연결·기본 응답·구조화 출력·시스템 지침·큰 입력·동시 요청을 확인합니다. 판정 품질은 보증하지 않습니다.</p></section><section><h3>150건 판정 평가도 실행<HelpTooltip label="150건 판정 평가">기능 검증을 통과한 뒤 waf-dummy-v1 테스트 로그를 난이도별 50건씩 분석합니다. 기대 답안은 모델 입력에서 분리하며 일치율을 운영 정확도로 해석하거나 자동 승격에 사용하지 않습니다.</HelpTooltip></h3><p>기능 검증 후 테스트 로그 150건을 분석합니다. 추가 검증과 재시도로 모델 호출은 150회를 넘을 수 있습니다.</p></section></div>
    {profile.provider === "openai" && <p className="validation-cost" role="note">OpenAI를 선택했습니다. 두 실행 옵션 모두 테스트 입력을 OpenAI 외부 API로 전송하며 API 비용이 발생할 수 있습니다. 150건 판정 평가는 큰 입력과 추가 호출로 비용이 늘어날 수 있습니다.</p>}
    {error && <div className="error" role="alert">{error}</div>}
    <div className="validation-dialog-actions"><button ref={cancelRef} type="button" className="secondary" disabled={busy} onClick={onCancel}>취소</button><button type="button" className="secondary" disabled={busy || needsReconfirm} onClick={() => onSubmit(false)}>연결·기능 검증만</button><button type="button" className="primary" disabled={busy || needsReconfirm} onClick={() => onSubmit(true)}>150건 판정 평가도 실행</button></div>
    {busy && <p role="status">검증 요청을 접수하는 중… 중복 요청하지 마세요.</p>}
  </>;
}

export default function FullValidationDialog({ state, controller }) {
  const dialog = useRef(null); const cancel = useRef(null);
  useEffect(() => {
    const node = dialog.current;
    if (!state.profile || !node) return undefined;
    const previous = document.activeElement;
    node.showModal(); cancel.current?.focus();
    return () => { node.close(); if (previous instanceof HTMLElement && previous.isConnected) previous.focus(); };
  }, [state.profile?.id]);
  if (!state.profile) return null;
  return <dialog ref={dialog} className="full-validation-dialog" aria-labelledby="full-validation-title" aria-describedby="full-validation-description" onCancel={event => { event.preventDefault(); if (!state.busy) controller?.close(); }}>
    <FullValidationContent profile={state.profile} name={state.name} onNameChange={name => controller?.setName(name)} busy={state.busy} error={state.error} needsReconfirm={state.needsReconfirm} cancelRef={cancel} onCancel={() => controller?.close()} onSubmit={include => controller?.submit(include)} />
  </dialog>;
}

export function DatasetEvaluation({ test, onViewDataset }) {
  const [summaryOpen, setSummaryOpen] = useState(false);
  if (!test.include_dataset) return null;
  const dataset = test.dataset_evaluation;
  if (!dataset) return <section className="dataset-evaluation"><h3>150건 판정 평가</h3><p>평가 실행 정보를 아직 확인할 수 없습니다. 미실행이나 완료로 추정하지 않습니다.</p></section>;
  const finished = dataset.completed + dataset.failed;
  const canView = Boolean(datasetListState(dataset.source_system)) && dataset.total > 0;
  return <section className="dataset-evaluation" aria-label="150건 테스트 판정 평가"><div className="dataset-evaluation-heading"><div><h3>{test.name || "150건 테스트 판정 평가"}</h3><p>{dataset.dataset_version} · {datasetEvaluationStatus(dataset.status)}</p></div>{(test.test_run_id || canView) && <button type="button" className="secondary" onClick={() => onViewDataset?.(dataset.source_system, test.test_run_id)}>150건 분석 결과 보기</button>}</div>
    <p>선택한 프로필로 실행한 결과입니다. 기대 답안 비교는 기능 검증의 통과·실패 및 Production 승격과 구분합니다.</p>
    <div className="dataset-progress"><progress aria-label="판정 평가 처리 진행률" max={dataset.total || 150} value={finished} /><span>처리 종료 {finished} / {dataset.total}건</span></div>
    <div className="dataset-counts">{[["대기", dataset.pending], ["분석 중", dataset.processing], ["분석 완료", dataset.completed], ["실행 실패", dataset.failed]].map(([label, count]) => <span key={label}>{label} <strong>{count}건</strong></span>)}</div>
    {dataset.status === "skipped" && <p className="validation-help">기능 검증 등 선행 조건을 충족하지 못해 판정 평가를 실행하지 않았습니다.</p>}
    {dataset.status === "failed" && <p className="validation-help">실행 실패를 오답으로 세지 않습니다. 완료된 분석이 있으면 해당 결과는 별도로 확인할 수 있습니다.</p>}
    <button type="button" className="secondary" onClick={() => setSummaryOpen(true)}>참고 답안 비교 집계</button><Dialog open={summaryOpen} title="150건 참고 답안 비교 집계" onClose={() => setSummaryOpen(false)}><EvaluationSummary summary={dataset.summary} /></Dialog>
    <p className="validation-help">기대값은 테스트 시나리오 기준입니다. 모델 보류·기대 보류·미탐 방향·과탐 방향과 실행 실패를 구분하며 일치율로 자동 승격하지 않습니다.</p>
  </section>;
}
