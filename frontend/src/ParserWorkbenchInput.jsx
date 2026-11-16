import { useEffect, useMemo, useRef, useState } from "react";
import { useConsolePreferences } from "./consolePreferences.jsx";
import { BASELINE_PARSER_ID } from "./parserManagement.js";
import { ParserRetentionNotice } from "./LegacyParserManagement.jsx";
import ParserSourcePreview from "./ParserSourcePreview.jsx";

export default function ParserWorkbenchInput({ catalog, busy, onSubmit }) {
  const { locale } = useConsolePreferences(), w = (ko, en) => locale === "en" ? en : ko;
  const [source, setSource] = useState("dataset"), [query, setQuery] = useState("");
  const [datasetId, setDatasetId] = useState(""), [versionId, setVersionId] = useState(""), [testId, setTestId] = useState("");
  const [name, setName] = useState(""), [baseline, setBaseline] = useState(BASELINE_PARSER_ID), [limit, setLimit] = useState(50);
  const [selection, setSelection] = useState([]), [previewReady, setPreviewReady] = useState(false);
  const initialized = useRef(false);
  useEffect(() => { if (!initialized.current && catalog) { setBaseline(catalog.active_version_id || BASELINE_PARSER_ID); initialized.current = true; } }, [catalog]);
  const datasets = useMemo(() => {
    const groups = new Map();
    for (const version of catalog?.dataset_versions || []) {
      const key = version.dataset_id;
      if (!groups.has(key)) groups.set(key, { id: key, name: version.dataset_name || version.name, versions: [] });
      groups.get(key).versions.push(version);
    }
    return [...groups.values()].filter(row => row.name.toLocaleLowerCase().includes(query.toLocaleLowerCase()));
  }, [catalog, query]);
  const selectedDataset = datasets.find(row => row.id === datasetId);
  const versions = selectedDataset?.versions || [];
  const sourceVersion = versions.find(row => row.id === versionId);
  const tests = catalog?.test_runs || [];
  const selectedTest = tests.find(row => row.id === testId);
  const total = source === "dataset" ? sourceVersion?.item_count : selectedTest?.item_count;
  const maximum = catalog?.limits?.max_cases || 100;
  const previewSource = useMemo(() => source === "dataset" ? versionId ? { dataset_version_id: versionId } : null : testId ? { test_run_id: testId } : null, [source, versionId, testId]);
  const ready = !busy && catalog && previewReady && selection.length >= 2 && selection.length <= Number(limit) && (source === "dataset" ? Boolean(sourceVersion) : Boolean(selectedTest)) && Number.isInteger(Number(limit)) && limit >= 2 && limit <= maximum;
  function submit(event) {
    event.preventDefault(); if (!ready) return;
    onSubmit({ ...(name.trim() ? { name: name.trim() } : {}), source: { ...previewSource, case_ids: selection }, baseline_version_id: baseline, max_cases: Number(limit) });
  }
  return <form className="panel pw-input-panel" onSubmit={submit}>
    <div className="pw-section-heading"><div><h2>{w("규칙을 만들 입력 선택", "Choose source cases")}</h2><p>{w("고정된 입력에서 형식을 찾습니다. 기대 판정과 분석 결과는 LLM에 전달하지 않습니다.", "Discover formats in fixed inputs. Expected verdicts and analysis results are not sent to the model.")}</p></div></div>
    <fieldset disabled={busy} className="parser-form">
      <div className="pw-field-pair"><label>{w("작업 이름", "Work name")}<input value={name} maxLength={120} onChange={event => setName(event.target.value)} placeholder={w("비워두면 자동 생성", "Generated automatically when empty")} /></label><label>{w("입력 출처", "Source")}<select value={source} onChange={event => setSource(event.target.value)}><option value="dataset">{w("평가 데이터셋", "Evaluation Dataset")}</option><option value="test">{w("평가에서 실행한 테스트", "A Test from Evaluation")}</option></select></label></div>
      {source === "dataset" ? <>
        <label>{w("평가 데이터셋 검색", "Find Evaluation Dataset")}<input type="search" maxLength={120} value={query} onChange={event => setQuery(event.target.value)} placeholder={w("이름으로 검색", "Search by name")} /></label>
        <div className="pw-field-pair"><label>{w("평가 데이터셋", "Evaluation Dataset")}<select value={datasetId} onChange={event => { setDatasetId(event.target.value); setVersionId(""); }}><option value="">{w("선택하세요", "Choose a dataset")}</option>{datasets.map(row => <option key={row.id} value={row.id}>{row.name}</option>)}</select></label><label>{w("고정 버전", "Fixed version")}<select value={versionId} disabled={!selectedDataset} onChange={event => setVersionId(event.target.value)}><option value="">{w("버전을 선택하세요", "Choose a version")}</option>{versions.map(row => <option key={row.id} value={row.id}>r{row.revision} / {row.item_count} {w("문항", "cases")} / {row.published ? w("공식 버전", "Published Version") : w("이전 저장본", "Legacy saved version")}</option>)}</select></label></div>
        {catalog && !datasets.length && <p className="pw-empty">{query ? w("일치하는 데이터셋이 없습니다.", "No matching datasets.") : w("선택할 고정 버전이 없습니다. 평가 데이터셋에서 버전을 먼저 저장하세요.", "No fixed versions are available. Save a dataset version first.")}</p>}
      </> : <label>{w("테스트", "Test")}<select value={testId} onChange={event => setTestId(event.target.value)}><option value="">{w("테스트 선택", "Choose Test")}</option>{tests.map(row => <option key={row.id} value={row.id}>{row.name} / {row.item_count} {w("문항", "cases")}</option>)}</select></label>}
      <div className="pw-field-pair"><label>{w("비교 기준 파서", "Baseline parser")}<select value={baseline} onChange={event => setBaseline(event.target.value)}>{(catalog?.parser_versions || []).map(row => <option key={row.id} value={row.id}>{row.name}</option>)}</select></label><label>{w("가져올 최대 문항 수", "Maximum cases")}<input type="number" min={2} max={maximum} step={1} value={limit} onChange={event => setLimit(event.target.value)} /></label></div>
    </fieldset>
    <ParserSourcePreview source={previewSource} maximum={Number(limit)} selected={selection} onSelection={setSelection} onReady={setPreviewReady} disabled={busy} />
    {Number.isFinite(total) && <p className="pw-source-total">{w("선택 버전", "Selected source")}: <strong>{total}</strong> {w("문항", "cases")} / {w("최대", "up to")} {Math.min(Number(limit) || 0, total)} {w("문항 사용", "cases selected")}</p>}
    <p className="pw-inline-note">{w("제안용과 별도 검증용 문항을 나누어 고정합니다. 기존 분석과 데이터셋은 변경하지 않습니다.", "Cases are frozen in separate authoring and validation sets. Existing analyses and datasets are unchanged.")}</p>
    <ParserRetentionNotice />
    <div className="parser-actions"><button type="submit" className="primary" disabled={!ready}>{busy ? w("준비 중…", "Preparing…") : w("입력 고정 후 계속", "Freeze inputs and continue")}</button></div>
  </form>;
}
