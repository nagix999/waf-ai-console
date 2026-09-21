# Canonical R3 SOLO 적용 기록

작성: 2026-09-21. 기준은 `WAF_AI_Console_Astra_Handoff_CANONICAL_V5_REVIEWED_R3_SOLO/`의 ASTRA_HANDOFF → BACKEND_CONTRACT → UI_IMPLEMENTATION_SPEC → UI_DESIGN_GUIDELINES → IMPLEMENTATION_CHECKLIST 순서다. 이전 V5/R2 문서와 목업을 구현 기준으로 사용하지 않았다.

이번 작업은 코드·임시 DB·브라우저 검증까지다. 실행 중인 서비스 재배포, 운영 DB 변경, 실제 LLM 호출, GitHub 게시와 main 병합은 하지 않았다.

## 1. 시작점과 보존

| 항목 | 기록 |
| --- | --- |
| 작업 시작 commit | `17c2f5fb1cb43fe9ff1683d97d79445de6d60ab8` |
| 시작 시 작업환경의 origin/main | 위 commit과 동일 |
| 리디자인 전 참고 commit | `b43158dafc928e7a541b18e1e4faf1b522faaf9a` |
| 기존 변경 안전 지점 | `77f71f6`, `safety/pre-r3-solo-20260921` |
| 별도 Git bundle | `.local-deploy/pre-r3-solo-20260921.bundle` |
| 작업 branch | `feature/canonical-r3-solo` |

리디자인 전 commit은 확인했지만, 이후 추가된 Backend와 배포 자동화까지 되돌아가는 것을 피하기 위해 **현재 유효한 코드 위에서 UI와 필요한 계약만 교체**했다. v0.2.0 reset, 강제 checkout, 이력 삭제는 하지 않았다.

시작 전에 있던 배포·업로드 스크립트 변경과 R3 전달 폴더는 안전 commit에 보존했다. R3 구현 변경은 현재 작업 branch의 작업 트리에 있으며 아직 별도 구현 commit으로 게시하지 않았다. 기존 untracked `backend/waf_ai_console_backend.egg-info/`는 건드리지 않았다.

이 환경에서는 Git metadata가 `.local-deploy/canonical-v5-reviewed.git`에 있다. 조사와 branch 작업은 `git --git-dir=.local-deploy/canonical-v5-reviewed.git …`로 수행했으며 루트 `.git`을 덮어쓰지 않았다.

## 2. UI 변경

- 메뉴를 **Overview → Evaluate → Configure → Operate → Connect**로 정리했다. 상시 Promote 메뉴는 제거했다.
- 승격은 자격이 확인된 Test 또는 Action Center에서 `#promotion/{test_run_uuid}`로 연다. 돌아가기는 해당 Test로 이동한다. 기존 `#promote`는 Tests 선택 화면으로 연결하고 후보를 추측하지 않는다.
- Overview는 실제 worker heartbeat, 키별 운영 요청, 현재 공식 평가와 **같은 비교 기준**의 추이·최근 테스트, 연결된 Ground Truth 초안, Action Center를 보여준다. 비교 가능한 운영 구성이 2개 미만이면 그래프 대신 현재 공식 평가를 표시한다.
- Tests 목록은 기본 5열이며, 지표 펼치기로 Accuracy / Precision / Recall / F1 / Coverage를 함께 보여준다. 없는 값은 `—`다. 실패·접수 거부가 남은 테스트 필터를 추가했다.
- Test 상세는 같은 작업 화면에서 열고 문항은 오른쪽 Drawer로 확인한다. 전체 분석 상세의 Result / Agent Trace / Input / Result JSON / Report와 기존 PDF·Excel을 유지했다.
- 브라우저 이력은 기존 방식을 유지했다. Test 필터·검색·지표 펼침·문항 선택을 메모리에서 복원하고 스크롤 복원을 보강했다. 검색어·폼·원문은 URL이나 history.state에 저장하지 않는다.
- Ground Truth는 데이터셋 선택기 + 문항 표 + 편집 패널이다. 저장 시 자동 검사, 상태·변경·답안·출처 검색, 일괄 포함/제외·태그·유형·삭제, 발행, 문항 되돌리기, 초안 전체 되돌리기를 제공한다.
- 분석 결과에서 선택해 가져오기는 새 작업 초안으로 연결했다. 과거 리비전과 당시 입력·필드 정의는 읽기 전용 이력에서 열 수 있다.
- Activity는 시각 / 종류 / 변경 중심으로 표시하며 작업자와 기술 식별자는 상세에 둔다.
- 로그인은 관리자 계정 폼과 테마/언어 선택만 둔다. SK 기본, Light·Dark, 공통 표·메트릭·섹션·Drawer·Dialog·테마 선택 컴포넌트를 재사용한다.
- 테스트 조회는 진행 중 5초, 안정 상태 25초 간격이며 숨겨진 탭에서는 멈춘다. 서버 오류는 간격을 늘려 재조회하고 인증 오류는 중단한다.

