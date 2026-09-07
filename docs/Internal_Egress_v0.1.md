# Internal Egress v0.1

설정 → Internal Egress에서 vLLM으로 요청할 내부 IP·포트를 여러 개 관리한다. LLM 공급자 표기는 `vLLM`이다. OpenAI의 공식 URL·TLS·외부 전송 승인 정책은 변경하지 않는다.

## 허용 기준

- IP는 RFC1918 IPv4(`10/8`, `172.16/12`, `192.168/16`) 또는 ULA IPv6(`fc00::/7`)의 단일 주소만 허용한다. IPv6 URL은 대괄호로 감싼다.
- 포트는 정수 1~65535. IP+포트의 정규화된 조합은 중복 등록할 수 없다.
- 도메인, CIDR, 공인 IP, loopback, link-local, IPv4-mapped IPv6, zone ID는 거부한다. 공개 IP를 사내에서 사용하는 특수 네트워크는 이번 허용 범위에 포함하지 않는다.
- vLLM Base URL은 `http`/`https`와 등록 IP·포트, `/v1` 경로만 허용한다. 기본 포트는 http=80, https=443이며 생략해도 해당 포트가 등록되어 있어야 한다. 입력의 루트 경로와 `/v1/`은 `/v1`로 정규화한다. userinfo·query·fragment·제어문자·비표준 숫자 주소는 거부한다.
- 이 기능은 애플리케이션의 요청 제한이며 OS/컨테이너 방화벽이나 실제 서비스 신원 검증을 대체하지 않는다. 실제 vLLM 여부·접속 가능 여부는 별도 검증한다.
- HTTPS로 실행하는 vLLM은 해당 IP가 인증서 SAN에 포함되어야 한다. 도메인 이름만 있는 인증서는 CA를 신뢰해도 IP URL 검증에 실패한다. `trust_env=False`로 `SSL_CERT_FILE`/`SSL_CERT_DIR`에 의존한 CA 설정도 사용하지 않으므로 사설 CA는 실제 HTTP 클라이언트가 사용하는 기본 신뢰 저장소에 설치하고 별도 확인한다. 인증서 검증을 끄는 방식으로 Agent의 HTTPS 정책을 우회하지 않는다.

## 관리자 API

관리자 로그인 세션이 필요하다. 미인증은 401, 서비스 API Key는 403. 경로의 id는 허용 항목 UUID이며 API Key 등 자격 증명은 등록하지 않는다.

| 메서드 | 경로 | 용도 |
|---|---|---|
| GET | `/api/v1/admin/internal-egress` | 목록 |
| POST | `/api/v1/admin/internal-egress` | 등록, 성공 201 |
| PUT | `/api/v1/admin/internal-egress/{id}` | 전체 수정, 성공 200 |
| DELETE | `/api/v1/admin/internal-egress/{id}?expected_revision=1` | 삭제, 성공 204 |

POST 예시(합성 주소, 연결을 실행하지 않음):

```json
{"ip_address":"10.0.0.10","port":8000,"description":"분석용 vLLM"}
```

PUT에는 동일 필드와 최신 `expected_revision`을 전달한다. 설명은 선택 필드(최대 500자, 앞뒤 공백 제거, 제어문자 거부)다. 응답은 `id`, `ip_address`, `port`, `description`, `revision`, `created_at`, `updated_at`, `in_use_profiles`를 제공한다. 사용 프로필은 `id`, `name`, `status`만 제공한다. GET은 항목 배열을 반환한다. 대상 추가가 모델 연결/검증/Production 승격을 수행하지는 않는다.

