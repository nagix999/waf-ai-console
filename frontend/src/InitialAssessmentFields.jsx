export default function InitialAssessmentFields({ value, onChange, words: w }) {
  const update = key => event => onChange({ ...value, [key]: event.target.value });
  return <section aria-label={w("1차 판정 입력", "Initial assessment input")}>
    <h3>{w("1차 판정 (선택)", "Initial assessment (optional)")}</h3>
    <p className="ux-muted">{w("외부 모델의 판정입니다. 심층 판정과 비교하며, LLM 입력이나 기대 판정으로 사용하지 않습니다.", "An external model's assessment, compared with Deep Assessment. Never used as model input or the expected verdict.")}</p>
    <div className="form-grid">
      <label>{w("1차 판정", "Initial verdict")}<select value={value.initial_verdict} onChange={update("initial_verdict")}><option value="">{w("없음", "Not provided")}</option><option value="true_positive">{w("정탐", "True positive")}</option><option value="false_positive">{w("오탐", "False positive")}</option></select></label>
      <label>{w("1차 신뢰도 (0~1)", "Initial confidence (0–1)")}<input type="number" min="0" max="1" step="any" value={value.initial_probability} onChange={update("initial_probability")} placeholder={w("예: 0.9 = 90%", "e.g. 0.9 = 90%")} /></label>
      <label>{w("1차 모델 버전", "Initial model version")}<input maxLength={255} value={value.initial_model_version} onChange={update("initial_model_version")} placeholder={w("선택 입력", "Optional")} /></label>
    </div>
  </section>;
}
