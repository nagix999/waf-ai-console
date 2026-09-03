# WAF Agent 판정 정책 v1

이 디렉터리는 ModuAgent 실행 자체와 독립적인 도메인 계약을 보관합니다. 모델이나 프롬프트를 교체해도 아래 최종 판정 규칙은 애플리케이션 코드가 강제합니다.

## 출력 계약

- `verdict`: `true_positive` / `false_positive` / `inconclusive`
- `confidence_score`: 0~1
- 한국어 요약과 위협 분석
- 시그니처 관계: `exact` / `partial` / `mismatch` / `unknown`
- 원문 발췌 근거: 최대 5개, 각 300자 이하
- 불확실성 및 분석가 확인 항목
- 영향·위험·검증 절차를 포함한 자문형 튜닝 제안
- 상충 증거 및 입력 잘림 여부

정탐 또는 오탐처럼 확정적인 판정에는 적어도 하나의 원문 근거가 필요합니다. 이 조건은 Pydantic 검증 단계에서 강제됩니다.

## 독립 Verifier 실행 조건

다음 중 하나라도 만족하면 동일한 원본 이벤트를 별도의 Agent에 전달합니다. Primary 출력은 전달하지 않습니다.

- Primary가 보류
- 신뢰도 0.75 미만(환경변수로 조정 가능)
- 시그니처가 부분 일치 또는 불일치
- 정탐인데 WAF가 Allow
- 오탐인데 WAF가 Deny
- 범용 HTTP 파싱이 부분/실패
- 모델 입력이 잘림
- 튜닝 제안이 있음
- 상충 증거가 있음

Verifier 실패 또는 판정 불일치는 최종 `inconclusive`로 결합합니다. 양쪽 판정이 같으면 더 낮은 신뢰도를 최종 신뢰도로 사용합니다. Primary의 튜닝 제안을 Verifier가 지지하지 않으면 제안을 비활성화합니다.

## 실행 및 이력

- ModuAgent 0.6.2 Standard execution
- Pydantic 구조화 출력
- timeout/network/HTTP 408/5xx에 한해 1회 재시도
- Tool 및 Memory 미사용
- Gemma thinking 비활성화
- Agent 입력과 검증된 출력은 DB에 암호화하여 저장
- framework run ID, agent fingerprint, failure ID와 안전한 실행 메타데이터 저장
- ModuAgent가 의도적으로 노출하지 않는 raw provider body와 private reasoning은 저장하지 않음
