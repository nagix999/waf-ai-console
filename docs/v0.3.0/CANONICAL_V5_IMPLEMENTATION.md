# Canonical V5 REVIEWED 구현 기록 — 2026-09-21

## 기준과 시작점

요청된 우선순위를 적용한다: ASTRA_HANDOFF → BACKEND_CONTRACT → UI_IMPLEMENTATION_SPEC → UI_DESIGN_GUIDELINES → IMPLEMENTATION_CHECKLIST → V5 목업. 원본 V5 문서는 수정하지 않았다. 이전 개발 중 UI나 `v0.2.0` 태그를 최종 기준으로 삼지 않았다.

- 확인한 원격 main / v0.3.0 작업 직전 commit: `b43158dafc928e7a541b18e1e4faf1b522faaf9a` (2026-09-16, LLM JSON 검증 진단 개선).
- 작업 전 모든 기존 소스와 미커밋 개발 내용을 보존한 안전 지점: `safety/pre-canonical-v5-20260921`, commit `c1a68ccb920c9a7043328877e8751fa6bd8ec6fc`.
- 구현 branch: `feature/canonical-v5-reviewed`, 위 main commit에서 시작. 작업 폴더를 과거 버전으로 덮어쓰거나 reset하지 않았다.
- 이 환경의 원래 `.git`은 비어 있는 읽기 전용 디렉터리여서 원래 로컬 branch/HEAD는 확인할 수 없었다. 원격 이력과 현재 파일을 별도로 비교해 시작점을 확인했다. 작업 Git 메타데이터는 `.local-deploy/canonical-v5-reviewed.git`, 안전 지점 bundle은 `.local-deploy/pre-canonical-v5-20260921.bundle`에 보존했다. 일반 Git 명령 대신 필요할 때 `git --git-dir=.local-deploy/canonical-v5-reviewed.git ...`을 사용한다.
- `.env`, API Key, DB, 운영 로그, 가상환경, node_modules, 실행 결과물은 커밋 대상이 아니다. GitHub push와 재배포는 수행하지 않았다.

## 보존과 대체

| 보존 | V5에서 변경 |
|---|---|
| FastAPI·SQLAlchemy·SQLite WAL·moduagent 비동기 실행 | Candidate 구성과 공식 평가를 확인하는 승격 트랜잭션 추가 |
| Primary·조건부 독립 Verifier·선택적 Evidence Editor | Production 역할은 읽기 전용, Test 기본값은 별도 저장 |
| 출력 교정 retry, lease recovery, 실패 일괄 재실행, 최신 재실행 평가 | 공식 평가는 고정된 답안·구성으로 새 기록을 생성 |
| payload/단계 I/O 암호화·원문 접근 감사·Origin/CSRF·egress·OpenAI 승인 | 변경 이력은 별도 불변 ChangeEvent, 민감 본문 제외 |
| Test/Production 키, 검증 데이터셋 CRUD/import, 참고 답안과 3×3 평가 | Ground Truth의 Draft/Reviewed/Approved와 승인 문항만의 공식 평가 |
| 기존 DB와 `0001`~`0018` migration | `0019`~`0021`의 유효한 기능을 V5 계약에 맞춰 검토·보존, `0022` 추가 |
| ReportLab PDF·Excel·Markdown, 원문/JSON 트리·근거 위치 이동 | 상세 5개 탭과 공통 레이아웃에 재사용 |
| 기존 URL의 뒤로 가기·필터·검색·선택·다운로드 | canonical hash route와 이전 경로 alias |

이전 5개 메뉴와 상시 하위 Sidebar, 별도 설정 탐색을 6개 Workspace로 교체했다. 직접 Production 모델 승격·지침 적용·스키마 적용/복귀 버튼을 제거했다. 기능을 삭제한 것이 아니라, 저장 버전을 후보로 테스트하고 Promote하는 경로로 통합했다. API도 이를 우회할 수 없다. 과거 분석·평가를 새 공식 평가로 소급 변환하지 않는다.

## 사용자 흐름

