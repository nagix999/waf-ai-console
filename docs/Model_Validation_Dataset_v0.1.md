# 선택형 합성 150건 모델 검증 v0.1

기준일: 2026-09-07. 범용 평가셋 업로드나 모델 간 자동 비교가 아니라, 설정에서 선택한 후보 프로필을 고정된 합성 파일로 확인하는 기능이다.

## 화면과 실행

전체 검증 버튼은 취소 / 연결·기능 검증만 / 150건 판정 평가도 실행 모달을 연다. 선택한 공급자·모델과 시간·비용을 확인한다. OpenAI는 합성 입력도 외부 전송하며 유료일 수 있다. 모달 이후 프로필 설정이 변경되면 실행을 거부하고 최신 정보로 재확인한다. 기본값은 150건 미포함이다.

150건 판정 평가에는 사용자가 테스트명을 입력한다. 새 공통 테스트 실행 이력에서 이름으로 찾고 난이도·유형별 Confusion Matrix와 공통 평가 지표를 확인한다. 각 지표의 `?`는 정의와 높거나 낮을 때의 의미를 설명한다. 테스트·Production 공통 수식과 API는 [테스트 실행 및 평가 정의서](Test_Runs_and_Evaluation_v0.1.md)를 따른다.

150건을 선택하면 먼저 기존 전체 연결·기능 검증을 수행한다. 통과 시 같은 후보 프로필로 `waf-dummy-v1`의 쉬움/보통/어려움 50건씩 분석한다. Primary 150회에 조건부 독립 Verifier·출력 교정·통신 Retry가 추가될 수 있고 기능 검증도 별도 복수 호출을 사용한다. 실제 총 호출·시간·비용은 사전 고정값이 아니다.

Production·Test 용도 지정은 필요하지 않으며 자동 지정·교체하지 않는다. 일반 테스트 화면의 파일 업로드/직접 입력은 별도로 검증·지정한 **Test 프로필**을 사용하지만 이 후보 검증은 선택한 후보 프로필을 직접 사용한다. 이 후보 검증만 model-test worker에서 실제 ModuAgent 경로로 처리하므로 일반 analysis worker의 stub 설정과 무관하게 모델 호출을 수행한다. 용도 설정은 [LLM 설정 정의서](LLM_Profile_Assignments_v0.1.md)를 따른다.

## 데이터·판정 비교

패키지의 `app/data/waf_dummy_v1.json`은 `samples/waf-dummy-v1/all_150.json`과 같은 150개 합성 문항이다. 기존 참고 답안을 사용하며 정탐 60 / 오탐 60 / 기대 보류 30건이다. 임의 파일 경로·URL을 받지 않는다.

접수 트랜잭션에서 다음을 함께 저장한다.

- 검증 run의 후보 프로필 ID/설정 지문, 데이터셋 버전/원본 파일 SHA-256.
- `test` / `model_validation`으로 분류한 150개 분석과 검증 연결. source는 `waf-internal-model-test-<run UUID>`로 매번 분리한다.
- 이벤트 검증·암호화 전에 분리한 `expected_verdict`. 별도 `synthetic_expected` Label에 `source_ref=waf-dummy-v1`, `ai_visible=false`, `attachment_id=검증 run ID`로 저장한다.
- 각 분석의 접수 당시 활성 프롬프트 전체 암호화 스냅샷.

모델 입력에는 답안·검증 연결 메타데이터를 넣지 않는다. 비교는 기존처럼 Primary/Verifier/근거 정책을 결합한 최종 판정이다. 합성 기대값 일치는 독립 정확도를 뜻하지 않는다. 검증 집계는 **최초 답안 attachment**를 사용하므로 사후 답안 정정으로 과거 점수를 바꾸지 않는다. 일반 분석 목록·상세는 기존대로 최신 참고 답안을 사용한다.

진행 상태(대기/분석 중/완료/실패), 참고 답안 일치·미탐·과탐·보류와 제외 사유·분모를 표시한다. `150건 분석 결과 보기`는 해당 source의 테스트 분석으로 이동한다. `inconclusive` 기대값은 기대 보류로 분리하며 실패·미완료를 오답으로 세지 않는다.

기능 검증 실패 시 150건 호출을 생략하고 문항을 실행 실패로 종료한다(`dataset_status=skipped`). 기능 통과 후 문항 실행 실패가 있으면 전체 검증은 실패한다. **답안 불일치율에 따른 자동 합격선은 없다.** 150건을 모두 실행했다는 사실과 품질이 적합하다는 판단은 별개이며 Production 승격은 기존의 명시적 관리자 동작이다.

## API

관리자 세션으로 기존 `POST /api/v1/model-profiles/{id}/tests`를 사용한다.

