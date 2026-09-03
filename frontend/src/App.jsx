import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "./api.js";

const labels = {
  pending: "대기",
  processing: "분석 중",
  completed: "완료",
  failed: "실패",
  running: "실행 중",
  passed: "통과",
  draft: "Draft",
  verified: "Verified",
  production: "Production",
  disabled: "비활성",
  true_positive: "정탐",
  false_positive: "오탐",
  inconclusive: "보류",
  exact: "일치",
  partial: "부분 일치",
  mismatch: "불일치",
  unknown: "확인 불가"
};

function Status({ value }) {
  return <span className={`status status-${value}`}>{labels[value] || value || "-"}</span>;
}

function Login({ onLogin }) {
  const [username, setUsername] = useState("admin");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(event) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      await api.login(username, password);
      onLogin();
    } catch (err) {
      setError(err.message === "invalid_credentials" ? "계정 정보를 확인하세요." : err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="login-shell">
      <form className="login-card" onSubmit={submit}>
        <div className="brand-mark">W</div>
        <h1>WAF AI 분석 콘솔</h1>
        <p>내부 관리자 계정으로 로그인하세요.</p>
        <label>아이디<input value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" /></label>
        <label>비밀번호<input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" /></label>
        {error && <div className="error" role="alert">{error}</div>}
        <button className="primary" disabled={busy}>{busy ? "확인 중…" : "로그인"}</button>
      </form>
    </main>
  );
}

function Dashboard({ items, onOpen }) {
  const stats = useMemo(() => ({
    total: items.length,
    active: items.filter((x) => ["pending", "processing"].includes(x.status)).length,
    tp: items.filter((x) => x.verdict === "true_positive").length,
    fp: items.filter((x) => x.verdict === "false_positive").length,
    hold: items.filter((x) => x.verdict === "inconclusive").length
  }), [items]);
  return (
    <div className="page-stack">
      <section className="stats-grid">
        <article><span>최근 조회</span><strong>{stats.total}</strong><small>최대 100건</small></article>
        <article><span>처리 대기/진행</span><strong>{stats.active}</strong><small>worker queue</small></article>
        <article><span>정탐 / 오탐</span><strong>{stats.tp} / {stats.fp}</strong><small>AI 판정 분포</small></article>
        <article><span>보류</span><strong>{stats.hold}</strong><small>stub 결과 포함</small></article>
      </section>
      <AnalysisTable items={items.slice(0, 10)} onOpen={onOpen} title="최근 분석" />
    </div>
  );
}

