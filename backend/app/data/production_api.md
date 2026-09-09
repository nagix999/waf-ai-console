# Production WAF Analysis API v0.2.0

기준일: 2026-09-08. 내부 WAF 수집기와 분석가 시스템을 위한 REST 계약이다.
이 문서는 실제 구현을 기준으로 한다. 상위 설계서의 미구현 재분석·범용 배치·모델 비교 API와 구분한다. 별도 참고 Label 연결과 테스트 파일 답안 비교는 아래 계약을 사용한다. 설정의 선택형 150건 후보 검증은 [별도 정의서](Model_Validation_Dataset_v0.1.md)를 따른다.

## 연결과 인증

- 서버 Origin (`WAF_API_BASE_URL`): `https://<internal-host>` (끝에 `/api/v1`을 붙이지 않음)
- API prefix: `/api/v1`
- 현재 로컬 연결: `http://localhost:18000/api/v1`
- Swagger: `/docs`, OpenAPI JSON: `/openapi.json` (API 호스트 기준)
- 최신 정의서: `GET /api/v1/production-api` (Markdown 및 입력 스키마 정보), PDF: `GET /api/v1/production-api.pdf`. 두 경로 모두 ingest 권한 또는 관리자 세션이 필요하며 현재 활성 입력 스키마를 반영한다.
- PDF는 전체 12개 항목과 입력 필드·제한·예시를 포함한다. 웹의 검색·선택 항목과 무관하다. 화면과 같은 정의를 받으려면 `expected_schema_version_id`와 `expected_schema_hash`를 query로 보낼 수 있다. 현재 활성 정의와 다르면 409 `input_schema_document_changed`이며 정의서를 새로 조회해야 한다. 출력 한도 초과는 413, 생성 불가는 503이고 부분 PDF는 제공하지 않는다.
- 요청 및 응답: UTF-8 JSON. 파일 업로드만 `multipart/form-data`.
- 서비스 인증: `X-API-Key: <SERVICE_API_KEY>`
- 설정 → 서비스 API Key에서 복수 키를 발급·이름 수정·삭제한다. 삭제하면 인증을 차단하고 키 목록·키별 대시보드에서 제거하지만 기존 분석·감사 이력은 보존한다. 만료 기간은 없으며 키마다 source system과 `ingest` / `review` 권한을 지정한다. 원문은 발급 직후 한 번만 표시한다. 관리자 API는 [서비스 키 정의서](Service_API_Keys_v0.1.md)를 따른다.
- 환경변수 기반 배포 키는 제거했다. `WAF_BOOTSTRAP_API_KEY`, `WAF_BOOTSTRAP_SOURCE_SYSTEM`, `WAF_BOOTSTRAP_API_KEY_ENABLED`는 새 버전에서 무시한다. 기본 서비스 키는 없으며 새 버전 배포 후 관리자 설정에서 발급한 키로 연동 시스템을 전환해야 한다.
- 서비스 조회와 리뷰 등록은 자신의 `source_system`에 속한 분석에만 허용한다. 다른 소스의 ID는 `404`로 응답한다.
- 관리자 세션은 전체 목록·상세와 원문·Agent 이력을 조회할 수 있다. 원문·Agent 이력 열람에는 접근 감사를 남긴다.

관리자 세션 Cookie와 API Key를 함께 보내면 관리자 세션이 우선한다. 관리자 로그인 상태의 Swagger도 이 규칙을 따른다. 서비스 권한·source를 검증할 때는 Cookie가 없는 HTTP 클라이언트를 사용하고 `/auth/me` 응답을 확인한다.

쿠키 없는 서비스 API Key 접수·업로드·리뷰에는 Origin/CSRF 토큰을 요구하지 않는다. 로그인·로그아웃과 관리자 세션의 변경 요청은 정확한 서비스 Origin을 검사하며, Origin이 없을 때만 같은 출처의 Referer를 허용한다. 둘 다 없거나 다른 출처이면 403이고 API Key를 함께 보내도 우회되지 않는다. 운영 설정의 `WAF_PUBLIC_ORIGIN`과 다른 Host는 421로 거부한다. 수집기는 IP 직결 주소 대신 승인된 HTTPS 서비스 주소를 사용한다.

인증 확인:

```http
GET /api/v1/auth/me
X-API-Key: <SERVICE_API_KEY>
```

실제 키를 소스, URL, 로그에 넣지 않는다. 예시 이벤트는 모두 테스트 데이터다.

## 목적과 유입 경로

클라이언트가 목적을 정하는 입력 필드는 없다. 서버가 접수 경로로 결정한다.

| 접수 경로 | 권한 | `analysis_purpose` | `ingest_channel` |
| --- | --- | --- | --- |
| `POST /analyses` | ingest | `production` | `service_api` |
| `POST /uploads` | ingest | `production` | `file_upload` |
| `POST /test-analyses` | admin | `test` | `test_lab` |
| `POST /test-uploads` | admin | `test` | `file_upload` |
| `POST /test-runs` | admin | `test` | `test_lab` |
| `POST /test-runs/uploads` | admin | `test` | `file_upload` |
| 선택형 모델 전체 검증의 테스트 데이터 150건 | admin | `test` | `model_validation` |
| 분류 도입 전 데이터 | 기존 권한 유지 | `legacy_unknown` | `legacy_unknown` |

웹의 **테스트 분석**은 사용자 테스트명을 받는 `/test-runs`와 `/test-runs/uploads`를 사용한다. 접수 당시 **Test 용도로 지정한 프로필**·프롬프트를 고정하고 일반 분석 worker를 사용한다. 실제 모델 모드에서 Test가 미지정이면 409이며 Production으로 대체하지 않는다. Production과 Test는 각각 전체 검증을 통과한 프로필만 지정할 수 있다. 기존 `/test-analyses`·`/test-uploads`도 이제 필수 query `name`, `idempotency_key`를 받아 이름 있는 실행에 연결하며 기존 응답에 `test_run_id`를 추가한다. 이름·키 없는 관리자 테스트 접수는 422다. Production 접수 계약은 그대로다. 설정의 테스트 데이터 150건 검증은 후보 프로필·model-test worker를 유지한다. 상세 계약은 [테스트 실행 및 평가 정의서](Test_Runs_and_Evaluation_v0.1.md)를 따른다.

