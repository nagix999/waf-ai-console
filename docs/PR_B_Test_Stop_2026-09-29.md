# PR B — 일반 테스트 중지

기준: `ASTRA_PR_ABC_FINAL_IMPLEMENTATION_INSTRUCTIONS.md`의 PR B. 시작 commit `bb27f24`(main)의 PR A와 기존 기능을 보존했다. PR C는 구현하지 않았다.

## 동작

- 목록 행의 `⋯ → 테스트 중지`, 상세의 `테스트 중지`에서 같은 확인창/API를 사용한다.
- 확인창은 필터·과거 평가와 무관하게 전체 현재 테스트를 다시 조회한다. 완료 건수와 중지 대상인 대기/처리 중 건수를 보여 준다. 확인 중 완료 건수가 바뀔 수 있음을 안내한다.
- 현재 문항의 최신 attempt 중 대기/처리 중인 것만 중지한다. 완료 문항·실제 실패·과거 attempt·라벨·저장 평가·Production 분석은 보존한다.
- 처리 중 요청의 HTTP 전송을 강제로 취소한다고 보장하지 않는다. 응답이 늦게 돌아와도 완료/실패로 덮어쓰지 않으며, 후속 Primary/Verifier/Editor/Retry 호출 전 중지 여부를 검사한다.
- 중지된 테스트는 개별/일괄 재실행, 새 문항 접수, 공식 평가 자동 완료, 운영 승격에 사용할 수 없다. 다시 실행하려면 기존 `이 설정으로 다시 테스트`에서 새 실행을 만든다.
- 150건 모델 검증(`model_test_run_id != null`)은 별도 worker 계약이므로 이번 중지 대상에서 제외한다.

## DB / API

Alembic head: `0026_test_run_stop` (`0025_analysis_read_index` 다음).

- `test_runs.stopped_at`: nullable datetime.
- `test_runs.stopped_by`: nullable string(255).
- `analyses.status`의 문자열 상태에 `canceled` 추가. 새 테이블/인덱스/패키지는 없다.
- 중지 이력이 있으면 downgrade는 거부한다. 중지 이력을 지우거나 `failed`로 바꾸는 rollback은 하지 않는다. 배포 전 DB 백업과 API·worker의 동일 버전 교체가 필요하다. 새 상태를 모르는 구버전 worker와 혼용하지 않는다.

관리자 전용 `POST /api/v1/test-runs/{run_id}/stop`, body 없음, idempotent:

- 성공: 현재 `TestRunSummary` 반환. 재요청은 처음 중지 시각/관리자를 보존하며 감사 이력을 중복 추가하지 않는다.
- 404 `test_run_not_found`.
- 409 `test_run_not_active`: 완료/실패이고 추가 접수도 닫힌 테스트.
- 409 `test_run_stop_not_supported_for_model_validation`: 150건 모델 검증.
- 기존 세션 인증·Origin/CSRF 정책을 유지한다. Service API Key로 중지할 수 없다.

Summary에 `canceled`, `stopped_at`, `stopped_by`, `can_stop`을 추가했다. `can_stop`은 **전체 현재 테스트** 기준이므로 일부 완료 문항 필터나 과거 평가를 보고 있어도 잘못 숨겨지지 않는다. 일반 조회의 중지 상태는 `stopped`가 최우선이다. 과거 공식 평가 조회는 저장된 status/점수/시각을 보존하고 별도의 현재 중지 메타데이터로 조작을 막는다. 저장된 `TestEvaluation.summary_json` 자체는 수정하지 않는다.

문항 status/evaluation_outcome 필터에 `canceled`를 허용한다. 평가 outcomes와 runtime outcome_summary/series에서 중지를 분리하고 정오답 지표·실행 실패율에 포함하지 않는다. 실패율 분모는 `completed + failed`이다. 완료된 일부 문항의 지표에는 전체 테스트 평가가 아니라는 안내가 붙는다.

## 저장 경합 보호

SQLite write lock 안에서 TestRun 재조회 → 현재 attempt 조회 → 중지 시각/접수 차단 → canceled/lease 무효화 → 진행 중 trace 종료 → audit → commit 순서로 처리한다.

Worker는 단계 저장·최종 저장·실패 저장 전에 active 상태의 조건부 UPDATE로 writer lock과 소유권을 확인한다. 일반 heartbeat claim이 없는 직접 실행 경로도 보호한다. 중지와 완료 저장 중 먼저 commit한 상태를 유지한다.

AgentRun/Step에는 새 enum을 추가하지 않고 내부 `failed` 및 `test_run_stopped`를 기록한다. 진행 중 Step의 시간은 `timing_incomplete=true`로 표시한다. 사용자에게는 `테스트 중지`로 표시하며 일반 실패 안내에 섞지 않는다.

Audit: `stop_test_run`, resource_type=`test_run`, 해당 run ID, 관리자 식별자. Payload/Cookie/모델 원문은 넣지 않는다.

## 검증

로컬 SQLite/합성 fixture·모의 HTTP로 검증했다.

- Backend 전체: **2,459 통과, 3 건너뜀**. 건너뛴 항목 중 실제 PDF 출력 2개는 출력 환경 플래그를 켜고 별도로 통과했다. 준비된 PDF 배치 fixture가 필요한 1개는 미실행이다.
- 최종 Backend 관련 회귀: **110 통과**. 테스트 중지, 공식 평가, 운영 승격, 실제 PDF 출력, 한 글자 출력 스키마/교정 Retry를 포함한다.
- DB: 빈 DB 설치, 기존 `0025` 형태의 DB에서 `0026`으로 업그레이드, 기존 분석 행 보존, 재실행, 안전한 downgrade 및 중지 이력 존재 시 downgrade 차단 통과. 임시 DB로 확인했으며 운영 DB에는 적용하지 않았다.
- Frontend: **542 통과**, Vite production build 성공. 기존 단일 JS 청크 500 kB 초과 경고는 남아 있다.
- 실제 React 화면 + 모의 API 브라우저 검증: **26개 스크린샷**. SK/Light/Dark, KR/EN, 좁은 화면, 키보드·모달 포커스·Escape, 버튼 글자 대비, 중복 클릭, 오류 원문 숨김/자동 재전송 없음, 필터·URL 보존, 중지 문항의 평가 제외를 확인했다. 화면 검증은 실 서비스 API 통합 테스트를 대체하지 않는다.

실제 vLLM/OpenAI 호출, 운영 DB migration, Docker 재배포, Git commit/push는 수행하지 않았다. 배포 후 실제 vLLM에서 새 테스트의 비교 설명 및 중지 동작을 확인해야 한다. 서버에 이미 전송한 모델 요청의 연산·과금이 즉시 멈춘다는 보장은 없다.

브라우저 산출물: `test-results/pr-b-stop/` (테스트 목록/상세/확인창/평가 상세, SK·Light·Dark, KR·EN, 좁은 화면). 스크립트: `frontend/scripts/pr-b-browser-smoke.mjs`.

별도로 보고된 탐지 비교의 한 글자 출력 수정은 [별도 기록](Signature_Output_Schema_Fix_2026-09-29.md)을 참고한다.
