import { useState } from "react";
import HelpTooltip from "./HelpTooltip.jsx";
import TextInspector from "./TextInspector.jsx";

export default function RawEventView({ event }) {
  const [section, setSection] = useState("http");
  const fields = Object.entries(event.extra_fields || {});
  return <section className="panel detail-body raw-event-view">
    <div className="panel-head-inline"><h2>요청 원문</h2><span className="ux-muted">접근 기록 남김<HelpTooltip label="요청 원문">마스킹하지 않은 접수 원문으로, 분석 당시 모델 입력과는 다를 수 있습니다. 추가 필드도 수신된 값 그대로 표시합니다. 복사한 자료의 보관·반출에 주의하세요.</HelpTooltip></span></div>
    <div className="tabs" role="tablist" aria-label="요청 자료"><button type="button" role="tab" aria-selected={section === "http"} onClick={() => setSection("http")}>원문 텍스트</button><button type="button" role="tab" aria-selected={section === "fields"} onClick={() => setSection("fields")}>추가 필드 · {fields.length}</button></div>
    {section === "http" ? <TextInspector value={event.payload} label="HTTP 원문" /> : <TextInspector value={fields.length ? event.extra_fields : null} label="추가 필드" empty="추가로 받은 필드가 없습니다." />}
  </section>;
}
