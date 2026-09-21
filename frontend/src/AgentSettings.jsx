import { Table } from "./DataTable.jsx";
import { useEffect, useRef, useState } from "react";
import { api } from "./api.js";
import Dialog from "./Dialog.jsx";
import PromptSettings from "./PromptSettings.jsx";
import ConcurrencySettings from "./ConcurrencySettings.jsx";
import { providerLabel } from "./llmProfiles.js";
import { createAgentSettingsController, diagnosticLabels, emptyAgentSettingsState, hasExternalRole, selectionIssue } from "./agentSettings.js";
import "./agentSettings.css";

export function AssignmentChanges({ catalog, draft }) {
  const name = id => catalog?.profiles.find(profile => profile.id === id)?.name || "미지정";
  const rows = [];
  for (const purpose of ["production", "test"]) {
    const before = catalog?.assignments?.[purpose] || {}, after = draft?.[purpose] || {};
    const role = (value, key) => key === "evidence_editor_profile_id" && !value.evidence_editor_enabled ? "사용 안 함" : name(value[key] || (key === "primary_profile_id" ? null : value.primary_profile_id));
    for (const [key, label] of [["primary_profile_id", "Primary"], ["verifier_profile_id", "Verifier"], ["evidence_editor_profile_id", "근거 정리"]]) {
      const oldValue = role(before, key), newValue = role(after, key);
      if (before[key] !== after[key] || oldValue !== newValue || (key === "evidence_editor_profile_id" && before.evidence_editor_enabled !== after.evidence_editor_enabled)) rows.push({ key: `${purpose}-${key}`, purpose, label, oldValue, newValue });
    }
  }
  return <section className="assignment-changes"><h3>변경 내용</h3>{rows.length ? <ul>{rows.map(row => <li key={row.key}><span>{row.purpose === "production" ? "프로덕션" : "테스트"} · {row.label}</span><span>{row.oldValue} <span aria-label="변경 후">→</span> <strong>{row.newValue}</strong></span></li>)}</ul> : <p className="ux-muted">변경 사항이 없습니다.</p>}</section>;
}

export function Diagnostics() {
  const [days, setDays] = useState(7); const [purpose, setPurpose] = useState("all");
  const [data, setData] = useState(null); const [error, setError] = useState(""); const [refresh, setRefresh] = useState(0);
  useEffect(() => {
    const controller = new AbortController(); let active = true; setData(null); setError("");
    api.agentDiagnostics({ days, purpose }, { signal: controller.signal }).then(value => { if (active) setData(value); })
      .catch(() => { if (active) setError("처리 현황을 불러오지 못했습니다."); });
    return () => { active = false; controller.abort(); };
  }, [days, purpose, refresh]);
  const counts = data?.counts;
  return <section className="panel agent-settings-panel"><div className="panel-head"><h2>보류·재시도 현황</h2><button className="secondary" onClick={() => setRefresh(value => value + 1)}>새로고침</button></div>
    <div className="ux-toolbar"><label>기간<select value={days} onChange={event => setDays(Number(event.target.value))}>{[7, 30, 90].map(value => <option key={value} value={value}>최근 {value}일</option>)}</select></label><label>대상<select value={purpose} onChange={event => setPurpose(event.target.value)}><option value="all">전체</option><option value="production">프로덕션</option><option value="test">테스트</option></select></label></div>
    <p className="ux-muted">접수 시각 기준입니다. 보류 사유는 새 버전으로 완료한 분석만 집계하며, 한 분석에 여러 사유가 있을 수 있습니다. 모델 정확도 지표는 아닙니다.</p>
    {error ? <p className="error" role="alert">{error}</p> : !counts ? <p role="status">조회 중…</p> : <><dl className="agent-stat-grid"><div><dt>집계 가능한 완료 분석</dt><dd>{counts.measured}건</dd></div><div><dt>보류</dt><dd>{counts.inconclusive}건 · {data.inconclusive_rate === null ? "비율 미측정" : `${(data.inconclusive_rate * 100).toFixed(1)}%`}</dd></div><div><dt>전체 실행 실패</dt><dd>{counts.failed}건</dd></div></dl>
      <p className="ux-muted">총 {counts.total}건 · 완료 {counts.completed}건 · 보류 사유 미기록·모의 완료 {data.unmeasured_completed}건</p>
      <div className="table-wrap"><Table><thead><tr><th>보류 사유</th><th>분석 수</th></tr></thead><tbody>{Object.entries(diagnosticLabels).filter(([key]) => Number.isInteger(counts[key])).map(([key, label]) => <tr key={key}><td>{label}</td><td>{counts[key]}건</td></tr>)}</tbody></Table></div>
      <h3>근거 오류 재시도 · 완료 분석 기준</h3><p>Primary: {counts.primary_repair_attempted}건 중 {counts.primary_repair_recovered}건 교정 · Verifier: {counts.verifier_repair_attempted}건 중 {counts.verifier_repair_recovered}건 교정</p><p className="ux-muted">교정은 출력 형식·원문 인용 검사를 통과했다는 뜻이며, 판정이 정답이 되었다는 의미는 아닙니다.</p>
      <h3>LLM 실행 실패</h3><p className="ux-muted">각 분석의 마지막 실행에 기록된 실패 단계 수입니다. 사유가 기록되지 않은 과거 실패는 포함하지 않습니다.</p><ul>{Object.entries({ output_validation_failed: "출력 형식 오류", output_incomplete: "응답 생성 중단", model_refusal: "모델 응답 거절", invocation_failed: "호출 실패" }).map(([key, label]) => <li key={key}>{label}: {data.llm_failure_counts?.[key] || 0}건</li>)}</ul>
      <p>입력 구조 일부 미인식 {counts.parser_incomplete}건 · 입력 생략 {counts.input_truncated}건{Number.isInteger(counts.input_integrity_observed) && <> · 누락·구조 문제 관찰 {counts.input_integrity_observed}건</>}</p><p className="ux-muted">문제 관찰과 판정 제한은 다릅니다. 정상 판정의 근거 구간에 영향을 준 경우에만 위 보류 사유로 집계합니다.</p>
    </>}
  </section>;
}

