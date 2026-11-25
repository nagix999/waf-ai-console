import { useEffect, useRef, useState } from "react";
import { api } from "./api.js";
import { useConsolePreferences } from "./consolePreferences.jsx";
import DataTable from "./DataTable.jsx";
import Pagination from "./Pagination.jsx";
import Dialog from "./Dialog.jsx";
import NavigationAction from "./NavigationAction.jsx";
import { Icon } from "./Icon.jsx";
import { emptySignatureExplanation, filteredSignatureEntries, signatureDraftIssue, signatureEntryIssue,
  signatureExplanationDraft, signatureExplanationLimits, signatureVersionPayload } from "./signatureExplanations.js";
import "./signatureExplanations.css";
import { SettingsSectionHeader } from "./SettingsWorkspace.jsx";

const useWords = () => { const { locale } = useConsolePreferences(); return (ko, en) => locale === "en" ? en : ko; };
const date = value => value ? new Date(value).toLocaleString() : "—";
const versionLabel = version => `v${version.version_no} · ${version.name}`;
const issueWords = {
  required: ["벤더, 시그니처, 설명을 모두 입력하세요.", "Enter the vendor, signature and explanation."],
  version_required: ["설명집 이름과 변경 사유를 입력하세요.", "Enter the collection name and change reason."],
  duplicate: ["같은 벤더와 시그니처가 이미 있습니다. 기존 항목을 수정하세요.", "This vendor and signature already exist. Edit the existing entry."],
  length: ["항목 수 또는 입력 길이 제한을 확인하세요.", "Check the entry count and field length limits."],
  bytes: ["설명집 전체 크기는 256 KiB 이하여야 합니다. 설명을 줄이거나 항목을 제외하세요.", "The collection must fit within 256 KiB. Shorten explanations or remove entries."],
};

export function SignatureExplanationNotice() {
  const w = useWords();
  return <div className="signature-catalog-note"><Icon name="shield" size={19} /><p><strong>{w("설명 저장은 운영 반영이 아닙니다.", "Saving does not change Production.")}</strong>{w("테스트에서 설명집을 선택하고 공식 평가와 운영 반영 검토를 거쳐 적용합니다. 설명은 탐지 의도를 해석하는 참고 정보이며, 개별 요청의 정답이나 공격 근거가 아닙니다.", "Select a collection in a Test, then apply it through official evaluation and Production Review. Explanations provide detection context, not verdict labels or observed attack evidence.")}<br />{w("매칭한 설명도 선택한 모델로 전달됩니다. OpenAI를 사용하면 외부로 전송됩니다.", "Matched explanations are also sent to the selected model. Using OpenAI sends them outside your network.")}</p></div>;
}

export function SignatureExplanationContent({ entry }) {
  const w = useWords();
  if (!entry) return null;
  return <div className="page-stack"><dl className="signature-entry-facts"><div><dt>{w("벤더", "Vendor")}</dt><dd>{entry.waf_vendor}</dd></div><div><dt>{w("시그니처", "Signature")}</dt><dd>{entry.signature}</dd></div></dl>
    <section className="signature-entry-section"><h3>{w("시그니처 설명", "Signature explanation")}</h3><p className="signature-entry-copy">{entry.signature_explanation}</p></section>
    {entry.reference_source && <section className="signature-entry-section"><h3>{w("참고 출처", "Reference source")}</h3><p className="signature-entry-copy signature-entry-source">{entry.reference_source}</p></section>}</div>;
}

