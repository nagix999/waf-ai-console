# PR A — 후속 확인 정합성 v2.14

기준: `ASTRA_PR_ABC_FINAL_IMPLEMENTATION_INSTRUCTIONS.md`의 PR A. 시작 커밋은 `ec152fa` (`Deploy current changes to main`). PR B의 테스트 중지와 PR C의 데이터셋 화면 변경은 포함하지 않는다. 재배포·Git 게시·Production 승격은 하지 않았다.

## 구현

- `backend/app/agent/followup_policy.py`: 자연어·키워드 검사 없이 **최종 판정 / WAF 조치 / 확인 목적 / 최종 튜닝 제안**으로 처리한다.
- `backend/app/worker.py`: 최종 판정 결합 → 요청 완전성 검사 → 후속 확인 정책 → 분석가 안내 → 저장 → 선택적 근거·확인사항 정리 순서다.
- 최종 **정탐 + 차단(D)**: 모든 `impact_followup`을 제거한다. 튜닝 제안도 없으면 최종 확인 목록은 빈 배열이다.
- 모든 판정에서 `tuning_recommendation.recommended=false`이면 `tuning_validation`을 제거한다. 실제 제안이 있는 검증은 유지한다.
- 정탐+허용, 오탐, 보류의 영향 확인과 보류의 `decision_condition`은 이 정책으로 제거하지 않는다. 판정·심각도·근거·신뢰도·튜닝 제안 자체는 변경하지 않는다.
- `analyst_guidance.checks`를 만든 뒤 Editor에 전달하므로 삭제된 항목은 Editor 입력에 없다. 번호 기반 대표 선택·목적별 분리·실패 시 원래 목록 복귀는 유지한다.
- Primary/Verifier 구조화 기록과 암호화된 단계 출력은 보존한다. 진단에는 제거 건수·고정 사유 코드만 추가한다.

현재 실제 요청 완전성 Guard는 정상 판정(FP)의 문제 구간을 제한한다. 지시서의 TP→HOLD 예시는 Guard의 적용 범위를 넓히지 않고 모의 Guard를 넣은 순서 테스트로 확인했다. 실제 Guard의 공격 판정 보존 원칙은 유지한다.

## 버전·API·DB 호환성

- 새 후보: `waf-judgment-v2.14`, `waf-system-v2.14`.
- 의미 계약·원문 후보 선택·분석가 근거·request-integrity-v2 활성 목록에 v2.14를 포함한다. 구조 오류 교정, 독립 Verifier, 출력 예산은 그대로다.
- 새 후속 확인 정책은 명시적으로 v2.14에만 적용한다. 버전 문자열의 크기 비교나 미지의 미래 버전 자동 적용은 없다.
- `legacy_prompts_v213.py`는 시작 커밋의 v2.13 내용과 첫 주석을 제외하고 동일하다. 기존 v2.12 보존 경로와 함께 승인된 운영 baseline을 복원한다. 암호화된 기존 대기/실패 재실행 스냅샷은 새 지침으로 바꾸지 않는다.
- 승인된 Production은 코드 배포만으로 v2.14가 되지 않는다. 공식 Test → 승인 Ground Truth 평가 → Production Review → Apply 경로를 유지한다.
- 새 API 경로·요청 필드·DB migration·운영 의존성은 없다. 기존 분석 JSON의 `diagnostics.followup_policy`만 추가된다. 과거 결과를 갱신하는 작업은 없다.

제거 발생 시 진단의 예시(아래 숫자는 실제 모델 집계가 아님):

```json
{
  "followup_policy": {
    "version": "followup-policy-v1",
    "suppressed_count": 1,
    "reason_codes": ["true_positive_denied_impact_followup_suppressed"]
  }
}
```

다른 사유는 `tuning_validation_without_recommendation`이다. check 본문·HTTP·Cookie·비밀값은 진단에 복사하지 않는다.