기존 데이터는 이벤트명이나 ID 접두사로 추정하지 않는다. `전체` 조회에 포함되지만 운영·테스트 필터에는 포함되지 않는다. 분류와 유입 경로는 LLM 판정 입력에 추가하지 않는다.

## 단건 분석 접수

```http
POST /api/v1/analyses?wait_seconds=0
X-API-Key: <SERVICE_API_KEY>
Content-Type: application/json
```

`wait_seconds`는 정수 0~60, 기본 0이다. worker는 HTTP 요청과 독립적으로 실행된다. 대기 시간 만료나 클라이언트 연결 종료가 접수된 작업을 취소하지 않는다.

<!-- INPUT_SCHEMA_START -->
아래는 출고 기본 스키마다. 현재 적용한 필드 정의는 인증된 `GET /api/v1/production-api`와 웹 UI의 Production API, `/openapi.json`에서 확인한다. 설정 → 입력 스키마에서 새 버전을 적용하면 새 접수 검증과 이 문서의 온라인 필드 정의에 즉시 반영된다. 저장소의 이 파일은 기본 계약을 설명하는 템플릿이다.

| 필드 | 필수 | 제한과 의미 |
| --- | --- | --- |
| `event_id` | O | 1~255자, 소스 내 이벤트 고유 ID |
| `company_name` | O | 1~255자 |
| `src_ip` | O | 1~64자, 현재 문자열 길이 검증 |
| `dest_ip` | O | 1~64자, 현재 문자열 길이 검증 |
| `src_port` | X | 정수 0~65535 또는 null |
| `dest_port` | X | 정수 0~65535 또는 null |
| `payload` | O | 빈 문자열 금지, UTF-8 인코딩 기준 기본 최대 2 MiB (`WAF_PAYLOAD_MAX_BYTES`) |
| `signature` | X | WAF 시그니처 문자열 또는 null |
| `event_name` | X | 최대 500자 또는 null |
| `waf_vendor` | O | 1~120자 |
| `waf_action` | O | `D`(Deny), `A`(Allow); 소문자도 대문자로 정규화 |
<!-- INPUT_SCHEMA_END -->

필드명·설명·타입·필수·null 허용·제한은 함께 버전관리한다. 신규 이벤트는 접수 당시 전체 정의를 암호화해 고정하고 응답 `input_schema_metadata`에서 버전과 지문을 확인한다. 동일 내용의 이벤트 재전송은 최초 접수 버전을 유지한다. 이미 접수한 테스트도 버전 변경이나 복귀로 재해석하지 않는다. 추론 단계의 암호화된 이력에는 당시 정의를 남기지만 필드 설명을 LLM 입력에 추가하지 않는다.

추가 필드의 JSON 타입은 엄격하게 검사한다. CSV는 기존 두 포트 필드만 숫자로 변환하며 다른 셀은 문자열이다. 숫자·배열·객체 등 사용자 추가 타입은 JSON 업로드를 사용한다. 필수는 키의 존재, nullable은 명시적인 null 허용으로 별개 조건이다. payload UTF-8 바이트 제한은 스키마의 문자 길이 제한과 별도로 적용된다.

`waf_action`은 WAF 관측값이며 정답이나 심각도를 지정하지 않는다. payload와 Cookie를 마스킹하지 않고 암호화 저장한다. 결과의 원문 발췌에도 민감한 내용이 포함될 수 있으므로 결과를 공유하거나 로깅할 때 동일한 데이터 접근 정책을 적용한다.

정의되지 않은 벤더 확장 필드는 `extra_fields`에 보존된다. `occurred_at`, `attributes`는 현재 별도 타입·검색 필드가 아닌 확장 데이터다. `source_system`, 목적, 내부 ID, 상태, 판정, 모델·프롬프트 설정 등 서버 제어용 예약 필드는 요청에서 지정할 수 없다.

정답 혼입을 막기 위해 최상위의 `label`, `expected_verdict`, `reference_label`, `ground_truth` 등 평가 필드와 `difficulty`, `expected_severity`, `rationale_ko`, `important_evidence` 등 참고 답안 필드도 422 `evaluation_labels_require_separate_attachment`로 거부한다. Label은 아래 별도 연결 API로 제공한다. 이는 임의의 중첩 필드나 payload 본문에서 정답 문장을 찾아 제거하는 기능이 아니며, 기존 입력 내용을 조용히 삭제하거나 재해석하지 않는다.

아래 요청은 기본 스키마의 예시 입력다. 운영 스키마에서 추가한 필수 필드·허용값·제한에 맞게 수정한 뒤 전송해야 한다.

```json
{
  "event_id": "evt-prod-20260905-000001",
  "company_name": "sample-company",
  "src_ip": "192.0.2.10",
  "dest_ip": "198.51.100.20",
  "src_port": 43120,
  "dest_port": 443,
  "payload": "GET /search?q=%27%20OR%201%3D1-- HTTP/1.1\r\nHost: example.internal\r\n\r\n",
  "signature": "Synthetic SQL Injection",
  "event_name": "Synthetic WAF Detection",
  "waf_vendor": "generic",
  "waf_action": "D"
}
```

## 접수 응답과 polling

POST는 `AnalysisDetail` 객체를 반환한다. 내부 분석 ID의 필드명은 `id`이며 `analysis_id`, `status_url` 필드는 없다. 아래는 핵심 필드만 발췌한 예시다.