| 오류 코드 | HTTP | 의미 |
|---|---|---|
| `internal_egress_ip_must_be_private` | 422 | 허용 내부 IP 형식/범위 아님 |
| 필드 검증 오류 배열 | 422 | 포트·설명·revision 등 잘못된 입력 |
| `internal_egress_target_exists` | 409 | 같은 IP·포트 이미 등록됨 |
| `internal_egress_target_in_use` | 409 | 비활성화되지 않은 프로필이 사용 중 |
| `internal_egress_changed` | 409 | 다른 관리자가 변경했거나 변경 충돌 |
| `internal_egress_not_found` | 404 | 삭제되었거나 없는 항목 |
| `vllm_target_not_allowed` | 422 | 프로필 IP·포트가 현재 목록에 없음 |
| `vllm_base_url_must_use_ip_address` | 422 | 프로필 URL이 IP 주소가 아님 |

사용 중인 대상은 설명만 수정할 수 있다. IP·포트를 바꾸거나 삭제하려면 표시된 프로필을 모두 비활성화해야 한다. 비활성 프로필/이전 테스트/분석 이력은 삭제하지 않으며 재활성화 시 최신 허용 목록을 검사한다. revision 충돌 시 최신 상태를 확인한 다음 명시적으로 다시 저장하며 자동 재전송하지 않는다. 성공한 등록·수정·삭제는 기존 감사 테이블에 남긴다.

## 실행 경계

프로필 등록·수정·활성화·테스트 접수·Production 승격 시 DB 허용 목록을 검사한다. 대상 CRUD와 프로필 변경은 동일 SQLite write lock으로 직렬화한다. worker는 실행 시작 시 검사하고 실제 HTTP 요청 직전에 별도 짧은 DB 조회로 다시 검사한다. Primary, 독립 Verifier, 출력 교정, SDK retry, 연결 테스트의 각 요청에 적용한다. redirect와 환경 프록시는 비활성화한다. 이미 네트워크로 전달된 요청은 회수하지 않으며 동시 변경과 dispatch 사이를 OS 수준으로 원자화하지 않는다.

모델 테스트 완료 시에도 프로필과 권한을 다시 읽고 짧은 변경 잠금 안에서 결과를 반영한다. 테스트 도중 비활성화·수정·권한 철회가 발생한 설정을 과거 성공 응답만으로 Verified로 되돌리지 않는다.

## 마이그레이션과 전환

`0007_internal_egress`는 `internal_egress_targets` 테이블만 추가한다. IP·포트 유일성, 포트 범위, 양수 revision 제약이 있다. 기존 프로필·분석·암호화 자료를 자동 수정하지 않는다. 새로운 운영 의존성은 없다.

중요: 초기 목록은 비어 있다. `WAF_VLLM_ALLOWED_TARGETS`는 더 이상 권한을 부여하지 않으며 환경값이나 기존 프로필에서 자동으로 대상을 가져오지 않는다. 기존 hostname/CIDR 설정을 사용한 vLLM은 IP 등록 및 프로필 변경/재검증을 마칠 때까지 차단된다. OpenAI는 영향받지 않는다.

배포 시 기존 DB를 일관성 백업하고 API/worker/model-tester를 함께 새 버전으로 전환해야 한다. 구버전 worker가 남으면 DB 허용 목록을 검사하지 않는다. 실제 DB migration과 Docker 재배포는 별도 실행 사항이다.

vLLM을 사용 중이면 접수/대기 작업 영향을 먼저 확인하고, API migration 후 허용 IP 등록과 프로필 전환을 마치기 전에는 분석 worker를 재개하지 않는다. 비어 있는 목록에서 먼저 재개하면 대기 중 vLLM 분석도 차단된다. 검증용 model-tester 역시 새 이미지로만 기동한다.

롤백 시 현재 DB와 허용 목록을 먼저 백업하고 모든 writer를 중지한다. 허용 항목이 남아 있으면 downgrade를 거부한다. 빈 목록에서만 테이블을 제거할 수 있지만 구버전으로 돌아가면 환경변수 기반 정책이 다시 적용되므로 그 허용 대상도 함께 검토해야 한다. 보안 설정을 지워 downgrade를 강제로 통과시키거나 오래된 DB를 무심코 복원하지 않는다.
