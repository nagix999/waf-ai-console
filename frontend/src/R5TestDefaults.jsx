import NavigationAction from "./NavigationAction.jsx";
import { useState } from "react";
import Dialog from "./Dialog.jsx";
import CandidateConfiguration from "./CandidateConfiguration.jsx";
import { useR5Words } from "./R5Evaluation.jsx";
import { ConfigurationRows, useRead } from "./LifecycleViews.jsx";
import { api } from "./api.js";
import { Icon } from "./Icon.jsx";
import { SettingsSectionHeader, SettingsWorkflow } from "./SettingsWorkspace.jsx";

export default function TestDefaults({ agentMode, primary = false }) {
  const w = useR5Words(), [open, setOpen] = useState(false), [notice, setNotice] = useState("");
  return <><button className={primary ? "primary" : "secondary"} onClick={() => { setNotice(""); setOpen(true); }}>{w("기본 테스트 설정", "Default Test Configuration")}</button><Dialog open={open} title={w("기본 테스트 설정", "Default Test Configuration")} onClose={() => setOpen(false)}>{open && <CandidateConfiguration agentMode={agentMode} editDefaults onSaved={() => setNotice(w("기본 테스트 설정을 저장했습니다. 운영에는 반영되지 않습니다.", "Defaults saved. Production is unchanged."))} />}{notice && <p className="notice" role="status">{notice}</p>}</Dialog></>;
}
export function ReadOnlyAgentRoles() {
  const w = useR5Words(); const { value, error } = useRead(api.productionConfiguration); const [notice, setNotice] = useState("");
  const profileName = role => !value ? "—" : role === "evidence_editor" && !value.snapshot?.evidence_editor?.enabled ? w("사용 안 함", "Disabled") : value.profile_names?.[value.snapshot?.[role]?.profile_id] || w("미지정", "Not assigned");
  return <div className="page-stack settings-workspace r5-readonly-roles"><SettingsSectionHeader title={w("운영 Agent 역할", "Production Agent Roles")} actions={<NavigationAction href="#evaluate/tests">{w("테스트 열기", "Open Tests")}</NavigationAction>} />
    <div className="settings-production-banner"><span className="status purpose-production">Production</span><p>{w("현재 운영에 사용하는 모델입니다. 변경은 공식 테스트와 운영 반영 검토를 거칩니다.", "Current Production models. Changes require an Official Test and Production Review.")}</p><span className="settings-readonly-badge">{w("읽기 전용", "Read only")}</span></div>
    {error && <p className="error" role="alert">{w("운영 설정을 읽지 못했습니다.", "Could not load Production configuration.")}</p>}
    <section className="settings-role-cards" aria-label={w("운영 역할별 모델", "Production role assignments")}>{[
      ["primary", "cube", w("주 분석", "Primary"), "Primary", w("요청을 분석하고 근거와 1차 판정을 작성합니다.", "Analyzes the request and produces evidence and the initial verdict.")],
      ["verifier", "shield", w("추가 검증", "Verifier"), "Verifier", w("정책 조건에 따라 독립 분석합니다. 주 분석의 판정은 보지 않습니다.", "Analyzes independently when policy requires it, without seeing the Primary verdict.")],
      ["evidence_editor", "file", w("근거 정리", "Evidence Editor"), "Evidence Editor", w("중복 설명을 정리합니다. 최종 판정은 바꾸지 않습니다.", "Organizes repeated evidence explanations without changing the final verdict.")],
    ].map(([role, icon, title, name, note]) => <article key={role} className={`panel settings-role-card settings-role-${role}`}><span className="settings-section-icon"><Icon name={icon} size={23} /></span><div className="settings-role-title"><h2>{title}</h2>{title !== name && <span>{name}</span>}</div><strong className="settings-role-model">{profileName(role)}</strong><p>{note}</p><span className="settings-readonly-badge">{w("읽기 전용", "Read only")}</span></article>)}</section>
    <div className="settings-detail-grid settings-defaults-layout"><section className="settings-inline-defaults"><header className="settings-inline-heading"><h2>{w("기본 테스트 설정", "Default Test Configuration")}</h2><span className="status purpose-test">Test</span></header><CandidateConfiguration editDefaults onSaved={() => setNotice(w("기본 테스트 설정을 저장했습니다. 운영에는 반영되지 않습니다.", "Defaults saved. Production is unchanged."))} />{notice && <p className="notice" role="status">{notice}</p>}</section><aside className="settings-context-stack"><section className="panel settings-context-card"><h3>{w("운영 반영 절차", "Production workflow")}</h3><SettingsWorkflow /><p>{w("기본 테스트 설정을 저장해도 운영 모델과 기존 테스트는 바뀌지 않습니다.", "Saving defaults changes neither Production nor existing Tests.")}</p><NavigationAction href="#evaluate/tests/new">{w("새 테스트", "New Test")}</NavigationAction></section><section className="panel settings-role-context"><header className="panel-head"><h2>{w("함께 사용하는 분석 기준", "Analysis configuration")}</h2></header><ConfigurationRows snapshot={value?.snapshot} names={value?.profile_names} versions={value?.version_names} showRoles={false} /></section><section className="panel settings-context-card"><h3>{w("동시 처리", "Concurrency")}</h3><p>{w("실행 중인 요청과 모델별 동시 처리 설정을 확인합니다.", "Check active requests and model concurrency settings.")}</p><NavigationAction href="#operate/runtime">{w("실행 상태", "Runtime")}</NavigationAction></section></aside></div>
  </div>;
}