```json
{
  "id": "6b683a42-9ba2-4b98-aea5-eab1283bca98",
  "source_system": "internal-parser",
  "event_id": "evt-prod-20260905-000001",
  "analysis_purpose": "production",
  "ingest_channel": "service_api",
  "status": "pending",
  "verdict": null,
  "severity": null,
  "result": null,
  "started_at": null,
  "completed_at": null,
  "error_code": null,
  "error_message": null
}
```

| HTTP | 응답 시점의 상태 |
| --- | --- |
| 202 | `pending` 또는 `processing` |
| 200 | `completed` 또는 `failed` (완료된 중복 접수 포함) |

조회는 `GET /api/v1/analyses/{id}`를 사용한다. 조회 성공은 진행 상태와 무관하게 HTTP 200이다.

1. 접수 응답의 `id`를 저장한다.
2. `pending`, `processing`이면 1초 후 조회하고 간격을 2~5초로 늘린다. 여러 클라이언트는 약간의 무작위 지연을 추가할 수 있다.
3. `completed`이면 최종 결과를 사용한다.
4. `failed`이면 `error_code`, `error_message`를 처리한다.

`completed + inconclusive`는 정상 완료된 보류 판정이다. HTTP 200이나 `confidence_score`만으로 성공·공격 여부를 결정하지 않는다. webhook과 서비스 키를 통한 임의 재분석은 제공하지 않는다. 관리자는 `GET /analyses/{id}/retry-eligibility`로 실패 당시 설정의 보존 여부를 확인하고 별도 `POST /analyses/{id}/retry`로 실패 재실행을 요청할 수 있다. 기존 실패는 보존하며 당시 설정을 확인할 수 없으면 현재 모델로 대체하지 않고 차단한다.

환경변수에 키가 이미 안전하게 주입되었다고 가정한 호출 예시:

```bash
curl --silent --show-error --fail-with-body \
  -X POST "${WAF_API_BASE_URL}/api/v1/analyses?wait_seconds=0" \
  -H "X-API-Key: ${WAF_API_KEY}" \
  -H "Content-Type: application/json" \
  --data-binary @synthetic-event.json

curl --silent --show-error --fail-with-body \
  -H "X-API-Key: ${WAF_API_KEY}" \
  "${WAF_API_BASE_URL}/api/v1/analyses/${WAF_ANALYSIS_ID}"
```

## 완료 결과와 버전

상세 응답에는 목록 필드와 `result`, `prompt_version`, 선택 정책 ID인 `prompt_policy_version_id`(기존 데이터는 null), `model_profile`, 오류 정보가 포함된다. 접수 응답의 프롬프트는 선택된 지침이며 실제 LLM 실행 완료를 뜻하지 않는다. 새 판정은 `result.schema_version="waf-analysis-v2"`다. 아래는 `result`의 핵심 필드 예시이며 서버가 Primary/Verifier·정책·실행 메타데이터도 덧붙인다.

모델 공급자는 관리자가 등록한 Production LLM 프로필(`vllm` / `openai`)로 결정한다. 접수 요청에서 provider나 외부 주소를 지정하지 않는다. 새 실제 분석의 `result.agent`에는 `llm_provider`, `model_name`, `model_profile_id`, `profile_fingerprint`, `external_data_approved`를 함께 기록한다. OpenAI의 `thinking_enabled`는 null이며 추론 비활성화 사실을 뜻하지 않는다. 이전 결과의 미기록 항목은 추정하지 않는다. OpenAI Production을 사용할 경우 마스킹하지 않은 payload·Cookie 등이 외부로 전송되므로 관리자 승인과 사내 반출 정책 확인이 선행되어야 한다.

```json
{
  "schema_version": "waf-analysis-v2",
  "verdict": "true_positive",
  "confidence_score": 0.87,
  "summary_ko": "SQL 조건 우회 구문과 주석 표기가 함께 확인되어 SQL Injection 시도로 판단합니다.",
  "threat_analysis": {
    "severity": "HIGH",
    "category": "sql_injection",
    "target": "payload.query",
    "technique_ko": "논리식과 주석으로 SQL 조건을 우회하려는 기법입니다.",
    "obfuscations": ["url_encoding"],
    "potential_impact_ko": "입력이 쿼리에 직접 사용되는 취약점이 있다면 인증 우회나 비인가 조회로 이어질 수 있습니다."
  },
  "signature_assessment": {
    "relation": "exact",
    "explanation_ko": "시그니처 탐지 의도와 입력의 SQL 조건 우회 패턴이 일치합니다."
  },
  "evidence": [{
    "field": "payload",
    "excerpt": "%27%20OR%201%3D1--",
    "interpretation_ko": "쿼리에 URL 인코딩된 따옴표, OR 조건식과 주석 표기가 연속해서 있습니다. 디코딩하면 항상 참인 SQL 조건을 삽입하는 패턴으로 해석됩니다. 시그니처와 부합하는 공격 시도 근거이지만 실제 쿼리 실행이나 침해 성공까지 입증하지는 않습니다."
  }],
  "recommended_checks": ["대상 애플리케이션의 쿼리 처리 및 요청 로그를 확인하세요."],
  "tuning_recommendation": {
    "recommended": false,
    "scope": null,
    "proposal_ko": null,
    "risk_ko": null,
    "validation_ko": null
  },
  "conflicting_evidence": [],
  "input_truncated": false
}
```

| `verdict` | 의미 | 허용 `severity` |
| --- | --- | --- |
| `true_positive` | 공격 또는 명확한 보안정책 위반 | CRITICAL, HIGH, MEDIUM, LOW |
| `false_positive` | 정상 요청의 오탐 | NONE |
| `inconclusive` | 현재 근거로 확정 불가 | UNKNOWN |

