import { useEffect, useRef } from "react";
import { providerLabel } from "./llmProfiles.js";
import { assignmentBlockReason, testModelAvailability } from "./modelAssignments.js";
import "./modelAssignments.css";

export function ModelAssignmentCards({ profiles, loading, error, busy, onUnassignTest }) {
  return <section className="model-assignment-cards" aria-label="용도별 LLM 지정">{[["production", "Production", "운영 API로 접수한 분석"], ["test", "Test", "직접 입력·파일 업로드 테스트"]].map(([role, label, description]) => {
    const profile = profiles.find(item => role === "production" ? item.status === "production" : item.is_test === true);
    return <article key={role} className={`panel model-assignment-card model-assignment-${role}`}><div className="model-assignment-heading"><strong>{label}</strong><span>{description}</span></div>{loading ? <p role="status">지정 상태 확인 중…</p> : error ? <p className="error" role="alert">최신 지정 상태를 조회하지 못했습니다.</p> : profile ? <><h3>{profile.name}</h3><p>{providerLabel(profile)} · {profile.model_name}</p><small>{profile.can_assign ? "현재 설정의 전체 검증 통과" : assignmentBlockReason(profile)}</small>{role === "test" && <button type="button" className="secondary small" disabled={busy} onClick={() => onUnassignTest(profile)}>Test 지정 해제</button>}</> : <><h3>미지정</h3><p>{role === "test" ? "아래에서 검증된 프로필을 Test로 지정하세요. Production으로 자동 대체하지 않습니다." : "아래에서 검증된 프로필을 Production으로 지정하세요."}</p></>}<small>{role === "production" ? "다른 프로필 지정으로 교체하거나 현재 프로필을 비활성화해 해제합니다." : "같은 프로필을 양쪽에 쓰려면 Production과 Test를 각각 지정하세요."}</small></article>;
  })}</section>;
}

export function AssignmentDialogContent({ target, busy, error, needsReconfirm, onCancel, onConfirm, cancelRef }) {
  const assigning = ["production", "test"].includes(target.action); const role = target.action === "production" ? "Production" : "Test";
  const title = target.action === "disable" ? "프로필 비활성화" : target.action === "unassign_test" ? "Test 지정 해제" : `${role} 모델 지정`;
  return <><h2 id="model-assignment-title">{title}</h2><dl className="model-assignment-target"><dt>프로필</dt><dd>{target.name}</dd><dt>공급자 / 모델</dt><dd>{providerLabel(target)} · {target.model_name}</dd></dl>
    {assigning ? <><p>{target.previous_name ? <><strong>{target.previous_name}</strong> 대신 이 프로필을 {role}로 지정합니다.</> : <>이 프로필을 {role}로 지정합니다.</>}</p><p>{role === "Test" ? "이후 직접 입력·파일 업로드 테스트에 사용합니다. Production 지정은 변경하지 않습니다." : "이후 운영 분석의 모델 선택에 사용합니다. Test 지정은 변경하지 않습니다."}</p>{target.provider === "openai" && <p className="assignment-warning">이 용도의 마스킹하지 않은 payload·Cookie 등이 OpenAI 외부 API로 전송되며 API 비용이 발생할 수 있습니다. 전송 범위를 확인하세요.</p>}</> : target.action === "unassign_test" ? <p>Test 지정만 해제합니다. Production 지정과 프로필은 유지합니다. 새 실제 테스트는 Test 모델을 다시 지정하기 전까지 접수할 수 없습니다.</p> : <p className="assignment-warning">이 프로필의 Production·Test 지정을 모두 해제하고 사용을 중지합니다. 이 프로필을 사용하는 진행 중 작업의 후속 요청도 차단될 수 있습니다.</p>}
    <p className="muted">이 작업 자체는 LLM을 호출하지 않습니다. 기존 결과와 접수된 테스트의 모델 정보를 다른 프로필로 바꾸지 않습니다.</p>{error && <p className="error" role="alert">{error}</p>}<div className="action-row"><button ref={cancelRef} type="button" className="secondary" disabled={busy} onClick={onCancel}>취소</button><button type="button" className="primary" disabled={busy || needsReconfirm} onClick={onConfirm}>{busy ? "적용 중…" : title}</button></div></>;
}

export function ModelAssignmentDialog({ state, controller }) {
  const dialog = useRef(null); const cancel = useRef(null);
  useEffect(() => { if (!state.target || !dialog.current) return undefined; const node = dialog.current; const previous = document.activeElement; node.showModal(); cancel.current?.focus(); return () => { node.close(); if (previous instanceof HTMLElement && previous.isConnected) previous.focus(); }; }, [state.target?.id, state.target?.action]);
  if (!state.target) return null;
  return <dialog ref={dialog} className="model-assignment-dialog" aria-labelledby="model-assignment-title" onCancel={event => { event.preventDefault(); if (!state.busy) controller?.close(); }}><AssignmentDialogContent {...state} cancelRef={cancel} onCancel={() => controller?.close()} onConfirm={() => controller?.submit()} /></dialog>;
}

export function TestModelNotice({ state, agentMode, onRefresh, onConfigure }) {
  const availability = testModelAvailability({ ...state, agentMode });
  return <section className="panel test-model-notice" aria-label="Test 모델 지정 상태"><div><strong>{agentMode === "stub" ? "모의 실행 모드" : "이 테스트에 사용할 Test 모델"}</strong>{availability.profile && <p>{availability.profile.name} · {providerLabel(availability.profile)} · {availability.profile.model_name}</p>}<p role={state.error ? "alert" : "status"}>{availability.blocked || availability.message}</p></div><div className="action-row"><button type="button" className="secondary small" disabled={state.loading} onClick={onRefresh}>지정 상태 새로고침</button>{onConfigure && <button type="button" className="secondary small" onClick={onConfigure}>LLM 설정</button>}</div></section>;
}
