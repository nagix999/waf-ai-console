import { useState } from "react";
import { answerNames, reviewActions, reviewNames } from "./validationData.js";

const actionNames = { reviewed: "검토 완료", approved: "승인", draft: "미검토로 되돌리기" };
const descriptions = {
  reviewed: "입력과 답안의 판단 이유를 확인했나요? 검토 완료 후 별도로 승인할 수 있습니다.",
  approved: "이 버전의 답안을 공식 평가에 사용하도록 승인합니다. 승인만으로 분석이 실행되거나 운영 설정이 변경되지는 않습니다.",
  draft: "이 문항을 다시 검토하도록 표시합니다. 이전 승인 이력과 기존 테스트는 그대로 보존됩니다.",
};

export default function GroundTruthReview({ item, readonly, busy, dirty, onReview }) {
  const [target, setTarget] = useState(null);
  const status = item?.review_status || "draft";
  return <section className="ground-truth-review" aria-label="답안 검토">
    <div className="ground-truth-review-heading"><span className={`review-badge ${status}`}>{reviewNames[status]}</span>
      <span>답안: {answerNames[item?.reference_verdict] || "없음"}</span></div>
    {item?.id && !readonly && <>
      {dirty ? <p className="ux-muted">먼저 변경 내용을 저장하세요. 수정본은 미검토 상태로 저장됩니다.</p> :
        <p className="ux-muted">참고 답안은 자동으로 승인되지 않습니다. 입력과 판단 이유를 확인한 뒤 승인하세요.</p>}
      <div className="ground-truth-review-actions">{reviewActions(status).map(value => <button key={value} type="button" className="secondary"
        disabled={busy || dirty || (value !== "draft" && !item.reference_verdict)} onClick={() => setTarget(value)}>{actionNames[value]}</button>)}</div>
      {!item.reference_verdict && <p className="ux-muted">검토를 완료하려면 답안을 먼저 저장하세요.</p>}
      {target && !dirty && <div className="ground-truth-confirm" role="group" aria-label={`${actionNames[target]} 확인`}>
        <p>{descriptions[target]}</p>
        <div className="ground-truth-review-actions"><button type="button" className="primary" disabled={busy} onClick={() => onReview(target)}>{actionNames[target]} 확인</button>
          <button type="button" className="secondary" disabled={busy} onClick={() => setTarget(null)}>취소</button></div>
      </div>}
    </>}
    {item?.id && <p className="ux-muted">버전 {item.revision} · 기록: {item.created_by || "미제공"}
      {item.source_created_by && ` · 가져온 답안 작성자: ${item.source_created_by}`}</p>}
  </section>;
}