1. Configure에서 LLM 프로필 등록·전체 검증, 분석 지침 버전 저장.
2. Connect에서 입력 스키마 저장·비교·샘플 검증. 이 단계는 Production에 적용하지 않는다.
3. Evaluate의 New Test에서 Primary/Verifier/Editor와 저장된 지침·스키마를 명시적으로 선택한다. 테스트명·답안·필드 설명은 LLM 입력이 아니다.
4. Ground Truth의 문항을 검토·승인한다. 수정은 새 Draft 버전을 만들고 기존 승인 버전을 덮어쓰지 않는다.
5. 승인 문항으로 공식 평가를 실행한다. 접수 당시 데이터셋 버전·승인 문항 ID·답안·구성이 고정된다.
6. Promote에서 실행 완료, 실패/반려 없음, 공식 평가, 현재 프로필 지문·검증·egress·외부 승인, 저장 지침·스키마 일치를 확인한다. Accuracy/F1 고정 합격선이나 자동 승격은 없다.
7. 관리자가 현재 구성과 후보의 변경 내역·평가를 확인하고 승격한다. 스키마가 바뀌면 API 요청의 거절 가능성을 안내하고 별도 동의를 받는다.

일반 단건/파일 테스트와 참고 답안 비교도 유지하지만 공식 Ground Truth 평가를 대신하지 않는다. 모의 실행(stub)은 승격 근거가 아니므로 새 V5 후보 테스트 화면에서 실행을 차단한다. 기존 legacy API의 optional candidate 호환은 유지한다. LLM 프로필의 연결/전체 검증과 선택형 150건 검증은 기존 기술 검증이며 공식 운영 승격이 아니다.

## Backend / DB / API

추가 runtime dependency는 없다. API·frontend package version은 `0.3.0`으로 맞췄다.

| Migration | 내용 |
|---|---|
| `0019_ground_truth_review` | 문항 버전 review_status와 제약. 기존 문항은 Draft, 기존 평가 보존 |
| `0020_test_candidate_configuration` | TestRun의 구성 snapshot/hash |
| `0021_official_evaluation` | 공식 평가 종류·고정 구성·승인 문항 범위·평가 저장/복구 상태 |
| `0022_production_lifecycle` | ProductionPromotion, ChangeEvent, WorkerHeartbeat. 승격/변경 이력 UPDATE·DELETE 차단 |

`0022`는 신규 설치 시 현재 metadata를 사용하는 기존 `0001`도 고려해 중복 테이블/인덱스 생성을 방지한다. 데이터가 남아 있는 승격/변경 이력을 삭제하는 downgrade는 거절한다. 실제 배포에서는 Alembic 이전 후 API 시작 시 기존 운영 설정의 baseline을 한 번 기록한다. 이는 운영 교체나 자동 승격이 아니다.

| API | 역할 |
|---|---|
| `GET /api/v1/admin/production-configurations` | 현재 구성·hash·승격 ID·이름·연결 공식 평가 |
| `GET .../production-configurations/history` | 이전 기준과 승격 기록 |
| `GET .../production-configurations/evaluations` | 현재 구성의 공식 평가 기록 |
| `GET .../production-configurations/preflight/{test_run_id}` | 승격 자격·구성 비교·스키마 차이·평가 비교 가능 여부 |
| `POST .../production-configurations/promote` | 공식 평가된 고정 구성을 원자적으로 승격 |
| `GET /api/v1/admin/runtime/status` | 1h/6h/24h/7d 요청·실패·큐·시간·실제 heartbeat |
| `GET /api/v1/admin/runtime/deployment` | 관측 가능한 배포 정보만 읽기 전용 조회 |
| `GET /api/v1/admin/activity` | 종류별 변경 이력·작업자·변경 전후·페이지 이동 |

새 관리 API는 admin 세션이 필요하고 쓰기는 기존 Origin 보호를 적용한다. 서비스 키는 이 관리 권한을 얻지 않는다. GET의 초기 기준 기록 외에 설정을 바꾸는 부수 효과는 없다.

승격 POST는 `candidate_test_run_id`, `expected_production_configuration_hash`, `acknowledge_schema_change`만 받는다. Profile/Prompt/Schema ID를 별도로 받아 후보를 재조립하지 않는다. 현재 hash 불일치는 409 `production_configuration_changed`; 스키마 동의 누락은 409 `schema_change_ack_required`; 미충족 후보는 409 `candidate_not_eligible_for_promotion`이다.

