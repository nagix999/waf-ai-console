# 서비스 API Key 관리 v0.1

기준일: 2026-09-15. WAF 수집기·연동 시스템이 `X-API-Key`로 사용하는 서비스 인증 키다. vLLM/OpenAI 공급자 API Key와는 별개다.

## 동작과 보안

- 관리자 설정에서 발급·목록 조회·이름 수정·폐기한다. 만료 기간은 없고 폐기한 키는 복구하지 않는다.
- 256비트 무작위 비밀값을 포함한 원문을 발급 응답에서 한 번만 반환한다. DB에는 SHA-256 해시, 공개 식별 접두사와 관리 메타데이터만 저장한다.
- 화면을 닫거나 설정을 벗어나면 표시 중인 원문을 지운다. 브라우저 영구 저장소에 넣지 않으며 복사한 클립보드는 사용자가 관리한다. 발급 응답을 잃으면 원문을 다시 조회할 수 없으므로 목록 확인 후 폐기·재발급한다. 자동 재발급은 하지 않는다.
- `purpose` (`production` / `test`), `source_system`과 `ingest` / `review` 중 하나 이상을 발급 시 지정한다. 같은 용도와 source의 키들만 분석 접근 범위를 공유한다. 키에 관리자 권한을 줄 수 없다. 기존 키와 purpose 생략 발급은 Production이다.
- 발급 후 purpose·source·scope는 수정하지 않는다. 새 키 발급 → 연동 전환 확인 → 이전 키 삭제 순서로 변경한다.
- Test 키의 `/analyses`, `/uploads` 요청은 Test 모델로 분석하고 테스트 결과에 적재한다. 여러 요청을 묶는 `/test-sessions` 계약은 [API 정의서](Production_API_v0.1.md)의 Test API 키 사용 절을 참고한다. 관리자 Cookie를 함께 보내면 관리자 세션이 우선하므로 키 용도를 시험할 때는 쿠키 없는 클라이언트를 사용한다.
- 마지막 사용 시각은 **마지막 인증 시각**이며 endpoint 성공을 뜻하지 않는다. SQLite 쓰기를 줄이기 위해 최대 분당 한 번 갱신한다. 폐기는 이후 인증을 차단하며 이미 인증된 요청을 취소하지 않는다.
- 발급·이름 수정·폐기는 ID만 감사 로그에 기록한다. 원문·해시를 API 목록, URL, 로그에 기록하지 않는다.

## 관리자 API

관리자 세션 Cookie 전용이다. 미인증은 401, 서비스 키는 403이다. 성공 응답과 관리 핸들러 오류 응답은 `Cache-Control: no-store`, `Pragma: no-cache`를 지정한다.

| 메서드와 경로 | 요청 | 응답 |
| --- | --- | --- |
| `GET /api/v1/admin/service-api-keys` | 없음 | `{items}` |
| `POST /api/v1/admin/service-api-keys` | `{name, source_system, scopes, purpose}` | 201 `{item, api_key}`; 원문은 여기만 반환 |
| `PATCH /api/v1/admin/service-api-keys/{id}` | `{name}` | 갱신된 item |
| `POST /api/v1/admin/service-api-keys/{id}/revoke` | 없음 | 폐기된 item; 반복 요청은 같은 상태 유지 |
| `DELETE /api/v1/admin/service-api-keys/{id}` | 없음 | 목록과 키별 대시보드에서 제거. 분석·감사 이력 보존 |

item: `id`, `name`, `key_prefix`, `source_system`, `scopes`, `purpose`, `created_at`, `last_used_at`(null 허용), `revoked_at`(null 허용). 시간은 UTC다. 삭제된 키는 목록에서 숨기되 내부 인증 차단·감사 기록은 보존한다.

name은 공백 정리 후 1~120자, 제어/서식 문자는 금지한다. 이름은 폐기 이력을 포함해 중복될 수 없다. source는 영문/숫자로 시작하고 영문/숫자/`.`/`_`/`-`만 사용하며 1~120자다. 대소문자를 무시하고 `admin-ui` 및 `waf-internal-` 접두사는 예약되어 있다. scopes는 중복 없는 `ingest`, `review`이며 최소 하나다. `ingest`가 필요한 조회와 `review`가 필요한 리뷰 등록의 기존 권한 규칙은 바꾸지 않는다.

대표 오류: 409 `service_api_key_name_exists`, 409 `service_api_key_revoked`, 404 `service_api_key_not_found`, 422 입력 검증, 503 `service_api_key_storage_unavailable`. 인증 DB 오류는 503 `service_api_key_authentication_unavailable`이며 다른 권한으로 우회하지 않는다.

## 기존 배포 키 제거와 전환

환경변수 bootstrap 인증과 관련 화면 항목은 제거했다. 이전 `WAF_BOOTSTRAP_API_KEY`, `WAF_BOOTSTRAP_SOURCE_SYSTEM`, `WAF_BOOTSTRAP_API_KEY_ENABLED`가 배포 환경에 남아 있어도 새 코드에서는 읽지 않으며 기존 키로 인증할 수 없다. 기본 개발 서비스 키도 제공하지 않고 이전 키를 DB로 자동 복제하지 않는다.

이 변경은 기존 배포 키로 동작하는 연동 시스템에 영향을 준다. 새 버전 배포 후 기존 관리자 계정으로 로그인해 서비스 키를 발급하고 수집기의 비밀 설정을 교체한다. 기존 source의 분석 조회·중복 기준을 유지하려면 새 키 발급 시 이전과 동일한 `source_system`을 사용한다. 관리자 로그인은 서비스 키와 독립적이므로 유지된다. 실제 배포 전에는 구버전 실행 프로세스의 인증 동작이 바뀌지 않는다.

관리자 세션과 서비스 키를 함께 보내면 관리자 세션이 우선한다. source 격리 검증에는 Cookie 없는 클라이언트를 사용한다.

## DB 변경·배포

Alembic `0008_service_api_keys`가 키 테이블을 추가했고 `0017_validation_data`가 키 용도를 추가한다. 기존 키는 Production으로 유지한다. 새로운 운영 패키지 의존성은 없으며 마이그레이션 자체는 키를 발급하지 않는다. Test 키·데이터셋·새 답안 이력을 사용한 뒤에는 0017 downgrade를 거부한다. 롤백에는 배포 전 백업이 필요하며 구버전 코드의 환경변수 배포 키 인증도 함께 검토해야 한다.

배포 전 일관성 있는 SQLite 백업과 복구 절차를 확보하고 기존 worker를 중지한 뒤 `alembic upgrade head`를 적용한다. 운영 DB 변경과 실제 키 발급은 구현 테스트와 구분해서 수행한다.
