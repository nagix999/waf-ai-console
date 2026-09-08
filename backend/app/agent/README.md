# WAF Agent 판정 정책 v2

이 디렉터리는 ModuAgent 실행 자체와 독립적인 도메인 계약을 보관합니다. 모델이나 프롬프트를 교체해도 아래 최종 판정 규칙은 애플리케이션 코드가 강제합니다.

## 출력 계약

- `verdict`: `true_positive` / `false_positive` / `inconclusive`
- `confidence_score`: 0~1
- 한국어 요약과 위협 분석
- `threat_analysis.severity`: `CRITICAL` / `HIGH` / `MEDIUM` / `LOW` / `NONE` / `UNKNOWN`
- 시그니처 관계: `exact` / `partial` / `mismatch` / `unknown`
- 원문 발췌 근거: 최대 5개, 각 300자 이하
- 상세 판정 해석: 근거마다 최대 2,000자, 관찰과 판단 이유를 필요한 만큼 설명
- 분석가 확인 항목과 상충 증거
- `analyst_checks`: 확인할 자료(`source_ko`, 최대 240자), 확인할 내용(`check_ko`, 최대 800자), 이유·구분 조건(`why_ko`, 최대 800자)의 구조화 안내. 계약상 최대 5개. 프롬프트는 보류를 해소할 확인 1~3개, 확정 판정은 기본 빈 배열이며 실제 필요한 선택적 후속 확인만 1~2개 요청함. 이전 출력에 없으면 빈 배열로 수용
- 영향·위험·검증 절차를 포함한 자문형 튜닝 제안
- 입력 잘림 여부

심각도는 판정과 일관되어야 합니다. `true_positive`에는 `CRITICAL`~`LOW`, `false_positive`에는 `NONE`, `inconclusive`에는 `UNKNOWN`만 허용합니다. 별도의 `uncertainties` 필드는 사용하지 않으며, 보류 사유는 `summary_ko`, 사람이 확인할 작업은 `recommended_checks`, 반대 방향의 근거는 `conflicting_evidence`에 기록합니다.

각 `evidence`는 실제 필드명인 `field`, `excerpt`, `interpretation_ko`를 사용합니다. 판정 해석은 문장 수를 채우기보다 관찰 사실과 공격 또는 정상 의미, 최종 판정과의 연결을 구체적으로 설명합니다. 서버는 `excerpt`가 **지정한 필드의 원문**에 대소문자까지 동일한 부분 문자열로 존재하는지 검증하고, 일치하지 않는 근거는 제거합니다. 예를 들어 시그니처에만 있는 문자열을 `payload.query`로 제시하면 인정하지 않습니다. 정탐 또는 오탐의 유효한 원문 근거가 모두 제거되면 결과를 `inconclusive`와 `UNKNOWN`으로 강등하고 튜닝 제안을 비활성화합니다.

근거 필드는 다음과 같이 해석합니다. `event.` 접두사는 생략할 수 있습니다.

- 전체 원문: `payload` (`raw_payload` 별칭 허용).
- HTTP 원문 구간: `payload.request_line`, `method`, `uri`, `request_target`, `path`, `query`, `headers`, `body`, `protocol` (`payload.` 접두사 공통). `uri`/`request_target`은 요청 대상 전체, `path`는 authority와 쿼리를 제외한 경로입니다. 요청 라인을 인식할 수 없으면 하위 구간을 추정하지 않고 전체 원문 인용만 허용합니다.
- 특정 헤더·쿼리 항목: `payload.headers.Cookie`, `payload.query.q`. 헤더명만 대소문자를 구분하지 않으며 발췌문은 원문과 정확히 일치해야 합니다. 반복 항목은 해당 이름의 원문 중 하나에서 일치해야 합니다.
- 실제 이벤트의 문자열·숫자 scalar: `signature`, `src_ip`, `extra_fields.attributes.policy_id`, `extra_fields.items.0.value` 등. 배열의 `[0]` 표기도 허용합니다. 존재하지 않는 경로와 컨테이너 전체를 지정한 인용은 인정하지 않습니다.

URL 디코딩이나 줄바꿈·헤더 재구성 없이 원문 구간에서 검증합니다. 이 검사는 출처·문자열의 일치를 확인하며, 근거 해석이나 공격 성공 여부의 의미적 정확성을 보증하지 않습니다. 단계 메타데이터에는 `field_exact_substring` 모드와 건수만 기록하고 거부된 원문 값은 기록하지 않습니다.

