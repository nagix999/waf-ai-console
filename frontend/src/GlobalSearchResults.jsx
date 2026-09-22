import { useEffect, useState } from "react";
import { api } from "./api.js";
import Dialog from "./Dialog.jsx";
import DataTable from "./DataTable.jsx";
import Pagination from "./Pagination.jsx";
import { useR5Words, verdictText } from "./R5Evaluation.jsx";

export default function GlobalSearchResults({ search, onClose, onOpen }) {
  const w = useR5Words(), [result, setResult] = useState(null), [error, setError] = useState(false), [offset, setOffset] = useState(0);
  useEffect(() => { if (!search) return; const controller = new AbortController(); setResult(null); setError(false);
    (search.field === "analysis_id" ? api.analysis(search.query, { signal: controller.signal }).then(row => ({ total: 1, items: [row] })) : api.analyses({ search_field: search.field, q: search.query, limit: 20, offset }, { signal: controller.signal }))
      .then(value => { if (!controller.signal.aborted) setResult(value); }).catch(() => { if (!controller.signal.aborted) setError(true); }); return () => controller.abort();
  }, [search, offset]);
  return <Dialog open={Boolean(search)} title={w("전체 검색", "Global Search")} onClose={onClose}><p className="v5-context">{w("검색 결과의 구분에 따라 운영 분석 또는 테스트로 이동합니다.", "Each result opens in its Production or Test context.")}</p>{error ? <p role="alert" className="error">{w("결과를 조회하지 못했습니다.", "Could not load results.")}</p> : !result ? <p role="status">{w("검색 중…", "Searching…")}</p> : <><DataTable label={w("전체 검색 결과", "Global Search results")} data={result.items} columns={[{ id: "event", header: w("이벤트", "Event"), render: row => <button className="text-button" onClick={() => { onClose(); onOpen(row); }}>{row.signature || row.event_name || row.event_id || w("분석 보기", "View analysis")}</button> }, { id: "purpose", header: w("구분", "Purpose"), render: row => row.analysis_purpose === "production" ? "Production" : row.analysis_purpose === "test" ? "Test" : w("이전 기록", "Legacy") }, { id: "verdict", header: w("심층 판정", "Deep Assessment"), render: row => verdictText(row.verdict, w) }]} /><Pagination total={result.total} limit={20} offset={offset} onOffsetChange={setOffset} label={w("검색 결과 페이지", "Search result pages")} /></>}</Dialog>;
}
