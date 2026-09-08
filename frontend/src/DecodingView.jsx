import { useState } from "react";
import { analystFieldLabel, decodingDisplayText, decodingEncodingText, decodingItemStatus, decodingWarningText, visibleDecoding } from "./analystView.js";
import HelpTooltip from "./HelpTooltip.jsx";
import Dialog from "./Dialog.jsx";
import TextInspector from "./TextInspector.jsx";

export default function DecodingView({ decoding }) {
  const [selected, setSelected] = useState(null);
  const [scopeOpen, setScopeOpen] = useState(false);
  const data = visibleDecoding(decoding);
  if (!data) return <p className="muted">변환 정보가 없습니다. 원문 조회 상태를 확인하세요.</p>;
  const chosen = data.items[selected];
  return <div className="decoding-view">
    <div className="ux-toolbar"><strong className="ux-grow">문자열 {data.items.length}개</strong><span className="ux-muted">조회 시점의 변환 후보<HelpTooltip label="변환 후보">대상 서버에서 같은 방식으로 처리했는지, 분석 당시 이 결과를 사용했는지는 확인되지 않았습니다. 조회 표현식 실행·외부 연결·원본 변경은 하지 않습니다.</HelpTooltip></span><button type="button" className="secondary" onClick={() => setScopeOpen(true)}>확인 범위</button></div>
    <p className="ux-muted">제어 문자는 ⟦U+0000⟧ 형식으로 표시·복사됩니다. 정확한 원문은 HTTP 원문에서 확인하세요.</p>
    {data.scan_truncated && <p className="analyst-notice">처리 한도로 일부 구간이나 후보를 확인하지 못했습니다.</p>}
    {!!data.warnings.length && <ul className="decoding-warnings">{data.warnings.map((warning, index) => <li key={index}>{decodingWarningText(warning)}</li>)}</ul>}
    {!data.items.length && <p className="empty">표시할 변환 후보가 없습니다. 인코딩이나 공격이 없다는 뜻은 아닙니다.</p>}
    {data.items.map((item, index) => <article className="decoding-candidate" key={`${item.id}-${index}`}>
      <div className="panel-head-inline"><h3>문자열 {index + 1} <small>{analystFieldLabel(item.field)}</small></h3><span className="ux-muted">{decodingItemStatus(item)}</span></div>
      <div className="decoding-pair"><div><h4>원본 문자열</h4><TextInspector compact label={`문자열 ${index + 1} 원본`} value={decodingDisplayText(item.original)} /></div><div><h4>{!item.steps.length && item.original === item.decoded ? "변환 없이 보존한 문자열" : "변환 결과"}</h4><TextInspector compact label={`문자열 ${index + 1} 변환 결과`} value={decodingDisplayText(item.decoded)} /></div></div>
      <div className="ux-toolbar"><span className="ux-muted">{item.steps.map(step => decodingEncodingText(step.encoding)).join(" → ")}</span><button type="button" className="secondary" onClick={() => setSelected(index)}>위치·변환 단계</button></div>
      {!!item.warnings.length && <ul className="decoding-warnings">{item.warnings.map((warning, warningIndex) => <li key={warningIndex}>{decodingWarningText(warning)}</li>)}</ul>}
    </article>)}
    <Dialog open={scopeOpen} title="문자열 확인 범위" onClose={() => setScopeOpen(false)}><dl className="detail-summary"><div><dt>확인한 문자</dt><dd>{data.scanned_chars ?? "미기록"} / {data.total_chars ?? "미기록"}자</dd></div><div><dt>변환 도구</dt><dd>{data.decoder_version}</dd></div><div><dt>일부 확인 여부</dt><dd>{data.scan_truncated ? "일부 구간 또는 후보 생략" : "생략 기록 없음"}</dd></div></dl><p>변환 후보를 찾는 도구이며 모든 인코딩 방식이나 공격 여부를 검증하지 않습니다.</p></Dialog>
    <Dialog open={selected !== null} title={`문자열 ${(selected ?? 0) + 1} 변환 상세`} className="inspection-dialog" onClose={() => setSelected(null)}>{chosen && <><p className="ux-muted">필드 {chosen.field} · 문자 위치 {chosen.start ?? "미기록"}–{chosen.end ?? "미기록"}</p>{chosen.steps.length ? <ol className="decoding-step-list">{chosen.steps.map((step, index) => <li key={index}><h3>{decodingEncodingText(step.encoding)}</h3><div className="decoding-pair"><TextInspector compact label={`단계 ${index + 1} 변환 전`} value={decodingDisplayText(step.input)} /><TextInspector compact label={`단계 ${index + 1} 변환 후`} value={decodingDisplayText(step.output)} /></div></li>)}</ol> : <p>문자열을 변환하지 않고 그대로 보존했습니다.</p>}</>}</Dialog>
  </div>;
}
