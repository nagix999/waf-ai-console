import { analystFieldLabel, decodingDisplayText, decodingEncodingText, decodingItemStatus, decodingWarningText, visibleDecoding } from "./analystView.js";

export default function DecodingView({ decoding }) {
  const data = visibleDecoding(decoding);
  if (!data) return <p className="muted">이 조회 응답에는 디코딩 정보가 없습니다. 서버 버전과 원문 조회 상태를 확인해 주세요.</p>;
  return <div className="decoding-view">
    <p className="analyst-notice">원문을 문자열로 변환해 비교한 결과입니다. 대상 서버에서 같은 방식으로 처리했는지, 분석 당시 이 결과를 사용했는지는 확인되지 않았습니다. 조회 표현식 실행·외부 연결·원본 변경은 하지 않습니다.</p>
    <p className="muted">이 비교 화면은 제어 문자·방향 제어 문자를 ⟦U+0000⟧ 형식으로 가시화합니다. 화면에서 복사하면 이 표시 문자열이 복사됩니다. 저장된 원본과 도구 응답은 수정하지 않으며, 인코딩된 원본은 HTTP 원문에서 확인할 수 있습니다.</p>
    <div className="decoding-scope"><span>문자열 {data.items.length}개 · 도구 {data.decoder_version}</span><span>확인 범위 {data.scanned_chars ?? "미기록"} / {data.total_chars ?? "미기록"}자</span></div>
    {data.scan_truncated && <p className="analyst-notice">크기·개수 등 도구 처리 한도로 일부 후보나 구간을 확인하지 못했습니다. 표시되지 않은 인코딩·조회 표현식이 있을 수 있습니다.</p>}
    {!!data.warnings.length && <ul className="decoding-warnings">{data.warnings.map((warning, index) => <li key={index}>{decodingWarningText(warning)}</li>)}</ul>}
    {!data.items.length && <p>확인한 범위에서 표시 가능한 변환 후보를 찾지 못했습니다. 인코딩이나 공격이 없다는 뜻은 아닙니다.</p>}
    {data.items.map((item, index) => <article className="decoding-candidate" key={`${item.id}-${index}`}>
      <h3>문자열 {index + 1} <small>{analystFieldLabel(item.field)} · {item.field} · 문자 위치 {item.start ?? "미기록"}–{item.end ?? "미기록"}</small></h3>
      <p className="muted">{decodingItemStatus(item)}</p>
      <div className="decoding-pair"><div><h4>원본 문자열</h4><pre tabIndex={0}>{decodingDisplayText(item.original)}</pre></div><div><h4>{!item.steps.length && item.original === item.decoded ? "변환 없이 보존한 문자열" : "변환 결과"}</h4><pre tabIndex={0}>{decodingDisplayText(item.decoded)}</pre></div></div>
      {!!item.steps.length && <details><summary>변환 단계 {item.steps.length}개 보기</summary><ol>{item.steps.map((step, stepIndex) => <li key={stepIndex}><strong>{decodingEncodingText(step.encoding)}</strong><div className="decoding-pair"><pre tabIndex={0}>{decodingDisplayText(step.input)}</pre><pre tabIndex={0}>{decodingDisplayText(step.output)}</pre></div></li>)}</ol></details>}
      {!!item.warnings.length && <ul className="decoding-warnings">{item.warnings.map((warning, warningIndex) => <li key={warningIndex}>{decodingWarningText(warning)}</li>)}</ul>}
    </article>)}
  </div>;
}
