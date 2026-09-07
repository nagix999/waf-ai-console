# 입력 스키마·필드 정보 버전 관리 v0.2.0

## 적용 범위

설정 → 입력 스키마에서 기본 11개 필드 및 사용자 추가 필드의 접수 계약을 관리한다. 필드명·설명·필수·null·타입·제한·하위 정의 전체를 불변 버전으로 보존한다. 값 자체와 정답은 필드 정의에 넣지 않는다. LLM 출력 스키마나 외부 필드→내부 필드 매핑은 이번 범위가 아니다.

| 구분 | 변경 가능 범위 |
| --- | --- |
| 기본 필수 7개 | 이름·타입·필수·null 불가 유지, 설명 및 추가 제한 |
| 기본 선택 4개 | 이름·내부 타입 유지, 필수 전환·null 제한·길이·숫자 범위·허용값 |
| 사용자 추가 | 추가/이름 변경/삭제/설명/타입/필수/null/제한. 저장된 버전은 수정하지 않고 새 버전 생성 |
| 서버/정답 필드 | 등록 금지. 예약명은 하위 정의에서도 등록 불가 |

기본 필수는 event_id/company_name/src_ip/dest_ip/payload/waf_vendor/waf_action, 선택은 src_port/dest_port/signature/event_name이다. 기본 문자열 길이, 포트 범위와 D/A 허용값은 완화할 수 없다. 기존 핵심 타입 정규화는 유지하며 추가 필드는 엄격한 JSON 타입을 사용한다. `required=true,nullable=true`는 키를 반드시 보내되 null은 허용한다. 미등록 벤더 필드는 기존처럼 보존한다.

string/integer/number/boolean/object/array를 지원한다. string은 min_length/max_length, 숫자는 minimum/maximum, 스칼라는 enum, 배열은 min_items/max_items/items, 객체는 properties 배열을 사용한다. 배열 items는 이름 없는 타입 정의이고 properties의 항목은 이름/설명/required를 갖는 필드 정의다. 실행 코드·regex·외부 참조·임의 JSON Schema는 지원하지 않는다.

전체 정의 최대 64 KiB, 전체 필드/배열 항목 타입 노드 64개, 깊이 4, enum 50개, 배열 값 최대 1000개이다. 값 검증은 노드 수 10000·오류 50개로 제한한다. payload의 서버 UTF-8 바이트 제한은 문자 길이와 별도다. CSV는 두 포트 필드만 변환하므로 숫자·객체·배열 사용자 필드는 JSON을 사용한다.

## 저장·적용 절차

1. 현재 또는 과거 버전을 복제해 변경 사유와 함께 새 버전으로 저장한다.
2. 현재 활성 버전과 변경 내용을 비교한다.
3. 해당 버전에 맞춘 합성 샘플 한 건을 검증한다. 분석 접수·LLM 호출은 없으며 샘플은 저장하지 않는다.
4. 성공한 샘플 검증의 5분 유효 토큰으로 적용한다. 토큰은 버전/정의 지문/활성 revision/관리자 세션에 묶인다. 다른 관리자의 적용·재로그인·만료 후에는 재검증한다.
5. 이전 버전으로 복귀할 때도 새 샘플 검증과 명시적 적용이 필요하다. 과거 버전을 삭제하지 않는다.

샘플 하나의 성공은 기존 수집기 전체 호환이나 모델 정확도 보장이 아니다. 추가 필수/엄격한 제한을 운영 적용하기 전에 각 수집기와 합성 150건 파일의 호환성을 확인한다. 새 필수 필드 때문에 번들 150건이 부적합해지면 후보 검증 접수를 거부하고 필드를 임의로 채우거나 스키마를 우회하지 않는다.

## API

아래 관리 API는 관리자 세션만 허용한다. 모든 저장·샘플 검증·적용·정의 조회는 감사 기록을 남긴다.