- 자동 연동은 최종 `verdict`와 `result.threat_analysis.severity`를 사용한다. 목록의 `severity`는 최종값의 조회용 복사본이다.
- `confidence_score`는 0~1 모델 자기평가이며 보정된 확률이 아니다.
- 근거 최대 5개, 발췌 최대 300자, 한국어 해석 최대 2,000자. `evidence.field`가 지정한 원문 구간에서만 일치 여부를 검증하며 다른 필드에만 있는 발췌나 디코딩한 문자열은 인정하지 않는다. 확정 판정의 유효 근거가 모두 제거되면 inconclusive/UNKNOWN으로 강등한다. 출처 검증은 해석의 의미적 정확성을 보장하지 않는다.
- `signature_assessment.relation`: exact / partial / mismatch / unknown.
- Verifier 실패 또는 판정 불일치는 최종 inconclusive/UNKNOWN이다. Primary 출력 검증은 교정 1회 후에도 실패하면 분석 failed로 종료한다.
- v2에는 `uncertainties`가 없다. 추가 확인 작업은 `recommended_checks`에 있다.
- ModuAgent 모드의 현재 프롬프트 코드 기준은 `waf-judgment-v2.6`이며 새 접수의 `prompt_version`은 `waf-judgment-v2.6/policy-N` 형식이다. Production과 Test는 같은 활성 공통 프롬프트를 사용한다. 결과 계약은 `waf-analysis-v2`를 유지한다. stub 모드는 별도 프롬프트 식별값을 사용한다.
- 정책은 서버가 접수 시 선택·고정한다. 운영 버전 변경은 이후 새 접수에만 적용되며 대기 작업·재시도·재선점·중복 접수는 원래 버전을 유지한다. 요청에서 정책 ID·스냅샷·지침을 지정할 수 없다. 관리자 버전 관리 API와 `0006` 마이그레이션은 [프롬프트 버전 관리 정의서](Prompt_Policies_v0.1.md)를 참고한다.
- `input_truncated=true`는 payload뿐 아니라 이벤트 메타데이터·파서 힌트가 축소된 경우도 포함한다. 정확한 모델 토큰 수는 운영 모델에서 별도 검증해야 한다.
- v1 JSON은 그대로 보존한다. 과거 결과에 심각도가 없다면 응답 최상위 `severity`는 null이고 보존된 `result` 내부 키는 누락될 수 있다. 임의 추정하지 않는다.
- 요청 본문에서 모델 프로필을 선택하지 않는다. 서버의 Production 설정을 사용한다. stub 모드는 `inconclusive/UNKNOWN`을 반환하며 실제 LLM 판정이 아니다.

### 분석가 확인 안내와 관리자 디코딩 조회

새 결과는 `analyst_checks`에 최대 5개 `{source_ko, check_ko, why_ko}`를 추가할 수 있다. 각각 확인할 자료(240자), 확인할 내용(800자), 판단에 도움이 되는 이유·구분 조건(800자)다. `recommended_checks`는 유지한다. 서버의 `result.analyst_guidance={summary_ko, checks, limitations}`는 최종 판정을 바꾸지 않고 분석가용 안내를 제공한다. 기존 결과에는 이 필드가 없을 수 있다. 연동 판정은 여전히 최종 `verdict`이며 새 안내가 재판정이나 확인 완료를 뜻하지 않는다.

v2.4는 보류에서 판정을 구분하는 확인 1~3개, 확정 판정에서는 영향 범위·대응 안전성 등에 유용한 선택적 후속 확인만 1~2개 요청한다. 확정 판정의 기본값은 두 확인 목록 모두 빈 배열이다. `analyst_guidance.checks=[]`도 명시적인 빈 목록으로 취급하며 이전 필드로 재채우지 않는다. 신뢰도 숫자로 확인 필요 여부를 결정하지 않는다. UI와 보고서의 추가 확인은 마지막에 배치한다. 같은 출처·원문·해석의 완전 중복 근거는 최종 결합에서 제거하되 개별 Agent 출력과 기존 결과는 보존한다. 문장 유사도로 반대 해석이나 다른 출처를 삭제하지 않는다.

일부 발췌가 원문 출처 검증에서 제외됐다는 기존 시스템 문구는 확인 업무가 아니라 `analyst_guidance.limitations`의 주의사항으로 표시한다. 저장된 이전 결과에도 UI가 같은 구분을 적용하지만 API의 과거 결과를 다시 쓰지는 않는다.

관리자 전용 `GET /api/v1/analyses/{id}/event`는 기존 원문 응답에 `decoding`을 추가한다. 서비스 키로 조회할 수 없고 기존 `view_raw_event` 감사를 남긴다. `decoding`은 조회 당시의 로컬 도구 결과이며 과거 모델의 분석 입력을 재현하지 않는다. 과거 분석·Label·원문을 수정하거나 LLM을 호출하지 않는다.

`decoding`의 필드는 `decoder_version`, `items`, `warnings`, `scan_truncated`, `scanned_chars`, `total_chars`다. 각 item은 `id`, `field="payload"`, `start`, `end`, `original`, `decoded`, `steps=[{encoding,input,output}]`, `warnings`를 포함한다. offset은 변경하지 않은 원문의 0부터 시작하는 Python 문자 위치이며 end는 미포함이다. 원문·변환 결과는 동일한 민감정보 정책으로 다루고 HTML/코드로 실행하지 않는다.

지원 범위는 URL percent, legacy `%uNNNN`, 세미콜론 있는 HTML 엔티티, `\uNNNN`/`\xNN`/`\u{...}`, 보수적 표준·URL-safe Base64와 Log4j 스타일 조회식의 정적 단순화이며 실제 애플리케이션 해석·공격성은 확인하지 않는다. 새 encoding 값은 `url_percent_u`, `base64url`, `log4j_lookup_static`이며 기존 encoding도 유지한다. 누락 padding 보완에는 `base64_padding_inferred`, legacy·중괄호 Unicode에는 문법별 candidate 경고를 표시한다. UTF-8 외 바이트, 잘못된 padding/혼합 alphabet, 압축, 문맥 없는 plus/hex/octal/CSS 해석은 지원하지 않는다.