새 결과와 프롬프트 코드 기준은 각각 `waf-analysis-v2`, `waf-judgment-v2.6`입니다. 기존 v1/v2 결과와 실행 이력은 감사 이력으로 보존합니다. 이전 필드 출처 검증에는 DB 변경이 없었으나, 현재 프롬프트 버전 관리에는 Alembic `0006_prompt_policies`가 필요합니다.

v2.5는 편집 가능한 판정·작성 지침과 읽기 전용 시스템 규칙을 분리합니다. 기본 지침은 요약·세부 동작과 영향 조건·시그니처 비교·개별 발췌 설명의 역할을 구분하고 추상적인 상투어와 반복을 줄이도록 요구합니다. 의미상 중복 제거를 보장하거나 결과를 새로 요약하는 LLM 단계를 추가한 것은 아닙니다.

접수 시 선택한 정책과 두 역할의 전체 지침을 암호화해 고정합니다. 운영 적용·코드 배포·worker 재선점 후에도 같은 스냅샷을 사용하며 교정 Retry는 같은 기본 지침에 교정문을 붙입니다. 손상된 스냅샷은 모델 호출 전에 실패합니다. 기존 스냅샷 없는 대기 작업만 최초 실행 시 선택하고 `legacy_execution`으로 구분합니다. 정책 버전·시스템 규칙 버전·내용 지문은 결과의 `policy`에, 지침 본문은 암호화 저장에 남기며 이벤트나 Label을 정책에 합치지 않습니다.

입력 예산은 기존 4,096 토큰 예약에 정책 UTF-8 바이트 수만큼 여유를 추가합니다. 정확한 tokenizer 검증은 아니며 최소 입력 공간이 부족하면 호출 전에 실패합니다. 저장·형식 검사와 실제 모델 품질 검증은 구분합니다. API·버전 수명주기·백업과 배포는 [프롬프트 버전 관리 정의서](../../../docs/Prompt_Policies_v0.1.md)를 따릅니다.

v2.4는 판정 요약을 확인 지시로 대체하지 않고 핵심 관찰과 보류 조건을 설명합니다. 추가 확인은 일반 화면과 보고서의 마지막에 배치하며, 확정 판정의 확인은 재판정의 필수 조건이 아닌 영향 범위·대응·튜닝 안전성을 위한 선택적 후속 확인입니다. 확인이 없으면 `analyst_checks`와 `recommended_checks` 모두 빈 배열을 요청합니다. 서버도 확정 판정에 기본 확인을 생성하지 않으며 `analyst_guidance.checks=[]`는 화면에서 빈 목록으로 존중합니다. 신뢰도는 보정된 확률이 아니므로 확인 필요 여부의 단독 기준으로 쓰지 않습니다.

같은 관찰을 여러 근거로 반복하지 않도록 프롬프트에서 요구합니다. 원문 출처 검증 이후의 최종 결합에서는 같은 출처 별칭·동일 발췌·동일 해석인 완전 중복만 제거합니다. 다른 해석·다른 원문 출처는 보존하며 기존 최대 5개·Primary 우선 순서 제한을 유지합니다. 개별 Primary/Verifier 결과와 암호화된 단계 출력은 그대로 보존합니다. 화면의 원문별 묶음은 판정이나 의미를 다시 평가하지 않습니다.

worker가 추가한 정확한 출처 검증 제외 문구는 `analyst_guidance.checks`의 업무로 만들지 않고 `limitations`의 주의사항으로 옮깁니다. 실제 요청별 확인은 그대로 유지하며 보류에서 확인이 없는 경우에는 기존의 명시적인 대체 안내를 사용합니다. 원문 근거 제외 정책과 저장된 `recommended_checks`는 바꾸지 않습니다.

일반 설명은 내부 판정 간 차이보다 추가 확인 작업에 집중합니다. 서버가 보류로 결합해도 기존 verdict·심각도·Verifier 정책을 바꾸지 않고, 최종 `analyst_guidance`에 확인 자료/내용/이유와 입력 생략·미완료 안내를 제공합니다. 모델의 구체적인 확인 항목이 없으면 기존 안내나 일반적인 요청 처리 규격 확인으로 대체하며 조회하지 않은 자료를 확인한 사실로 만들지 않습니다. 공격 시도 여부와 침해 성공은 별도입니다. 과거 저장 결과는 변경하지 않습니다.

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
- 제시된 근거 중 원문과 일치하지 않아 제거된 항목이 있음

