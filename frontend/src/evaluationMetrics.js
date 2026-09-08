export const mainMetrics = [
  ["accuracy", "Accuracy", "확정 판정 중 참고 답안과 일치한 비율"],
  ["precision", "Precision", "AI 정탐 판정 중 참고 답안도 정탐인 비율"],
  ["recall", "Recall", "참고 정탐인 확정 판정 중 정탐으로 맞힌 비율 · 보류 제외"],
  ["f1", "F1 score", "정탐 Precision과 Recall의 조화평균"],
  ["coverage", "판정 커버리지", "이진 답안 평가 대상 중 정탐·오탐으로 확정한 비율"],
  ["abstention_rate", "판정 보류율", "이진 답안 평가 대상 중 AI가 보류한 비율"],
];

export const extraMetrics = [
  ["specificity", "Specificity · 오탐 재현율", "참고 오탐인 확정 판정 중 오탐으로 맞힌 비율"],
  ["false_positive_rate", "FPR · 과탐률", "참고 오탐인 확정 판정 중 AI가 정탐으로 판정한 비율"],
  ["false_negative_rate", "FNR · 미탐률", "참고 정탐인 확정 판정 중 AI가 오탐으로 판정한 비율"],
  ["balanced_accuracy", "Balanced Accuracy", "정탐·오탐 재현율의 평균 · 클래스 불균형 보완"],
  ["macro_f1", "Macro F1", "정탐·오탐별 F1의 평균 · 클래스별 동일 비중"],
  ["mcc", "MCC", "두 클래스의 전체 혼동행렬을 반영한 상관계수 · −1~1"],
  ["overall_binary_correct_rate", "보류 포함 정답률", "이진 답안 평가 대상 전체에서 맞힌 비율 · 보류도 분모에 포함"],
];

