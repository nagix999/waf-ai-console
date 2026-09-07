# LLM 용도별 설정

기준일: 2026-09-07. 설정 → LLM 프로필에서 Production과 Test를 각각 지정한다. 이 설정은 모델 선택만 분리하며 별도 GPU·worker 큐·프롬프트 정책을 만들지 않는다.

## 사용 범위

| 실행 | 모델 선택 |
| --- | --- |
| Production `/analyses`, `/uploads` | Production 프로필 |
| 웹 테스트 분석의 직접 입력·파일 업로드 | Test 프로필을 접수 시 고정 |
| 설정의 빠른 테스트·전체 검증·선택형 150건 평가 | 선택한 후보 프로필을 직접 사용 |

provider 전체에서 각 용도는 최대 한 프로필이다. 같은 프로필을 두 용도에 명시적으로 지정할 수 있다. Test 지정·교체·해제는 Production 지정을 변경하지 않는다. 일반 테스트에 Test가 없으면 `409 test_model_profile_required`이며 Production이나 stub로 자동 대체하지 않는다. 서버가 명시적으로 `stub` 모드이면 기존 모의 실행을 유지하고 실제 LLM 검증·품질 평가로 표시하지 않는다.

150건 후보 검증은 미검증 모델을 확인하는 과정이므로 Production·Test 지정을 요구하지 않는다. 선택한 후보의 지문·프롬프트 고정과 model-test worker 격리는 유지한다.

## 공통 검증 조건

두 용도 모두 서버에서 다음을 검사한다. UI의 버튼 비활성화만으로 제한하지 않는다.

- 프로필 상태가 `verified` 또는 `production`이어야 한다. draft·disabled는 지정할 수 없다.
- 현재 `profile_fingerprint`와 일치하는 `mode=full`, `status=passed` 이력이 있어야 한다. 빠른 테스트 성공은 부족하다.
- vLLM의 현재 Internal Egress 등록, OpenAI 공식 주소·TLS·API Key·외부 전송 승인 정책을 충족해야 한다.
- UI에서 확인한 지문과 서버 지문이 달라지면 지정하지 않고 재확인을 요구한다.

기존 전체 검증의 성공 이력 기준을 유지한다. 같은 설정의 후속 검증 실패가 이전 성공 이력이나 현재 지정을 자동 박탈하는 정책은 추가하지 않는다. 검증 성공은 당시 연결·기능 확인이며 지속적인 가용성·모델 품질·사내 반출 승인을 보증하지 않는다. 150건 실행은 선택 사항이고 참고 답안 일치율의 자동 합격선은 없다.

## 변경과 실행 중 작업

Production 또는 Test로 지정 중인 프로필은 직접 수정할 수 없다. 다른 검증 프로필로 교체하거나 해당 지정을 해제/비활성화한 다음 수정하고 전체 검증을 다시 수행한다. Test만 해제해도 Production 지정이 남아 있으면 수정할 수 없다.

비활성화하면 해당 프로필의 용도 지정을 해제한다. 다시 활성화할 때는 draft로 시작하며 검증·용도 지정을 다시 해야 한다. 비활성화된 프로필이 재검증 작업의 오래된 성공 응답으로 자동 복구되지 않도록 기존 검사를 유지한다.

이미 접수한 이름 있는 테스트는 지정 변경·해제 후에도 원래 프로필을 사용한다. 변경·비활성화·통신 허용 해제 시 이후 요청을 막고 다른 모델로 대체하지 않는다. 따라서 **Test 지정 해제는 접수된 테스트의 취소가 아니다**. 이미 전송한 HTTP 요청은 회수하지 않는다. 이전 버전에서 Production을 고정한 테스트도 원래 스냅샷을 보존한다. 실행 묶음이 없는 이전 `test` 작업은 Test를 사용하며 Production으로 자동 우회하지 않는다.

OpenAI 비용·비마스킹 payload와 Cookie의 외부 전송 정책은 두 용도 모두 동일하다. 용도를 나눠도 모델 서버의 자원이 자동 격리되지는 않는다.

## 관리자 API

기존 `/api/v1/model-profiles` 경로를 유지하며 모두 관리자 권한이 필요하다.

