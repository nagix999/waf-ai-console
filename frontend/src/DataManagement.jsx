import { useEffect, useRef, useState } from "react";
import DataTable from "./DataTable.jsx";
import Pagination from "./Pagination.jsx";
import { api } from "./api.js";
import Dialog from "./Dialog.jsx";
import { formatDate } from "./analysisView.js";
import { answerNames, reviewNames, validationDataError } from "./validationData.js";
import GroundTruthReview from "./GroundTruthReview.jsx";
import MoreActions from "./MoreActions.jsx";
import DetailTabs from "./DetailTabs.jsx";
import { Icon } from "./Icon.jsx";
import "./validationData.css";

const eventFields = { event_id: "이벤트 ID", company_name: "회사", src_ip: "출발지 IP", dest_ip: "목적지 IP", src_port: "출발지 포트", dest_port: "목적지 포트", waf_vendor: "WAF 종류", waf_action: "WAF 조치", signature: "탐지 규칙", event_name: "이벤트명" };

function ItemEditor({ initial, dataset, onSaved, onBusy, onDirty }) {
  const [tab, setTab] = useState("event");
  const [data, setData] = useState(initial);
  const [form, setForm] = useState(() => editState(initial));
  const [busy, setBusy] = useState(false), [error, setError] = useState("");
  const reviewPending = useRef(false);
  useEffect(() => { onBusy?.(busy); return () => onBusy?.(false); }, [busy, onBusy]);
  const oldVersion = Boolean(data?.id && initial?.id !== data.id);
  const readonly = oldVersion || dataset.revision !== dataset.current_revision;
  const dirty = JSON.stringify(form) !== JSON.stringify(editState(data));
  useEffect(() => { onDirty?.(dirty); return () => onDirty?.(false); }, [dirty, onDirty]);
  function editState(item) {
    const event = item?.event || {};
    return { event: Object.fromEntries([...Object.keys(eventFields), "payload"].map(key => [key, event[key] ?? ""])),
      extras: JSON.stringify(Object.fromEntries(Object.entries(event).filter(([key]) => !Object.hasOwn(eventFields, key) && key !== "payload")), null, 2),
      reference_verdict: item?.reference_verdict || "", comment: item?.comment || "", difficulty: item?.difficulty || "", test_category: item?.test_category || "", case_name: item?.case_name || "" };
  }
  async function version(id) {
    setBusy(true); setError("");
    try { const result = await api.datasetItem(dataset.id, initial.item_id, id); setData(result); setForm(editState(result)); }
    catch (err) { setError(validationDataError(err)); } finally { setBusy(false); }
  }
  async function save(event) {
    event.preventDefault(); setError("");
    let extras;
    try { extras = JSON.parse(form.extras || "{}"); if (!extras || Array.isArray(extras) || typeof extras !== "object") throw new Error(); }
    catch { setError("추가 필드는 JSON 객체로 입력하세요."); return; }
    if (Object.keys(extras).some(key => Object.hasOwn(eventFields, key) || key === "payload")) { setError("추가 필드에 기본 필드를 중복 입력하지 마세요."); return; }
    const document = { ...extras, ...form.event };
    for (const key of ["src_port", "dest_port"]) document[key] = document[key] === "" ? null : Number(document[key]);
    for (const key of ["signature", "event_name"]) if (!document[key]) document[key] = null;
    setBusy(true);
    try {
      await api.saveDatasetItem(dataset.id, initial?.item_id, { expected_revision: dataset.current_revision, event: document,
        reference_verdict: form.reference_verdict || null, comment: form.comment, difficulty: form.difficulty || null, test_category: form.test_category || null, case_name: form.case_name || null });
      onSaved();
    } catch (err) { setError(validationDataError(err)); } finally { setBusy(false); }
  }
  async function review(status) {
    if (reviewPending.current || busy || readonly || dirty) return;
    reviewPending.current = true; setBusy(true); setError("");
    try {
      await api.reviewDatasetItem(dataset.id, initial.item_id, { expected_revision: dataset.current_revision, review_status: status });
      onSaved();
    } catch (err) { setError(validationDataError(err)); }
    finally { reviewPending.current = false; setBusy(false); }
  }
  const change = key => event => setForm(current => ({ ...current, [key]: event.target.value }));
  return <form className="data-form gt-editor" onSubmit={save} onInvalid={event => { event.preventDefault(); const field = event.target; setError("입력 탭의 필수 값과 형식을 확인하세요."); setTab("event"); requestAnimationFrame(() => field.focus()); }}>
    <div className="gt-editor-summary"><span className={`review-badge ${data?.review_status || "draft"}`}>{reviewNames[data?.review_status] || "미검토"}</span><span>{answerNames[data?.reference_verdict] || "답안 없음"}</span><span className="ux-muted">{data?.id ? `버전 ${data.revision}` : "새 문항"}</span></div>
    {readonly && <p className="notice">이전 버전 · 읽기 전용</p>}{data?.internal_only && <p className="notice">운영에서 가져온 문항 · 수정해도 내부 모델 전용으로 유지됩니다.</p>}
    {data?.original_analysis_deleted && <p className="notice">원본 연결: 삭제된 분석 · 이 데이터셋 사본과 답안은 보존됐습니다.</p>}
    <DetailTabs label="검증 문항 상세" items={[["event", "입력"], ["answer", "답안·검토"], ["history", "버전 이력"]]} value={tab} onChange={setTab}>{key => <>
    {key === "event" && <fieldset disabled={busy || readonly} className="data-form">
      <label>문항명<input value={form.case_name} onChange={change("case_name")} maxLength={240} placeholder="분석 목적이나 공격 유형" /></label>
      <div className="event-fields">{Object.entries(eventFields).map(([key, label]) => <label key={key}>{label}{key === "waf_action" ? <select value={form.event[key]} onChange={event => setForm(current => ({ ...current, event: { ...current.event, [key]: event.target.value } }))} required><option value="">선택하세요</option><option value="D">차단 (D)</option><option value="A">허용 (A)</option></select> : <input value={form.event[key]} type={key.endsWith("_port") ? "number" : "text"} min={key.endsWith("_port") ? 0 : undefined} max={key.endsWith("_port") ? 65535 : undefined} onChange={event => setForm(current => ({ ...current, event: { ...current.event, [key]: event.target.value } }))} placeholder={label} />}</label>)}</div>
      <label>HTTP 원문<textarea rows={9} value={form.event.payload} onChange={event => setForm(current => ({ ...current, event: { ...current.event, payload: event.target.value } }))} spellCheck={false} required placeholder="HTTP 요청 또는 WAF에서 수집한 원문" /></label>
      <label>추가 필드 (JSON)<textarea value={form.extras} onChange={change("extras")} rows={4} spellCheck={false} placeholder={'{"필드명": "값"}'} /></label>
      <div className="event-fields"><label>난이도<input value={form.difficulty} onChange={change("difficulty")} maxLength={80} placeholder="예: hard" /></label><label>유형<input value={form.test_category} onChange={change("test_category")} maxLength={120} placeholder="예: SQL Injection" /></label></div>
    </fieldset>}
    {key === "answer" && <div className="data-form"><fieldset disabled={busy || readonly} className="data-form"><label>답안<select aria-label="답안" value={form.reference_verdict} onChange={change("reference_verdict")}><option value="">답안 없음 · 검토 전 입력</option>{Object.entries(answerNames).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
      <label>판단 이유<textarea rows={3} maxLength={4000} value={form.comment} onChange={change("comment")} placeholder="이 답안으로 판단한 이유 또는 확인한 내용" /></label>
      <p className="ux-muted">답안·메모는 모델에 전달하지 않습니다. 원본 분석과 기존 테스트는 변경되지 않습니다.</p>
    </fieldset><GroundTruthReview item={data} readonly={readonly} busy={busy} dirty={dirty} onReview={review} /></div>}
    {key === "history" && <div className="data-form">{initial?.history?.length > 0 ? <><label>문항 버전<select aria-label="문항 버전" value={data.id} onChange={event => version(event.target.value)} disabled={busy || dirty}>{initial.history.map(item => <option key={item.id} value={item.id}>버전 {item.revision} · {reviewNames[item.review_status] || "미검토"} · {formatDate(item.created_at)}</option>)}</select></label><p className="ux-muted">버전을 선택한 뒤 입력·답안 탭에서 당시 내용을 확인하세요. 이전 버전은 수정할 수 없습니다.</p>{dirty && <p className="notice">버전을 바꾸려면 작성 중인 내용을 먼저 저장하세요.</p>}</> : <p className="ux-muted">저장 후 버전 이력이 남습니다.</p>}</div>}
    </>}</DetailTabs>
    <div className="editor-footer">{!readonly && <button type="submit" className="primary" disabled={busy}>{busy ? "저장 중…" : data?.id ? "수정본 저장" : "문항 저장"}</button>}<span className="ux-muted">저장과 승인은 별도입니다. 수정본은 미검토로 저장합니다.</span></div>{error && <p role="alert" className="error">{error}</p>}
  </form>;
}

export default function DataManagement({ id, onSelect }) {
  const [search, setSearch] = useState("");
  const [data, setData] = useState(null), [error, setError] = useState(""), [reload, setReload] = useState(0);
  const [query, setQuery] = useState({ offset: 0, revision: "", review_status: "" }), [busy, setBusy] = useState(false);
  const [modal, setModal] = useState(null), [name, setName] = useState(""), [description, setDescription] = useState("");
  const [dirty, setDirty] = useState(false), [discard, setDiscard] = useState(false);
  useEffect(() => { setQuery({ offset: 0, revision: "", review_status: "" }); setSearch(""); setModal(null); }, [id]);
  useEffect(() => {
    const controller = new AbortController(); setData(null); setError("");
    const params = { limit: 50, offset: query.offset, ...(query.revision ? { revision: query.revision } : {}), ...(id && query.review_status ? { review_status: query.review_status } : {}) };
    const request = id ? api.validationDataset(id, params, { signal: controller.signal }) : query.q ? api.searchValidationDatasets({ query: query.q, limit: 50, offset: query.offset }, { signal: controller.signal }) : api.validationDatasets(params, { signal: controller.signal });
    request.then(result => { if (!controller.signal.aborted) setData(result); }).catch(err => { if (!controller.signal.aborted) setError(validationDataError(err)); });
    return () => controller.abort();
  }, [id, query, reload]);
  function refreshed() { setModal(null); setQuery(current => ({ ...current, offset: 0, revision: "" })); setReload(value => value + 1); }
  async function action(callback) { setBusy(true); setError(""); try { await callback(); } catch (err) { setError(validationDataError(err)); } finally { setBusy(false); } }
  async function saveDataset(event) {
    event.preventDefault(); await action(async () => {
      const payload = { name: name.trim(), description };
      if (modal.kind === "rename") { await api.updateValidationDataset(id, { ...payload, expected_revision: data.current_revision }); refreshed(); }
      else { const result = await api.createValidationDataset(payload); setModal(null); onSelect(result.id); }
    });
  }
  const historical = id && data && data.revision !== data.current_revision;
  return <div className="page-stack">
    <div className="ux-toolbar">{id && <button className="back" type="button" disabled={busy || dirty} onClick={() => onSelect(null)}>← 데이터셋 목록</button>}<span className="ux-grow" /><button className="secondary" type="button" disabled={busy || dirty} onClick={() => setReload(value => value + 1)}>새로고침</button>{!id && <button className="primary" type="button" onClick={() => { setName(""); setDescription(""); setModal({ kind: "create" }); }}>데이터셋 만들기</button>}</div>
    {!id && <form className="dataset-search" onSubmit={event => { event.preventDefault(); setQuery(current => ({ ...current, q: search.trim(), offset: 0 })); }}><label>데이터셋명 검색<input value={search} onChange={event => setSearch(event.target.value)} maxLength={120} placeholder="데이터셋 이름" /></label><button type="submit" className="secondary">검색</button>{query.q && <button type="button" className="text-button" onClick={() => { setSearch(""); setQuery(current => ({ ...current, q: "", offset: 0 })); }}>검색 초기화</button>}</form>}
    {error && <p className="error" role="alert">{error}</p>}{!data && !error && <p role="status">불러오는 중…</p>}
    {data && !id && <section className="v5-section"><DataTable label="Ground Truth 데이터셋" data={data.items} columns={[
      { id: "name", header: "데이터셋", render: item => <button type="button" className="text-button" onClick={() => onSelect(item.id)}>{item.name}</button> },
      { id: "description", header: "설명", render: item => <span className="v5-clamp" title={item.description}>{item.description || "—"}</span> },
      { id: "approved", header: "승인 / 전체", render: item => `${item.review_counts?.approved ?? "—"} / ${item.total}` },
      { id: "revision", header: "버전", render: item => `r${item.revision}` },
      { id: "scope", header: "전송 범위", render: item => item.internal_only ? "내부 모델 전용 문항 포함" : "선택한 테스트 모델" },
    ]} empty="데이터셋을 만든 뒤 문항을 입력하거나 분석 결과에서 가져오세요." /></section>}
    {data && id && <><section className="panel"><div className="panel-head"><div><h2>{data.name}</h2><p className="ux-muted">{data.description}</p></div><span className="gt-approved-count">승인 {data.review_counts?.approved ?? "—"} / 전체 {data.total}문항</span></div><div className="dataset-version-bar"><label>버전<select disabled={busy || dirty} value={query.revision || data.current_revision} onChange={event => setQuery({ offset: 0, revision: event.target.value })}>{data.versions.map(version => <option key={version.id} value={version.revision}>버전 {version.revision} · {formatDate(version.created_at)}</option>)}</select></label><span className="ux-grow" /><MoreActions label="데이터셋 관리" disabled={busy || dirty}><button type="button" className="secondary" disabled={historical || busy || dirty} onClick={() => { setName(data.name); setDescription(data.description); setModal({ kind: "rename" }); }}>이름·설명 수정</button><button type="button" className="secondary" disabled={historical || busy || dirty} onClick={() => setModal({ kind: "delete" })}>데이터셋 삭제</button></MoreActions><button type="button" className="primary" disabled={historical || busy || dirty} onClick={() => setModal({ kind: "item", item: null })}>문항 추가</button></div>{historical && <p className="notice">이전 버전은 읽기 전용입니다. 문항을 눌러 당시 입력을 확인하세요.</p>}</section>
      <div className={`v5-ground-truth-workbench ${modal?.kind === "item" ? "has-detail" : ""}`}><section className="panel gt-case-table"><div className="review-filters" role="group" aria-label="검토 상태 필터">
        {Object.entries({ "": "전체", ...reviewNames }).map(([status, label]) => <button type="button" key={status} className="secondary" aria-pressed={(query.review_status || "") === status} disabled={busy || dirty}
          onClick={() => setQuery(current => ({ ...current, offset: 0, review_status: status }))}>{label} {status ? data.review_counts?.[status] ?? "—" : data.total}</button>)}
      </div><DataTable label="검증 데이터셋 문항" data={data.items} columns={[
        { id: "case_name", header: "문항", width: "30%", render: (item, index) => <button type="button" className="text-button" aria-current={modal?.item?.item_id === item.item_id ? "true" : undefined} disabled={busy || dirty} onClick={() => action(async () => { const result = await api.datasetItem(id, item.item_id, item.id); setModal({ kind: "item", item: result }); })}>{item.case_name || `문항 ${query.offset + index + 1}`}</button> },
        { id: "category", header: "난이도 / 유형", render: item => <>{item.difficulty || "미분류"} / {item.test_category || "미분류"}</> },
        { id: "reference", header: "답안", render: item => answerNames[item.reference_verdict] || "답안 없음" },
        { id: "review", header: "검토 상태", render: item => <span className={`review-badge ${item.review_status || "draft"}`}>{reviewNames[item.review_status] || "미검토"}</span> },
        { id: "scope", header: "전송 범위", render: item => item.internal_only ? "내부 모델 전용" : "설정한 Test 모델" },
        { id: "revision", header: "버전 / 저장 시각", render: item => <><span>버전 {item.revision ?? "—"}</span><small>{formatDate(item.created_at)}</small></> },
        { id: "actions", header: <span className="sr-only">작업</span>, width: 70, render: item => <MoreActions label={`${item.case_name || "문항"} 관리`} disabled={busy || dirty}><button type="button" disabled={historical || busy || dirty} onClick={() => setModal({ kind: "remove", item })}>문항 삭제</button></MoreActions> },
      ].filter(column => modal?.kind !== "item" || ["case_name", "reference", "review", "actions"].includes(column.id))} empty={query.review_status ? "이 상태의 문항이 없습니다." : "등록된 문항이 없습니다."} /></section>
      {modal?.kind === "item" && <aside className="v5-workbench-detail" aria-label="검증 문항 상세"><div className="v5-section-head"><h2>검증 문항</h2><button type="button" className="icon-button" disabled={busy} aria-label="검증 문항 닫기" onClick={() => dirty ? setDiscard(true) : setModal(null)}><Icon name="close" size={18} /></button></div><ItemEditor key={modal.item?.id || "new"} initial={modal.item} dataset={data} onSaved={refreshed} onBusy={setBusy} onDirty={setDirty} />{dirty && <p className="ux-muted">수정 중인 내용을 저장하거나 닫은 뒤 다른 문항을 선택하세요.</p>}</aside>}</div></>}
    {data && <Pagination label={id ? "데이터셋 문항 페이지" : "데이터셋 목록 페이지"} total={id ? data.filtered_total ?? data.total : data.total} limit={50} offset={query.offset} disabled={busy || dirty} onOffsetChange={offset => setQuery(current => ({ ...current, offset }))} />}
    <Dialog open={Boolean(modal) && modal.kind !== "item"} title={["delete", "remove"].includes(modal?.kind) ? "삭제 확인" : "데이터셋 정보"} onClose={() => { if (!busy) setModal(null); }}>
      {["create", "rename"].includes(modal?.kind) && <form className="data-form" onSubmit={saveDataset}><label>데이터셋명<input value={name} onChange={event => setName(event.target.value)} maxLength={120} required placeholder="예: 운영 오탐 재검증" /></label><label>설명<textarea value={description} onChange={event => setDescription(event.target.value)} rows={3} maxLength={1000} placeholder="문항을 모으는 목적" /></label><button type="submit" className="primary" disabled={busy || !name.trim()}>저장</button></form>}
      {["delete", "remove"].includes(modal?.kind) && <><p>{modal.kind === "delete" ? "데이터셋을 목록에서 삭제할까요?" : "이 문항을 데이터셋에서 삭제할까요?"}</p><p className="ux-muted">원본 분석, 이전 버전과 이미 접수한 테스트는 보존됩니다.</p><button type="button" className="primary danger" disabled={busy} onClick={() => action(async () => { if (modal.kind === "delete") { await api.deleteValidationDataset(id, data.current_revision); setModal(null); onSelect(null); } else { await api.deleteDatasetItem(id, modal.item.item_id, data.current_revision); refreshed(); } })}>삭제</button></>}
      {error && modal && <p className="error" role="alert">{error}</p>}
    </Dialog>
    <Dialog open={discard} title="수정 내용 버리기" onClose={() => setDiscard(false)}><p>저장하지 않은 수정 내용만 버립니다. 기존 문항은 유지됩니다.</p><div className="action-row"><button className="secondary" onClick={() => setDiscard(false)}>계속 작성</button><button className="primary danger" onClick={() => { setModal(null); setDiscard(false); }}>수정 내용 버리기</button></div></Dialog>
  </div>;
}