Verifier 실패 또는 판정 불일치는 최종 `inconclusive`/`UNKNOWN`으로 결합합니다. 양쪽 판정이 같으면 더 낮은 신뢰도를 최종 신뢰도로 사용하며, 정탐 심각도가 다르면 더 낮은 등급을 선택합니다. Primary의 튜닝 제안을 Verifier가 지지하지 않으면 제안을 비활성화합니다.

## 실행 및 이력

- 새 parser 단계는 `parser_version=generic-http-v2`, 실제 `parse_status`, `fallback_llm_used=false`를 기록합니다. 별도 Payload Extractor를 실행한 것으로 표시하지 않습니다. 원문을 자동 복원/디코딩하지 않으며, 파서 상태는 best-effort 요청·헤더 구조의 인식 수준이지 메시지 완전성이나 공격성 검증 결과가 아닙니다.
- ModuAgent 0.6.2 Standard execution
- Pydantic 구조화 출력
- timeout/network/HTTP 408/5xx에 한해 1회 재시도
- `output_validation_failed`이면 검증 오류와 요구 스키마를 포함한 교정 지시로 1회 재시도
- 교정 후에도 Primary 검증이 실패하면 분석 실패, Verifier 검증이 실패하면 최종 `inconclusive`
- 모델이 호출하는 ModuAgent Tool 및 Memory 미사용. 아래 로컬 디코딩 전처리 도구는 worker가 직접 실행
- vLLM/Gemma thinking 비활성화; OpenAI는 vLLM 전용 옵션을 사용하지 않으며 thinking 비활성화를 보장하지 않음
- Agent 입력과 검증된 출력은 DB에 암호화하여 저장
- framework run ID, agent fingerprint, failure ID와 안전한 실행 메타데이터 저장
- ModuAgent가 의도적으로 노출하지 않는 raw provider body와 private reasoning은 저장하지 않음

새 실행의 토큰 `usage`는 ModuAgent의 dataclass 표현 문자열이 아닌 숫자 필드 객체입니다. 허용 필드는 `input_tokens`, `output_tokens`, `total_tokens`이며 임의 provider 본문은 저장하지 않습니다. 직렬화 시점에 없거나 잘못된 카운터는 null이고, 모두 0인 framework 집계도 미측정으로 취급합니다(프레임워크가 usage 미제공 응답을 0으로 표현함). 일부 카운터만 제공된 응답은 프레임워크 내부에서 이미 0이나 계산된 합계로 바뀔 수 있어 이 단계에서 그 출처를 복원하지는 않습니다. 최상위 `usage`는 마지막 시도의 값이고 각 교정 시도의 값은 `output_validation_retry.attempts`에 따로 있으므로 둘을 더하면 중복입니다. 이전 저장 문자열은 변환하거나 다시 쓰지 않습니다. 이는 과금 금액이나 모든 실패 호출의 사용량을 완전히 측정했다는 의미는 아닙니다.

### 로컬 디코딩 전처리

`waf-text-decoder-v2`는 네트워크·eval·압축 해제 없이 URL percent/legacy `%uNNNN`/HTML 엔티티/Unicode 이스케이프(중괄호 코드 포인트 포함)/보수적인 표준·URL-safe Base64 및 Log4j 스타일 조회식을 해석합니다. 최대 262,144자 검색, 후보 256회, 20개 출력, 원문 조각 1,024자·결과 4,096자·3단계·직렬화 32 KiB 제한입니다. 일부만 처리하면 경고를 남깁니다. `+`는 그대로 유지하고 Base64 누락 padding은 충분히 긴 정규 UTF-8 텍스트 후보에서만 보완 가정을 표시합니다. 실제 애플리케이션 디코딩 동작은 미검증이고 디코딩된 텍스트도 비신뢰 데이터입니다.

