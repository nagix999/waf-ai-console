import NavigationAction from "./NavigationAction.jsx";
import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "./api.js";
import { Icon } from "./Icon.jsx";
import Dialog from "./Dialog.jsx";
import {
  MAX_POLICY_CHARS, createPromptDraft, createPromptSettingsController, emptyPromptSettingsState,
  promptCharacterCount, promptLineDiff, validatePromptDraft, visiblePromptText,
} from "./promptSettings.js";
import "./promptSettings.css";
import { SettingsSectionHeader, SettingsWorkflow } from "./SettingsWorkspace.jsx";
import { useConsolePreferences } from "./consolePreferences.jsx";

const versionName = (version) => version ? `v${version.version_number} · ${version.name}` : "미지정";
const timestamp = (value) => {
  if (!value) return "등록 시각 미기록";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "등록 시각 확인 필요" : date.toLocaleString("ko-KR");
};

function PolicyDiff({ before, after, beforeLabel, afterLabel }) {
  const diff = useMemo(() => promptLineDiff(before, after), [before, after]);
  return <div className="prompt-diff" aria-label="판정 지침 텍스트 변경 비교">
    <div className="prompt-diff-labels"><span>− 기준: {beforeLabel}</span><span>+ 비교: {afterLabel}</span></div>
    {!diff.changed ? <p className="prompt-empty">판정 지침 텍스트에 차이가 없습니다. 이름·변경 설명은 비교하지 않습니다.</p>
      : diff.mode === "limit" ? <p className="prompt-empty">길이 제한을 넘겨 변경 비교를 생략했습니다. 판정 지침을 4,000자 이하로 줄여 주세요.</p>
      : <><p className="prompt-help">{diff.mode === "whole" ? "줄이 많아 전체 텍스트를 비교합니다." : "− 삭제 · + 추가 · 기호 없는 줄은 동일합니다."} 의미나 모델 품질을 평가한 결과가 아닙니다.</p>
        <div className={`prompt-diff-rows prompt-diff-${diff.mode}`}>{diff.rows.map((row, index) => <div key={index} className={`prompt-diff-row prompt-diff-${row.kind}`}>
          <span className="prompt-diff-marker" aria-label={row.kind === "removed" ? "삭제" : row.kind === "added" ? "추가" : "동일"}>{row.kind === "removed" ? "−" : row.kind === "added" ? "+" : " "}</span>
          {diff.mode === "lines" && <span className="prompt-line-number" aria-label={`기준 ${row.oldLine || "없음"}, 비교 ${row.newLine || "없음"}행`}>{row.oldLine || "·"}/{row.newLine || "·"}</span>}
          <pre>{visiblePromptText(row.text) || " "}</pre>
        </div>)}</div>
      </>}
  </div>;
}