## 화면·보고서

`decisionDetail.js`, `analysisReport.js`, `analysis_exports.py`에서 다음 제목을 사용한다.

| 목적 | KR | EN 화면 |
| --- | --- | --- |
| 판정 조건 | 판정에 필요한 확인 | Checks needed for a decision |
| 영향 확인 | 영향·대응 확인 | Impact and response checks |
| 튜닝 검증 | 튜닝 전 검증 | Pre-tuning validation |

빈 영역은 표시하지 않는다. 보류에서도 실제 항목·저장 조건이 없으면 빈 확인 카드를 만들지 않는다. 보류 사유에 따른 기존 검토 안내는 보고서의 판정 요약에 보존한다. 확정 판정의 영향 확인에는 “판정은 이미 확정되었습니다”를 안내하되, 보류의 영향 확인에는 그 문장을 사용하지 않는다.

화면·Markdown·PDF·Excel에 같은 제목·목적 구분을 적용한다. UI가 WAF 조치만 보고 과거 항목을 소급 삭제하지 않는다. 저장된 v2.13 항목은 새 제목 아래 그대로 볼 수 있다. 기존 보고서는 한국어이며 EN 전환이 모델 작성 본문이나 보고서를 번역하지는 않는다.

## 실제 모델 검증

사용자가 지정한 로컬 Test 프로필은 미지정이었다. 추가 승인을 받아 등록·전체 검증된 **gpt-5.4-mini / OpenAI**를 이번 후보로만 사용했다. Production·Test 기본 역할을 바꾸지 않았다.

- 새 코드의 실제 `process_moduagent` 경로, 별도 임시 SQLite DB에서 실행.
- 지침: v2.14 + 기본 편집 지침, 기본 입력 스키마. 운영의 사용자 편집 지침·스키마 검증은 아니다.
- Primary와 조건부 Verifier는 같은 승인 프로필. Editor는 꺼짐이며 해당 경로는 모의 모델 회귀로 확인했다.
- 원본 DB에서는 해당 프로필과 기존 전체 검증 승인만 읽었다. 실제 요청 직전 승인·프로필 변경을 재검사하고 원본 DB의 공통 LLM 슬롯 제한을 사용했다. 운영 이벤트·payload·분석 결과는 읽거나 복사하지 않았다.
- 임시 DB는 종료 후 삭제했다. 합성 결과는 `test-results/followup-policy-v214/live/`에 보존했다. 운영 UI에 테스트를 접수한 것은 아니다.

| 합성 사례 | 기대 | 실제 | 최종 확인 |
| --- | --- | --- | --- |
| 차단 SQL Injection | 정탐 | 정탐 | 0 |
| 차단 XSS | 정탐 | 정탐 | 0 |
| 차단 Command Injection | 정탐 | 정탐 | 0 |
| 차단 Path Traversal | 정탐 | 정탐 | 0 |
| 차단 JNDI 조회식 | 정탐 | 정탐 | 0 |
| 허용 SQL Injection | 정탐 | 정탐 | 0 |
| 차단 일반 검색 | 오탐 | 오탐 | 0 |
| 허용 일반 조회 | 오탐 | 오탐 | 0 |
| 차단 관리자 권한 변경 | 보류 | **정탐** | 0 |
| 허용 관리자 권한 변경 | 보류 | **오탐** | 0 |

10건 모두 실행 완료, 기대 답안 일치 **8/10**, 최종 후속 확인 정책 위반 **0건**. 모델이 처음부터 확인 항목을 생성하지 않아 서버 제거 건수도 **0건**이다. 삭제가 실제로 일어나는 경우는 단위/Worker 회귀로 검증했으며 실모델에서 발생한 것으로 표시하지 않는다.

