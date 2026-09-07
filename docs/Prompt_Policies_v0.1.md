# 프롬프트 버전 관리 v0.1

2026-09-07 구현 범위: 저장·복제·변경 비교·운영 적용·이전 저장 버전으로 복귀. 후보 버전별 실행·자동 평가·모델 비교는 포함하지 않는다.

## 사용 방법

설정 → 프롬프트에서 저장 버전을 읽고 복제해 수정한다. 이름, 변경 설명과 판정·작성 지침을 입력해 **새 버전으로 저장**한다. 저장은 운영 적용과 별개다. 운영 적용은 대상 버전과 품질 미검증 안내를 명시적으로 확인해야 하며, 이전 버전을 선택해 같은 절차로 복귀할 수 있다. 모든 저장 버전은 불변이고 수정·삭제 API가 없다.

편집 가능한 내용은 판정 지침·검토 관점·문체·상세도의 일반 텍스트이며 최대 4,000자다. 템플릿 변수·코드·URL을 실행하지 않는다. 실제 로그·Cookie·비밀값·참고 정답을 넣지 않는다. 고정 시스템 규칙은 읽기 전용이며 비신뢰 입력 처리, 원문 근거 검증, 출력 계약, Verifier 독립성과 최종 결합 규칙은 바꿀 수 없다. 정책 지침은 고정 규칙 범위 안에서만 적용한다.