JNDI는 실행하지 않는다(`jndi_lookup_not_executed`). ASCII lower/upper 및 중첩 식을 정적으로 해석하고 `${::-j}`/`${:-j}`는 속성이 정의되지 않은 조건의 후보(`default_lookup_candidate`)로만 표시한다. env/sys 등의 실제 값은 추정하지 않는다(`unresolved_lookup`). 변환하지 않은 조회식도 `original=decoded`, `steps=[]`와 경고로 반환할 수 있다. `$${...}` 지연 조회식은 원문을 남긴다. 이는 취약 Log4j 사용·외부 연결·침해 성공을 확인했다는 뜻이 아니다.

최대 262,144자 검색·후보 시도 256회·20개 결과·원문 조각 1,024자·결과 4,096자·3단계·직렬화 32 KiB 제한이다. 출력이 없거나 제한됐다고 인코딩 또는 공격 부재를 뜻하지 않는다. 전체 변환 결과는 일반 `AnalysisDetail.result`에는 추가하지 않는다. 새 실행의 `agent.preprocessors=["waf-text-decoder-v2"]`는 worker 전처리 기록이며 모델 Tool 호출이나 추가 LLM 호출이 아니다. 이전 decoder 버전의 실행 이력을 변경하지 않는다.

## 소요 시간

분석 목록·상세에 다음 값을 밀리초 정수 또는 null로 반환한다.

| 필드 | 의미 |
| --- | --- |
| `total_elapsed_ms` | 접수(`created_at`)부터 완료까지; 진행 중이면 조회 시각까지 |
| `queue_wait_ms` | 접수부터 worker의 최초 선점까지; 대기 중이면 현재까지 |
| `processing_duration_ms` | 최초 선점부터 완료까지; 실행 중이면 현재까지, 미시작이면 null |

lease 만료 후 재선점되어도 최초 시작은 유지한다. 처리 시간은 복구 대기도 포함하는 경과시간이며 CPU 사용시간이나 단계 합계가 아니다. 과거 데이터에서 최초 선점 이력이 없으면 이를 역산하지 않는다.

관리자 전용 `GET /analyses/{id}/agent-runs`는 실행과 각 단계의 `duration_ms`도 반환한다. LLM 단계는 내부 네트워크 Retry, 교정 Retry와 출력·근거 검증을 포함한다. 새 단계는 실제 시작 시점부터 측정한다. 이전 단계의 가짜 0ms 값은 null로 반환하며 UI에 `측정 전 데이터`로 표시한다. 강제 중단으로 종료 시각을 알 수 없는 실행은 정확한 완료 시간을 만들어내지 않는다.

## 목록 검색

```http
GET /api/v1/analyses?analysis_purpose=production&status=completed&severity=HIGH&limit=50&offset=0
```

조건은 AND로 결합된다. 서비스 키에는 항상 자기 source 범위를 먼저 적용한다.

| 파라미터 | 규칙 |
| --- | --- |
| `analysis_purpose` | production / test / legacy_unknown; 생략 시 전체 |
| `ingest_channel` | service_api / file_upload / test_lab / legacy_unknown |
| `q`, `search_field` | 검색어와 대상 필드; `all`, event_id, company_name, source_system, src_ip, dest_ip, signature, event_name, threat_category |
| `event_id`, `company_name`, `source_system`, `signature`, `event_name`, `threat_category` | 개별 부분 문자열 조건 |
| `src_ip`, `dest_ip`, `src_port`, `dest_port` | 정확 일치 |
| `status` | pending / processing / completed / failed |
| `verdict` | true_positive / false_positive / inconclusive |
| `severity` | CRITICAL / HIGH / MEDIUM / LOW / NONE / UNKNOWN |
| `waf_vendor`, `waf_action`, `model_profile` | 정확 일치; action은 D/A |
| `review_state` | unreviewed / confirmed / deferred |
| `label_presence` | labeled / unlabeled |
| `reference_label` | true_positive / false_positive / inconclusive |
| `evaluation_outcome` | Label 비교·평가 제외 상태; 아래 참고 Label 절 참조 |
| `label_source_kind` | reference / synthetic_expected |
| `label_source_ref` | 참고 답안 출처 식별자, 정확 일치 |
| `label_ai_visible` | true / false / unknown |
| `input_truncated` | true / false |
| `confidence_min`, `confidence_max` | 각각 0~1, 최소 ≤ 최대 |
| `created_from`, `created_to` | timezone이 포함된 ISO 8601; 시작 포함, 종료 미포함 |
| `limit`, `offset` | 기본 50/0; limit 1~200, offset ≥ 0 |
| `service_api_key_id` | 최초 접수 키 UUID로 제한; 과거 미기록 키를 출처로 추정하지 않음 |
| `include_retries` | 기본 false. true는 재실행 이력 조회용이며 원본과 자식을 섞은 품질 집계에 사용하지 않음 |

부분 문자열의 `%`, `_`는 와일드카드가 아닌 문자로 취급한다. IP 검색은 정확 일치이며 CIDR 검색은 없다. 텍스트 대소문자 처리는 DB의 기본 문자 지원에 따른다. payload, Cookie, 암호화된 Agent 이력, 임의 확장 필드는 목록 검색 대상이 아니다.

응답: `{"items": [...], "total": 42, "limit": 50, "offset": 0, "evaluation_summary": {...}}`. `total`과 평가 집계는 인증 범위와 모든 필터를 적용한 전체 건수이며 limit/offset에 의한 현재 페이지로 제한하지 않는다. 정렬은 `created_at DESC, id DESC`다. 새 접수가 계속되면 offset 페이지 경계는 이동할 수 있다.