export default function AgentSettings({ onModels, onProductionChange, standalone = false }) {
  const [section, setSection] = useState("models"); const [state, setState] = useState(emptyAgentSettingsState);
  const [instructionsVisited, setInstructionsVisited] = useState(false);
  const [concurrencyVisited, setConcurrencyVisited] = useState(false);
  const [confirm, setConfirm] = useState(false); const [acknowledged, setAcknowledged] = useState(false);
  const controller = useRef(null);
  useEffect(() => {
    const instance = createAgentSettingsController({ api, onChange: setState, onCommitted: value => {
      onProductionChange?.(value.profiles.find(p => p.status === "production")?.name || "미지정");
    } });
    controller.current = instance; void instance.refresh(); return () => { instance.dispose(); controller.current = null; };
  }, [onProductionChange]);
  const { catalog, draft } = state;
  const issue = selectionIssue(catalog, draft);
  const disabled = state.loading || state.busy || state.needsRefresh;
  const changed = catalog && JSON.stringify(catalog.assignments) !== JSON.stringify(draft);
  const external = hasExternalRole(catalog, draft);
  const profileName = id => catalog?.profiles.find(p => p.id === id)?.name || (id ? "사용할 수 없음" : "미지정");
  return <div className="page-stack agent-settings">{!standalone && <section className="panel agent-settings-panel"><h2>Agent 설정</h2><p className="ux-muted">Primary는 1차 판정, Verifier는 필요한 경우의 추가 검증을 담당합니다. 두 역할은 서로의 판정을 보지 않습니다.</p>
    <div className="tabs" role="tablist" aria-label="Agent 설정 구분">{[["models", "모델 배정"], ["concurrency", "동시 처리"], ["instructions", "공통 지침"], ["diagnostics", "처리 현황"]].map(([key, label]) => <button type="button" role="tab" key={key} aria-selected={section === key} disabled={state.busy} onClick={() => { setSection(key); if (key === "instructions") setInstructionsVisited(true); if (key === "concurrency") setConcurrencyVisited(true); }}>{label}</button>)}</div></section>}
    <div hidden={section !== "models"} className="page-stack"><section className="panel agent-settings-panel"><div className="panel-head"><div><h2>역할별 모델</h2><p className="ux-muted">전체 검증을 통과한 프로필만 선택할 수 있습니다. 연결 정보와 검증은 LLM 프로필에서 관리합니다.</p></div><div className="ux-toolbar"><button className="secondary" disabled={state.busy} onClick={onModels}>LLM 프로필</button><button className="secondary" disabled={state.busy || state.loading} onClick={() => { setConfirm(false); void controller.current?.refresh(); }}>새로고침</button></div></div>
      {state.error && <p className="error" role="alert">{state.error}</p>}{state.notice && <p className="notice" role="status">{state.notice}</p>}
      {!draft ? <p role="status">{state.loading ? "모델 배정을 확인하는 중…" : "설정을 다시 조회해 주세요."}</p> : <><div className="agent-role-grid">{[["production", "Production · 읽기 전용"], ["test", "Test 기본값"]].map(([purpose, label]) => <fieldset key={purpose} disabled={disabled || purpose === "production"}><legend>{label}</legend>{[["primary_profile_id", "Primary · 1차 판정"], ["verifier_profile_id", "Verifier · 추가 검증"]].map(([role, title]) => {
        const selected = catalog.profiles.find(p => p.id === (draft[purpose][role] || (role === "verifier_profile_id" ? draft[purpose].primary_profile_id : null)));
        return <label key={role}>{title}<select aria-label={title} value={draft[purpose][role] || ""} onChange={event => controller.current?.change(purpose, role, event.target.value)}><option value="">{role === "primary_profile_id" ? "미지정" : "Primary와 동일"}</option>{catalog.profiles.map(p => <option key={p.id} value={p.id} disabled={!p.can_assign}>{p.name}{!p.can_assign ? " · 검증 필요" : ""}</option>)}</select>{selected && <small className="ux-muted">{providerLabel(selected)} · {selected.model_name}</small>}</label>;
      })}<label className="checkbox-row"><input type="checkbox" checked={draft[purpose].evidence_editor_enabled === true} onChange={event => controller.current?.change(purpose, "evidence_editor_enabled", event.target.checked)} />근거·확인사항 정리 사용</label>
      <label>정리 모델<select disabled={!draft[purpose].evidence_editor_enabled} value={draft[purpose].evidence_editor_profile_id || ""} onChange={event => controller.current?.change(purpose, "evidence_editor_profile_id", event.target.value)}><option value="">Primary와 동일</option>{catalog.profiles.map(p => <option key={p.id} value={p.id} disabled={!p.can_assign}>{p.name}{!p.can_assign ? " · 검증 필요" : ""}</option>)}</select></label>
      <small className="ux-muted">중복 설명을 정리하며 판정은 바꾸지 않습니다. 최대 1회 호출하고, 실패하면 원래 근거를 표시합니다.</small>
      </fieldset>)}</div><p className="ux-muted">Production은 후보 구성으로 테스트하고 승인된 답안으로 공식 평가한 뒤 승격합니다. Test 기본값 저장은 운영과 기존 결과를 변경하지 않습니다.</p><div className="ux-toolbar"><a href="#evaluate/tests/new">후보 테스트 만들기 →</a><a href="#promote">운영 승격 →</a><a href="#operate/runtime">동시 처리 설정 →</a></div>
      {issue && <p className="notice">{issue}</p>}<button className="primary" disabled={disabled || Boolean(issue) || !changed} onClick={() => { setAcknowledged(false); setConfirm(true); }}>Test 기본값 저장</button></>}
    </section></div>
    <div hidden={section !== "instructions"}>{instructionsVisited && <PromptSettings />}</div>{section === "diagnostics" && <Diagnostics />}
    <div hidden={section !== "concurrency"}>{concurrencyVisited && <ConcurrencySettings />}</div>
    <Dialog open={confirm} title="모델 배정 확인" onClose={() => { if (!state.busy) setConfirm(false); }}>
      {draft && <><div className="table-wrap"><Table><thead><tr><th>용도</th><th>Primary</th><th>Verifier</th><th>근거·확인사항 정리</th></tr></thead><tbody>{["production", "test"].map(purpose => <tr key={purpose}><td>{purpose === "test" ? "테스트" : "프로덕션"}</td><td>{profileName(draft[purpose].primary_profile_id)}</td><td>{profileName(draft[purpose].verifier_profile_id || draft[purpose].primary_profile_id)}{!draft[purpose].verifier_profile_id && " · Primary와 동일"}</td><td>{draft[purpose].evidence_editor_enabled ? profileName(draft[purpose].evidence_editor_profile_id || draft[purpose].primary_profile_id) : "사용 안 함"}</td></tr>)}</tbody></Table></div>
      <AssignmentChanges catalog={catalog} draft={draft} /><p>이 작업은 모델 배정만 저장하며 지침 변경이나 LLM 호출은 하지 않습니다.</p>{Object.values(draft).some(role => !role.primary_profile_id) && <p className="notice">Primary 미지정 용도의 새 실제 분석은 실행할 수 없습니다.</p>}
      {external && <><p className="notice">OpenAI로 지정한 역할은 HTTP 원문·Cookie를 마스킹 없이 외부 API로 전송하며 호출 비용이 발생할 수 있습니다. 내부 모델과 함께 사용해도 해당 역할의 외부 전송은 발생합니다.</p><label className="checkbox-row"><input type="checkbox" checked={acknowledged} disabled={state.busy} onChange={event => setAcknowledged(event.target.checked)} />전송 범위와 비용을 확인했습니다.</label></>}
      {state.error && <p className="error" role="alert">{state.error}</p>}<div className="action-row"><button className="secondary" disabled={state.busy} onClick={() => setConfirm(false)}>취소</button><button className="primary" disabled={disabled || (external && !acknowledged)} onClick={async () => { if (await controller.current?.save(acknowledged)) setConfirm(false); }}>{state.busy ? "저장 중…" : "적용"}</button></div></>}
    </Dialog>
  </div>;
}
