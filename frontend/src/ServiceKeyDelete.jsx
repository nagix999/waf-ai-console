import { useEffect, useState } from "react";
import { api } from "./api.js";
import { serviceKeyError } from "./serviceApiKeys.js";

export default function ServiceKeyDelete({ item, controller, busy, blocked, error, onClose }) {
  const production = item.purpose !== "test";
  const [preview, setPreview] = useState(null), [loadError, setLoadError] = useState("");
  const [reload, setReload] = useState(0), [removeAnalyses, setRemoveAnalyses] = useState(false), [name, setName] = useState("");
  useEffect(() => {
    if (!production) return;
    const request = new AbortController(); setPreview(null); setLoadError(""); setRemoveAnalyses(false); setName("");
    api.serviceApiKeyDeletionPreview(item.id, { signal: request.signal }).then(data => {
      if (!request.signal.aborted) setPreview(data);
    }).catch(err => { if (!request.signal.aborted) setLoadError(serviceKeyError(err)); });
    return () => request.abort();
  }, [item.id, production, reload]);
  const keyName = preview?.name ?? item.name;
  const cannotPurge = Boolean(preview?.active_analyses || preview?.blocked_references);
  async function submit(event) {
    event.preventDefault();
    if (blocked || (production && (!preview || name !== keyName || (removeAnalyses && cannotPurge)))) return;
    if (await controller?.remove({ ...item, name: keyName }, { confirm_name: name, delete_analyses: removeAnalyses, expected_scope: preview?.scope })) onClose();
  }
  return <form className="service-key-form" onSubmit={submit}>
    <p><strong>{keyName}</strong> · {item.source_system}</p>
    <p>키와 키별 대시보드가 삭제되고, 이 키의 API 접근이 차단됩니다.</p>
    {production && <>
      {!preview && !loadError && <p role="status">연결된 분석을 확인하는 중…</p>}
      {loadError && <p className="error" role="alert">{loadError}</p>}
      {preview && <><fieldset disabled={busy} className="service-key-delete-options"><legend>연결된 분석 {preview.analyses.toLocaleString()}건</legend>
        <label><input type="radio" name="analysis-retention" checked={!removeAnalyses} onChange={() => setRemoveAnalyses(false)} />분석 보존</label>
        <label><input type="radio" name="analysis-retention" checked={removeAnalyses} disabled={cannotPurge} onChange={() => setRemoveAnalyses(true)} />분석도 함께 삭제</label>
      </fieldset>
      {preview.active_analyses > 0 && <p className="notice">대기·처리 중 {preview.active_analyses}건 · 완료 후 분석을 삭제할 수 있습니다.</p>}
      {preview.blocked_references && <p className="notice">기존 테스트 또는 다른 키의 분석에서 참조 중인 결과가 있어 분석을 함께 삭제할 수 없습니다.</p>}
      {removeAnalyses && <p className="service-key-warning">이 키로 접수한 분석 {preview.analyses.toLocaleString()}건의 결과·HTTP 원문·Agent 실행 이력·참고 답안·리뷰가 삭제됩니다. 되돌릴 수 없습니다. 감사 이력과 검증 데이터셋 사본{preview.dataset_copies ? ` ${preview.dataset_copies.toLocaleString()}개 버전` : ""}은 보존됩니다. 다른 키의 분석은 삭제하지 않습니다.</p>}
      {!removeAnalyses && <p className="ux-muted">분석 결과와 감사 이력은 그대로 남습니다.</p>}
      <label>확인을 위해 키 이름을 입력하세요<input value={name} onChange={event => setName(event.target.value)} disabled={busy} autoComplete="off" spellCheck={false} maxLength={120} placeholder={keyName} /></label></>}
      <button type="button" className="text-button" disabled={busy} onClick={() => setReload(value => value + 1)}>대상 새로고침</button>
    </>}
    {!production && <p className="service-key-warning">분석 결과와 감사 이력은 보존됩니다. 삭제한 키는 다시 사용할 수 없습니다.</p>}
    {error && <p className="error" role="alert">{error}</p>}
    <div className="service-key-actions"><button type="button" className="secondary" disabled={busy} onClick={onClose}>취소</button><button type="submit" className="primary" disabled={blocked || (production && (!preview || name !== keyName || (removeAnalyses && cannotPurge)))}>{busy ? "삭제 중…" : removeAnalyses ? "키와 분석 삭제" : "API 키 삭제"}</button></div>
  </form>;
}