export function SignatureExplanationEntryForm({ value, onChange, onSubmit, onCancel, onRemove, error, inputRef }) {
  const w = useWords(), change = (key, next) => onChange({ ...value, [key]: next });
  return <form className="signature-entry-form" onSubmit={onSubmit}>
    <div className="signature-entry-grid"><label>{w("벤더", "Vendor")}<input ref={inputRef} required maxLength={120} value={value.waf_vendor} onChange={e => change("waf_vendor", e.target.value)} placeholder={w("이벤트의 waf_vendor 값", "The event's waf_vendor value")} /></label>
      <label>{w("시그니처", "Signature")}<input required maxLength={1000} value={value.signature} onChange={e => change("signature", e.target.value)} placeholder={w("이벤트의 signature 값과 정확히 일치", "Exact event signature value")} /></label></div>
    <label><span className="signature-form-count"><span>{w("시그니처 설명", "Signature explanation")}</span><small>{value.signature_explanation.length.toLocaleString()} / {signatureExplanationLimits.explanation.toLocaleString()}</small></span><textarea required maxLength={signatureExplanationLimits.explanation} value={value.signature_explanation} onChange={e => change("signature_explanation", e.target.value)} placeholder={w("공격 방식, 탐지 의도, 해석 시 주의할 점을 작성하세요.", "Describe the attack method, detection intent and interpretation limits.")} /><small>{w("실제 로그, 인증정보, 정오탐 답안이나 모델에 대한 지시문을 넣지 마세요.", "Do not include real logs, credentials, verdict labels or instructions to the model.")}</small></label>
    <label>{w("참고 출처 (선택)", "Reference source (optional)")}<input maxLength={signatureExplanationLimits.source} value={value.reference_source} onChange={e => change("reference_source", e.target.value)} placeholder={w("벤더 문서명 또는 URL. 분석 중 자동 접속하지 않습니다.", "Vendor document title or URL. Never fetched during analysis.")} /></label>
    {error && <p role="alert" className="error">{w(...(issueWords[error] || issueWords.required))}</p>}
    <div className="signature-dialog-actions">{onRemove && <button type="button" className="danger" onClick={onRemove}>{w("변경안에서 제외", "Remove from draft")}</button>}<button type="button" className="secondary" onClick={onCancel}>{w("취소", "Cancel")}</button><button type="submit" className="primary">{w("변경안에 반영", "Update draft")}</button></div>
  </form>;
}

export function SignatureExplanationEntries({ entries, editing, disabled = false, onOpen, vendor, query, onVendorChange, onQueryChange, offset, onOffsetChange, limit = 10, onLimitChange }) {
  const w = useWords(), filtered = filteredSignatureEntries(entries, vendor, query);
  const vendors = [...new Set(entries.map(entry => entry.waf_vendor))].sort((left, right) => left.localeCompare(right));
  return <><div className="signature-catalog-filters"><label>{w("벤더", "Vendor")}<select value={vendor} onChange={e => onVendorChange(e.target.value)}><option value="">{w("전체 벤더", "All vendors")}</option>{vendors.map(value => <option key={value} value={value}>{value}</option>)}</select></label>
    <label>{w("검색", "Search")}<input type="search" maxLength={500} value={query} onChange={e => onQueryChange(e.target.value)} placeholder={w("시그니처 또는 설명 검색", "Search signatures or explanations")} /></label></div>
    <DataTable label={w("시그니처 설명 목록", "Signature explanations")} data={filtered.slice(offset, offset + limit)} empty={entries.length ? w("조건에 맞는 설명이 없습니다.", "No explanations match these filters.") : w("아직 등록한 설명이 없습니다.", "No explanations have been added.")} columns={[
      { id: "waf_vendor", header: w("벤더", "Vendor"), width: "16%", minWidth: 140 },
      { id: "signature", header: w("시그니처", "Signature"), width: "31%", minWidth: 200, render: row => <button type="button" disabled={disabled} className="text-button signature-catalog-signature" onClick={() => onOpen(row._index)}>{row.signature}</button> },
      { id: "signature_explanation", header: w("설명", "Explanation"), minWidth: 280, render: row => <span className="signature-catalog-preview">{row.signature_explanation}</span> },
      { id: "open", header: w("상세", "Details"), width: "90px", minWidth: 90, render: row => <button type="button" disabled={disabled} className="secondary" aria-label={`${row.signature} ${editing ? w("수정", "edit") : w("상세 보기", "details")}`} onClick={() => onOpen(row._index)}>{editing ? w("수정", "Edit") : w("보기", "View")}</button> },
    ]} /><Pagination total={filtered.length} limit={limit} offset={offset} onOffsetChange={onOffsetChange} onLimitChange={onLimitChange} pageSizes={[10, 25, 50]} disabled={disabled} label={w("설명 목록", "Explanation list")} /></>;
}

