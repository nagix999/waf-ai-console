# Agent 의미 계약 2차 — 2026-09-29

기준: `ASTRA_AGENT_UI_SEMANTIC_ALIGNMENT_FINAL.md`의 **PR 2 — Agent 계약**. 시작 커밋은 `19380ae`이며, 기존 PR 1 작업을 보존했다. 이 기록은 [PR 1 표시 개선](Agent_UI_Semantic_Alignment_2026-09-29.md) 이후의 변경이다.

## 구현 내용

- 새 후보 분석은 `waf-system-v2.13` / `waf-judgment-v2.13`을 사용한다. Primary와 독립 Verifier에 같은 공통 지침과 입력을 제공한다. Verifier 조건·실패/불일치 보류·최종 판정 정책은 유지한다.
- 탐지 설명과 요청의 관계를 `signature-assessment-v2`로 구조화했다. 관계 요약은 기록된 relation으로 만들고, 모델은 일치 지점·차이 지점·비교 제한을 설명한다. `mismatch + 정탐`은 허용하며 relation으로 판정을 바꾸지 않는다.
- 확인 항목의 `purpose`를 필수로 받는다. 보류 조건, 선택적 영향·대응 확인, 튜닝 검증을 화면·보고서·PDF·Excel에서 구분한다.
- 새 결과의 `recommended_checks`는 빈 배열이다. 확인사항의 기준은 `analyst_checks`이며, 모델이 반환하지 않은 보류 조건을 서버가 임의로 채우지 않는다. 원문 근거 검증 실패는 해석 주의사항으로 남긴다. 실제 누락이 관찰된 기존 입력 무결성 방어는 유지하고, 이때 필요한 확인에만 `decision_condition`을 붙인다.
- Result Editor는 원래 항목의 묶음과 대표 번호만 정한다. 서로 다른 자료나 purpose는 병합하지 못한다. purpose·문장·판정·관계를 바꾸지 않는다.
- 기록된 요청·WAF 조치를 다시 입증하도록 요구하지 않고, 공격 시도와 성공을 분리하는 지침을 추가했다. D/A는 여전히 관측값이며 자동 판정 규칙으로 사용하지 않는다.

### 새 출력 예

```json
{
  "signature_assessment": {
    "version": "signature-assessment-v2",
    "relation": "partial",
    "matched_points": ["SQL 조건을 바꾸려는 구문은 탐지 설명과 관련됩니다."],
    "mismatched_points": ["탐지 설명과 달리 요청 본문에 구문이 있습니다."],
    "uncertainty_ko": null,
    "explanation_ko": null
  },
  "analyst_checks": [{
    "purpose": "impact_followup",
    "source_ko": "애플리케이션 처리 기록",
    "check_ko": "같은 요청으로 실제 조회 범위가 달라졌는지 확인합니다.",
    "why_ko": "판정과 별개로 영향 범위를 파악하는 데 도움이 됩니다."
  }],
  "recommended_checks": []
}
```

| 구조 | 검사 |
| --- | --- |
| exact | 일치 지점 ≥ 1, 차이 지점 없음, 비교 제한 null |
| partial | 일치·차이 지점 각각 ≥ 1 |
| mismatch | 차이 지점 ≥ 1. 정탐과 함께 사용 가능 |
| unknown | 비교 제한 이유 필수 |
| 정탐·오탐의 확인 | `decision_condition` 금지 |
| 보류의 확인 | 세 목적 모두 가능하나 화면에서 분리 |

지점은 각각 최대 5개, 문장당 400자이며 비교 제한은 최대 500자다. 빈 문장은 거절한다. 자연어 키워드 금지 목록으로 의미를 추정하지 않는다. **구조 검사를 통과했다고 설명의 사실성까지 검증된 것은 아니다.**

### 교정 재시도

기존 executor를 재사용한다. `signature_mismatch_requires_difference`, `signature_unknown_requires_reason`, `decisive_verdict_disallows_decision_condition` 같은 고정 코드·필드 위치·안내만 전달한다. 실패한 모델 답변이나 원문 조각을 오류 안내에 복사하지 않는다. 최초 포함 최대 4회의 기존 상한, 모델·출력 한도·호출 경로는 유지한다. 수정에 실패하면 기존 실패/보류 경로를 따른다.

