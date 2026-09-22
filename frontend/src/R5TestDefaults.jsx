import { useState } from "react";
import Dialog from "./Dialog.jsx";
import CandidateConfiguration from "./CandidateConfiguration.jsx";
import { useR5Words } from "./R5Evaluation.jsx";
import { ConfigurationRows, useRead } from "./LifecycleViews.jsx";
import { api } from "./api.js";

export default function TestDefaults({ agentMode, primary = false }) {
  const w = useR5Words(), [open, setOpen] = useState(false), [notice, setNotice] = useState("");
  return <><button className={primary ? "primary" : "secondary"} onClick={() => { setNotice(""); setOpen(true); }}>{w("기본 테스트 설정", "Default Test Configuration")}</button><Dialog open={open} title={w("기본 테스트 설정", "Default Test Configuration")} onClose={() => setOpen(false)}>{open && <CandidateConfiguration agentMode={agentMode} editDefaults onSaved={() => setNotice(w("기본 테스트 설정을 저장했습니다. 운영에는 반영되지 않습니다.", "Defaults saved. Production is unchanged."))} />}{notice && <p className="notice" role="status">{notice}</p>}</Dialog></>;
}
export function ReadOnlyAgentRoles() {
  const w = useR5Words(); const { value, error } = useRead(api.productionConfiguration);
  return <section className="panel r5-readonly-roles"><h2>{w("운영 Agent 역할", "Production Agent Roles")}</h2><p className="v5-context">{w("운영 모델은 공식 테스트와 운영 반영 검토를 거쳐서만 바뀝니다. 기본 테스트 설정은 평가 > 테스트에서 관리합니다.", "Production roles change only through an Official Test and Production Review. Manage Default Test Configuration in Evaluation > Tests.")}</p>{error && <p className="error" role="alert">{w("운영 설정을 읽지 못했습니다.", "Could not load Production configuration.")}</p>}<ConfigurationRows snapshot={value?.snapshot} names={value?.profile_names} versions={value?.version_names} /><div className="ux-toolbar"><a href="#evaluate/tests">{w("테스트 열기", "Open Tests")} →</a><a href="#operate/runtime">{w("동시 처리·실행 상태", "Concurrency and Runtime")} →</a></div></section>;
}