제거한 것은 기존 UI의 독립 Promote 메뉴, 새 Ground Truth 화면의 문항별 Draft/Reviewed/Approved 조작, 데이터셋 계층 URL을 새로 만드는 동작이다. **기존 DB 승인 기록·리비전·평가와 읽기 API는 삭제하지 않았다.**

## 3. Backend / DB

새 migration: [`0023_ground_truth_working`](../../backend/alembic/versions/0023_ground_truth_working.py). 부모는 `0022_production_lifecycle`이다. 새 운영 패키지는 없다.

| 변경 | 의미 |
| --- | --- |
| `validation_dataset_working_states` | 데이터셋별 초안 revision과 수정 시각 |
| `validation_dataset_working_items` | 수정 가능한 문항·태그·제외 여부·자동 검사 결과; 입력·필드 정의·메모 암호화 |
| `validation_dataset_versions.is_published` | 명시적으로 발행한 새 리비전 표시; 기존 행은 false |
| `membership_hash`, `publish_metadata` | 고정 문항 구성, 발행 당시 전체/포함/확인 필요/제외 수·포함 비율·초안 revision |
| SQLite 불변성 trigger | 발행 리비전과 그 문항의 수정·삭제 차단; 원본 분석 삭제 시 연결 해제만 허용 |

초안 저장은 immutable 문항/데이터셋 버전을 매번 만들지 않는다. 기존 최신 저장본은 읽기에서는 가상 초안으로 보여주고 첫 쓰기에서 복사한다. 기존 데이터를 자동 발행하거나 과거 평가를 새 공식 평가로 소급 인정하지 않는다.

발행은 쓰기 잠금 안에서 초안 revision을 대조하고 모든 문항을 다시 검사한다. Ready이면서 Excluded가 아닌 문항만 고정한다. 빠지는 문항이 있으면 데이터셋 단위 확인이 필요하다. 같은 초안의 중복 발행은 동일 리비전을 반환한다.

같은 입력을 다시 가져오는데 답안·내부 전용 조건이 다르면 기존 답안을 덮어쓰지 않고 해당 초안을 `Needs attention`으로 표시한다. 문항을 확인해 저장하기 전까지 이 표시를 유지한다. 내부 전용 조건은 더 강한 제한을 유지한다.

새 공식 평가는 선택한 **Published Revision의 정확한 전체 문항**을 고정한다. 운영 승격은 기존 원자적 transaction을 보존하며 Primary / Verifier / Evidence Editor / Instructions / Input Schema를 함께 반영한다. 최신 운영 hash, 모델 fingerprint·검증, 고정 평가, 스키마 변경 확인을 다시 검사한다. Concurrency는 별도 Runtime 설정이다.

비교 키는 dataset/revision + 지표 버전 + 정규화한 평가 범위를 반영한다. 공식 평가에서 서로 다른 키는 서버와 UI 모두 직접 점수 비교를 막는다. 기존 비공식 참고 답안 비교 기능은 유지한다.

재실행의 최신 시도를 기준으로 실패 테스트를 집계한다. 운영 키로 원본 분석을 삭제하더라도 작업 초안·발행 사본은 유지하고 삭제된 원본 연결과 감사 이력만 갱신한다.