function AnalysisTable({ items, onOpen, title = "분석 결과" }) {
  return (
    <section className="panel">
      <div className="panel-head"><h2>{title}</h2><span>{items.length}건</span></div>
      <div className="table-wrap">
        <table>
          <thead><tr><th>Event ID</th><th>회사</th><th>벤더</th><th>WAF</th><th>상태</th><th>AI 판정</th><th>접수 시각</th></tr></thead>
          <tbody>
            {items.map((item) => (
              <tr key={item.id} onClick={() => onOpen(item.id)}>
                <td><button className="text-button">{item.event_id}</button></td>
                <td>{item.company_name}</td><td>{item.waf_vendor}</td><td>{item.waf_action}</td>
                <td><Status value={item.status} /></td><td><Status value={item.verdict} /></td>
                <td>{new Date(item.created_at).toLocaleString("ko-KR")}</td>
              </tr>
            ))}
            {!items.length && <tr><td colSpan="7" className="empty">아직 접수된 이벤트가 없습니다.</td></tr>}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function UploadPage({ onUploaded }) {
  const [file, setFile] = useState(null);
  const [result, setResult] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  async function upload() {
    if (!file) return;
    setBusy(true); setError(""); setResult(null);
    try {
      const next = await api.upload(file);
      setResult(next);
      onUploaded();
    } catch (err) { setError(err.message); }
    finally { setBusy(false); }
  }
  return (
    <section className="panel upload-panel">
      <h2>CSV / JSON 일괄 접수</h2>
      <p>UTF-8 파일을 사용하며, 필수 컬럼은 event_id, company_name, src_ip, dest_ip, payload, waf_vendor, waf_action입니다.</p>
      <label className="dropzone">
        <input type="file" accept=".csv,.json,application/json,text/csv" onChange={(e) => setFile(e.target.files?.[0] || null)} />
        <strong>{file ? file.name : "파일을 선택하세요"}</strong>
        <span>CSV 또는 JSON · 최대 10 MB</span>
      </label>
      <button className="primary" onClick={upload} disabled={!file || busy}>{busy ? "접수 중…" : "분석 큐에 등록"}</button>
      {error && <div className="error" role="alert">{error}</div>}
      {result && <div className="notice" aria-live="polite">신규 {result.accepted}건 · 중복 {result.duplicates}건 · 거부 {result.rejected}건</div>}
    </section>
  );
}

function SingleTest({ onCreated }) {
  const [form, setForm] = useState({
    event_id: `test-${Date.now()}`,
    company_name: "내부 테스트",
    src_ip: "192.0.2.10",
    dest_ip: "198.51.100.20",
    src_port: 42310,
    dest_port: 443,
    waf_vendor: "generic",
    waf_action: "D",
    signature: "Synthetic SQL Injection",
    event_name: "manual-test",
    payload: "GET /search?q=%27+OR+1%3D1-- HTTP/1.1\nHost: example.internal\nCookie: session=test-only\n\n"
  });
  const [message, setMessage] = useState("");
  const update = (key) => (event) => setForm({ ...form, [key]: event.target.value });
  async function run(event) {
    event.preventDefault(); setMessage("등록 중…");
    try {
      const created = await api.createAnalysis({ ...form, src_port: Number(form.src_port), dest_port: Number(form.dest_port) });
      setMessage(`등록 완료 · ${created.id}`);
      onCreated(created.id);
    } catch (err) { setMessage(err.message); }
  }
  return (
    <form className="panel form-panel" onSubmit={run}>
      <div className="panel-head"><h2>단건 테스트</h2><span>합성 데이터 기본값</span></div>
      <div className="form-grid">
        <label>Event ID<input value={form.event_id} onChange={update("event_id")} /></label>
        <label>회사명<input value={form.company_name} onChange={update("company_name")} /></label>
        <label>WAF 벤더<input value={form.waf_vendor} onChange={update("waf_vendor")} /></label>
        <label>WAF Action<select value={form.waf_action} onChange={update("waf_action")}><option value="D">Deny</option><option value="A">Allow</option></select></label>
        <label>Source IP<input value={form.src_ip} onChange={update("src_ip")} /></label>
        <label>Destination IP<input value={form.dest_ip} onChange={update("dest_ip")} /></label>
        <label>Source Port<input type="number" value={form.src_port} onChange={update("src_port")} /></label>
        <label>Destination Port<input type="number" value={form.dest_port} onChange={update("dest_port")} /></label>
      </div>
      <label>Signature<input value={form.signature} onChange={update("signature")} /></label>
      <label>HTTP payload<textarea rows="10" value={form.payload} onChange={update("payload")} /></label>
      <div className="action-row"><button className="primary">분석 큐에 등록</button><span aria-live="polite">{message}</span></div>
    </form>
  );
}

function Detail({ id, onBack }) {
  const [detail, setDetail] = useState(null);
  const [raw, setRaw] = useState(null);
  const [runs, setRuns] = useState([]);
  const [tab, setTab] = useState("result");
  const [error, setError] = useState("");
  const load = useCallback(async () => {
    try {
      const [analysis, agentRuns] = await Promise.all([api.analysis(id), api.agentRuns(id)]);
      setDetail(analysis); setRuns(agentRuns);
    } catch (err) { setError(err.message); }
  }, [id]);
  useEffect(() => { load(); const timer = setInterval(load, 3000); return () => clearInterval(timer); }, [load]);
  useEffect(() => {
    if (tab !== "raw" || raw) return;
    api.rawEvent(id).then(setRaw).catch((err) => setError(err.message));
  }, [id, raw, tab]);
  if (error) return <div className="error">{error}</div>;
  if (!detail) return <div className="loading">불러오는 중…</div>;
  return (
    <div className="page-stack">
      <button className="back" onClick={onBack}>← 분석 결과</button>
      <section className="detail-summary panel">
        <div><span>Event ID</span><strong>{detail.event_id}</strong></div>
        <div><span>처리 상태</span><Status value={detail.status} /></div>
        <div><span>AI 판정</span><Status value={detail.verdict} /></div>
        <div><span>신뢰도</span><strong>{detail.confidence_score ?? "-"}</strong></div>
        <div><span>분석가 리뷰</span><strong>{labels[detail.review_state] || detail.review_state}</strong></div>
        <div><span>모델 프로필</span><strong>{detail.model_profile || "-"}</strong></div>
        <div><span>프롬프트</span><strong>{detail.prompt_version || "-"}</strong></div>
      </section>
      <div className="tabs" role="tablist" aria-label="분석 상세">
        {[['result','판정 결과'],['raw','HTTP 원문'],['agent','Agent 실행 이력']].map(([value,label]) => <button key={value} aria-selected={tab === value} onClick={() => setTab(value)}>{label}</button>)}
      </div>
      {tab === "result" && <ResultView detail={detail} />}
      {tab === "raw" && <section className="panel detail-body"><h2>HTTP payload</h2><pre>{raw?.payload || "불러오는 중…"}</pre><h3>추가 필드</h3><pre>{JSON.stringify(raw?.extra_fields || {}, null, 2)}</pre></section>}
      {tab === "agent" && <AgentRuns runs={runs} />}
    </div>
  );
}

function ResultView({ detail }) {
  const result = detail.result;
  if (!result) return <section className="panel detail-body"><h2>요약</h2><p>{detail.summary_ko || "아직 결과가 없습니다."}</p></section>;
  const threat = result.threat_analysis;
  const signature = result.signature_assessment;
  const tuning = result.tuning_recommendation;
  const verifier = result.verifier;
  return (
    <div className="result-stack">
      <section className="panel decision-card">
        <div><span>최종 판정</span><Status value={result.verdict} /></div>
        <strong>{result.summary_ko}</strong>
        <small>신뢰도 {Math.round((result.confidence_score || 0) * 100)}% · 입력 잘림 {result.input_truncated ? "있음" : "없음"}</small>
      </section>
      <div className="result-grid">
        <section className="panel result-card">
          <h2>위협 분석</h2>
          {threat ? <dl><dt>분류</dt><dd>{threat.category}</dd><dt>대상</dt><dd>{threat.target}</dd><dt>기법</dt><dd>{threat.technique_ko}</dd><dt>영향</dt><dd>{threat.potential_impact_ko}</dd><dt>난독화</dt><dd>{threat.obfuscations?.join(", ") || "없음"}</dd></dl> : <p>분석 정보가 없습니다.</p>}
        </section>
        <section className="panel result-card">
          <h2>시그니처 평가</h2>
          {signature ? <><Status value={signature.relation} /><p>{signature.explanation_ko}</p></> : <p>평가 정보가 없습니다.</p>}
          <h3>독립 검증</h3>
          <p>{verifier?.executed ? (verifier.agreement ? "Primary와 Verifier가 일치했습니다." : "불일치 또는 검증 실패로 보류되었습니다.") : "정책 조건에 해당하지 않아 실행하지 않았습니다."}</p>
          {!!verifier?.reasons?.length && <div className="chip-row">{verifier.reasons.map((reason) => <span key={reason}>{reason}</span>)}</div>}
        </section>
      </div>
      <section className="panel result-card evidence-card">
        <h2>판정 근거</h2>
        <div className="evidence-list">
          {(result.evidence || []).map((item, index) => <article key={`${item.field}-${index}`}><span>{item.field}</span><code>{item.excerpt}</code><p>{item.interpretation_ko}</p></article>)}
          {!result.evidence?.length && <p>명시된 근거가 없습니다.</p>}
        </div>
      </section>
      <div className="result-grid">
        <TextList title="불확실성" items={result.uncertainties} empty="명시된 불확실성이 없습니다." />
        <TextList title="분석가 확인 항목" items={result.recommended_checks} empty="추가 확인 항목이 없습니다." />
      </div>
      <section className="panel result-card tuning-card">
        <div className="panel-head-inline"><h2>튜닝 제안</h2><Status value={tuning?.recommended ? "pending" : "disabled"} /></div>
        {tuning?.recommended ? <dl><dt>범위</dt><dd>{tuning.scope}</dd><dt>제안</dt><dd>{tuning.proposal_ko}</dd><dt>위험</dt><dd>{tuning.risk_ko}</dd><dt>검증</dt><dd>{tuning.validation_ko}</dd></dl> : <p>{tuning?.risk_ko || "현재 자동 적용 가능한 튜닝 제안은 없습니다."}</p>}
      </section>
      <details className="panel json-details"><summary>전체 구조화 JSON</summary><pre>{JSON.stringify(result, null, 2)}</pre></details>
    </div>
  );
}

function TextList({ title, items = [], empty }) {
  return <section className="panel result-card"><h2>{title}</h2>{items.length ? <ul>{items.map((item, index) => <li key={index}>{item}</li>)}</ul> : <p>{empty}</p>}</section>;
}

function AgentRuns({ runs }) {
  const [selected, setSelected] = useState(null);
  const [selectedRunId, setSelectedRunId] = useState(null);
  const run = runs.find((item) => item.id === selectedRunId) || runs[0];
  const step = selected || run?.steps?.[0];
  if (!run) return <section className="panel empty">아직 생성된 Agent 실행 이력이 없습니다.</section>;
  return (
    <section className="agent-history">
      {runs.length > 1 && <div className="run-selector">{runs.map((item, index) => <button key={item.id} aria-pressed={run.id === item.id} onClick={() => { setSelectedRunId(item.id); setSelected(null); }}>Run #{runs.length - index} · {item.id.slice(0, 8)}</button>)}</div>}
      <div className="agent-grid">
        <div className="panel timeline">
        <div className="panel-head"><h2>Run {run.id.slice(0, 8)}</h2><Status value={run.status} /></div>
        <div className="run-meta"><span>Framework Run</span><code>{run.framework_run_id || "-"}</code><span>Agent fingerprint</span><code>{run.fingerprint || "-"}</code>{run.failure_id && <><span>Failure ID</span><code>{run.failure_id}</code></>}</div>
        {run.steps.map((item) => <button key={item.id} aria-pressed={step?.id === item.id} onClick={() => setSelected(item)}><span>{item.sequence}</span><div><strong>{item.name}</strong><small>{item.step_type}</small></div></button>)}
        </div>
        <div className="panel step-detail">
          <h2>{step?.name}</h2>
          <h3>Input</h3><pre>{step?.input || "-"}</pre>
          <h3>Output</h3><pre>{step?.output || "-"}</pre>
          <h3>Metadata / Tool calls</h3><pre>{JSON.stringify({ metadata: step?.metadata, tool_calls: step?.tool_calls }, null, 2)}</pre>
        </div>
      </div>
    </section>
  );
}

const emptyProfile = {
  name: "",
  base_url: "http://vllm.internal:8000/v1",
  model_name: "google/gemma-4-26B-A4B-it",
  api_key: "",
  timeout_seconds: 120,
  context_window: 32768,
  max_output_tokens: 3072,
  test_concurrency: 3,
  tls_verify: true
};

function Settings({ onProductionChange }) {
  const [profiles, setProfiles] = useState([]);
  const [tests, setTests] = useState({});
  const [form, setForm] = useState(emptyProfile);
  const [editingId, setEditingId] = useState(null);
  const [busy, setBusy] = useState("");
  const [message, setMessage] = useState("");
  const update = (key) => (event) => setForm({ ...form, [key]: event.target.type === "checkbox" ? event.target.checked : event.target.value });

  const loadProfiles = useCallback(async () => {
    try {
      const next = await api.modelProfiles();
      setProfiles(next);
      onProductionChange(next.find((profile) => profile.status === "production")?.name || "미설정");
      const histories = await Promise.all(next.map(async (profile) => [profile.id, await api.modelProfileTests(profile.id)]));
      setTests(Object.fromEntries(histories));
    } catch (err) { setMessage(err.message); }
  }, [onProductionChange]);

  useEffect(() => {
    loadProfiles();
    const timer = setInterval(loadProfiles, 3000);
    return () => clearInterval(timer);
  }, [loadProfiles]);

  function edit(profile) {
    setEditingId(profile.id);
    setForm({
      name: profile.name,
      base_url: profile.base_url,
      model_name: profile.model_name,
      api_key: "",
      timeout_seconds: profile.timeout_seconds,
      context_window: profile.context_window,
      max_output_tokens: profile.max_output_tokens,
      test_concurrency: profile.test_concurrency,
      tls_verify: profile.tls_verify
    });
    setMessage("API Key를 비워두면 기존 값을 유지합니다.");
  }

  function resetForm() {
    setEditingId(null);
    setForm(emptyProfile);
  }

  async function save(event) {
    event.preventDefault();
    setBusy("save"); setMessage("");
    const payload = {
      ...form,
      timeout_seconds: Number(form.timeout_seconds),
      context_window: Number(form.context_window),
      max_output_tokens: Number(form.max_output_tokens),
      test_concurrency: Number(form.test_concurrency)
    };
    if (editingId && !payload.api_key) delete payload.api_key;
    try {
      if (editingId) await api.updateModelProfile(editingId, payload);
      else await api.createModelProfile(payload);
      setMessage(editingId ? "프로필을 수정했습니다. 다시 전체 검증이 필요합니다." : "Draft 프로필을 등록했습니다.");
      resetForm();
      await loadProfiles();
    } catch (err) { setMessage(err.message); }
    finally { setBusy(""); }
  }

  async function act(key, action) {
    setBusy(key); setMessage("");
    try { await action(); await loadProfiles(); }
    catch (err) { setMessage(err.message); }
    finally { setBusy(""); }
  }

  return (
    <div className="page-stack">
      <section className="panel profile-section">
        <div className="panel-head"><div><h2>vLLM 프로필</h2><small>전체 검증을 통과한 프로필만 Production으로 승격할 수 있습니다.</small></div><Status value={profiles.find((item) => item.status === "production") ? "completed" : "pending"} /></div>
        <div className="table-wrap">
          <table className="profile-table">
            <thead><tr><th>프로필</th><th>Endpoint / 모델</th><th>설정</th><th>최근 테스트</th><th>상태</th><th>작업</th></tr></thead>
            <tbody>
              {profiles.map((profile) => {
                const latest = tests[profile.id]?.[0];
                const active = ["pending", "running"].includes(latest?.status);
                return <tr key={profile.id}>
                  <td><strong>{profile.name}</strong><small>{profile.has_api_key ? "API Key 저장됨" : "인증 없음"}</small></td>
                  <td><span>{profile.base_url}</span><small>{profile.model_name}</small></td>
                  <td><span>{profile.context_window.toLocaleString()} ctx</span><small>{profile.timeout_seconds}s · 동시 {profile.test_concurrency}</small></td>
                  <td>{latest ? <><Status value={latest.status} /><small>{latest.mode} · {latest.completed_at ? new Date(latest.completed_at).toLocaleString("ko-KR") : "진행 중"}</small></> : <span>-</span>}</td>
                  <td><Status value={profile.status} /></td>
                  <td><div className="profile-actions">
                    <button className="secondary small" disabled={active || profile.status === "disabled"} onClick={() => act(`q-${profile.id}`, () => api.runModelProfileTest(profile.id, "quick"))}>{busy === `q-${profile.id}` ? "등록 중" : "빠른 테스트"}</button>
                    <button className="secondary small" disabled={active || profile.status === "disabled"} onClick={() => act(`f-${profile.id}`, () => api.runModelProfileTest(profile.id, "full"))}>{busy === `f-${profile.id}` ? "등록 중" : "전체 검증"}</button>
                    <button className="secondary small" disabled={profile.status === "production"} onClick={() => edit(profile)}>편집</button>
                    {profile.status === "verified" && <button className="primary small" onClick={() => act(`p-${profile.id}`, () => api.promoteModelProfile(profile.id))}>Production 승격</button>}
                    {profile.status === "disabled"
                      ? <button className="secondary small" onClick={() => act(`e-${profile.id}`, () => api.enableModelProfile(profile.id))}>활성화</button>
                      : <button className="secondary small" onClick={() => act(`d-${profile.id}`, () => api.disableModelProfile(profile.id))}>비활성화</button>}
                  </div></td>
                </tr>;
              })}
              {!profiles.length && <tr><td colSpan="6" className="empty">등록된 vLLM 프로필이 없습니다.</td></tr>}
            </tbody>
          </table>
        </div>
      </section>

      {profiles.map((profile) => tests[profile.id]?.[0] && <TestResult key={profile.id} profile={profile} test={tests[profile.id][0]} />)}

      <form className="panel form-panel profile-form" onSubmit={save}>
        <div className="panel-head"><div><h2>{editingId ? "vLLM 프로필 편집" : "vLLM 프로필 추가"}</h2><small>허용 대상은 서버 배포 설정에서 별도로 제한됩니다.</small></div>{editingId && <button type="button" className="secondary small" onClick={resetForm}>취소</button>}</div>
        <div className="form-grid">
          <label>프로필 이름<input value={form.name} onChange={update("name")} required placeholder="gemma4-candidate" /></label>
          <label>Base URL<input value={form.base_url} onChange={update("base_url")} required /></label>
          <label>모델 이름<input value={form.model_name} onChange={update("model_name")} required /></label>
          <label>API Key<input type="password" value={form.api_key} onChange={update("api_key")} placeholder={editingId ? "변경할 때만 입력" : "필요한 경우 입력"} autoComplete="new-password" /></label>
          <label>Timeout(초)<input type="number" min="5" max="600" value={form.timeout_seconds} onChange={update("timeout_seconds")} /></label>
          <label>Context window<input type="number" min="4096" value={form.context_window} onChange={update("context_window")} /></label>
          <label>최대 출력 토큰<input type="number" min="256" value={form.max_output_tokens} onChange={update("max_output_tokens")} /></label>
          <label>검증 동시 요청<input type="number" min="1" max="10" value={form.test_concurrency} onChange={update("test_concurrency")} /></label>
        </div>
        <label className="checkbox-row"><input type="checkbox" checked={form.tls_verify} onChange={update("tls_verify")} />TLS 인증서 검증</label>
        <div className="action-row"><button className="primary" disabled={busy === "save"}>{busy === "save" ? "저장 중…" : editingId ? "수정 저장" : "Draft 등록"}</button><span aria-live="polite">{message}</span></div>
      </form>
    </div>
  );
}

function TestResult({ profile, test }) {
  return <section className="panel test-result">
    <div className="panel-head"><div><h2>{profile.name} · {test.mode === "full" ? "전체 검증" : "빠른 테스트"}</h2><small>Thinking 비활성화 · 설정 지문 {test.profile_fingerprint.slice(0, 12)}</small></div><Status value={test.status} /></div>
    {test.error_message && <div className="error">{test.error_code} · {test.error_message}</div>}
    <div className="check-grid">
      {test.checks.map((check) => <article key={check.name}><div><strong>{check.name}</strong><Status value={check.status} /></div><span>{check.latency_ms} ms</span><small>{check.message || JSON.stringify(check.detail)}</small></article>)}
      {!test.checks.length && <div className="empty">worker 실행을 기다리고 있습니다.</div>}
    </div>
  </section>;
}

export default function App() {
  const [principal, setPrincipal] = useState(null);
  const [checking, setChecking] = useState(true);
  const [page, setPage] = useState("dashboard");
  const [items, setItems] = useState([]);
  const [selectedId, setSelectedId] = useState(null);
  const [productionName, setProductionName] = useState("미설정");

  const load = useCallback(async () => {
    try {
      const [data, profiles] = await Promise.all([api.analyses(), api.modelProfiles()]);
      setItems(data.items);
      setProductionName(profiles.find((profile) => profile.status === "production")?.name || "미설정");
    }
    catch (err) { if (err.status === 401) setPrincipal(null); }
  }, []);
  async function checkSession() {
    try { setPrincipal(await api.me()); } catch { setPrincipal(null); }
    finally { setChecking(false); }
  }
  useEffect(() => { checkSession(); }, []);
  useEffect(() => {
    if (!principal) return undefined;
    load(); const timer = setInterval(load, 5000); return () => clearInterval(timer);
  }, [principal, load]);
  if (checking) return <div className="loading full">세션 확인 중…</div>;
  if (!principal) return <Login onLogin={checkSession} />;

  function openDetail(id) { setSelectedId(id); setPage("detail"); }
  const title = { dashboard: "대시보드", analyses: "분석 결과", upload: "파일 분석", test: "테스트 랩", settings: "설정", detail: "분석 상세" }[page];
  return (
    <div className="app-shell">
      <aside>
        <div className="brand"><span className="brand-mark">W</span><strong>WAF AI Console</strong></div>
        <nav>
          {[['dashboard','대시보드'],['analyses','분석 결과'],['upload','파일 분석'],['test','테스트 랩'],['settings','설정']].map(([value,label]) => <button key={value} aria-current={page === value ? "page" : undefined} onClick={() => setPage(value)}>{label}</button>)}
        </nav>
        <div className="model-note"><span>Production</span><strong>{productionName}</strong></div>
      </aside>
      <main className="workspace">
        <header><div><h1>{title}</h1><p>사내 WAF 정·오탐 판정 지원</p></div><button className="secondary" onClick={async () => { await api.logout(); setPrincipal(null); }}>로그아웃</button></header>
        <div className="content">
          {page === "dashboard" && <Dashboard items={items} onOpen={openDetail} />}
          {page === "analyses" && <AnalysisTable items={items} onOpen={openDetail} />}
          {page === "upload" && <UploadPage onUploaded={load} />}
          {page === "test" && <SingleTest onCreated={openDetail} />}
          {page === "settings" && <Settings onProductionChange={setProductionName} />}
          {page === "detail" && <Detail id={selectedId} onBack={() => setPage("analyses")} />}
        </div>
      </main>
    </div>
  );
}