## 관리자 대시보드 집계

```http
GET /api/v1/dashboard/summary?days=7
```

관리자 세션 전용이다. 미인증은 401, 일반 서비스 API Key는 403이다. `days`는 정수 1~90이며 기본 7이다. 서버 조회 시각을 기준으로 최근 N일에 접수된 Production 분석 전체를 집계하며 목록 페이지 크기의 영향을 받지 않는다. 테스트와 기존 미분류 분석은 제외한다.

`service_api_key_id=<UUID>`를 추가하면 최초 접수 키별로 제한한다. 삭제·미존재 키의 전용 대시보드는 404이며 해당 분석은 전체 집계에 보존한다. 재실행 자식은 중복 집계하지 않는다. 응답은 아래 기본 필드와 함께 `service_api_key`, `attribution_unknown_count`, `evaluation_summary` 및 UTC 접수일별 `trend`를 제공한다. 지표는 참고 답안이 연결된 평가 가능 표본 기준이지 운영 트래픽 전체의 정확도가 아니다.

```json
{
  "window": {
    "days": 7,
    "created_from": "2026-08-29T12:00:00Z",
    "created_to": "2026-09-05T12:00:00Z"
  },
  "counts": {
    "total": 0,
    "pending": 0,
    "processing": 0,
    "completed": 0,
    "failed": 0,
    "true_positive": 0,
    "false_positive": 0,
    "inconclusive": 0,
    "unreviewed": 0,
    "critical_high_allowed": 0,
    "false_positive_denied": 0
  },
  "severity_counts": {
    "CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0,
    "NONE": 0, "UNKNOWN": 0, "unrated": 0
  },
  "runtime": {
    "agent_mode": "stub",
    "production_profile": null,
    "source": "api_configuration",
    "worker_health_verified": false
  }
}
```

기간은 UTC `created_at >= created_from AND created_at < created_to`이다. 브라우저 날짜 정밀도와 맞추기 위해 조회 시각의 밀리초 미만을 내린 값을 종료 경계로 사용하며 시작·종료 모두 밀리초 정밀도다. 목록으로 이동할 때 이 응답의 정확한 시작·종료 값을 기존 검색 API에 전달하면 동일 접수 범위를 조회할 수 있다.

`total`과 상태별 건수는 해당 기간 내 전체 상태를 포함한다. `pending`, `processing`도 **기간 내 접수된 작업**의 대기·진행 건수이며 전체 기간의 큐 크기가 아니다. 판정별 건수와 `severity_counts`는 완료된 분석만 집계한다. 심각도가 없는 완료 결과는 `unrated`이며 `UNKNOWN`으로 추정하지 않는다.

`unreviewed`는 상태와 무관하게 해당 분석의 리뷰가 아직 없는 건수다. `critical_high_allowed`는 완료된 정탐 중 CRITICAL/HIGH이고 WAF Action이 A인 건수, `false_positive_denied`는 완료된 오탐 중 WAF Action이 D인 건수다. 관측된 WAF 조치와 AI 판정의 조합이며 침해 성공이나 사람의 확정 판정을 의미하지 않는다.

`runtime.agent_mode`는 **API 프로세스의 설정값**이다. Production 프로필이 있으면 `production_profile`에 `id`, `name`, `model_name`만 반환한다. 프로필 등록 정보와 API 설정은 worker heartbeat나 실제 모델 연결 성공을 검증한 값이 아니므로 `worker_health_verified`는 false다. 내부 endpoint URL, API Key, payload와 리뷰 내용은 이 응답에 포함하지 않는다.

## 파일 접수

```http
POST /api/v1/uploads
X-API-Key: <SERVICE_API_KEY>
Content-Type: multipart/form-data
```

필드명은 `file`. 확장자는 `.csv`, `.json`, 인코딩은 UTF-8/BOM 허용이다. 기본 파일 한도는 10 MiB이다. JSON은 단일 이벤트, 이벤트 배열, `{"events": [...]}`를 지원한다. CSV 헤더는 단건 입력 필드명을 사용하고 각 payload는 단건과 같은 크기 제한을 적용한다.

```json
{
  "accepted": 8,
  "duplicates": 1,
  "rejected": 1,
  "label_attached": 0,
  "label_unchanged": 0,
  "analysis_ids": ["6b683a42-9ba2-4b98-aea5-eab1283bca98"],
  "errors": []
}
```

위 숫자와 ID 배열은 형식 설명용이다. 실제 `analysis_ids`에는 접수·중복 분석 ID가 입력 순서대로 들어간다. 전체 파일을 읽을 수 있으면 HTTP 202이며 행 오류는 `rejected`, `errors`로 확인한다. 최대 100개 오류 상세를 반환한다. 파일 형식 오류는 422, 파일 크기 초과는 413이다. 배치 ID나 진행 상태 전용 API는 없다.

### 테스트 파일의 참고 답안

관리자 테스트 파일은 최상위 `expected_verdict`와 선택적 `difficulty`·`test_category`·`case_name`을 받는다. 답안은 `true_positive`, `false_positive`, `inconclusive`; 누락·JSON null·CSV 빈칸은 답안 없음이다. 서버는 이벤트 검증·fingerprint 계산·암호화 전에 답안·평가 메타데이터를 분리하고 접수와 같은 트랜잭션에서 append-only Label과 실행 항목을 연결한다. 답안은 `source_kind=synthetic_expected`, `source_ref=test-upload:expected_verdict`, `ai_visible=null`이다. 파일 답안 작성 과정을 독립 검수로 추정하지 않는다.

응답의 `label_attached`는 새 연결 수, `label_unchanged`는 기존 최신 답안과 같아 유지된 수다. 기존 답안과 다르면 해당 행을 `expected_verdict_conflict`로 거부하며 이벤트·답안에 부분 저장을 남기지 않는다. 정정은 별도 참고 Label 미리보기·확정을 사용한다. 답안은 이벤트 및 Primary/Verifier 입력에 포함되지 않고, 완료된 최종 판정과 목록·상세·보고서에서 비교한다. 진행 중·실패·stub는 오답으로 세지 않는다.

