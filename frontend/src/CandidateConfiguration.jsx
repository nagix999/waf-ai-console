import { useEffect, useState } from "react";
import { api } from "./api.js";
import Dialog from "./Dialog.jsx";
import { Icon } from "./Icon.jsx";
import { candidateIssue, candidateUsesExternal, defaultCandidate } from "./candidateConfiguration.js";
import "./candidateConfiguration.css";

export default function CandidateConfiguration({ agentMode, busy, onChange, onConfigure }) {
  const [catalog, setCatalog] = useState(null), [value, setValue] = useState(null), [draft, setDraft] = useState(null);
  const [loading, setLoading] = useState(true), [error, setError] = useState(""), [reload, setReload] = useState(0);
  const [open, setOpen] = useState(false);
  const stub = agentMode === "stub";
  useEffect(() => {
    const controller = new AbortController();
    if (stub) { onChange({ configuration: null, blocked: "후보 테스트는 moduagent 실행 모드에서 사용할 수 있습니다.", external: false }); setLoading(false); return () => controller.abort(); }
    setLoading(true); setError(""); setCatalog(null); setValue(null);
    onChange({ configuration: null, blocked: "실행 구성을 불러오는 중입니다.", external: false });
    Promise.all([api.agentSettings({ signal: controller.signal }), api.promptPolicies({ signal: controller.signal }), api.inputSchemas({ signal: controller.signal })]).then(([agents, prompts, schemas]) => {
      if (controller.signal.aborted) return;
      const nextCatalog = { agents, prompts, schemas }, next = defaultCandidate(agents, prompts, schemas);
      setCatalog(nextCatalog); setValue(next); setLoading(false);
      onChange({ configuration: next, blocked: candidateIssue(nextCatalog, next), external: candidateUsesExternal(nextCatalog, next) });
    }).catch(() => { if (!controller.signal.aborted) { const message = "실행 구성을 불러오지 못했습니다. 다시 조회하세요."; setError(message); setLoading(false); onChange({ configuration: null, blocked: message, external: false }); } });
    return () => controller.abort();
  }, [stub, reload, onChange]);
  if (stub) return <p className="notice">현재 모의 실행 모드입니다. 후보 구성으로 테스트하려면 서버를 moduagent 모드로 실행하세요. 이전 모의 결과는 이력에서 확인할 수 있으며 기존 API 모의 실행은 유지됩니다.</p>;
  const profileName = id => catalog?.agents.profiles.find(item => item.id === id)?.name || "미지정";
  const versionName = (items, id) => { const item = items?.find(row => row.id === id); return item ? `v${item.version_number} · ${item.name}` : "미지정"; };
  const apply = () => { if (busy || candidateIssue(catalog, draft)) return; setValue(draft); onChange({ configuration: draft, blocked: "", external: candidateUsesExternal(catalog, draft) }); setOpen(false); };
  const change = (field, next) => setDraft(previous => ({ ...previous, [field]: next, ...(field === "evidence_editor_enabled" && !next ? { evidence_editor_profile_id: null } : {}) }));
  const profiles = catalog?.agents.profiles || [];
  const modelSelect = (field, label, optional = false) => <label>{label}<select value={draft?.[field] || ""} onChange={event => change(field, event.target.value || null)}>
    <option value="">{optional ? "1차 판정과 동일" : "모델 선택"}</option>{profiles.map(profile => <option key={profile.id} value={profile.id} disabled={!profile.can_assign}>{profile.name} · {profile.provider}{profile.can_assign ? "" : " · 검증 필요"}</option>)}</select></label>;
  return <section className="panel candidate-configuration" aria-busy={loading}>
    <div className="panel-head"><div className="candidate-title"><span className="candidate-icon"><Icon name="layers" size={28} /></span><div><h2>실행 구성</h2><p className="ux-muted">이번 테스트에만 적용합니다. 운영 설정은 바뀌지 않습니다.</p></div></div><button type="button" className="secondary" disabled={busy || loading || !catalog} onClick={() => { setDraft({ ...value }); setOpen(true); }}>구성 변경</button></div>
    {loading ? <p role="status">구성을 불러오는 중…</p> : value && <dl className="candidate-summary">
      <div><dt>1차 판정</dt><dd>{profileName(value.primary_profile_id)}</dd></div><div><dt>추가 검증</dt><dd>{profileName(value.verifier_profile_id || value.primary_profile_id)}</dd></div>
      <div><dt>근거 정리</dt><dd>{value.evidence_editor_enabled ? profileName(value.evidence_editor_profile_id || value.primary_profile_id) : "사용 안 함"}</dd></div>
      <div><dt>지침</dt><dd>{versionName(catalog.prompts.items, value.prompt_policy_version_id)}</dd></div><div><dt>입력 스키마</dt><dd>{versionName(catalog.schemas.items, value.input_schema_version_id)}</dd></div>
    </dl>}
    {(error || (!loading && candidateIssue(catalog, value))) && <p role="alert" className="error">{error || candidateIssue(catalog, value)}</p>}
    {candidateUsesExternal(catalog, value) && <p className="notice">OpenAI 사용 · HTTP 원문과 Cookie가 외부로 전송되며 호출 비용이 발생할 수 있습니다.</p>}
    <div className="action-row"><button type="button" className="text-button" disabled={busy || loading} onClick={() => setReload(n => n + 1)}>기본 구성 다시 불러오기</button>{onConfigure && <button type="button" className="text-button" disabled={busy} onClick={onConfigure}>모델 설정</button>}<a href="#configure/instructions">분석 지침 →</a><a href="#connect/input-schema">입력 스키마 →</a></div>
    <Dialog open={open} title="테스트 실행 구성" onClose={() => setOpen(false)}>
      <div className="data-form">{modelSelect("primary_profile_id", "1차 판정 · Primary")}{modelSelect("verifier_profile_id", "추가 검증 · Verifier", true)}
        <label className="comparison-checkbox"><input type="checkbox" checked={draft?.evidence_editor_enabled || false} onChange={event => change("evidence_editor_enabled", event.target.checked)} />근거 정리 사용</label>
        {draft?.evidence_editor_enabled && modelSelect("evidence_editor_profile_id", "근거 정리 모델", true)}
        {[["prompt_policy_version_id", "지침 버전", catalog?.prompts], ["input_schema_version_id", "입력 스키마 버전", catalog?.schemas]].map(([field, label, versions]) => <label key={field}>{label}<select value={draft?.[field] || ""} onChange={event => change(field, event.target.value)}><option value="">버전 선택</option>{versions?.items.map(item => <option key={item.id} value={item.id}>v{item.version_number} · {item.name}{item.id === versions.active_version_id ? " · 운영 중" : ""}</option>)}</select></label>)}
        <p className="ux-muted">저장된 다른 버전도 선택할 수 있습니다. 운영에 적용하거나 기본 모델 배정을 바꾸지 않습니다.</p>
        {candidateIssue(catalog, draft) && <p className="error">{candidateIssue(catalog, draft)}</p>}
        <button type="button" className="primary" disabled={busy || Boolean(candidateIssue(catalog, draft))} onClick={apply}>이번 테스트에 적용</button>
      </div>
    </Dialog>
  </section>;
}