export default function SignatureExplanations() {
  const w = useWords(), mounted = useRef(true), pending = useRef(false), entryFocus = useRef(null), matchRequest = useRef(null);
  const [catalog, setCatalog] = useState(null), [catalogOffset, setCatalogOffset] = useState(0), [reload, setReload] = useState(0), [loading, setLoading] = useState(true), [error, setError] = useState("");
  const [selectedId, setSelectedId] = useState(""), [version, setVersion] = useState(null), [versionLoading, setVersionLoading] = useState(false), [versionError, setVersionError] = useState(false);
  const [draft, setDraft] = useState(null), [busy, setBusy] = useState(false), [notice, setNotice] = useState("");
  const [vendor, setVendor] = useState(""), [query, setQuery] = useState(""), [offset, setOffset] = useState(0), [limit, setLimit] = useState(10);
  const [entry, setEntry] = useState(null), [entryIndex, setEntryIndex] = useState(-1), [entryError, setEntryError] = useState(""), [viewEntry, setViewEntry] = useState(null), [metadataOpen, setMetadataOpen] = useState(false), [discardOpen, setDiscardOpen] = useState(false);
  const [matchOpen, setMatchOpen] = useState(false), [matchVendor, setMatchVendor] = useState(""), [matchSignature, setMatchSignature] = useState(""), [matching, setMatching] = useState(false), [matchResult, setMatchResult] = useState(null), [matchError, setMatchError] = useState(false);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; matchRequest.current?.abort(); }; }, []);
  useEffect(() => {
    const controller = new AbortController(); setLoading(true); setError("");
    api.signatureExplanations({ limit: 25, offset: catalogOffset }, { signal: controller.signal }).then(value => {
      if (controller.signal.aborted) return; setCatalog(value); setSelectedId(current => current || value.active_version_id || value.items[0]?.id || ""); setLoading(false);
    }).catch(() => { if (!controller.signal.aborted) { setError("load"); setLoading(false); } });
    return () => controller.abort();
  }, [catalogOffset, reload]);
  useEffect(() => {
    const controller = new AbortController(); setVersion(null); setVersionError(false); setVendor(""); setQuery(""); setOffset(0);
    if (!selectedId) { setVersionLoading(false); return () => controller.abort(); }
    setVersionLoading(true);
    api.signatureExplanation(selectedId, { signal: controller.signal }).then(value => { if (!controller.signal.aborted) { setVersion(value); setVersionLoading(false); } })
      .catch(() => { if (!controller.signal.aborted) { setVersionError(true); setVersionLoading(false); } });
    return () => controller.abort();
  }, [selectedId, reload]);
  function startDraft(base) { setDraft(signatureExplanationDraft(base)); setVendor(""); setQuery(""); setOffset(0); setError(""); setNotice(""); }
  function openEntry(index) { if (!draft) { setViewEntry(version.entries[index]); return; } setEntryIndex(index); setEntry({ ...draft.entries[index] }); setEntryError(""); }
  function addEntry() { setEntryIndex(-1); setEntry(emptySignatureExplanation()); setEntryError(""); }
  function applyEntry(event) {
    event.preventDefault(); const issue = signatureEntryIssue(entry, draft.entries, entryIndex); if (issue) { setEntryError(issue); return; }
    const value = { ...entry, waf_vendor: entry.waf_vendor.trim(), signature: entry.signature.trim(), signature_explanation: entry.signature_explanation.trim(), reference_source: entry.reference_source.trim() };
    setDraft(current => ({ ...current, entries: entryIndex < 0 ? [...current.entries, value] : current.entries.map((old, index) => index === entryIndex ? value : old) }));
    setEntry(null); setVendor(""); setQuery(""); setOffset(0); setError("");
  }
  function removeEntry() { setDraft(current => ({ ...current, entries: current.entries.filter((_, index) => index !== entryIndex) })); setEntry(null); setOffset(0); setVendor(""); setError(""); }
  async function saveVersion() {
    const issue = signatureDraftIssue(draft); if (issue) { setError(issue); return; } if (pending.current) return;
    pending.current = true; setBusy(true); setError("");
    try {
      const value = await api.createSignatureExplanation(signatureVersionPayload(draft));
      if (mounted.current) { setDraft(null); setSelectedId(value.id); setVersion(value); setCatalogOffset(0); setReload(count => count + 1); setNotice("saved"); }
    } catch (failure) { if (mounted.current) setError(failure.message === "duplicate_signature_explanation" ? "duplicate" : failure.message === "signature_explanation_catalog_size_limit" ? "bytes" : "save"); }
    finally { pending.current = false; if (mounted.current) setBusy(false); }
  }
  async function checkMatch(event) {
    event.preventDefault(); if (matching || !selectedId || !matchVendor.trim() || !matchSignature.trim()) return;
    matchRequest.current?.abort(); const controller = new AbortController(); matchRequest.current = controller; setMatching(true); setMatchError(false); setMatchResult(null);
    try { const result = await api.matchSignatureExplanation({ version_id: selectedId, waf_vendor: matchVendor.trim(), signature: matchSignature.trim() }, { signal: controller.signal }); if (!controller.signal.aborted) setMatchResult(result); }
    catch { if (!controller.signal.aborted) setMatchError(true); }
    finally { if (!controller.signal.aborted) setMatching(false); }
  }
  function closeMatch() { matchRequest.current?.abort(); setMatchOpen(false); setMatching(false); setMatchResult(null); setMatchError(false); }
  const entries = draft?.entries || version?.entries || [], versions = catalog?.items || [];
  const draftError = draft ? signatureDraftIssue(draft) : "";
  return <div className="page-stack signature-catalog settings-workspace">
    <SettingsSectionHeader title={w("시그니처 설명", "Signature explanations")} actions={<><button type="button" className="secondary" disabled={busy || Boolean(draft)} onClick={() => setReload(value => value + 1)}>{w("새로고침", "Refresh")}</button><button type="button" className="primary" disabled={busy || Boolean(draft)} onClick={() => startDraft(null)}>{w("새 설명집", "New collection")}</button></>} />
    <div className="signature-catalog-bar"><label className="signature-catalog-picker">{w("설명집 버전", "Explanation collection")}<select value={selectedId} disabled={loading || busy || Boolean(draft)} onChange={event => { setSelectedId(event.target.value); setNotice(""); }}><option value="">{w("버전 선택", "Select version")}</option>{selectedId && !versions.some(item => item.id === selectedId) && <option value={selectedId}>{version ? versionLabel(version) : w("선택한 버전", "Selected version")}</option>}{versions.map(item => <option key={item.id} value={item.id}>{versionLabel(item)}{item.id === catalog.active_version_id ? w(" · 운영 사용 중", " · Production") : ""}</option>)}</select></label>
      <span className="ux-muted">{version ? w(`${version.entry_count ?? version.entries?.length ?? 0}개 설명`, `${version.entry_count ?? version.entries?.length ?? 0} explanations`) : "—"}</span></div>
    {catalog && catalog.total > 25 && <Pagination total={catalog.total} limit={25} offset={catalogOffset} disabled={busy || Boolean(draft)} onOffsetChange={setCatalogOffset} label={w("버전 목록", "Collection versions")} />}
    <SignatureExplanationNotice />
    {loading && <p role="status">{w("설명집을 불러오는 중입니다…", "Loading collections…")}</p>}
    {notice && <p role="status" className="notice">{w("새 버전을 저장했습니다. 운영 설정은 변경되지 않았습니다.", "New version saved. Production settings are unchanged.")}</p>}
    {error && <p role="alert" className="error">{issueWords[error] ? w(...issueWords[error]) : error === "save" ? w("저장하지 못했습니다. 중복 항목과 입력 제한을 확인한 뒤 다시 시도하세요.", "Could not save. Check duplicate entries and input limits, then retry.") : w("설명집 목록을 불러오지 못했습니다. 다시 조회하세요.", "Could not load collections. Please refresh.")}</p>}
    {versionError && <p role="alert" className="error">{w("선택한 버전을 불러오지 못했습니다. 다시 조회하세요.", "Could not load the selected version. Please refresh.")}</p>}
    {versionLoading && <p role="status">{w("선택한 버전을 불러오는 중입니다…", "Loading the selected version…")}</p>}
    {!loading && !versionLoading && !versionError && !version && !draft && <section className="panel signature-catalog-empty"><p>{w("벤더와 시그니처에 맞는 설명을 등록하세요.", "Add explanations matched to each vendor and signature.")}</p><p>{w("등록된 설명이 없어도 기존 분석은 그대로 진행됩니다.", "Analyses continue normally without a matching explanation.")}</p></section>}
    {(draft || version) && !versionLoading && <section className="panel"><header className="panel-head"><div><h2>{draft ? w("설명집 변경안", "Collection draft") : version.name}</h2><p className="ux-muted">{draft ? w("추가, 수정, 제외한 내용은 새 버전으로 저장합니다. 기존 버전은 유지됩니다.", "Save added, edited and removed entries as a new version. Existing versions remain unchanged.") : w("벤더와 시그니처가 일치할 때만 분석 입력에 설명을 추가합니다.", "An explanation is added only when both vendor and signature match.")}</p></div><div className="signature-catalog-actions">{draft ? <><button type="button" className="secondary" disabled={busy || entries.length >= signatureExplanationLimits.entries} onClick={addEntry}>{w("설명 추가", "Add explanation")}</button><button type="button" className="text-button" disabled={busy} onClick={() => setDiscardOpen(true)}>{w("변경 취소", "Discard draft")}</button></> : <><button type="button" className="text-button" onClick={() => setMetadataOpen(true)}>{w("버전 정보", "Version details")}</button><button type="button" className="secondary" onClick={() => { setMatchOpen(true); setMatchResult(null); }}>{w("매칭 확인", "Check matching")}</button><button type="button" className="primary" onClick={() => startDraft(version)}>{w("복제해 수정", "Duplicate and edit")}</button></>}</div></header>
      {draft && <div className="signature-version-form"><label>{w("설명집 이름", "Collection name")}<input required maxLength={120} disabled={busy} value={draft.name} onChange={event => { setDraft(current => ({ ...current, name: event.target.value })); setError(""); }} placeholder={w("이 버전을 구분할 이름", "Name this version")} /></label><label>{w("변경 사유", "Change reason")}<input required maxLength={1000} disabled={busy} value={draft.change_reason} onChange={event => { setDraft(current => ({ ...current, change_reason: event.target.value })); setError(""); }} placeholder={w("추가하거나 바꾼 내용", "What changed and why")} /></label></div>}
      <SignatureExplanationEntries entries={entries} editing={Boolean(draft)} disabled={busy} onOpen={openEntry} vendor={vendor} query={query} onVendorChange={value => { setVendor(value); setOffset(0); }} onQueryChange={value => { setQuery(value); setOffset(0); }} offset={offset} onOffsetChange={setOffset} limit={limit} onLimitChange={value => { setLimit(value); setOffset(0); }} />
      <footer className="signature-catalog-state"><p className="ux-muted">{draft ? w(`변경안 ${entries.length}개 / 최대 ${signatureExplanationLimits.entries}개`, `${entries.length} draft entries / maximum ${signatureExplanationLimits.entries}`) : version.id === catalog?.active_version_id ? w("현재 운영에서 사용하는 버전입니다.", "This version is currently used in Production.") : catalog?.active_version_id ? w("현재 운영에서 사용하는 버전은 아닙니다.", "This version is not currently used in Production.") : w("운영에 반영된 설명집은 없습니다.", "No explanation collection is applied to Production.")}</p>{draft ? <button type="button" className="primary" disabled={busy || Boolean(draftError)} onClick={saveVersion}>{busy ? w("저장 중…", "Saving…") : w("새 버전 저장", "Save new version")}</button> : <NavigationAction href="#evaluate/tests/new">{w("테스트에서 사용", "Use in a Test")}</NavigationAction>}</footer>
      {draft && !entries.length && <p className="ux-muted">{w("빈 설명집도 저장할 수 있습니다. 운영 설명을 없애려면 이 버전으로 공식 평가 후 운영에 반영하세요.", "An empty collection can be saved. Removing Production explanations still requires official evaluation and Production Review.")}</p>}
      {draft && draftError && draftError !== "version_required" && <p role="alert" className="error">{w(...issueWords[draftError])}</p>}
    </section>}
    <Dialog open={Boolean(entry)} title={entryIndex < 0 ? w("설명 추가", "Add explanation") : w("설명 수정", "Edit explanation")} onClose={() => setEntry(null)} initialFocusRef={entryFocus} className="signature-catalog-dialog">{entry && <SignatureExplanationEntryForm value={entry} onChange={value => { setEntry(value); setEntryError(""); }} onSubmit={applyEntry} onCancel={() => setEntry(null)} onRemove={entryIndex < 0 ? null : removeEntry} error={entryError} inputRef={entryFocus} />}</Dialog>
    <Dialog open={Boolean(viewEntry)} title={w("시그니처 설명", "Signature explanation")} onClose={() => setViewEntry(null)} className="signature-catalog-dialog"><SignatureExplanationContent entry={viewEntry} /></Dialog>
    <Dialog open={metadataOpen} title={w("설명집 버전 정보", "Collection version details")} onClose={() => setMetadataOpen(false)} className="signature-catalog-dialog">{version && <div className="signature-version-meta"><h3>{versionLabel(version)}</h3><dl>{[[w("변경 사유", "Change reason"), version.change_reason], [w("작성자", "Created by"), version.created_by], [w("저장 시각", "Created"), date(version.created_at)], [w("버전 ID", "Version ID"), version.id], [w("내용 지문", "Content fingerprint"), version.fingerprint || version.content_hash]].map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value || "—"}</dd></div>)}</dl><p className="ux-muted">{w("기존 버전은 수정하거나 삭제하지 않습니다. 복제한 변경안으로 새 버전을 만듭니다.", "Existing versions are immutable. Duplicate one to save changes as a new version.")}</p></div>}</Dialog>
    <Dialog open={discardOpen} title={w("변경안을 취소할까요?", "Discard this draft?")} onClose={() => setDiscardOpen(false)} className="signature-catalog-dialog"><p>{w("저장하지 않은 추가, 수정, 제외 내용이 사라집니다. 기존 설명집은 유지됩니다.", "Unsaved additions, edits and removals will be lost. Saved versions remain unchanged.")}</p><div className="signature-dialog-actions"><button type="button" className="secondary" onClick={() => setDiscardOpen(false)}>{w("계속 수정", "Keep editing")}</button><button type="button" className="danger" onClick={() => { setDraft(null); setDiscardOpen(false); setVendor(""); setQuery(""); setOffset(0); setError(""); }}>{w("변경 취소", "Discard draft")}</button></div></Dialog>
    <Dialog open={matchOpen} title={w("매칭 확인", "Check matching")} onClose={closeMatch} className="signature-catalog-dialog"><form className="signature-match-form" onSubmit={checkMatch}><p className="ux-muted">{w("선택한 저장 버전에서 설명을 찾습니다. LLM 호출이나 분석은 실행하지 않습니다.", "Finds an explanation in the selected saved version. No model call or analysis is run.")}</p><div className="signature-entry-grid"><label>{w("벤더", "Vendor")}<input required maxLength={120} value={matchVendor} onChange={event => { setMatchVendor(event.target.value); setMatchResult(null); }} placeholder="waf_vendor" /></label><label>{w("시그니처", "Signature")}<input required maxLength={1000} value={matchSignature} onChange={event => { setMatchSignature(event.target.value); setMatchResult(null); }} placeholder="signature" /></label></div><div className="signature-dialog-actions"><button type="submit" className="primary" disabled={matching || !matchVendor.trim() || !matchSignature.trim()}>{matching ? w("확인 중…", "Checking…") : w("매칭 확인", "Check matching")}</button></div></form>{matchError && <p role="alert" className="error">{w("매칭 결과를 확인하지 못했습니다. 다시 시도하세요.", "Could not check matching. Please retry.")}</p>}{matchResult && <div className="signature-match-result" role="status"><strong>{matchResult.matched ? w("일치하는 설명이 있습니다.", "A matching explanation was found.") : w("일치하는 설명이 없습니다. 설명 없이 분석합니다.", "No explanation matches. Analysis proceeds without one.")}</strong><SignatureExplanationContent entry={matchResult.entry} /></div>}</Dialog>
  </div>;
}
