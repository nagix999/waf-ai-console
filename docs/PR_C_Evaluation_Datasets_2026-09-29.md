# PR C — 평가 데이터셋 UX

기준: `ASTRA_PR_ABC_FINAL_IMPLEMENTATION_INSTRUCTIONS.md`의 PR C. 시작 commit은 main의 `3f20d29`이며 PR A/B와 한 글자 출력 수정을 보존했다. 기존 미추적 `backend/waf_ai_console_backend.egg-info/`는 변경하지 않았다.

## 구현

- 평가 메뉴의 `정답 데이터`를 `평가 데이터셋`으로 정리했다. 초안 문항은 `기대 판정`, 접수·분석에 별도로 연결하는 비교 값은 `참고 판정`으로 표시한다. 관련 화면·검색·평가 설명·보고서의 고정 문구도 맞췄다. 사용자 이름, 모델 출력, 저장된 원문/JSON은 번역하거나 다시 쓰지 않는다.
- 진입 화면은 공통 DataTable 기반 목록이다. 이름/설명, 초안 문항·확인 필요 수, 최근 공식 버전·문항 수, 현재 운영 평가에 사용된 버전, 변경 수를 표시한다. 기존 catalog 검색·서버 페이지네이션을 재사용하며 행마다 상세를 조회하지 않는다.
- `현재 운영 평가 · rN`은 `productionConfiguration().evaluation.summary.ground_truth`에서만 가져온다. 최근 발행 r4와 운영 평가 r3가 달라도 각각 표시한다. 데이터셋 자체를 공식 상태로 태깅하거나 행을 강제로 상단에 복제·정렬하지 않는다. 운영 평가 조회 실패는 별도 안내하고 버전을 추정하지 않는다.
- 상세는 `← 평가 데이터셋`, 초안 요약, 최근 공식 버전, 공식 버전 만들기와 기존 관리 메뉴를 제공한다. 문항 편집, 추가·삭제·복원, 일괄 변경, 출처 필터, 초안 변경 되돌리기, 발행/이전 이력, 미저장 수정 확인을 보존한다.
- 상세 URL은 `#evaluate/ground-truth/{UUID}`이다. 목록 주소와 이전 `#datasets/{UUID}` 링크를 지원한다. 목록 검색·페이지는 기존 메모리 이력에만 보존하고 URL/browser history.state/storage에 검색어·초안·원문을 넣지 않는다.
- 테스트 전체·선택 문항·문항 상세의 복사 경로가 같은 접수 종료 조건을 사용한다. `테스트 문항을 평가 데이터셋에 복사` 창은 입력과 기존 기대 판정만 복사하고, 미리보기/명시적 저장/공식 버전 만들기를 분리한다.
- 미리보기는 `신규·기대 판정 있음 / 신규·기대 판정 없음 / 중복 / 판정 충돌 / 가져올 수 없음`의 배타적 구분이다. API `new_count`에 이미 포함된 `missing_reference_count`를 이중으로 세지 않는다.

## DB / API / 보존 계약

새 migration·테이블·인덱스·운영 의존성은 없다. head는 PR B의 `0026_test_run_stop` 그대로다. 내부 `ground_truth`, `reference_verdict`, `reference_label` 이름과 요청/응답 필드를 유지한다.

기존 관리자 API:

```text
POST /api/v1/test-runs/{run_id}/ground-truth-import/preview
POST /api/v1/test-runs/{run_id}/ground-truth-import/confirm
```

복사 허용 조건만 변경했다.

| 문항 접수 | 모델 처리 상태 | 복사 |
|---|---|---|
| 열림 (`accepting_items=true`) | 모든 상태 | 409 `test_ingestion_open` |
| 닫힘 (`accepting_items=false`) | pending / processing / completed / failed / stopped | 허용 |