보류 기대 2건은 같은 권한 변경 요청의 WAF 조치만 다르다. 차단 사례에서는 관리자 API 호출 자체를 공격으로 해석했고, 허용 사례에서는 권한 오남용의 증거가 없다는 이유로 정상으로 판단했다. 호출자 권한이 없는 입력에서 서로 다른 확정이 나왔지만 **이 두 결과만으로 WAF 조치가 원인이라고 입증할 수는 없다**. 반복·대조 검증이 필요하다. 이 품질 문제를 확인 목록 삭제나 Verifier 기준 완화로 감추지 않았다.

따라서 PR A의 정탐+차단 핵심 사례는 만족했지만, 실제 **HOLD+D/HOLD+A 보존 경로와 튜닝 검증 보존**까지 실모델로 확인한 것은 아니다. 소규모 작성자 기대 답안과의 일치율이며 승인된 Ground Truth 정확도가 아니다. 이 결과로 운영 승격을 권하지 않는다.

재현 자료: [합성 10건](../samples/waf-followup-v214/README.md), `backend/scripts/validate_followup_policy.py`. 도구는 정확한 검수 파일의 SHA-256과 명시적 `--execute-model-calls`를 요구한다. 기대 답안 불일치도 최종 실패 코드로 반환하도록 보강했다. 최초 실행의 `output_retries` 값은 수집 필드 오류가 있어 검증 수치로 사용하지 않는다. 수집기를 수정했지만 추가 유료 호출은 하지 않았다.

## 회귀·빌드 검증

- Backend 전체: 별도 임시 DB를 쓰는 4개 묶음에서 **2,422개 통과·3개 생략**(634+586+610+592). 전체 실행 뒤 추가한 v2.13 실패 재실행 1건을 포함해 정책 회귀 **25개 추가 실행 통과**. 중복 실행을 제외한 기본 테스트 통과 수는 2,423개다.
- Frontend: **538개 통과**, `npm run build`, `npm run build:report` 성공.
- 실제 ReportLab PDF + 목적별 내보내기 + 새 DB/기존 DB 업그레이드: **20개 통과**. Node 24 PATH로 실행했으며 Chromium은 필요하지 않다.
- 전체 실행의 생략 3개 중 실제 PDF 2개는 위 별도 실행에서 통과했다. 나머지 장문 레이아웃 golden fixture 1개는 준비된 파일이 없어 미실행이다. Starlette의 기존 deprecation 경고와 Vite의 500 kB 초과 번들 경고가 남는다.
- `semantic-contract-smoke.mjs`: KR/EN × SK/Light/Dark × 정탐/오탐/보류 + 모바일, **21장**. 키보드 펼치기·가로 넘침·오류·외부 요청 없음 확인.
- `analysis-decision-smoke.mjs`: 네 탭·원문 접근·이력·JSON·답안·보고서·재실행·딥링크, **50장** 브라우저 회귀 통과.
- 대표 이미지 직접 검토: `test-results/followup-policy-v214/inconclusive-KR-Light.png`, `true_positive-EN-Dark.png`, `true_positive-mobile.png`.
- OpenAI Docs 확인을 반영해 구조화 출력 통과를 의미적 정확성으로 취급하지 않고 서버 최종 정책·기존 스키마 검증을 병행했다. [공식 구조화 출력 가이드](https://developers.openai.com/api/docs/guides/structured-outputs).

## 운영·남은 범위

재배포와 Production Apply는 **미실행**이다. 현재 실행 중인 서비스의 코드·프로필·지침을 바꾸지 않았다. Git 커밋/푸시도 하지 않았다. 기존 미추적 `backend/waf_ai_console_backend.egg-info/`는 보존했다.

실제 Gemma/vLLM, 내부 운영 데이터, 운영의 사용자 편집 지침, 실제 Editor, 공식 Ground Truth 평가는 미검증이다. PR B/C는 미착수다. 다음은 보류 기대 2건의 권한·소유 조건 판단을 별도 범위로 대조 검증하고 공식 평가 후보를 준비하는 단계다.
