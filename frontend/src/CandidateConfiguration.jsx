import NavigationAction from "./NavigationAction.jsx";
import { useEffect, useRef, useState } from "react";
import { api } from "./api.js";
import { candidateIssue, candidateUsesExternal, candidatePrefill } from "./candidateConfiguration.js";
import { useConsolePreferences } from "./consolePreferences.jsx";
import "./candidateConfiguration.css";

const noop = () => {};

export default function CandidateConfiguration({ agentMode, busy, onChange = noop, initialConfiguration, editDefaults = false, onSaved }) {
  const { locale, t } = useConsolePreferences(), w = (ko, en) => locale === "en" ? en : ko;
  const [catalog, setCatalog] = useState(null), [value, setValue] = useState(null), [defaults, setDefaults] = useState(null);
  const [loading, setLoading] = useState(true), [error, setError] = useState(""), [reload, setReload] = useState(0), [saving, setSaving] = useState(false);
  const savePending = useRef(false);
  useEffect(() => {
    const controller = new AbortController(); setLoading(true); setError("");
    Promise.all([api.agentSettings({ signal: controller.signal }), api.promptPolicies({ signal: controller.signal }), api.inputSchemas({ signal: controller.signal }), api.testDefaults({ signal: controller.signal })]).then(([agents, prompts, schemas, saved]) => {
      if (controller.signal.aborted) return;
      setCatalog({ agents, prompts, schemas }); setDefaults(saved);
      // undefined means a new Test. null means the source Test has no snapshot:
      // never silently replace a missing source configuration with today's defaults.
      setValue(candidatePrefill(initialConfiguration, saved.candidate_configuration)); setLoading(false);
    }).catch(() => { if (!controller.signal.aborted) { setError("load"); setLoading(false); } });
    return () => controller.abort();
  }, [reload, initialConfiguration]);
  const issue = candidateIssue(catalog, value);
  useEffect(() => { onChange({ configuration: value, blocked: loading || error ? "configuration_unavailable" : agentMode === "stub" ? "candidate_requires_llm_test" : issue, external: candidateUsesExternal(catalog, value) }); }, [catalog, value, loading, error, issue, onChange, agentMode]);
  function change(key, next) { setValue(old => ({ ...old, [key]: next, ...(key === "evidence_editor_enabled" && !next ? { evidence_editor_profile_id: null } : {}) })); }
  async function save() {
    if (savePending.current || busy || issue || !defaults) return;
    savePending.current = true;
    setSaving(true); setError("");
    try { const result = await api.saveTestDefaults({ expected_revision: defaults.revision, candidate_configuration: value }); setDefaults(result); onSaved?.(); }
    catch (err) { setError(err.status === 409 ? "stale" : "save"); }
    finally { savePending.current = false; setSaving(false); }
  }
  const profiles = catalog?.agents.profiles || [];
  const model = (key, label, optional) => <label>{label}<select disabled={busy || saving} value={value?.[key] || ""} onChange={e => change(key, e.target.value || null)}><option value="">{optional ? w("주 분석 모델과 동일", "Same as Primary") : w("모델 선택", "Select model")}</option>{value?.[key] && !profiles.some(p => p.id === value[key]) && <option value={value[key]} disabled>{w("이전 모델을 사용할 수 없음", "Source model unavailable")}</option>}{profiles.map(p => <option key={p.id} value={p.id} disabled={!p.can_assign}>{p.name} · {p.provider}{p.can_assign ? "" : w(" · 검증 필요", " · Validation required")}</option>)}</select></label>;
  return <section className="panel candidate-configuration" aria-busy={loading}>
    <div className="panel-head"><div>{!editDefaults && <h2>{w("2. 테스트 설정", "2. Test Configuration")}</h2>}<p className="ux-muted">{editDefaults ? w("새 테스트의 초기값만 바꿉니다. 운영과 기존 테스트는 바뀌지 않습니다.", "Changes New Test prefill only. Production and existing Tests are unchanged.") : initialConfiguration !== undefined ? w("이전 테스트 설정에서 불러옴", "Loaded from the source Test") : w("기본 테스트 설정에서 불러옴", "Loaded from Default Test Configuration")}</p></div></div>
    {loading ? <p role="status">{t("loading")}</p> : catalog && value && <div className="data-form r5-configuration-form">
      {model("primary_profile_id", t("primary"))}{model("verifier_profile_id", t("verifier"), true)}
      <label className="checkbox-row"><input type="checkbox" checked={value.evidence_editor_enabled} disabled={busy || saving} onChange={e => change("evidence_editor_enabled", e.target.checked)} />{t("editor")}</label>
      {value.evidence_editor_enabled && model("evidence_editor_profile_id", t("editor"), true)}
      {[["prompt_policy_version_id", "instructions", catalog.prompts], ["input_schema_version_id", "schema", catalog.schemas]].map(([key, label, versions]) => <label key={key}>{t(label)}<select disabled={busy || saving} value={value[key] || ""} onChange={e => change(key, e.target.value)}><option value="">{w("버전 선택", "Select version")}</option>{value[key] && !versions.items.some(v => v.id === value[key]) && <option value={value[key]} disabled>{w("이전 버전을 사용할 수 없음", "Source version unavailable")}</option>}{versions.items.map(v => <option key={v.id} value={v.id}>v{v.version_number} · {v.name}</option>)}</select></label>)}
    </div>}
    {issue && !loading && <p className="notice">{w("모델 검증 상태와 지침·입력 스키마 선택을 확인하세요.", "Check model validation and select instructions and input schema.")}</p>}
    {error && <p role="alert" className="error">{error === "stale" ? w("기본 설정이 변경됐습니다. 다시 불러와 확인하세요.", "Defaults changed. Reload and review.") : w("설정을 읽거나 저장하지 못했습니다.", "Could not read or save the configuration.")}</p>}
    {candidateUsesExternal(catalog, value) && <p className="notice">{w("OpenAI 사용 시 HTTP 원문·Cookie가 외부로 전송되며 비용이 발생할 수 있습니다.", "OpenAI sends raw HTTP and cookies externally and may incur charges.")}</p>}
    <div className="action-row"><button type="button" className="text-button" disabled={loading || busy || saving} onClick={() => setReload(n => n + 1)}>{t("retry")}</button><NavigationAction href="#configure/instructions">{t("instructions")}</NavigationAction><NavigationAction href="#configure/input-schema">{t("schema")}</NavigationAction>{editDefaults && <button type="button" className="primary" disabled={loading || saving || Boolean(issue)} onClick={save}>{saving ? w("저장 중…", "Saving…") : w("기본 설정 저장", "Save defaults")}</button>}</div>
  </section>;
}
