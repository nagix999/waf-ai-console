import { analystFieldLabel, decodingDisplayText } from "./analystView.js";
import { assessmentView, decisionIssues, evidenceLabels, evidenceNotice } from "./analystAssessment.js";

export function DecisionIssues({ detail }) {
  const issues = decisionIssues(detail);
  if (!issues.length) return null;
  return <section className="decision-issues" aria-label="판단이 필요한 부분">
    <h3>판단이 필요한 부분</h3>
    {issues.map((issue, index) => <article key={index}>
      <p>{issue.point_ko}</p>
      {issue.missing_condition_ko && <p><span className="issue-label">아직 확인되지 않은 조건</span>{issue.missing_condition_ko}</p>}
      <small>관련 근거 {issue.evidence_numbers.join(" · ")}</small>
    </article>)}
  </section>;
}

export function AnalystEvidence({ result, onViewInput }) {
  const view = assessmentView(result);
  if (!view) return null;
  const groups = Object.keys(evidenceLabels).map(supports => ({ supports, items: view.evidence.filter(item => item.supports === supports) }));
  return <section className="panel result-card evidence-card">
    <h2>판정 근거</h2><p className="muted evidence-notice">{evidenceNotice}</p>
    <div className="evidence-sides">
      {groups.filter(group => ["true_positive", "false_positive"].includes(group.supports) || group.items.length).map(group => <section key={group.supports} className={`evidence-side evidence-${group.supports}`} aria-label={evidenceLabels[group.supports]}>
        <h3>{evidenceLabels[group.supports]}</h3>
        {!group.items.length && <p className="muted evidence-empty">기록된 근거가 없습니다.</p>}
        {group.items.map(item => <article className="evidence-item" key={item.number}>
          <div className="evidence-heading"><span className="evidence-number">근거 {item.number}</span><strong>{analystFieldLabel(item.field)}</strong></div>
          <div className="evidence-section"><span>로그 발췌</span><code dir="ltr">{decodingDisplayText(item.excerpt)}</code></div>
          {onViewInput && <button type="button" className="text-button evidence-input-link" onClick={() => onViewInput(item)}>입력에서 보기</button>}
          <div className="evidence-section evidence-interpretation"><span>판단 이유</span><p>{item.interpretation_ko}</p></div>
          {!!item.related_numbers.length && <small className="evidence-related">같은 로그 발췌: 근거 {item.related_numbers.join(" · ")}</small>}
        </article>)}
      </section>)}
    </div>
  </section>;
}