기존 FastAPI·SQLite WAL·암호화·Origin/CSRF·vLLM 허용 대상·OpenAI 외부 전송 승인·키 처리·Agent/Verifier·Evidence Editor·retry/recovery·PDF/Excel을 제거하거나 다른 구현으로 교체하지 않았다.

## 4. API 변경

아래 데이터셋 경로의 공통 prefix는 `/api/v1/validation-datasets/{dataset_id}`다. 모두 기존 admin 권한과 세션 보호를 사용한다.

| Method / suffix | 기능 |
| --- | --- |
| GET `/working` | 초안·자동 상태·변경 수·발행 이력 |
| POST `/working/search` | 검색어/상태/변경/답안/출처 필터와 pagination; 검색어는 body |
| GET `/working/items/{case_id}` | 감사 기록을 남기고 입력·답안·필드 정의 조회 |
| POST `/working/items` | 초안 문항 추가 |
| PUT `/working/items/{case_id}` | 초안 문항 수정 |
| POST `/working/bulk` | include / exclude / tag / categorize / delete |
| POST `/working/imports` | 선택한 분석 복사, 중복·답안 충돌 구분 |
| POST `/working/items/{case_id}/revert` | 해당 문항을 최근 발행 내용으로 복구 |
| POST `/working/discard` | 초안을 최근 발행 전체로 복구 |
| PATCH `/working/metadata` | 데이터셋 이름·설명 변경 |
| POST `/publish` | 확인된 초안의 Ready 문항을 불변 리비전으로 발행 |

모든 초안 쓰기는 `expected_working_revision`을 받는다. 오래된 값은 `409 ground_truth_working_changed`, 누락 문항 확인이 없으면 `409 ground_truth_exclusion_ack_required`다. 발행할 Ready 문항이 없으면 422다.

추가/확장:

- GET `/api/v1/admin/production-configurations/overview`: 현재 공식 평가에 고정된 Overview context·비교 추이·초안 상태·확인할 항목.
- 기존 데이터셋 실행 POST `…/runs`: 선택한 `dataset_revision_id` 지원. 공식 모드는 발행 리비전만 허용한다.
- GET `/api/v1/test-runs`: 선택적 `has_failures=true` 필터. 재실행으로 해결된 과거 실패는 제외한다.
- Test 평가 요약: dataset/revision, sample_count, metrics_version, evaluation_scope_hash, comparison_key 메타데이터 추가.
- 기존 데이터셋 읽기·과거 평가 API는 유지한다. 새 작업 초안을 만든 데이터셋에 기존 문항별 버전 생성 API로 쓰려고 하면 409로 막아 두 저장 경로가 어긋나지 않게 했다.

기존 Production 분석 접수 형식과 인증 계약은 바꾸지 않았다. 입력 스키마 변경은 해당 스키마를 포함한 후보의 공식 평가·승격 뒤에만 운영 접수 기준으로 반영된다.

## 5. 검증

실제 사내 로그·운영 DB·API Key를 연결하지 않았다. 백엔드는 네트워크를 차단한 임시 컨테이너, SQLite 테스트 DB와 합성 입력으로 검증했다. 브라우저는 실제 React 렌더링에 합성 API 응답을 연결했다.

| 검사 | 결과 |
| --- | --- |
| 백엔드 전체 회귀 | 전체 실행 완료. 첫 실행 실패 6건은 head 기대값 4건과 검사용 scripts mount 누락 2건; 아래 재검증으로 해소 |
| migration·실패 항목·Test 비교 재검증 | 108 passed |
| Ground Truth·재실행·공식 평가·승격 검사 | 48 passed |
| 최종 R3 입력 경계 + PDF 검사 | 48 passed, 1 skipped; 한글 글꼴·긴 보고서의 실제 ReportLab 출력 포함 |
| 최종 중복 답안 충돌 + R3 migration 검사 | 12 passed |
| R3 실제 이전 DB 모양 → 0023 upgrade | 기존 모든 컬럼 값·참조 보존, 재실행 가능, 발행 수정/삭제 차단 검증 통과 |
| 프런트엔드 | 47개 테스트 파일 통과 |
| Vite build | 통과; 기존 단일 JS chunk 500 KB 초과 경고는 남음 |
| 기존 배포/업로드 자동화 회귀 | 43 tests, OK |
| 브라우저 | 5개 메뉴, 3개 테마, 초안 발행 확인, 공식 평가 fallback, 승격 중복 클릭, Case Drawer/Escape/뒤로 가기, 테마·언어 전환 상태 보존 통과 |
| 스크린샷 | 1440×900 데스크톱 기준 48장 생성 |
| Git diff 공백 검사 | 통과 |