Production Primary, Verifier, Editor, 활성 지침, 활성 스키마, 스키마 적용 이력, 승격 이력, 변경/접근 감사가 한 DB 트랜잭션으로 반영된다. Test 기본값과 Concurrency는 바꾸지 않는다. 이전에 접수한 실행의 암호화 snapshot도 바꾸지 않는다. 동일 후보·동일 평가의 중복 확인은 현재 hash가 일치할 때 같은 승격 기록을 반환한다.

기존 모델 `/promote`, 지침/스키마 `/activate`, Production 프로필 비활성화, Agent 설정의 Production 변경은 409 `production_promotion_required`로 거절한다. Test 설정만 전달하는 요청은 허용한다. 기존 Production API 입력 검증·동적 OpenAPI·정의서는 승격된 스키마를 따른다.

Runtime의 Configuration ID는 최신 성공 ProductionPromotion.id이며, 최초 승격 전에는 null이다. 이전 baseline을 승격 ID로 가장하지 않는다. worker loop는 15초 간격으로 heartbeat를 남기고 45초를 넘으면 지연으로 표시한다. 모델 자체 readiness probe는 없으므로 미확인이다. 지연시간은 접수→완료/실패, 재선점은 queue claim 기준이며 LLM 내부 교정 횟수와 다르다. 대기/처리 중은 기간 밖의 현재 큐도 포함한다. API 키 필터는 요청 통계에만 적용하고 공식 평가를 키별 실시간 정확도로 표시하지 않는다.

ChangeEvent는 허용한 메타데이터만 저장한다. 키 원문/해시, 암호문, payload/Cookie, 프롬프트 본문은 포함하지 않는다. AccessAudit는 기존 보안 열람 기록으로 유지한다. 이미지·commit·배포 시각처럼 수집하지 않는 값은 null로 반환한다.

## UI

- Sidebar: Overview / 구성 / 평가 / 승격 / 운영 / 연동. 하위 탐색은 본문 상단 Workspace tabs.
- 공통 Section·표·필터·Pagination·Dialog·상태·버튼·테마를 재사용한다. 일반 저장/추가는 neutral primary, 운영 승격은 brand, 삭제는 danger.
- Overview만 요약 대시보드. 모델·키·연결 대상·데이터셋은 검색/필터+표, 지침/스키마는 버전 rail+상세, Ground Truth는 목록+편집 workbench, Promote는 비교+승격 조건이다.
- Runtime: 관측 상태·요청 추이·실패·현재 구성·기존 동시 처리와 진단. Activity: 이력 목록+변경 전후. Production API의 목차/검색/복사/Swagger/OpenAPI/PDF 유지.
- Inference Detail: 판정 결과 / Agent 실행 이력 / 입력 / 결과 JSON / 보고서. AgentHistory 구조·입력/출력·원문 접근 감사·실패 재실행·답안/데이터셋 작업 유지.
- 기본 SK, Light의 mint, Dark의 green. 테마는 한 popover. KR/EN 전환은 화면·선택·폼 상태를 유지한다.
- URL에는 경로/허용된 ID·탭만 저장한다. 검색어·폼·원문·비밀 값은 저장하지 않는다.
- Ground Truth 편집 중 다른 문항/버전/새로고침/삭제 동작은 비활성화하고, 편집을 닫을 때 미저장 내용 폐기를 확인한다.

KR/EN은 공통 탐색·상세 탭과 신규 운영 수명주기 화면에 적용했다. 기존 세부 폼·지표 도움말·업무 설명은 한국어가 남아 있으며 전체 영문 번역을 완료한 것으로 표시하지 않는다. 원문·사용자 입력·저장 보고서를 자동 번역하지 않는다.

## 검증

모든 신규 fixture는 가상 데이터다. 실제 DB·내부 WAF 로그·LLM·유료 API는 사용하지 않았다. 새 production dependency나 Chromium은 추가하지 않았다. 개발 브라우저 검증은 기존 로컬 Playwright 캐시만 사용했다.

