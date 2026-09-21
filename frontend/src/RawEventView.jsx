import { useEffect, useMemo, useState } from "react";
import HelpTooltip from "./HelpTooltip.jsx";
import TextInspector from "./TextInspector.jsx";
import DetailTabs from "./DetailTabs.jsx";
import DecodingView from "./DecodingView.jsx";
import { evidenceLocation, recordedInputFields } from "./inferenceDetail.js";

export default function RawEventView({ event, detail = {}, target, active = true }) {
  const [section, setSection] = useState("http");
  const fields = Object.entries(event.extra_fields || {});
  const location = useMemo(() => evidenceLocation(event, detail, target), [event, detail, target]);
  // A fresh click, including on the same excerpt, reopens the matching section.
  useEffect(() => { if (location) setSection(location.section); }, [target]);
  const request = useMemo(() => target ? { query: location?.found ? target.excerpt : "" } : undefined, [target, location?.found]);
  const httpRequest = useMemo(() => request ? { query: location?.section === "http" ? request.query : "" } : undefined, [request, location?.section]);
  return <section className="panel detail-body raw-event-view">
    <div className="panel-head-inline"><h2>접수된 입력</h2><span className="ux-muted">열람 기록이 남습니다<HelpTooltip label="요청 원문">마스킹하지 않은 접수 원문으로, 분석 당시 모델 입력과는 다를 수 있습니다. 추가 필드도 수신된 값 그대로 표시합니다. 복사한 자료의 보관·반출에 주의하세요.</HelpTooltip></span></div>
    {location && <div className="input-jump" role="status"><p><strong>근거 위치 · {location.field}</strong></p>{location.found ? <p>{location.section === "http" ? "HTTP 원문 전체에서 같은 문구를 찾습니다. 지정 필드의 위치를 다시 검증한 결과는 아닙니다." : "지정된 입력 필드에서 같은 문구를 찾았습니다."}</p> : <><p>이 필드에서 발췌문과 같은 문구를 찾지 못했습니다. 변환된 표현이나 일부만 저장된 입력인지 확인하세요.</p><code>{target.excerpt}</code></>}</div>}
    <DetailTabs label="요청 자료" items={[["http", "HTTP 원문"], ["fields", "입력 필드"], ["decoding", "인코딩·난독화"]]} value={section} onChange={setSection}>{key => {
      if (key === "http") return <TextInspector value={event.payload} label="HTTP 원문" searchRequest={httpRequest} />;
      if (key === "decoding") return active && section === "decoding" && <DecodingView decoding={event.decoding} />;
      return <div className="input-fields-grid">
        {location?.section === "fields" && location.value !== null && <section><h3>{location.field}</h3><TextInspector value={location.value} label="근거의 입력 필드" searchRequest={request} /></section>}
        <section><h3>기본 필드</h3><TextInspector value={recordedInputFields(detail)} label="기본 입력 필드" /></section>
        <section><h3>추가 필드 · {fields.length}</h3><TextInspector value={fields.length ? event.extra_fields : null} label="추가 필드" empty="추가로 받은 필드가 없습니다." /></section>
      </div>;
    }}</DetailTabs>
  </section>;
}