전체 검사와 수정된 항목 재검증을 나눠 실행했다. 이를 최종 수정본의 단일 전체 실행 결과로 표현하지 않는다. 새 R3 테스트도 별도로 추가 실행했다. 브라우저의 수치와 문항은 QA fixture이며 실제 모델의 평가 결과가 아니다.

PDF 검사에서 건너뛴 1건은 선택적 `/preview` 준비 문서 3종이 없어 실행하지 않은 보조 fixture 검사다. 실제 한글 글꼴·활성 링크/스크립트 차단·긴 근거와 긴 문자열·여러 페이지 ReportLab 출력 검사는 `WAF_TEST_REPORT_RENDERER=1`로 실행해 통과했다. 새 초안의 payload 저장 한도와 입력 JSON 안의 참고 답안 필드가 Ready로 인정되지 않는 것도 확인했다.

## 6. 주요 화면

스크린샷은 Git 제외 경로 `test-results/r3-solo/`에 있다. 재생성 스크립트는 [`frontend/scripts/r3-solo-smoke.mjs`](../../frontend/scripts/r3-solo-smoke.mjs)다. QA용 로컬 브라우저만 사용하며 배포 이미지에 Chromium을 추가하지 않았다.

| 화면 | 스크린샷 |
| --- | --- |
| Overview / SK | [overview-SK.png](../../test-results/r3-solo/overview-SK.png) |
| Ground Truth / SK | [ground-truth-SK.png](../../test-results/r3-solo/ground-truth-SK.png) |
| Contextual Promotion / SK | [promotion-SK.png](../../test-results/r3-solo/promotion-SK.png) |
| Tests 지표 펼침 / Dark | [tests-expanded-Dark.png](../../test-results/r3-solo/tests-expanded-Dark.png) |
| Case Drawer / Dark | [test-case-drawer-Dark.png](../../test-results/r3-solo/test-case-drawer-Dark.png) |
| 입력 스키마 / Light | [Light-connect-input-schema.png](../../test-results/r3-solo/Light-connect-input-schema.png) |
| 로그인 / SK | [login-SK.png](../../test-results/r3-solo/login-SK.png) |

## 7. 운영 적용 전 남은 확인

- 실행 중인 DB의 별도 백업·복제본에서 migration 리허설 후 배포한다. 이번 검사는 실제 운영 DB 복제본이 아니라 임시 fixture DB였다.
- 실제 vLLM/Gemma/Qwen/OpenAI 호출과 내부 데이터의 정확도·성공률·동시 부하는 측정하지 않았다. 이번 변경으로 개선됐다고 주장하지 않는다.
- 브라우저 검사는 합성 API 응답 기반이며 실제 배포 Backend와 연결한 종단 간 동작은 아직 확인하지 않았다.
- 새 평가 공식 경로: 초안 확인 → 리비전 발행 → 후보 구성 선택 → 공식 Test 완료 → 승격 화면에서 검토 → 관리자 적용. 기존 운영은 최초 승격 전까지 그대로다.
- 신규 초안이나 발행 이력이 쌓인 뒤 `alembic downgrade`는 차단한다. 롤백은 앱만 옛 버전으로 바꾸지 말고 **배포 전 DB 백업과 맞는 코드/설정**으로 복구해야 한다.
- SK/Light/Dark의 주요 화면은 확인했다. 모바일·다른 브라우저·스크린리더 전수 검사는 수행하지 않았다. 기존 세부 분석 문구의 전체 영문 번역도 이번 범위의 완료 항목은 아니다.
- Overview는 최근 500개 공식 평가 기록을 비교 후보로 읽는다. 긴 운영 이력 전체를 대상으로 한 성능·보관 정책 검증은 별도다.