export default function PromptSettings() {
  const { locale } = useConsolePreferences(); const w = (ko, en) => locale === "en" ? en : ko;
  const [state, setState] = useState(emptyPromptSettingsState);
  const [draft, setDraft] = useState(null);
  const [submitted, setSubmitted] = useState(false);
  const [discardOpen, setDiscardOpen] = useState(false);
  const [view, setView] = useState(""); const [draftCompareOpen, setDraftCompareOpen] = useState(false);
  const controller = useRef(null);
  const draftName = useRef(null);
  useEffect(() => {
    const instance = createPromptSettingsController({ api, onChange: setState });
    controller.current = instance;
    void instance.refresh();
    return () => { instance.dispose(); if (controller.current === instance) controller.current = null; };
  }, []);
  useEffect(() => {
    if (!state.selected || !state.catalog) return;
    const baseline = state.selected.id !== state.catalog.active_version_id ? state.catalog.active_version_id : state.selected.parent_version_id;
    void controller.current?.compare(baseline || "");
  }, [state.selected?.id, state.catalog?.active_version_id]);

  const { catalog, selected } = state;
  const versions = catalog?.items || [];
  const active = versions.find((version) => version.id === catalog?.active_version_id);
  const limit = catalog?.max_policy_chars || MAX_POLICY_CHARS;
  const busy = Boolean(state.busy);
  const blocked = busy || state.loading || state.needsRefresh || !catalog;
  const editor = draft || createPromptDraft(selected);
  const errors = validatePromptDraft(editor, limit);
  const characterCount = promptCharacterCount(editor.policy_text || "");
  const updateDraft = (changes) => { controller.current?.cancelActivation(); setDraft({ ...editor, ...changes }); };
  function closeDraft() {
    if (busy) return;
    setDraft(null); setSubmitted(false); setDraftCompareOpen(false); setDiscardOpen(false);
  }
  async function save(event) {
    event.preventDefault(); setSubmitted(true);
    if (errors.length || blocked) return;
    const saved = await controller.current?.save(editor);
    if (saved) { setDraft(null); setSubmitted(false); }
  }

  return <div className="prompt-settings settings-workspace">
    <SettingsSectionHeader title={w("분석 지침", "Analysis Instructions")} actions={<button type="button" className="secondary" disabled={busy || state.loading || Boolean(draft)} onClick={() => controller.current?.refresh()}>새로고침</button>}>
      <div className="prompt-active"><span className="prompt-tag prompt-tag-active">Production 적용 중</span><strong>{catalog ? versionName(active) : "조회 중…"}</strong></div>
    </SettingsSectionHeader>

    {state.error && <div className="error" role="alert">{state.error}</div>}
    {state.notice && <div className="notice" role="status">{state.notice}</div>}

    <div className="settings-instruction-layout">
      <section className="panel settings-instruction-editor">
        <div className="panel-head"><h2>{w("새 버전 작성", "New instruction version")}</h2><span className="prompt-tag">{draft ? w("저장 전", "Unsaved") : selected ? `v${selected.version_number} ${w("기반", "source")}` : w("새 지침", "New instructions")}</span></div>
        {state.selectedError && <div className="error" role="alert">{state.selectedError}<button type="button" className="secondary" onClick={() => controller.current?.select(state.selectedId)}>상세 다시 조회</button></div>}
        {state.selectedLoading ? <p role="status">선택한 버전을 불러오는 중…</p> : <form className="prompt-editor" onSubmit={save} noValidate>
          <p className="prompt-help">저장된 원본은 바뀌지 않습니다. 수정한 지침은 새 버전으로 저장합니다.</p>
          <div className="settings-inline-fields"><label>버전 이름<input ref={draftName} value={editor.name} disabled={blocked} onChange={(event) => updateDraft({ name: event.target.value })} autoComplete="off" maxLength={120} aria-invalid={submitted && errors.some((error) => error.field === "name")} placeholder="새 버전 이름" /></label>
          <label>변경 설명<input value={editor.change_note} disabled={blocked} onChange={(event) => updateDraft({ change_note: event.target.value })} autoComplete="off" maxLength={1000} placeholder="무엇을 왜 바꾸었는지 작성해 주세요." aria-invalid={submitted && errors.some((error) => error.field === "change_note")} /></label></div>
          <label>판정 지침<textarea rows={18} value={editor.policy_text} disabled={blocked} spellCheck={false} onChange={(event) => updateDraft({ policy_text: event.target.value })} aria-describedby="prompt-policy-help" aria-invalid={characterCount > limit || (submitted && errors.some((error) => error.field === "policy_text"))} /></label>
          <div className="prompt-editor-meta"><span id="prompt-policy-help">최대 {limit.toLocaleString()}자 · 저장 시 맨 앞뒤 공백은 제거됩니다.</span><strong className={characterCount > limit ? "prompt-over-limit" : ""}>{characterCount.toLocaleString()} / {limit.toLocaleString()}자</strong></div>
          <p className="prompt-help">긴 지침은 로그 입력 공간을 줄입니다. 컨텍스트가 부족하면 모델을 호출할 수 없습니다.</p>
          <p className="prompt-privacy">실제 원문·Cookie·API 키·개인정보·정답·과거 분석가 판정을 넣지 마세요.</p>
          {submitted && errors.length > 0 && <div className="error" role="alert">{errors.map((error) => <p key={error.field}>{error.message}</p>)}</div>}
          <div className="prompt-actions"><button type="submit" className="primary" disabled={blocked || characterCount > limit}><Icon name="file" size={15} />{state.busy === "save" ? "저장 중…" : "새 버전 저장"}</button><button type="button" className="secondary" disabled={blocked} onClick={() => setDraftCompareOpen(true)}>초안 변경 비교</button>{draft && <button type="button" className="text-button" disabled={busy} onClick={() => setDiscardOpen(true)}>초안 삭제</button>}</div>
        </form>}
      </section>
      <aside className="settings-context-stack">
      <section className="panel prompt-history" aria-label="저장된 프롬프트 버전">
        <div className="panel-head"><h2>저장 버전</h2><span>{versions.length}개</span></div>
        {!versions.length ? <p className="prompt-empty">{state.loading ? "버전을 불러오는 중…" : "저장된 버전이 없습니다."}</p> : <div className="prompt-version-list">{versions.map((version) => <button key={version.id} type="button" aria-pressed={state.selectedId === version.id} disabled={busy || state.loading || Boolean(draft)} onClick={() => controller.current?.select(version.id)}>
          <div><span className="prompt-version-number">v{version.version_number}</span>{version.id === catalog.active_version_id && <span className="prompt-tag prompt-tag-active">적용 중</span>}</div>
          <strong>{version.name}</strong><small>{timestamp(version.created_at)}</small><span className="prompt-version-note">{version.change_note}</span>
        </button>)}</div>}
        {draft && <p className="prompt-help settings-card-padding">다른 버전을 선택하려면 작성 중인 초안을 저장하거나 삭제하세요.</p>}
        {selected && <div className="settings-version-actions"><button type="button" className="text-button" onClick={() => setView("compare")}>저장 버전 비교</button><button type="button" className="text-button" onClick={() => setView("technical")}>기술정보</button></div>}
      </section>
      <section className="panel settings-context-card"><h3>시스템 규칙 · 읽기 전용</h3><p>안전 규칙과 출력 형식은 시스템에서 관리합니다. 분석 지침으로 이 규칙을 바꿀 수 없습니다.</p><button type="button" className="text-button" disabled={!catalog} onClick={() => setView("system")}>시스템 규칙 보기</button></section>
      <section className="panel settings-context-card"><h3>운영 반영 절차</h3><SettingsWorkflow /><p>저장만으로 공통 적용 버전이 바뀌거나 LLM이 호출되지 않습니다.</p><NavigationAction href="#evaluate/tests/new">공식 테스트 만들기</NavigationAction></section>
      </aside>
    </div>
    <Dialog open={draftCompareOpen} title="프롬프트 초안 비교" onClose={() => setDraftCompareOpen(false)} className="prompt-settings prompt-dialog"><PolicyDiff before={editor.source_policy_text} after={editor.policy_text} beforeLabel={editor.source_version_number === null ? "빈 지침" : `v${editor.source_version_number}`} afterLabel="작성 중 초안" /></Dialog>
    <Dialog open={discardOpen && Boolean(draft)} title="프롬프트 초안 삭제" onClose={() => setDiscardOpen(false)} className="prompt-settings prompt-dialog"><p>저장하지 않은 초안 입력을 버립니다. 삭제한 초안은 복구할 수 없으며 저장된 버전과 공통 적용 지침은 바뀌지 않습니다.</p><div className="prompt-actions"><button type="button" className="secondary" onClick={() => setDiscardOpen(false)}>계속 작성</button><button type="button" className="primary" disabled={busy} onClick={closeDraft}>초안 삭제 확인</button></div></Dialog>
    <Dialog open={view === "compare" && Boolean(selected)} title="프롬프트 변경 비교" onClose={() => setView("")} className="prompt-settings prompt-dialog"><label>비교 기준 버전<select value={state.comparisonId} onChange={event => controller.current?.compare(event.target.value)}><option value="">비교 기준 선택</option>{versions.map(version => <option key={version.id} value={version.id}>{versionName(version)}</option>)}</select></label>{state.comparisonLoading ? <p role="status">불러오는 중…</p> : state.comparisonError ? <p className="error" role="alert">{state.comparisonError}</p> : state.comparison && selected && <PolicyDiff before={state.comparison.policy_text} after={selected.policy_text} beforeLabel={versionName(state.comparison)} afterLabel={versionName(selected)} />}</Dialog>
    <Dialog open={view === "technical" && Boolean(selected)} title="프롬프트 기술정보" onClose={() => setView("")} className="prompt-settings prompt-dialog">{selected && <dl className="prompt-integrity"><dt>버전 ID</dt><dd>{selected.id}</dd><dt>내용 지문</dt><dd>{selected.content_hash}</dd><dt>설정 버전</dt><dd>{catalog?.revision}</dd><dt>등록</dt><dd>{selected.created_by} · {timestamp(selected.created_at)}</dd><dt>복제 원본</dt><dd>{selected.parent_version_id || "기본 버전"}</dd></dl>}</Dialog>
    <Dialog open={view === "system" && Boolean(catalog)} title="시스템 규칙 · 읽기 전용" onClose={() => setView("")} className="prompt-settings prompt-dialog">{catalog && <><p>코드에서 관리하는 읽기 전용 안전·출력 규칙입니다. 편집 지침은 이 규칙 안에서 적용됩니다.</p><p>{catalog.fixed_rules_version}</p><pre>{visiblePromptText(catalog.fixed_instructions)}</pre></>}</Dialog>
  </div>;
}