| 경로 | 의미 |
| --- | --- |
| GET /api/v1/admin/input-schemas | items, active_version_id, revision, default_fields, bounds |
| GET /api/v1/admin/input-schemas/activation-history | 적용/복귀 이력 |
| GET /api/v1/admin/input-schemas/{id} | 불변 버전과 fields |
| POST /api/v1/admin/input-schemas | name, change_note, parent_id(선택), fields로 새 버전 저장 |
| POST /api/v1/admin/input-schemas/{id}/validate | event 객체 검증. valid/issues/validation_token/expires_in_seconds 반환 |
| POST /api/v1/admin/input-schemas/{id}/activate | expected_revision, validation_token으로 활성 버전 변경 |
| GET /api/v1/production-api | ingest 또는 관리자. 현재 필드 정의를 반영한 markdown 및 input_schema 식별 정보 |

버전 등록은 201, 검증 요청이 유효하면 200이며 실제 샘플 부적합은 valid=false이다. 정의 부적합·검증 토큰 오류는 422, stale revision은 409, 정의/스냅샷 손상은 503으로 실패한다. 오류에는 필드 경로·오류 종류·일반 설명만 남기고 원문 값은 반환하지 않는다. 스키마 버전 ID와 스냅샷은 요청 필드로 직접 지정할 수 없다.

Production POST /analyses와 /uploads의 새 이벤트는 활성 정의를 적용한다. 같은 source/event_id/정규화 내용 재전송은 처음 접수한 분석을 돌려주고 당시 스키마를 유지한다. 내용 충돌은 기존 409 규칙을 유지한다. 파일 하나와 이름 있는 테스트의 문항들은 접수 시 선택한 같은 버전을 사용한다. 정답과 테스트명 등은 기존 경계에서 별도로 분리한다.

온라인 문서·다운로드·OpenAPI는 같은 활성 정의를 읽으며 캐시하지 않는다. 열린 UI는 새로고침해야 최신 버전을 보인다. 저장소 Production_API_v0.1.md는 파일명 호환을 유지한 v0.2.0 기본 계약 템플릿이며 backend/app/data/production_api.md와 함께 갱신한다. Swagger의 공개 여부는 기존과 같으므로 필드 설명에 비밀 정보를 넣지 않는다. `Try it out`은 실제 요청이다.

## 추론 및 보존

Analysis/TestRun/VLLMTestRun의 input_schema_version_id와 input_schema_snapshot_ciphertext에 접수 당시 전체 정의를 고정한다. 필드 설명 변경만으로도 새 정의 지문과 버전이 생긴다. 추론 이력의 입력 단계 output에 전체 정의를 암호화해 기록하고 metadata에는 version_id/version_number/content_hash/selection_origin/field_count와 history_only를 남긴다. **필드 설명을 LLM 입력에 추가하지 않는다.** 실제 이벤트 추가 값은 기존 길이 예산 안에서 모델 입력에 남는다.

스냅샷은 복호화·구조·지문·연결 ID를 검증하고 손상 시 모델 호출 전에 실패한다. 예전 완료 분석은 null을 보존하고 재작성하지 않는다. 예전 미완료·미기록 분석은 현재 활성 정의가 아닌 기본 v1을 legacy_default로 기록한다. 일반 조회의 input_schema_metadata에는 전체 정의나 이벤트 값이 없고, 상세 정의와 Agent 암호화 입출력은 관리자 조회 및 기존 감사 정책을 따른다.

## DB·배포·복구

Alembic 0012_input_schemas: input_schema_versions/input_schema_state/input_schema_activations 세 테이블과 분석/테스트/후보 검증에 nullable FK·암호화 스냅샷을 추가한다. 필드마다 DB 컬럼을 추가하지 않는다. SQLite의 기존 분석 부모 테이블은 재작성하지 않아 하위 원문·Label·Agent 이력을 보존한다. 기존 행을 백필하지 않으며 기본 버전은 최초 필요 시 트랜잭션 안에서 초기화한다.

이번 작업은 운영 DB나 Docker에 적용하지 않는다. 배포 시 API/worker/model-tester 정지 → 일관성 DB 백업 및 암호화 키 별도 보관 → 동일 환경에서 alembic upgrade head → API 준비 확인 → 같은 v0.2.0 worker/UI 순으로 진행한다. 필드 버전 이력/스냅샷이 생긴 DB는 downgrade를 차단한다. 코드만 이전으로 내리는 것은 지원하는 복구 절차가 아니며, 필요하면 중지 상태에서 일관성 백업을 복원하고 백업 이후 접수 손실을 확인한다. 실제 모델 품질·운영 부하·운영 백업 복원은 별도 검증 항목이다.