preview와 confirm 모두 기존 SQLite writer lock 안에서 TestRun을 새로 읽고 접수 종료를 검사한다. 원본 membership/기대 판정과 대상 초안 변경 감지, 미리보기 만료, 관리자별 토큰, 재전송 중복 방지, 원문 암호화와 내부 모델 전용 조건은 유지한다. 접수 종료는 기존 `POST /api/v1/test-sessions/{id}/close`를 사용한다. UI는 이전 서버의 `test_must_finish_before_import` 오류도 읽을 수 있다.

기대 판정 출처는 공식 버전의 고정 값 → 접수 당시 참고 판정 → 없음 순서다. 모델 최종 판정을 fallback으로 사용하지 않는다. 없음은 `확인 필요`로 복사하며 자동 발행·사람 승인·운영 승격은 없다. 진행 중 모델 결과가 저장되거나 테스트가 중지되더라도 원본 입력/기대 판정이 같으면 미리보기를 무효화하지 않는다.

새 평가 데이터셋 이름을 비우면 테스트명을 그대로 사용한다. 이전에 자동으로 붙였던 `Ground Truth` 접미사를 새 생성에만 생략하며 기존 이름은 변경하지 않는다.

## 검증 기록

- 초기 복사 API/R5 회귀: 31개 통과.
- 전체 Backend: 테스트 파일 111개를 4묶음으로 실행해 **2,476개 통과, 1개 생략**. `/preview`의 선택적 PDF 예제 fixture가 없어 해당 검사만 생략했다. PDF 렌더러 연동 검사는 `WAF_TEST_REPORT_RENDERER=1`로 포함했다. 기존 Starlette/anyio deprecation 경고는 남아 있다.
- 신규 DB와 이전 revision의 임시 DB를 head로 올리는 기존 migration 회귀도 통과했다. 운영 DB에 migration을 실행한 것은 아니다.
- Frontend: 546개 통과. 빌드 성공; 기존 JS 단일 청크 크기 경고는 남아 있다.
- 실제 React/CSS + 모의 API 브라우저 확인: SK/Light/Dark, KR/EN, 모바일, 키보드 진입·배지 도움말, 검색·페이지/뒤로·앞으로 복원, N+1 상세 조회 없음, 현재 운영 구성 기준 r3/최신 r4 구분, 접수 중 차단, 진행·중지 테스트 복사, 중복 제출 방지, 안전한 오류 안내를 확인했다. 스크린샷 24개를 저장하고 대표 목록·상세·복사·모바일 화면을 시각 검토했다.
- 마지막 화면 검토에서 변경 수 열의 숫자 정렬과 조회 실패 후 불필요한 로딩 문구를 수정하고 frontend 테스트·build·브라우저 검사를 다시 통과했다. `git diff --check`도 통과했다.

스크린샷: `test-results/pr-c-datasets/`. 재현 스크립트: `frontend/scripts/pr-c-browser-smoke.mjs`.

대표 화면: [목록](../test-results/pr-c-datasets/catalog-KR-Light.png), [상세](../test-results/pr-c-datasets/detail-KR-Light.png), [복사](../test-results/pr-c-datasets/copy-EN-Dark.png), [모바일 상세](../test-results/pr-c-datasets/detail-mobile.png). 스크린샷은 로컬 검증 산출물이며 Git 추적 대상이 아니다.

## 미검증 / 이번 작업에서 하지 않은 일

- 실제 운영 DB/API를 연결한 통합 사용자 검증과 운영 규모의 성능 측정.
- 실제 vLLM/OpenAI 호출. PR C는 Agent 출력·지침·모델 선택을 변경하지 않는다.
- 운영 DB migration, Docker 재배포, Git commit/push, Production 설정 변경.

브라우저 fixture는 합성 데이터이며 실제 서비스의 평가 수치나 운영 데이터가 아니다. 기존 공식 버전·과거 평가 snapshot을 다시 쓰거나 human-review 정책을 변경하지 않았다.