OpenAI Docs의 [Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs)에 따라 새 wire schema의 필수 필드·null·추가 속성 금지 조건을 검사했다. OpenAI와 vLLM은 서로의 공급자 옵션을 공유하지 않는다. SDK나 운영 의존성을 추가하지 않았다.

## 기존 기록과 운영 구성 보존

- 바깥 결과 버전 `waf-analysis-v2`는 유지한다. 새 계약은 중첩된 signature 버전과 purpose로 식별한다. 기존 결과의 누락된 필드는 호환 모델로 읽고, purpose나 일치/차이 지점을 추정해 저장하지 않는다.
- 과거 `explanation_ko`는 ‘기존 분석 설명’으로 보존한다. 기존 결과에만 `recommended_checks` fallback을 유지한다.
- 대기 중인 실행과 재실행은 고정된 프롬프트를 사용한다. 구버전 출력 schema에서 새 필드를 요구하지 않는다. 공급자 연결 검증의 작은 기존 schema도 유지하고, 새 분석 계약의 검증은 후보 테스트에서 수행한다.
- 기존 Editor `result-editor-v2`와 표시 `follow-up-editor-v1`을 계속 읽는다. 새 Editor는 `result-editor-v3`, purpose가 있는 표시 기록은 `follow-up-editor-v2`다. 과거 묶음을 새 항목에 잘못 적용하지 않는다.

### 고정 시스템 지침도 공식 승격 대상

기존 코드에서는 관리자 지침 버전은 같아도 배포된 코드의 최신 공통 지침으로 새 Production 요청을 구성할 수 있었다. 이번 계약 변경이 운영에 자동 적용되지 않도록 다음 경계를 추가했다.

1. Production은 최신 승인 승격 기록의 공식 테스트에 암호화 저장된 전체 지침을 사용한다.
2. 승격 이전 baseline은 시작 커밋과 동일하게 보존한 v2.12 템플릿과 당시 지침 버전으로 재구성하고 해시를 대조한다. 신규 설치의 v2.13 baseline도 지원한다.
3. 새 후보는 v2.13을 테스트한다. 공식 Ground Truth 평가 → Production Review → Apply를 완료한 뒤에만 Production이 새 계약을 사용한다.
4. Editor도 승인된 버전·지침 해시를 유지한다. 설정 모델을 다른 모델로 자동 대체하지 않는다.

승인 스냅샷의 손상, 사라진 원본 테스트, 지원되지 않는 더 오래된 baseline 버전은 최신 지침으로 우회하지 않고 거절한다. 해당 상태의 운영 DB는 배포 전 별도로 확인해야 한다. 이 작업에서 실제 운영 DB를 열거나 고치지 않았다.

## API / DB / 의존성

- 새 endpoint나 DB migration 없음. 기존 `0025_analysis_read_index` 유지.
- 분석 상세·Agent 기록의 JSON에 위 중첩 필드가 추가된다. 과거 결과 JSON을 덮어쓰지 않는다.
- Production 조회·승격 API는 기존 경로를 사용하되, 구성 해시는 승인된 공통 지침/Editor 버전을 반영한다.
- ReportLab PDF, 암호화, 관리자 원문/이력 접근과 감사, URL 이동, 모델 승인·내부 egress 경계를 유지한다.
- 새 패키지·Chromium·외부 서비스 없음. 브라우저는 로컬 UI 검증에만 사용했다.
- 고정 지침은 v2.12의 3,574자/7,353 bytes에서 v2.13의 4,113자/8,232 bytes로 증가했다. 기본 Primary 전체는 3,931자에서 4,470자로 증가했다. 이는 문자/UTF-8 크기이며 토큰 수나 성능 측정이 아니다. 새 계약 의미를 명시하기 위한 증가이며 기존 문맥·보안 지침을 삭제하지 않았다.

## 검증

검증은 합성 입력·Mock HTTP·임시 SQLite DB로 수행했다. 실제 LLM 호출이나 유료 검증은 하지 않았다.

