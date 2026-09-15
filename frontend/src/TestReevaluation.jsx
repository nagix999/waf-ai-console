import { useEffect, useRef, useState } from "react";
import { api } from "./api.js";
import Dialog from "./Dialog.jsx";
import { newTestRequestKey } from "./testRuns.js";
import { validationDataError } from "./validationData.js";
import { formatDate } from "./analysisView.js";

export default function TestReevaluation({ run, value, onChange }) {
  const [items, setItems] = useState([]), [open, setOpen] = useState(false), [busy, setBusy] = useState(false), [error, setError] = useState("");
  const [reload, setReload] = useState(0); const key = useRef(null);
  useEffect(() => { let active = true; api.testEvaluations(run.id).then(result => { if (active) setItems(result.items); }).catch(err => { if (active) setError(validationDataError(err)); }); return () => { active = false; }; }, [run.id, reload]);
  async function rescore() { setBusy(true); setError(""); key.current ||= newTestRequestKey(); try { const result = await api.rescoreTest(run.id, key.current); setOpen(false); setReload(v => v + 1); onChange(result.id); } catch (err) { setError(validationDataError(err)); } finally { setBusy(false); } }
  return <div className="dataset-version-bar"><label>평가 기준<select value={value || ""} onChange={event => onChange(event.target.value)}><option value="">접수 당시 답안</option>{items.map(item => <option key={item.id} value={item.id}>재평가 {item.revision} · {formatDate(item.created_at)}</option>)}</select></label><button type="button" className="secondary" disabled={run.pending > 0 || run.processing > 0 || run.accepting_items || busy} onClick={() => { key.current = null; setOpen(true); }}>수정 답안으로 재평가</button>{error && !open && <p role="alert" className="error">{error}</p>}<Dialog open={open} title="수정 답안으로 재평가" onClose={() => { if (!busy) setOpen(false); }}><p>이 테스트의 모든 문항을 현재 참고 답안과 다시 비교합니다. 기존 평가 기록은 유지됩니다.</p><p className="ux-muted">판정은 다시 실행하지 않습니다. LLM 호출과 비용은 없습니다.</p>{error && <p className="error" role="alert">{error}</p>}<button type="button" className="primary" disabled={busy} onClick={rescore}>{busy ? "비교 중…" : "새 평가 저장"}</button></Dialog></div>;
}