export const metricHelp = {
  accuracy: "Accuracy = (TP + TN) / 확정 판정 수. 높을수록 확정한 문항을 많이 맞혔고 낮으면 확정 판정의 오답 비중이 크다는 뜻입니다. 클래스가 한쪽으로 치우치거나 어려운 문항을 보류하면 높아질 수 있으므로 클래스별 지표와 커버리지를 함께 확인하세요.",
  precision: "Precision = TP / (TP + FP). 높을수록 AI가 정탐이라고 한 판정의 과탐이 적습니다. 낮으면 정상 요청을 공격으로 판단하는 경우가 많습니다. 정탐을 적게 선택해도 높아질 수 있어 미탐과 Recall을 함께 봐야 합니다. 정탐 예측이 없으면 계산 불가입니다.",
  recall: "Recall = TP / (TP + FN). 참고 답안이 정탐이고 AI가 확정한 문항에서 공격을 찾아낸 비율입니다. 높을수록 이 범위의 미탐이 적고 낮을수록 공격을 오탐으로 잘못 판단합니다. 보류한 공격 문항은 분모에서 빠지므로 전체 공격 탐지율이 아닙니다. 확정한 참고 정탐이 없으면 계산 불가입니다.",
  f1: "F1 = 2TP / (2TP + FP + FN). 정탐 Precision과 Recall의 조화평균으로 높을수록 두 지표가 함께 좋습니다. 낮으면 과탐이나 미탐 중 하나 이상이 많을 수 있습니다. TN과 보류는 직접 반영하지 않으며 분모가 0이면 계산 불가입니다.",
  coverage: "판정 커버리지 = 확정 판정 수 / 비교 가능한 이진 답안 문항 수(보류 포함). 높으면 더 많은 문항에 정탐·오탐 판정을 냈고 낮으면 보류가 많다는 뜻입니다. 높다고 정확한 것은 아닙니다. 품질 지표와 정탐·오탐별 보류 건수를 함께 확인하세요.",
  abstention_rate: "판정 보류율 = AI 보류 수 / 비교 가능한 이진 답안 문항 수. 높으면 분석가의 추가 확인이 필요한 문항이 많습니다. 낮아도 무조건 좋은 것은 아니며 근거가 부족한 요청에는 적절한 보류가 필요합니다. 실패와 기대 답안 자체가 보류인 문항은 제외합니다.",
  specificity: "Specificity = TN / (TN + FP). 참고 답안이 오탐인 확정 문항을 오탐으로 맞힌 비율입니다. 높을수록 정상 요청을 정탐으로 잘못 판단하는 일이 적습니다. 낮으면 과탐이 많습니다. 보류는 제외하며 확정한 참고 오탐이 없으면 계산 불가입니다.",
  false_positive_rate: "FPR = FP / (TN + FP). 참고 답안이 오탐인 확정 문항을 정탐으로 잘못 판단한 비율입니다. 낮을수록 과탐이 적고 높으면 정상 요청 검토 부담이 커질 수 있습니다. WAF 차단 성공률이 아니며 확정한 참고 오탐이 없으면 계산 불가입니다.",
  false_negative_rate: "FNR = FN / (TP + FN). 참고 답안이 정탐인 확정 문항을 오탐으로 잘못 판단한 비율입니다. 낮을수록 미탐이 적고 높으면 공격을 놓칠 위험이 큽니다. 보류한 공격은 제외되므로 보류 건수도 함께 확인하세요. 확정한 참고 정탐이 없으면 계산 불가입니다.",
  balanced_accuracy: "Balanced Accuracy = (정탐 Recall + 오탐 Specificity) / 2. 두 클래스를 같은 비중으로 평가합니다. 높을수록 양쪽을 고르게 맞히고 낮으면 한쪽 또는 양쪽 판정이 약합니다. 확정한 참고 정탐·오탐이 모두 있어야 계산하며 작은 표본과 보류의 영향은 남습니다.",
  macro_f1: "Macro F1 = (정탐 F1 + 오탐 F1) / 2. 클래스별 F1에 같은 비중을 줍니다. 높을수록 양쪽 클래스의 Precision·Recall이 고르게 좋습니다. 낮으면 특정 클래스에서 오류가 많을 수 있습니다. 확정한 참고 정탐·오탐이 모두 있어야 계산하며 보류는 제외합니다.",
  mcc: "MCC는 TP·TN·FP·FN을 함께 반영한 이진 판정 상관계수입니다. +1은 완전 일치, 0은 상관 없음, −1은 완전히 반대 방향입니다. 높을수록 일치 경향이 강하고 음수이면 판정 방향을 점검해야 합니다. 0이 무작위 모델과 같은 성능을 보장하지는 않습니다. 분모가 0이면 계산 불가이며 보류는 제외합니다.",
  overall_binary_correct_rate: "보류 포함 정답률 = (TP + TN) / 비교 가능한 이진 답안 문항 수(보류 포함). 높을수록 보류하지 않고 맞힌 문항이 많습니다. 낮으면 오류 또는 보류가 많을 수 있습니다. 보류를 미탐·과탐으로 바꾸어 세지 않으며 오류율과 보류율을 구분해 확인하세요.",
  label_coverage: "참고 답안 연결률 = 답안이 연결된 문항 수 / 현재 검색 범위의 전체 분석 수. 높을수록 답안이 붙은 비중이 크지만 답안의 정확성이나 대표성을 보장하지 않습니다. 낮으면 평가가 소수의 선택된 표본에 치우칠 수 있습니다. 연결된 문항 중 실패·미완료·모의 실행 등은 실제 품질 평가에서 제외됩니다.",
  agreement: "기준 판정 일치율 = 참고 답안과 같은 최종 판정 수 / 비교 가능한 실제 완료 분석 수. 기대 답안이 보류인 문항도 포함합니다. 높을수록 해당 참고값과 많이 일치하지만 기대 답안·AI를 보고 작성한 답안과의 일치는 독립적인 운영 정확도가 아닙니다. 낮으면 답안 품질과 분석 결과를 함께 검토하세요.",
};

export function metricText(value, key) {
  if (typeof value !== "number" || !Number.isFinite(value)) return "계산 불가";
  if (key === "mcc") return value.toFixed(3);
  return `${(value * 100).toFixed(1)}%`;
}

export const matrixCells = [
  ["tp", "TP · 정탐 일치", "true_positive", "true_positive", "match"],
  ["fn", "FN · 미탐 방향", "true_positive", "false_positive", "false_negative"],
  ["abstained_positive", "참고 정탐 · AI 보류", "true_positive", "inconclusive", "abstained"],
  ["fp", "FP · 과탐 방향", "false_positive", "true_positive", "false_positive"],
  ["tn", "TN · 오탐 일치", "false_positive", "false_positive", "match"],
  ["abstained_negative", "참고 오탐 · AI 보류", "false_positive", "inconclusive", "abstained"],
];

export function matrixDrilldown(key) {
  const cell = matrixCells.find(([name]) => name === key);
  return cell ? { reference_verdict: cell[2], verdict: cell[3], evaluation_outcome: cell[4] } : {};
}
