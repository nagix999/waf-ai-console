import { useEffect, useState } from "react";
import { api } from "./api.js";
import DataTable from "./DataTable.jsx";
import Pagination from "./Pagination.jsx";
import TextInspector from "./TextInspector.jsx";
import { useWords } from "./LifecycleViews.jsx";
import { validationDataError } from "./validationData.js";

// History stays read-only. Opening encrypted input uses the audited item API.
export default function DatasetRevisionBrowser({ datasetId, versions }) {
  const w = useWords();
  const [revision, setRevision] = useState(versions[0]?.revision), [offset, setOffset] = useState(0);
  const [data, setData] = useState(null), [item, setItem] = useState(null), [error, setError] = useState("");
  const [selected, setSelected] = useState(null);
  useEffect(() => {
    if (!revision) return;
    const controller = new AbortController(); setData(null); setItem(null); setSelected(null); setError("");
    api.validationDataset(datasetId, { revision, offset, limit: 25 }, { signal: controller.signal })
      .then(value => { if (!controller.signal.aborted) setData(value); })
      .catch(e => { if (!controller.signal.aborted) setError(validationDataError(e)); });
    return () => controller.abort();
  }, [datasetId, revision, offset]);
  useEffect(() => {
    if (!selected) return;
    let active = true; setItem(null); setError("");
    api.datasetItem(datasetId, selected.item_id, selected.id)
      .then(value => { if (active) setItem(value); })
      .catch(e => { if (active) setError(validationDataError(e)); });
    return () => { active = false; };
  }, [datasetId, selected]);
  const verdict = { true_positive: w("정탐", "True positive"), false_positive: w("오탐", "False positive"), inconclusive: w("보류", "Inconclusive") };
  return <section className="r3-history-detail">
    <label>{w("내용 확인 · 읽기 전용", "Inspect revision · read only")}<select value={revision || ""} onChange={e => { setRevision(Number(e.target.value)); setOffset(0); }}>{versions.map(v => <option key={v.id} value={v.revision}>r{v.revision} · {v.total} {w("문항", "cases")}</option>)}</select></label>
    {error && <p role="alert" className="error">{error}</p>}
    {data && <><DataTable label={w("리비전 문항", "Revision cases")} data={data.items} columns={[
      { id: "name", header: w("문항", "Case"), render: row => <button className="text-button" onClick={() => setSelected(row)}>{row.case_name || w("이름 없는 문항", "Untitled case")}</button> },
      { id: "answer", header: w("답안", "Answer"), render: row => verdict[row.reference_verdict] || "—" },
      { id: "source", header: w("출처", "Source"), render: row => row.internal_only ? w("내부 운영", "Internal Production") : row.source_kind === "synthetic_expected" ? w("기대 답안", "Expected answer") : w("참고 답안", "Reference answer") },
    ]} /><Pagination total={data.filtered_total} offset={offset} limit={25} onOffsetChange={setOffset} /></>}
    {item && <div className="r3-history-detail"><h3>{item.case_name || w("문항 상세", "Case details")}</h3><p>{verdict[item.reference_verdict] || "—"} · {item.comment || w("메모 없음", "No note")}</p><TextInspector label={w("당시 입력", "Saved input")} value={item.event} /><TextInspector label={w("당시 필드 정의", "Saved field definitions")} value={item.field_metadata || item.input_schema} /></div>}
  </section>;
}
