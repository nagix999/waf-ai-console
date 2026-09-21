import { useEffect, useRef, useState } from "react";
import { api } from "./api.js";
import Dialog from "./Dialog.jsx";
import { newTestRequestKey } from "./testRuns.js";
import { validationDataError } from "./validationData.js";
import { formatDate } from "./analysisView.js";

export default function TestReevaluation({ run, value, onChange }) {
  const [items, setItems] = useState([]), [open, setOpen] = useState(false), [busy, setBusy] = useState(false), [error, setError] = useState("");
  const [reload, setReload] = useState(0); const key = useRef(null);
  useEffect(() => { let active = true; api.testEvaluations(run.id).then(result => { if (active) setItems(result.items); }).catch(err => { if (active) setError(validationDataError(err)); }); return () => { active = false; }; }, [run.id, reload, run.official_evaluation_pending]);
  async function rescore() { setBusy(true); setError(""); key.current ||= newTestRequestKey(); try { const result = await api.rescoreTest(run.id, key.current); setOpen(false); setReload(v => v + 1); onChange(result.id); } catch (err) { setError(validationDataError(err)); } finally { setBusy(false); } }
  const waiting = run.pending > 0 || run.processing > 0 || run.accepting_items;
  if (run.evaluation_mode === "ground_truth") return <div className="dataset-version-bar">
    <label>공식 평가 기록<select aria-label="공식 평가 기록" value={!value || ["latest", "initial"].includes(value) ? "initial" : value} onChange={event => onChange(event.target.value)}>
      <option value="initial">현재 결과 · 승인 답안 고정</option>{items.filter(item => item.evaluation_kind === "ground_truth").map(item => <option key={item.id} value={item.id}>평가 {item.revision} · {formatDate(item.created_at)}</option>)}
    </select></label><span className="ux-muted">{value && !["latest", "initial"].includes(value) ? "저장 당시 결과입니다. 이후 재실행 결과는 이 기록을 바꾸지 않습니다." : waiting ? "판정 진행 중 · 완료 후 평가 기록을 자동 저장합니다." : run.official_evaluation_pending ? "평가 기록을 저장하는 중입니다. 잠시 후 새로고침됩니다." : "평가 기록 저장됨 · 답안을 수정하려면 데이터 관리에서 새 버전을 검토·승인하고 다시 실행하세요."}</span>
    {error && <p role="alert" className="error">{error}</p>}
  </div>;
  return <div className="dataset-version-bar">
    <label>참고 답안<select aria-label="참고 답안 기준" value={value || "latest"} onChange={event => onChange(event.target.value)}>
      <option value="latest">최신 답안</option><option value="initial">접수 당시 답안</option>
      {items.map(item => <option key={item.id} value={item.id}>저장한 평가 {item.revision} · {formatDate(item.created_at)}</option>)}
    </select></label>
    <button type="button" className="secondary" disabled={waiting || busy} onClick={() => { key.current = null; setOpen(true); }}>평가 기록 저장</button>
    <span className="ux-muted">{value && !["latest", "initial"].includes(value) ? "저장 당시 결과와 답안을 보는 중입니다. 재실행 결과는 ‘최신 답안’에서 확인하세요." : waiting ? "최신 점수는 자동 반영됩니다. 접수를 마치고 분석이 완료되면 평가 기록을 저장할 수 있습니다." : value === "initial" ? "접수 당시 답안과 최신 실행 결과를 비교합니다." : "답안 수정과 재실행 결과를 점수에 자동 반영합니다."}</span>
    {error && !open && <p role="alert" className="error">{error}</p>}
    <Dialog open={open} title="평가 기록 저장" onClose={() => { if (!busy) setOpen(false); }}>
      <p>이 테스트 전체의 현재 결과와 참고 답안으로 평가 기록을 저장합니다. 이후 답안을 수정하거나 재실행해도 저장한 기록은 유지됩니다.</p>
      <p className="ux-muted">판정은 다시 실행하지 않습니다. LLM 호출과 비용은 없습니다.</p>
      {error && <p className="error" role="alert">{error}</p>}
      <button type="button" className="primary" disabled={busy} onClick={rescore}>{busy ? "저장 중…" : "저장"}</button>
    </Dialog>
  </div>;
}