```json
{
  "mode": "full",
  "include_dataset": true,
  "name": "후보 모델 150건 1차 검증",
  "idempotency_key": "synthetic-validation-20260907-01",
  "expected_profile_fingerprint": "<프로필 응답의 64자리 SHA-256 지문>"
}
```

`include_dataset`은 strict boolean, 기본 false이며 `mode=full`에서만 true를 허용한다. `expected_profile_fingerprint`는 선택적인 64자리 소문자 hex이며 UI는 모달에서 확인한 값을 항상 보낸다. 서버는 쓰기 잠금 안에서 일치 여부를 검사하고 다르면 409 `model_profile_changed_reconfirm`으로 아무 작업도 접수하지 않는다. 프로필 응답에 현재 `profile_fingerprint`가 추가된다. 알 수 없는 요청 필드는 거부한다.

150건을 포함하면 `name`과 `idempotency_key`가 필요하다. 같은 키·요청의 재전송은 이전 접수를 반환하며 바뀐 요청은 409다. 새 실행은 새 키를 사용한다. 응답의 `test_run_id`로 공통 실행 상세를 조회한다. 기존 이벤트/답안 데이터셋 원본은 보존하고 분류는 패키지 `waf_dummy_v1_metadata.json`에서 항목에만 연결한다.

202 응답 및 기존 검증 목록/상세 GET에 `include_dataset`, `dataset_evaluation`을 추가한다. 미포함이면 evaluation은 null이다. 포함 시 객체는 다음 필드를 반환한다.

| 필드 | 의미 |
| --- | --- |
| `dataset_version`, `dataset_hash`, `source_system` | 검증 데이터/결과 검색 식별자 |
| `total`, `pending`, `processing`, `completed`, `failed` | 전체 및 처리 상태별 문항 수 |
| `status` | `waiting`, `running`, `completed`, `failed`, `skipped` |
| `summary` | 기존 EvaluationSummary: total/labeled/evaluable/matches/outcomes/source_groups |

검증 상태와 집계는 같은 짧은 SQLite 읽기 스냅샷에서 반환한다. active run 중복은 기존처럼 409다. 데이터셋 읽기/검증 오류는 422 `model_validation_dataset_unavailable`, 분석·프롬프트 접수 오류는 안전한 코드와 원래 409/503 상태를 반환하며 전체 접수를 rollback한다.

## 중단·설정 변경

일반 analysis worker는 검증 연결 문항을 선점하지 않는다. model-test worker가 고유 claim UUID와 부모 run heartbeat를 사용한다. 모든 단계 저장 전 현재 owner를 확인해 이전 worker가 새 결과를 덮어쓰지 못하게 한다. 만료된 run을 다시 선점하면 이미 완료/실패한 문항을 건너뛰고 미완료 문항만 처리한다. 저장된 기능 검증 통과 결과는 재개 시 재호출하지 않는다.

실제 HTTP 요청 직전 후보 프로필의 지문·비활성화·승인·허용 대상을 다시 확인한다. 변경 후 요청은 차단하지만 이미 전송된 요청은 회수하지 못한다. HTTP 응답 후 저장 전에 중단되면 해당 미완료 문항을 다시 호출할 수 있으므로 비용상 exactly-once는 보장하지 않는다. 사용자용 실행 취소/실패 문항만 재평가 API는 이번 범위가 아니다.

## DB 변경·검증 한계

Alembic `0009_model_validation_dataset`는 `vllm_test_runs`에 `include_dataset`, `dataset_version`, `dataset_hash`, `analyses`에 nullable FK `model_test_run_id`와 인덱스를 추가한다. 기존 행은 미포함/null로 보존하고 샘플 분석을 자동 접수하지 않는다. 새 운영 패키지 의존성은 없다.

이름 있는 실행·문항·초기 답안 연결과 검증 이름/접수 키에는 추가로 `0010_test_runs`가 필요하다. 새 공통 실행으로 과거 데이터를 추정해서 소급 생성하지 않으며 기존 검증 API의 과거 이력은 그대로 조회할 수 있다.

upgrade는 기존 분석 부모 테이블을 재생성하지 않는다. downgrade는 검증 이력이 있으면 거부하며 일반 분석 이력이 있더라도 SQLite 재생성의 cascade 삭제 위험 때문에 거부한다. 빈 분석 테이블에서만 되돌릴 수 있다. 운영 배포에는 백업·worker 중지·최신 migration·새 worker 기동이 필요하다.

합성 모의 응답/격리 SQLite 테스트와 실제 LLM 품질·과금·장시간 다중 프로세스 장애 검증은 다르다. 실제 vLLM/OpenAI 연결 테스트는 승인된 환경에서 별도로 수행한다.
