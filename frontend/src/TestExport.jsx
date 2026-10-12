import { useEffect, useRef, useState } from "react";
import { useWords } from "./LifecycleViews.jsx";
import { referenceQuery } from "./testStatistics.js";
import { fetchTestExport, saveTestExport, testExportError } from "./testExport.js";

export default function TestExport({ id, filters, onUnauthorized }) {
  const w = useWords(), current = useRef(null);
  const [busy, setBusy] = useState(false), [error, setError] = useState("");
  const query = referenceQuery(filters), key = JSON.stringify([id, query]);
  useEffect(() => {
    setBusy(false); setError("");
    return () => { current.current?.abort(); current.current = null; };
  }, [key]);
  async function download() {
    if (current.current) return;
    const controller = new AbortController(); current.current = controller;
    setBusy(true); setError("");
    try {
      const blob = await fetchTestExport(id, query, { signal: controller.signal });
      if (!controller.signal.aborted && current.current === controller) saveTestExport(blob, id);
    } catch (error) {
      if (!controller.signal.aborted && current.current === controller) {
        setError(testExportError(error, w)); if (error.status === 401) onUnauthorized?.();
      }
    } finally { if (current.current === controller) { current.current = null; setBusy(false); } }
  }
  return <div className="test-run-export"><button type="button" className="secondary" disabled={busy} onClick={download}>{busy ? w("Excel 작성 중…", "Preparing Excel…") : w("전체 문항 Excel", "Export all cases")}</button>
    <span className="test-export-scope">{w("payload 포함 · 선택한 답안 기준 · 전체 문항", "Includes payload · selected reference basis · all cases")}</span>
    {error && <p className="error" role="alert">{error}</p>}</div>;
}