이름 있는 실행은 새 접수 키마다 별도 분석을 생성하고 같은 키의 재전송은 원래 접수 결과를 반환한다. 재전송 응답의 접수·답안 연결 건수는 최초 접수 내역이며 다시 저장한 수가 아니다. 실행별 평가는 초기 답안을 고정하고 일반 분석 목록은 최신 답안을 비교한다.

Production `/uploads`, 단건 `/analyses`, 기존 직접 입력 이벤트 `/test-analyses`는 `expected_verdict`를 계속 거부한다. 새 `/test-runs`는 `event`와 분리된 답안·평가 필드를 받는다. Production에는 평가 정보를 넣지 않으며 임의 중첩 데이터·payload 본문의 답안 문장까지 찾아 지우지는 않는다. JSON 중복 키와 CSV 중복 헤더는 거부한다. 샘플 150건·난이도별 파일은 평가 정보가 포함되므로 Production에 그대로 보내지 않는다.

## 분석가 리뷰

```http
POST /api/v1/analyses/{id}/reviews
```

```json
{
  "external_review_id": "review-20260905-0001",
  "event_id": "evt-prod-20260905-000001",
  "decision": "false_positive",
  "analyst_id": "analyst-7",
  "comment": "정상 업무 요청으로 확인",
  "ai_visible": false
}
```

필수: `external_review_id`, `event_id`, `decision`. 판정값은 true_positive / false_positive / deferred이다. `ai_visible`는 AI 결과를 보고 판단했는지를 나타낸다. 리뷰는 모델 입력이나 학습 데이터로 전달되지 않는다.

신규 이력은 201, 동일 `(source_system, external_review_id)`의 동일 리뷰 재전송은 200이다. 대상 분석이나 내용이 달라진 리뷰 ID 재사용은 409다. 수정·삭제 API는 없으며 새로운 external review ID로 추가한다. analysis의 event ID와 본문의 event ID가 다르면 409 `event_id_mismatch`다.

## 참고 Label 연결과 평가

Label 연결은 관리자 세션 전용이며 서비스 API Key로는 작성할 수 없다. 기존 리뷰를 자동으로 정답으로 사용하지 않고 별도 추가형 이력에 저장한다. 기존 분석·원문·fingerprint·AI 결과를 수정하거나 LLM을 호출하지 않는다. 서비스의 분석 조회에는 기존 source 권한 범위를 그대로 적용한다.

### 미리보기

```http
POST /api/v1/evaluation-labels/preview
Content-Type: multipart/form-data
```

| 폼 필드 | 규칙 |
| --- | --- |
| `file` | UTF-8 JSON 참고 답안 배열; 최대 2 MiB, 500행 |
| `source_system` | 원래 분석의 정확한 source. 새 테스트는 실행 상세의 source_system을 사용하며 이전 웹 테스트만 admin-ui일 수 있음 |
| `source_kind` | reference 또는 synthetic_expected |
| `source_ref` | 1~120자 출처 식별자; 문자·숫자·공백·점·밑줄·하이픈 사용 |
| `ai_visible` | 답안 작성 시 AI 결과 열람 여부 true / false / unknown; 미확인은 unknown |

참고 답안 예시:

```json
[
  {"event_id": "evt-prod-20260905-000001", "expected_verdict": "true_positive"},
  {"event_id": "evt-prod-20260905-000002", "expected_verdict": "false_positive"}
]
```

`samples/waf-dummy-v1/reference/*_answers.json`도 사용 가능하며 source_kind는 synthetic_expected를 선택한다. 난이도·심각도·해설·원문 발췌는 이번 평가에 사용하지 않는다. reference는 정탐/오탐의 이진 기준이며 inconclusive 기대값은 synthetic_expected에만 허용한다. WAF Action D/A는 Label이 아니다.

서버는 정확한 `(source_system, event_id)`로 기존 분석만 찾고 신규 연결·기존 값·변경·미연결·파일 내 중복을 미리 보여 준다. 현재 웹 목록의 검색 필터는 연결 범위를 제한하지 않는다. 파일의 source나 ID가 맞지 않으면 분석을 새로 만들거나 다른 source로 자동 매칭하지 않는다.

미리보기는 Label을 쓰지 않으며 확인 대상에 묶인 서명된 `preview_token`을 반환한다. 유효기간은 15분이다. 원문 파일이나 토큰을 로그에 남기지 않는다.

### 연결 확정과 이력

```http
POST /api/v1/evaluation-labels/confirm
Content-Type: application/json
```

```json
{"preview_token": "<PREVIEW_RESPONSE_TOKEN>"}
```

확정은 미리보기 대상만 한 트랜잭션으로 연결한다. 기존 Label을 정정하면 새 revision을 추가하고 이전 이력을 보존한다. 그 사이 Label이 변경되었거나 토큰이 만료되었으면 다시 미리보기한다. 같은 확정의 재시도는 Label을 중복 생성하지 않으며 변경 없는 재연결은 새 이력을 만들지 않는다. 분석의 비동기 완료 자체는 Label 정정 충돌이 아니다.

```http
GET /api/v1/analyses/{id}/evaluation-labels
```

관리자용 이력 조회는 `items`에 revision·Label·출처·AI 열람 여부·등록자·등록 시각을 반환한다. 수정·삭제 API는 제공하지 않는다. Label 연결 감사 이력을 남기며 분석가 리뷰와 독립적으로 보존한다.

### 비교 의미와 집계