첫 정책 목록 조회 또는 새 분석 접수 시 기본 지침을 암호화한 버전 1과 활성 선택을 등록한다. 초기 기본 지침도 실제 모델 품질 검증 완료를 뜻하지 않는다. 이후 코드 기본 지침이 달라져도 저장된 정책은 덮어쓰지 않는다. 모든 버전의 품질 상태는 `not_evaluated`이며 형식 검사·저장을 품질 검증으로 표시하지 않는다. 작성 지침과 별도 평가를 구분하는 데 [OpenAI 공식 프롬프트 가이드](https://developers.openai.com/api/docs/guides/prompt-engineering)를 참고했다.

기존 아키텍처의 `draft/verified/production/disabled` 전체 승인 흐름 대신, 합의한 1차 범위에서는 **불변 저장 버전 + 단일 활성 선택**만 구현한다. `verified` 자동 판정이나 모델 호출은 없다.

## 실행 시 버전 고정

새 이벤트를 접수할 때 정책 ID·내용 지문과 함께 두 역할의 **전체 지침**을 암호화한 스냅샷을 저장한다. 현재 코드 지침은 `waf-judgment-v2.5`, 고정 규칙은 `waf-system-v2.5`이며 상세의 `prompt_version`은 `waf-judgment-v2.5/policy-N` 형식이다. 정책 ID는 추가 응답 필드 `prompt_policy_version_id`로 확인한다.

- 운영 적용과 복귀는 새 접수에만 영향을 준다.
- Primary·독립 Verifier·출력 교정 Retry·worker 재선점은 같은 기본 지침을 사용한다. Verifier에 Primary 결과를 전달하지 않는다.
- 코드 배포로 고정 규칙이 달라져도 이미 접수한 전체 스냅샷은 바꾸지 않는다.
- 같은 `(source_system,event_id)`의 중복 접수는 기존 분석과 정책을 반환한다. 새 버전 평가를 위한 재분석 기능이 아니다.
- 스냅샷이 없는 기존 대기 작업만 최초 실행 시 선택하며 `selection_origin=legacy_execution`으로 구분한다. 기존 완료 결과는 수정하지 않는다.
- 손상되거나 일관되지 않은 스냅샷은 LLM 호출 전에 실패하며 최신 코드 지침으로 자동 대체하지 않는다.
- 정책 메타데이터는 모델 입력에 넣지 않는다. 기존 암호화된 Agent 단계 입력에서도 실제 사용 지침을 조회할 수 있다.
- 교정문은 같은 기본 지침에 추가한다. 교정된 전체 지침의 별도 저장은 기존과 같이 제공하지 않으며 오류 유형·위치와 시도별 실행 메타데이터를 남긴다.

접수 응답의 버전은 선택한 지침이며 실행 완료를 뜻하지 않는다. stub 실행은 실제 사용 버전을 `stub-v0`으로 표시한다. stub에 연결된 정책 ID를 실모델 품질 결과로 해석하면 안 된다. 모델은 기존처럼 worker 실행 시점의 Production 프로필을 사용하며 이번 변경은 모델 선택 정책을 바꾸지 않는다.

## 입력 예산과 미검증 항목

고정 시스템/스키마의 기존 4,096 토큰 예약에 편집 정책의 UTF-8 바이트 수만큼 추가 여유를 둔다. 전체 입력 문자 예산은 `3 × (context_window - max_output_tokens - reserved_tokens)`다. 정책이 길면 모델에 전달할 이벤트 공간이 줄며 최소 공간이 부족하면 `agent_context_budget_too_small`로 LLM 호출 전에 실패한다.

이 여유는 모델별 tokenizer·chat template의 실제 토큰 상한을 검증한 값이 아니다. 문체·중복 감소·원문 인용 정확성·실제 입력 한도는 별도 합성 실모델 평가가 필요하다. provider·모델·LLM 호출 횟수·최종 판정 결합 규칙은 변경하지 않는다.

운영 적용 시 현재 Production 프로필이 있으면 위 예약량으로 최소 입력 공간이 남는지도 검사한다. 불가능하면 `422 prompt_policy_context_budget_too_small`로 적용을 거부하고 기존 활성 버전을 유지한다. 해당 버전 자체의 저장은 가능하다. 예를 들어 기본 지침과 context 8,192 / output 3,072 조합은 공간 부족으로 실패하므로 모델의 한도를 확인해야 한다. Production 프로필이 없는 초기 설정에서는 선택할 수 있지만 worker는 실행 전에 다시 검사한다. 이 검사는 내용 정확성이나 실제 tokenizer 한도 검증이 아니다.

## 관리자 API

세션의 `admin` 권한이 필요하며 서비스 API 키로 접근할 수 없다. 요청 검증 오류와 감사 기록에 정책 본문을 넣지 않는다.

| 메서드 / 경로 | 동작 |
| --- | --- |
| `GET /api/v1/admin/prompt-policies` | 버전 요약 목록, active_version_id, revision, 현재 코드의 읽기 전용 fixed_instructions/fixed_rules_version, max_policy_chars |
| `GET /api/v1/admin/prompt-policies/{id}` | 버전 요약과 복호화한 policy_text |
| `POST /api/v1/admin/prompt-policies` | name, policy_text, change_note, 선택적 parent_version_id로 새 버전 생성; 201 |
| `POST /api/v1/admin/prompt-policies/{id}/activate` | expected_revision와 acknowledge_unverified=true로 활성 버전 교체 |

각 버전에는 id, version_number, name, change_note, parent_version_id, content_hash, created_by, created_at, quality_status를 제공한다. 조회·생성·적용은 접근 감사를 남긴다. 실제 사용된 고정 규칙은 과거 분석의 스냅샷/Agent 이력으로 확인하며 현재 설정 화면의 규칙을 과거 실행 당시 규칙으로 간주하지 않는다.

활성 선택은 원자적 revision 비교로 변경한다. 다른 관리 화면에서 먼저 적용했다면 `409 prompt_policy_changed_concurrently`를 반환한다. 목록을 다시 조회하고 대상 버전을 재확인해야 한다. 통신 결과가 불명확하면 자동 재전송하지 말고 활성 버전을 먼저 확인한다.

## DB와 배포

Alembic `0006_prompt_policies`가 필요하다.

- `prompt_policy_versions`: 불변 버전 메타데이터와 암호화된 지침
- `prompt_policy_state`: 단일 활성 버전과 변경 revision
- `analyses`: nullable 정책 FK와 암호화된 전체 지침 스냅샷 추가

기존 분석·Label·리뷰·Agent 이력·모델 설정·접근 감사를 다시 쓰지 않는다. SQLite upgrade는 기존 analyses 테이블을 재생성하지 않고 nullable 컬럼을 추가한다. 새 외부 서비스나 패키지는 없다.

운영 적용 전 SQLite 일관성 백업을 만들고 실행 중인 작업이 없는지 확인한다. API/worker 쓰기를 멈춘 상태에서 마이그레이션한 뒤 같은 새 코드의 API/worker를 시작한다. 분석 또는 프롬프트 이력이 있는 DB는 안전한 자동 downgrade가 불가능하므로 downgrade를 거부한다. 배포 복귀 시 해당 시점 DB 백업과 호환 이미지를 함께 복원해야 한다. 정책의 **이전 버전 적용**은 DB downgrade와 다르며 분석 데이터를 삭제하지 않는다.

기존 로컬 서비스의 실제 비공개 배포 설정은 `.local-deploy/README.md`를 따른다. README의 Compose 기본 포트/모드를 실제 설정 위에 덮어쓰지 않는다.