- 계약 구조, relation × 판정 조합, purpose × 판정 조합, 구버전 serialization, 두 공급자의 교정 성공/4회 중단, 안전한 오류 기록 검사.
- 기존 Production v2.12 유지 → 새 공식 후보 v2.13 → 승인 후 동일 지침 적용, 손상 스냅샷 우회 금지, 구버전 Editor 유지 검사.
- WAF D/A × 정탐/오탐/보류, PDF 허용 필드, Excel의 구조화 설명·목적별 구분 검사.
- **Backend 전체:** 107개 테스트 파일을 네 프로세스로 실행해 **2,396 passed / 0 failed / 3 skipped**. 임시 fresh DB → head와 기존 스키마의 데이터 보존 upgrade 회귀 포함. 실제 운영 DB는 사용하지 않았다.
- **실제 PDF / 내보내기:** 최종 보고서 빌드에서 **17 passed**. 전체 검사에서 기본 제외한 PDF 연동 2건도 이 검사에서 실행했다. 남은 선택형 golden PDF 1건은 준비된 자료가 없어 미실행이다. 새 계약을 담은 Light/Dark PDF 3페이지도 각각 생성했다.
- **Frontend:** **536 passed / 0 failed**. `npm run build`, `npm run build:report` 통과. 기존 대형 JS 번들 경고와 Starlette/AnyIO deprecation 경고는 남아 있다.
- 전체 검사에서 발견한 구버전 고정 기대값과 mocking 경계를 갱신한 후 전체를 다시 실행했다. 구조 없는 새 확인 목록을 자동 생성하지 않는 기대값도 갱신했다. 브라우저 이력 테스트는 무작위 UUID에 `420`이 포함되면 실패하던 정규식을 저장 구조 검증으로 바꿨으며 제품의 이력 동작은 변경하지 않았다.
- 프로필 연결 검증의 기존 schema를 명시적으로 확인하는 회귀 **36 passed**. `git diff --check` 통과.
- 새 화면 21개, 기존 표시·평가·가져오기 화면 17개 브라우저 캡처. KR/EN, SK/Light/Dark, 모바일 390px, 가로 넘침, 키보드 펼치기, 초점 이동과 기존 기록 호환 확인. 저장된 한국어 분석 본문을 임의로 영문 번역하지 않는다.
- 기존 `analysis-decision-smoke.mjs`도 50개 캡처로 통과했다. 원문 접근, 실행 이력, JSON, 참고 라벨, 보고서, 재실행 창, 이전 URL과 키보드 동작을 확인했다.
- `r5-final-smoke.mjs` 통과: 기본 설정, 고정 구성 복제, 가져오기 미리보기/확정, Production 범위, 문항 이동·뒤로가기·초점과 테마/언어 확인. 모든 브라우저 검사는 외부 요청을 차단했다.

재현 명령:

```bash
cd backend
.venv/bin/pytest -o addopts='' -q -p no:cacheprovider
WAF_TEST_REPORT_RENDERER=1 .venv/bin/pytest tests/test_report_pdf.py tests/test_semantic_exports.py -o addopts='' -q
cd ../frontend
node --test src/*.test.mjs
npm run build
npm run build:report
node scripts/semantic-contract-smoke.mjs
node scripts/semantic-alignment-smoke.mjs
```

주요 화면: [보류 · Light](../test-results/semantic-contract/inconclusive-KR-Light.png), [오탐 · EN/Dark](../test-results/semantic-contract/false_positive-EN-Dark.png), [정탐 · SK](../test-results/semantic-contract/true_positive-KR-SK.png), [보류 · 모바일](../test-results/semantic-contract/inconclusive-mobile.png). 캡처는 로컬 검사 산출물이며 운영 데이터가 아니다.

## 미검증 / 다음 단계

- 실제 Gemma/vLLM/OpenAI의 새 출력 계약 준수율, 토큰 사용량, 보류율·미탐·과탐 변화는 미검증이다. 자동 검사 통과를 판정 정확도 향상으로 해석하지 않는다.
- 다음은 승인된 테스트 데이터의 후보 실행으로 구버전/새 버전을 비교하는 단계다. 예산·모델 승인 없이 실제 호출하지 않는다.
- 운영 DB 적용, Docker 재배포, Git commit/push는 수행하지 않았다.
- Ground Truth 사람 검토 제도, Production 품질 합격선, Verifier 전략 변경은 PR 3 이후 별도 승인 범위로 남긴다.