- 전체 Backend 회귀 테스트: **2,282 passed, 3 skipped** (18분 56초). 이후 추가한 경계 조건은 아래 집중 테스트로 확인했다. 테스트가 건너뛴 사유는 PDF 실출력 플래그 2건과 별도 `/preview` 샘플 1건이다.
- 승격·역할·지침·스키마 집중 검증 271건, 추가 승격/키별 집계/변경 이력/마이그레이션 검증 120건 통과. 서로 중복되므로 합산하지 않는다.
- 신규 설치 migration 후 실제 앱 시작·재시작과 baseline 보존: 2건 통과.
- PDF 실출력 플래그를 켠 별도 검증: **38 passed, 1 skipped**. 한글·긴 본문·테마별 다중 페이지 PDF를 실제 생성했다. 남은 skip은 선택형 `/preview` 샘플을 마운트하지 않은 1건이다.
- frontend `node --test src/*.test.mjs`: **47개 파일 통과**. 표 전환 후에도 전체 재실행했다.
- Vite production build 통과. 단일 JS bundle 약 689 kB(압축 약 229 kB)의 500 kB 경고는 남아 있다. Backend 테스트에서는 기존 AnyIO BlockingPortal deprecation 경고가 1개 발생한다.
- `canonical-v5-smoke.mjs`: 6개 Workspace, 주요 14개 화면, 분석 상세 5개 탭, SK/Light/Dark, 1440×900/1600×1000, 언어 전환 후 선택/폼 보존, 승격 스키마 동의·중복 클릭 차단 확인.
- `inference-detail-smoke.mjs`: 뒤로 가기·5개 탭·활성화 시에만 원문/Agent 조회·발췌 위치 이동·늦은 응답 격리·보고서 옵션·참고 답안·재실행/데이터셋 창·390px overflow 확인.
- 마이그레이션: 임시 SQLite에서 신규/이전 head upgrade, 기존 모든 컬럼 값과 외래키 확인, baseline 중복 방지, 이력 변경/삭제 방지, 위험 downgrade 거절.
- 승격: 현재 구성 충돌·프로필 변경/비활성화·자격 증명 손상·egress 해제·지침 정책 변경·미완료 평가·답안 범위 변경 거절, 중간 실패 전체 rollback, 두 요청 동시 승격에서 한 요청만 성공, 과거 실행 고정값 보존.

### 주요 화면 캡처

캡처는 실제 React를 가상 API에 연결한 화면이며 운영 평가 수치가 아니다. 재생성: `node frontend/scripts/canonical-v5-smoke.mjs`. 테스트 결과 폴더는 Git에서 제외한다.

- [Overview · SK · 1600](../../test-results/canonical-v5/overview-sk-1600.png)
- [Ground Truth · SK · 1600](../../test-results/canonical-v5/ground-truth-sk-1600.png)
- [Promote · SK · 전체 승격 조건](../../test-results/canonical-v5/promote-sk-full.png)
- [Promote · EN / Dark · 1600](../../test-results/canonical-v5/promote-en-dark-1600.png)
- [Runtime](../../test-results/canonical-v5/runtime-sk-1600.png)
- [Agent 실행 이력](../../test-results/canonical-v5/detail-agent-trace-sk-1600.png)
- [입력 스키마](../../test-results/canonical-v5/input-schema-sk-1600.png)

## 미검증 및 적용 시 주의

- 실제 vLLM/Gemma/Qwen/OpenAI의 판정 품질·GPU 처리량·장시간 동시 실행은 이번 검증에 포함하지 않았다.
- 실제 운영 DB 이전·운영 worker 재시작·reverse proxy/로그인·배포 후 브라우저 연동은 수행하지 않았다. 오프라인 API 테스트와 가상 API 브라우저 검증은 운영 E2E 검증을 대신하지 않는다.
- 배포 시 쓰기 접수/API·worker를 정지한 일관된 DB와 암호화 키의 복구 가능한 백업을 먼저 준비하고, 복사 DB에서 `alembic upgrade head`를 리허설한다. API/worker/frontend를 같은 코드로 교체한다. 오래된 API를 동시에 실행하면 이전 직접 변경 경로가 남으므로 혼합 운영하지 않는다.
- schema migration은 설정을 승격하지 않는다. 새 API 시작 후 기존 Primary·Verifier·Editor·지침·스키마가 유지되는지, baseline이 하나인지 확인한다.
- 이력 있는 DB에 강제 downgrade/reset을 하지 않는다. 문제가 생기면 쓰기를 멈춘 뒤 백업 DB와 당시 코드/암호화 키를 함께 복구해야 한다. 이 문서는 배포 수행 기록이 아니다.
- 사용자 선택·실제 공식 평가 없이 운영 구성을 새 후보로 변경하지 않았다. GitHub 게시/태그/운영 배포는 별도 승인 범위다.