목록·상세의 `evaluation`은 `outcome`과 최신 `reference_label`(없으면 null)을 반환한다. 참고 Label에는 id, revision, verdict, source_kind, source_ref, ai_visible(bool/null), created_at이 있다. null인 ai_visible를 false로 추정하지 않는다.

| `evaluation_outcome` | 의미 |
| --- | --- |
| `match` | 참고 정탐/오탐과 최종 판정 일치 |
| `false_negative` | 참고 정탐, AI 오탐: 미탐 방향의 불일치 |
| `false_positive` | 참고 오탐, AI 정탐: 과탐 방향의 불일치 |
| `abstained` | 참고 정탐/오탐, AI 판단 보류 |
| `expected_abstention_match` | 테스트 기대 보류, AI도 보류 |
| `expected_abstention_mismatch` | 테스트 기대 보류, AI는 정탐/오탐 |
| `unlabeled` | 연결된 Label 없음 |
| `pending` | Label이 있으나 분석 진행 중 |
| `failed` | Label이 있으나 분석 실패 |
| `stub` | 실제 모델이 아닌 모의 실행 |
| `unknown_provenance` | 실제 최종 판정 실행 근거 미확인 또는 결과 불일치 |
| `input_contaminated` | 이전 이벤트 확장 필드에서 알려진 정답/평가 정보 필드 발견 |

비교는 Primary 단독 결과가 아니라 Verifier·근거·정책 결합 후의 최종 판정을 기준으로 한다. 미탐은 참고 정탐 → AI 오탐, 과탐은 참고 오탐 → AI 정탐이다. 확정 참고값에 대한 모델 inconclusive는 판단 보류로 구분하고 이진 오류에 섞지 않는다. 테스트의 inconclusive 기대값은 기대 보류의 일치 여부로 별도 표시한다. 미라벨·진행 중·실패·stub·출처 불명 또는 정답 혼입이 확인된 이전 입력은 정상 품질 표본으로 세지 않는다.

`evaluation_summary`는 테스트와 Production 모두 적용된 모든 검색 조건과 권한 범위 전체를 집계한다. 전체 및 출처·AI 열람 여부별 `source_groups`에 `confusion_matrix`(tp/fn/fp/tn/abstained_positive/abstained_negative), `metrics`를 추가한다. Accuracy/Precision/Recall/F1·Specificity·FPR/FNR·Balanced Accuracy·Macro F1·MCC·커버리지·보류율·보류 포함 정답률을 제공한다. 비율은 0~1, MCC는 -1~1이며 산출 불가는 null이다. 전체 집계의 `label_coverage`는 답안 연결률이다. 수식과 분모는 [공통 평가 정의서](Test_Runs_and_Evaluation_v0.1.md)를 따른다. 테스트 기대 답안·AI 지원 답안 일치율과 선택된 운영 표본을 독립적인 전체 운영 정확도로 표현하지 않는다. 심각도 채점·자동 모델 비교는 포함하지 않는다.

집계 필드는 total(전체), labeled(Label 존재), evaluable(비교 가능), matches(일치), outcomes(상태별 건수), source_groups(출처 종류 × AI 열람 여부)다. 그룹의 판정 일치율은 `matches / evaluable`로, 테스트 기대 보류와 실제 보류가 같으면 일치에 포함하고 확정 참고값에 대한 AI 보류는 분모에 남는다. 이진 확정판정 coverage는 `binary_decided / binary_evaluable`이며 기대 보류 Label은 양쪽에서 제외한다. 분모가 0이면 비율을 산출하지 않는다. 그룹의 false_negatives, false_positives, abstained, expected_abstention_matches, expected_abstention_mismatches를 함께 확인한다.

정답 파일은 분석 접수 파일과 분리한다. 분석 요청에 정답을 섞으면 평가 정보가 모델 입력으로 유입될 수 있으므로 별도 연결 경로만 사용한다. 이미 실행된 분석에 정답이 포함되었던 경우에는 사후 연결로 과거 모델 입력이 정화되지 않는다. 저장된 과거 원문·fingerprint·Agent 이력은 변경하지 않는다.

## 멱등성과 오류

이벤트 멱등키는 `(source_system, event_id)`다. 별도 `Idempotency-Key` 헤더는 사용하지 않는다. 서버가 정규화된 전체 이벤트 내용의 fingerprint를 계산한다. 같은 목적·내용은 기존 ID와 분석을 반환하고, 내용 또는 목적 충돌은 409다. 기존 미분류 이벤트의 재전송은 내용이 같으면 기존 미분류 행을 유지한다.

| 상태 | 처리 |
| --- | --- |
| 401 | 키 누락·불일치: `authentication_required` |
| 403 | 권한 부족: `scope_required:<scope>` |
| 404 | 분석 없음 또는 다른 소스: `analysis_not_found` |
| 409 | 이벤트 내용/목적 충돌, 리뷰 대상·내용 충돌 |
| 413 | 단건 payload 또는 업로드 제한 초과 |
| 422 | 필드/범위/예약 필드/파일 형식 오류 |
| 500 | 예상하지 못한 서버 처리 오류 |

애플리케이션에서 처리한 HTTP 오류는 `{"detail": "error_code"}` 또는 검증 상세 배열(`field`, `type`, `message`)을 반환한다. 예상하지 못한 500이나 프록시가 반환한 413 등은 JSON이 아닐 수 있으므로 클라이언트는 상태 코드와 Content-Type도 확인한다. 모델 실행 실패는 HTTP 오류와 별개로 분석 본문의 `status="failed"`, `error_code`, `error_message`에 기록한다. API Key, 원문 payload와 모델 응답을 오류 로그에 넣지 않는다.

네트워크 오류로 접수 여부가 불명확하면 동일 Event ID와 같은 내용으로 재전송한다. 401/403/409/413/422는 입력·권한 문제를 해결한 후 재요청한다. 완료된 failed 이벤트를 같은 ID로 재접수해도 새로운 분석은 시작되지 않는다.