| 경로 | 역할 |
| --- | --- |
| `GET /api/v1/model-profiles` | 현재 프로필·용도·검증 지정 가능 여부 조회 |
| `POST /api/v1/model-profiles/{id}/promote` | Production 지정; 같은 트랜잭션에서 이전 Production 해제 |
| `POST /api/v1/model-profiles/{id}/assign-test` | Test 지정; 같은 트랜잭션에서 이전 Test 해제 |
| `POST /api/v1/model-profiles/{id}/unassign-test` | 해당 프로필의 Test 지정만 해제 |
| `POST /api/v1/model-profiles/{id}/disable` | 비활성화 및 해당 프로필의 용도 지정 해제 |

새 지정/해제 요청은 `{"expected_profile_fingerprint":"<현재 64자리 SHA-256>"}`를 보낸다. 기존 `promote`는 본문 생략 호환성을 유지하되 UI는 확인한 지문을 보낸다. 설정 지문에 용도는 포함하지 않으므로 역할 지정만으로 연결 설정 검증을 무효화하지 않는다.

프로필 응답은 기존 필드에 `is_test`, `can_assign`, `assignment_block_reason`을 추가한다. Production 여부는 기존 `status=production`을 유지한다. `can_assign`은 검증 상태·성공 이력을 나타내며 지정 시 수행하는 네트워크 정책 재검사를 대체하지 않는다. 지정·해제는 감사 기록에 남긴다. 원문 키·입력 로그는 응답하지 않는다.

## DB 변경과 적용

Alembic `0011_model_test_role`은 기존 `vllm_profiles`에 `is_test`(기본 false)와 Test 단일 지정 partial unique index를 추가한다. 기존 Production 단일 지정 제약도 유지한다. 프로필·암호화 키·검증·분석·답안 이력은 수정하거나 삭제하지 않는다. Test를 기존 Production에서 자동 복사하지 않는다. 새 운영 패키지 의존성은 없다.

배포 시 DB 일관성 백업과 암호화 키 백업을 확인하고 API·쓰기 worker를 중지한 뒤 `alembic upgrade head`를 실행한다. 같은 버전의 API·analysis worker·model-test worker와 재빌드한 UI를 시작한 후 관리자가 검증된 Test 프로필을 명시적으로 지정한다. 이전 worker를 계속 실행하면 이전 Production 공유 정책이 적용될 수 있으므로 혼합 버전으로 운영하지 않는다. 이전 버전으로 되돌리면 테스트의 모델 선택 정책도 바뀌므로 대기 작업을 확인하고 의도적으로 롤백해야 한다.

Test가 지정되어 있으면 `0011` downgrade를 거부한다. 해제 후 SQLite downgrade는 참조 이력 보존을 위해 부모 테이블을 재구성하지 않고 사용하지 않는 false 컬럼을 남긴다. 다시 upgrade하면 같은 컬럼을 재사용하고 단일 지정 인덱스를 복원한다.

이 개발의 자동 테스트는 합성 DB·모의 모델 실행을 사용한다. 운영 DB 마이그레이션·Docker 재배포·실제 GPU/OpenAI 호출은 별도 수행 사항이다.

기존 한계: 일반 Production의 OpenAI 실행과 150건 미포함 OpenAI 연결 검증은 실행 진입 시 설정을 검사하지만 후속 HTTP마다 DB의 비활성화를 재확인하는 경로는 없다. 용도 지정 해제를 이미 시작된 모든 OpenAI 실행의 즉시 취소로 해석하지 않는다. 이름 있는 테스트·후보 150건의 요청 직전 검사는 이와 구분한다.

## 개발 검증 결과

- 백엔드 전체 46개 테스트 파일을 네 그룹으로 나눠 실행: **1,206개 통과**. 합성 SQLite와 모의 provider/Agent만 사용하고 테스트 컨테이너의 네트워크는 차단했다.
- 프런트엔드 **209개 통과**, Vite Production 빌드 성공.
- 실제 Chrome에서 합성 API 응답을 연결한 설정·테스트 화면을 검증했다. 미검증 지정 차단, 용도별 모델 유지, 같은 프로필 겸용, 지정 중 편집 차단, Test 해제, 확인한 지문 전송, Esc 취소, Test 미지정 접수 차단 및 명시적인 stub 구분을 확인했다.
- 1,280px·375px 화면과 모바일 확인 창의 경계를 확인했다. 운영 화면·실제 GPU/OpenAI·운영 DB·배포 결과를 검증한 것은 아니다.