`log4j_lookup_static` 단계는 bounded 정적 문자열 단순화이지 Java/Log4j 실행이 아닙니다. ASCII lower/upper와 중첩을 지원하며 locale 의존 가능성을 경고합니다. `${::-j}`/`${:-j}`는 속성 미정 조건의 기본값 후보, env/sys 등과 `$${...}`는 미해석 원문입니다. JNDI는 주소를 조회하지 않고 식을 보존합니다. 단계가 없는 original=decoded 항목도 정적 확인 안내로 전달합니다. 프롬프트 v2.3은 이러한 경고를 무시하거나 디코딩 문자열을 원문 근거로 인용하지 않으며, 대상 Log4j 버전·설정·로그 처리 경로·외부 연결 기록 등 필요한 확인을 제시하도록 요구합니다. 공격 시도와 취약성·침해 성공은 구분합니다. 문법 기준은 [Apache Lookups](https://logging.apache.org/log4j/2.x/manual/lookups.html), [RFC 4648](https://www.rfc-editor.org/rfc/rfc4648.html)이며 실제 서버의 해석 환경은 재현하지 않습니다.

한 `log4j_lookup_static` 단계 내부의 조회식은 중첩 깊이 8·노드 128·연산 예산 65,536·입출력 4,096자로 제한합니다. 구문 오류나 한도 초과는 해당 단계의 전체 입력을 보존하며 중간에 일부만 변환하지 않습니다. 이 정적 해석 뒤에는 다른 변환 순서를 추측해 다시 실행하지 않습니다.

worker는 parser 다음 `decoder`, `agent_input` 단계를 기록합니다. 전체 디코딩 결과와 실제 구성 입력은 암호화된 단계 출력에만 보관합니다. Primary/Verifier는 같은 `decoded_payload_hints`를 받으며 원문 offset이 실제 제출한 연속 구간에 포함되는 항목만 전달합니다. 전체 문자 예산 중 최대 8,192자 또는 1/8을 도구용으로 예약하고 항목은 통째로 선택합니다. 도구의 생략/검색 제한은 힌트에 표시하며 원문 `input_truncated`와 구분합니다. 결과에는 `agent.preprocessors`의 버전만 더하고 `tools=[]`는 유지합니다. 추가 LLM 호출은 없습니다.

`GET /analyses/{id}/event`의 `decoding`은 관리자 감사 조회 시 계산한 자료이며 과거 실행의 재현은 아닙니다. 당시 모델에 전달된 자료는 암호화된 Agent 이력에서 확인합니다. 새 구조화 필드는 실제 모델 출력 길이·정확성·출력 교정 빈도에 영향을 줄 수 있으므로 모의 테스트와 실모델 검증을 구분해야 합니다.

## LLM Provider

Production과 Test는 provider 전체에서 각각 최대 하나만 지정하며, 둘 다 현재 설정으로 전체 검증을 통과해야 지정할 수 있습니다. 같은 프로필의 양쪽 지정은 명시적으로 허용합니다. 일반 테스트는 Test를 사용하고 Production으로 자동 대체하지 않습니다. Primary와 독립 Verifier는 해당 실행에서 선택한 같은 프로필을 사용합니다. OpenAI로 자동 우회하거나 기존 vLLM 프로필을 자동 변경하지 않습니다. `provider=vllm`은 DB의 Internal Egress 목록에 등록한 개별 내부 IP·포트와 `VLLMClient`를 사용하고, `provider=openai`는 공식 HTTPS API와 `OpenAICompatibleClient`를 사용합니다. 새로운 프레임워크나 SDK 의존성은 추가하지 않습니다.

vLLM의 기존 `WAF_VLLM_ALLOWED_TARGETS`는 무시하며 호스트명/CIDR 허용을 유지하지 않습니다. 관리자 설정에서 RFC1918 IPv4 또는 ULA IPv6와 포트를 등록해야 합니다. 새 허용 목록은 자동 초기화하지 않고 비어 있으면 차단합니다. worker 진입 시와 실제 HTTP 요청 직전(Primary/Verifier/출력 교정/SDK 재시도/연결 테스트 모두)에 DB를 재조회합니다. vLLM 프로필이 비활성화되거나 대상이 해제되면 이후 요청은 전송하지 않습니다. 이미 보낸 요청은 취소하지 않습니다. 양쪽 provider 모두 명시적인 HTTP 클라이언트로 redirect와 환경 프록시를 비활성화합니다. 관련 스키마/API/배포 전환은 `docs/Internal_Egress_v0.1.md`를 참고하세요.

OpenAI 프로필은 API Key·TLS 검증·외부 전송 승인이 필수이며 worker에서도 호출 전에 재검사합니다. 프로필의 승인 여부는 사내 데이터 반출 승인을 대체하지 않습니다. payload와 Cookie를 마스킹하지 않는 기존 정책이 그대로 적용되므로 외부 전송 가능한 데이터만 접수해야 합니다.

OpenAI에는 `store=false`와 `max_completion_tokens`를 적용하고 `chat_template_kwargs`를 보내지 않습니다. 구조화 출력용 스키마는 모든 객체의 속성을 필수로 표시하며 nullable 항목은 null을 허용합니다. 기존 도메인 모델과 결과 버전은 변경하지 않고 Pydantic 검증·필드별 근거 대조·1회 출력 교정 Retry·독립 Verifier 결합 규칙을 유지합니다. 모델별 API 기능·출력 예산·거절 응답은 실제 계정의 합성 테스트로 별도 확인해야 합니다.

새 실행의 Agent 메타데이터에 `llm_provider`, 모델 ID, 프로필 ID/지문, 외부 전송 승인 여부를 기록합니다. OpenAI의 `thinking_enabled`는 비활성화 사실을 추정하지 않도록 null이며, 이전 결과는 다시 쓰거나 provider를 추정해 채우지 않습니다. API Key와 임의 upstream 오류 본문은 이 메타데이터에 넣지 않습니다.

프로필에 필요한 DB 변경은 Alembic `0004`이며 기존 결과·Agent 이력은 보존합니다. 공식 규격: [구조화 출력](https://developers.openai.com/api/docs/guides/structured-outputs), [Chat Completions](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create), [데이터 관리](https://developers.openai.com/api/docs/guides/your-data). `store=false`는 별도 승인된 Zero Data Retention 설정과 동일하지 않습니다.

## 실행 시간

단계 시작 전에 실행 중 상태를 저장하고, 작업·검증이 끝난 뒤 실제 종료 시각과 monotonic clock으로 측정한 `duration_ms`를 기록합니다. Primary/Verifier의 한 논리적 단계에는 내부 네트워크 재시도, 출력 교정 재시도 및 근거 검증이 포함됩니다. 실패한 단계도 종료 시점까지 측정합니다.

기존 단계의 동일한 시작·종료 시각은 측정값으로 취급하지 않습니다. API는 미측정 duration을 null로 반환하고 UI는 `측정 전 데이터`를 표시합니다. lease 만료로 포기한 실행에는 알 수 없는 종료 시각을 만들어 넣지 않습니다.

분석 전체 경과 시간은 접수부터 종료까지이며, 큐 대기 시간과 최초 선점 이후 처리 경과 시간을 함께 제공합니다. 테스트/운영 분류는 서버 메타데이터이고 LLM 판정 입력에는 포함하지 않습니다.

## 이름 있는 테스트 실행의 입력 격리

`0010_test_runs`의 새 일반 테스트는 접수 당시 모델 프로필 지문·전체 프롬프트·Verifier 임계값을 고정한다. `0011_model_test_role`부터는 **Test 지정 모델**을 고정하고 미지정이면 실제 모델 테스트를 접수하지 않는다. 기존에 고정한 모델은 바꾸지 않으며 Test 지정 변경·해제는 이미 접수한 테스트의 취소가 아니다. 테스트명·난이도·유형·문항명·초기 답안은 이벤트와 별도로 보존하고 Primary/Verifier에 넣지 않는다. 프로필 변경·비활성화·허용 대상 해제·실행 모드 불일치 시 다른 모델로 조용히 바꾸지 않는다. 기존 Production 접수·worker 모델 선택 계약과 후보 검증 전용 worker는 유지한다. 상세 입력·평가 기준은 [테스트 실행 및 평가 정의서](../../../docs/Test_Runs_and_Evaluation_v0.1.md)를 따른다.

2026-09-08 공통 지침 v2.6: Production과 Test는 같은 시스템 지침·활성 편집 지침을 접수 시 고정하며, 용도별 차이는 LLM 모델이다. 테스트 전용 프롬프트 선택은 제공하지 않는다. 반복 설명을 압축해 새 기본 조합은 5,018→3,226자로 줄였으나 실제 tokenizer·품질 개선을 보장하지 않는다. 양 역할·Retry·기존 대기 작업의 암호화 스냅샷은 유지하며 저장된 편집 지침을 덮어쓰지 않는다. 읽기 전용 실행 비교는 최종 판정·고정 답안·안전한 시간/사용량 메타데이터만 사용하고 모델을 다시 호출하지 않는다. 같은 이벤트 지문이어도 실행별 source·프롬프트 길이에 따른 실제 입력 범위가 같다는 보장은 하지 않는다. 상세는 [공통 프롬프트·실행 비교 안내](../../../docs/Prompt_Optimization_and_Comparison_v0.2.0.md)를 따른다.

## 입력 스키마와 필드 정의 이력 — v0.2.0

입력 스키마는 프롬프트/LLM 출력 계약과 별개다. 접수 당시 필드명·설명·타입·필수·null·제한을 한 불변 버전으로 암호화해 분석과 테스트에 고정한다. 실행 시 현재 활성 버전으로 다시 검증하거나 교체하지 않는다. 입력 단계의 암호화된 output에는 당시 `input_schema` 전체 정의를, 평문 metadata와 결과의 `agent.input_schema`에는 버전·지문·필드 수·`field_metadata_usage=history_only`를 기록한다. 필드 설명을 Primary/Verifier의 system/user 입력에 추가하지 않는다. 이벤트의 실제 추가 필드 값은 기존 입력 구성·길이 제한 정책대로 처리한다.

스냅샷 손상은 LLM 호출 전에 실패 처리한다. 미기록 과거 대기 작업은 원래 기본 11개 정의를 `legacy_default`로 고정하며 이미 완료한 과거 이력은 추정해 채우지 않는다. stub도 스키마 출처를 기록하지만 LLM 호출 또는 실제 판정으로 표시하지 않는다. 상세 조회 권한과 감사 기록은 기존 관리자 Agent 이력 정책을 유지한다. [입력 스키마 정의서](../../../docs/Input_Schemas_v0.2.0.md)를 참고한다.

## 참고 Label의 분리

관리자가 별도 파일로 연결한 정답 Label은 추가형 평가 이력에만 저장하고 이벤트·fingerprint·Primary/Verifier 입력에 넣지 않습니다. 테스트 파일 업로드는 최상위 `expected_verdict`만 입력 경계에서 먼저 분리한 뒤 같은 트랜잭션으로 연결합니다. Production·직접 입력의 정답 필드 금지는 유지합니다. payload 본문·임의 중첩 필드 속 정답을 제거하는 기능은 아닙니다. 비교 대상은 정책 결합과 원문 근거 검증을 마친 최종 판정이며, Primary 단독 출력이나 WAF Action을 정답으로 사용하지 않습니다. 비교 집계 자체는 LLM을 다시 호출하지 않습니다. 합성 기대값, AI 열람 여부 미확인 답안, stub·실행 출처 불명 결과를 검증된 독립 품질로 표시하지 않습니다. 이미 모델에 전달된 정답 정보는 사후 Label 연결로 정화되지 않습니다.

## 선택형 합성 150건 검증

설정의 전체 검증에서 명시적으로 150건 실행을 선택한 경우만 model-test worker가 패키지에 고정된 `waf-dummy-v1`을 분석합니다. 후보 프로필 ID/지문과 접수 당시 프롬프트 스냅샷을 사용하고 Production을 승격·교체하지 않습니다. 기능 검증을 먼저 통과해야 하며 같은 Primary·조건부 독립 Verifier·근거 검증·출력 교정 정책을 실행합니다. 원래 참고 답안은 별도 attachment에 저장해 재접속·재개·사후 답안 정정으로 과거 검증 집계가 바뀌지 않게 합니다. 일반 분석 화면은 기존처럼 최신 답안을 보여줍니다.

분석은 `model_test_run_id`로 검증 이력에 연결되고 `test` / `model_validation`로 분류합니다. 일반 analysis worker는 이 문항들을 선점하지 않습니다. 검증 run의 고유 lease owner와 heartbeat를 사용하며 매 단계 쓰기와 실제 HTTP 요청 전 소유권을 확인합니다. 후보 프로필 변경·비활성화·vLLM 허용 대상 해제 시 이후 요청을 차단합니다. 이미 전송된 요청은 취소할 수 없고, 중간에 프로세스가 중단되면 해당 미완료 문항의 재호출은 발생할 수 있습니다. 완료/실패된 문항은 재개 시 다시 실행하지 않습니다.

선택형 검증에는 Alembic `0009`가 필요합니다. 합성 문항 일치율은 자동 Production 승격 조건이나 실환경 정확도 보장이 아니며, 기술 검증 실패/문항 실행 실패와 참고 답안 불일치를 구분합니다. 실제 vLLM/OpenAI 호출·비용·장시간 다중 프로세스 검증은 별도 운영 환경에서 수행해야 합니다.
