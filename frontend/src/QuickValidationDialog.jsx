import { useEffect, useRef, useState } from "react";
import Dialog from "./Dialog.jsx";
import { api } from "./api.js";
import { modelProfileError, providerOf } from "./llmProfiles.js";
import { autoTestName, newTestRequestKey, validateTestName } from "./testRuns.js";

export default function QuickValidationDialog({ profile, open, onClose, onSubmitted }) {
  const [name, setName] = useState(""); const [approved, setApproved] = useState(false);
  const [busy, setBusy] = useState(false); const [error, setError] = useState("");
  const request = useRef(null); const submitting = useRef(false); const mounted = useRef(false);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  async function submit(event) {
    event.preventDefault();
    if (submitting.current || !profile || (providerOf(profile) === "openai" && !approved)) return;
    const fingerprint = `${profile.id}:${profile.profile_fingerprint}:${name}`;
    if (request.current?.fingerprint !== fingerprint) request.current = { fingerprint, id: newTestRequestKey() };
    const normalized = autoTestName(name, request.current.id);
    const invalid = validateTestName(normalized);
    if (invalid) { setError(invalid); return; }
    submitting.current = true; setBusy(true); setError("");
    try {
      await api.runModelProfileTest(profile.id, "quick", false, profile.profile_fingerprint, { name: normalized, idempotency_key: request.current.id });
      if (mounted.current) { request.current = null; setName(""); setApproved(false); onSubmitted(); }
    } catch (err) { if (mounted.current) setError(modelProfileError(err)); }
    finally { submitting.current = false; if (mounted.current) setBusy(false); }
  }
  return <Dialog open={open} title="빠른 연결 테스트" onClose={() => { if (!busy) onClose(); }}><form onSubmit={submit} className="page-stack">
    <p><strong>{profile?.name}</strong>의 연결·기본 응답·출력 형식을 확인합니다. 이 테스트만으로 운영 모델을 지정할 수는 없습니다.</p>
    <label>테스트명 · 선택<input value={name} maxLength={120} disabled={busy} placeholder="비워두면 ID 자동 생성" onChange={event => setName(event.target.value)} /></label>
    {providerOf(profile) === "openai" && <><p className="warning">예시 입력을 OpenAI로 전송하며 API 비용이 발생할 수 있습니다.</p><label className="checkbox-line"><input type="checkbox" checked={approved} disabled={busy} onChange={event => setApproved(event.target.checked)} />외부 전송과 비용을 확인했습니다.</label></>}
    {error && <p className="error" role="alert">{error}</p>}
    <div className="ux-toolbar"><button type="submit" className="primary" disabled={busy || !profile || (providerOf(profile) === "openai" && !approved)}>{busy ? "접수 중…" : "테스트 시작"}</button><button type="button" className="secondary" disabled={busy} onClick={onClose}>취소</button></div>
  </form></Dialog>;
}
