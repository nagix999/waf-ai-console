# Canonical R5 FINAL COMPLETE — 구현 및 검증 기록

기준일: 2026-09-22. 이 문서는 코드 작업 결과이며 배포 완료 보고서가 아닙니다.

## 기준과 보존

- Source of Truth: `WAF_AI_Console_Astra_Handoff_CANONICAL_V5_REVIEWED_R5_FINAL_COMPLETE/`의 9개 문서. 사용자 요청 순서대로 적용하며 사용자 표시 문구는 `UI_TERMINOLOGY.md`를 우선합니다.
- 시작점: 원격 `main`, `ee4797b53a775312bdb112f7aadb3ec01dfa5135`.
- 작업 브랜치: `feature/canonical-r5-final`.
- 안전 지점: `safety/pre-r5-final-20260922`, `.local-deploy/pre-r5-final-20260922.bundle`.
- 이 환경의 Git 메타데이터는 `.local-deploy/canonical-v5-reviewed.git`에 있습니다. 일반적인 다른 clone의 `.git`을 변경할 필요는 없습니다.
- 저장소 reset, 기존 데이터 삭제, 운영 DB migration, 재배포, Git 게시, 유료 또는 내부 LLM 호출을 하지 않았습니다.
- 기존 암호화·관리자 세션/CSRF·접근 감사·내부 egress·OpenAI 전송 동의·프로필 검증·작업자/동시 처리·고정 지침·retry/recovery·Agent Trace·Raw Event·Report/PDF·API 키 관리·Deployment read-only를 보존했습니다.
- 제거한 것은 이전 메뉴 배치, 운영 분석의 Test/All 전환, 테스트 상세의 중복 지표 상단과 별도 평가 팝업입니다. 상세 평가를 화면 안으로 옮겼으며 기능과 기존 기록을 삭제하지 않았습니다. 불필요한 후보/승격 문구를 공식 테스트/운영 반영으로 정리했습니다.

## 구현 내용

### 운영 보호와 테스트 설정

Production의 Primary / Verifier / Evidence Editor / Analysis Instructions / Input Schema 변경은 기존 원자적 적용 서비스 하나만 사용합니다. 공식 평가 기록, 전체 공식 버전 문항, 테스트 완료/실패, 구성 해시, 검증 프로필 fingerprint, 스키마 영향 확인, 운영 기준 변경을 적용 시 다시 검사합니다. 과거 `legacy_unknown` 테스트는 새 공식 테스트 없이 운영 반영 근거가 되지 않습니다.

기본 테스트 설정을 평가 > 테스트에 두었습니다. 모델 역할·지침·스키마를 완전한 객체로 저장하고 새 테스트의 보이는 초기값으로만 사용합니다. 운영과 기존 실행, 기존 Test API 역할 배정을 변경하지 않습니다. 설정 > Agent 역할은 현재 운영 구성을 읽기 전용으로 보여줍니다.

새 테스트는 목적 → 테스트 설정 → 테스트 데이터의 세 구역입니다. 공식 테스트는 공식 버전 전체로 평가하고 개발 테스트는 단건·파일·데이터셋으로 실행합니다. 다시 테스트는 원래 구성과 정답 버전을 유지합니다. 누락·무효한 구성을 현재 기본값이나 최신 버전으로 자동 대체하지 않습니다.

### 정답 데이터

테스트 전체/선택 문항/개별 사례에서 대상 선택 → 미리보기 → 확정으로 편집 중 데이터에 가져옵니다. 미리보기의 새 항목·중복·답안 충돌·답안 없음·사용 불가 목록을 확인할 수 있습니다. 기존 답안을 충돌 답안으로 덮어쓰지 않습니다. 확정은 짧은 유효기간의 사용자별 토큰, 원본 상태 해시, 대상 working revision, idempotency key를 확인합니다.

답안은 해당 실행에 고정된 공식 정답 또는 참고 라벨에서만 가져옵니다. 심층 판정과 나중에 바뀐 라벨을 자동 정답으로 쓰지 않습니다. 답안이 없으면 확인 필요 상태로 가져오며, 자동 공식 버전 생성은 없습니다. 출처·원본 실행/문항·내부 전용 제한을 유지하고 공식 버전에는 답안 출처별 포함 건수를 기록합니다.

데이터셋 선택에 공식 버전/문항 수/변경 수/확인 필요 수를 표시합니다. 현재 운영 평가 기준을 별도로 표시하고 추가 취소·변경 되돌리기·삭제 복원과 일괄 변경 취소는 기존 revision 보호를 유지합니다.

### 화면과 탐색

- 메뉴: 홈 / 평가 / 설정 / 운영 / 연동. 입력 스키마는 설정에 배치했습니다.
- 홈: 미설정/기존 운영/운영 반영 상태를 구분하고 첫 운영의 5단계와 하나의 다음 행동을 표시합니다. API 키 발급과 실제 요청 관측은 별개입니다. 모든 데이터셋의 확인 필요 항목을 집계하며 지표는 같은 comparison key에서만 비교합니다.
- 평가 > 테스트: 구성 변경점과 비교 기준, 간단/확장 지표, 문항별 결과/평가 상세를 제공합니다. 2×2 행렬의 축을 정답 × 심층 판정으로 명시하고 보류 답안·분석 보류·실패·제외와 추가 지표를 따로 표시합니다. 실행 실패와 답안 불일치는 구분합니다.
- 운영 > 분석 이력: Production만 표시합니다. Test 결과를 운영 목록으로 혼합하지 않습니다.
- 전체 검색: 실제 분석 목적을 확인한 뒤 운영 상세 또는 테스트/문항으로 이동합니다. 이전 ungrouped Test도 평가 영역에서 보존합니다.
- 기존 hash/history를 유지했습니다. 검색어·폼·원문은 hash에 넣지 않습니다. Drawer → 크게 보기 → 브라우저 뒤로 가기로 테스트 문맥을 복원합니다.
- 1차 판정은 심층 판정과 두 줄로 비교하며 불일치/보류만 강조합니다. 목록에서는 열을 늘리는 대신 표시와 필터를 추가했습니다.

## DB migration

새 head: `0024_r5_contract` (이전 `0023_ground_truth_working`). 새 운영 패키지는 없습니다.

| 대상 | 추가 내용 | 기존 기록 |
|---|---|---|
| `analyses` | `initial_verdict`, `initial_probability`, `initial_model_version` | NULL 유지, 입력·모델 스냅샷은 변경하지 않음 |
| `test_runs` | `test_purpose` | `legacy_unknown`, 과거 테스트를 공식 테스트로 소급하지 않음 |
| `validation_dataset_working_items` | `reference_origin`, `reference_origin_ref_id`, `provenance_json` | 출처를 추측해서 채우지 않음 |
| `test_configuration_defaults` | 단일 기본값 객체와 optimistic revision | 기존 Production/Test 배정을 자동 복사하지 않음 |
| `ground_truth_import_previews` | 암호화 manifest, 사용자/만료/확정/idempotency 정보 | 기존 데이터에 영향 없음 |

fresh DB와 실제 R5 열을 제거한 `0023` 구조의 합성 기존 DB에서 업그레이드를 검증했습니다. 기존 열의 값, 암호화 문자열, 참조 무결성을 비교하고 head 재실행도 확인합니다. 운영 DB 사본에 대한 리허설은 이번에 하지 않았습니다.

R5 데이터가 기록되면 downgrade는 `r5_history_requires_pre_upgrade_backup`으로 중단합니다. 새 기록이 없는 경우만 schema downgrade가 가능합니다. 운영 롤백은 배포 직전 DB·키·구성을 함께 백업한 복구 절차를 사용해야 합니다. 변경 후 데이터가 있는 DB를 과거 코드로 즉시 실행하지 마세요.

## API 변경

모든 관리 요청은 기존 관리자 인증과 CSRF 정책을 따릅니다.

| Method / endpoint | 용도 |
|---|---|
| GET/PATCH `/api/v1/admin/test-configuration-defaults` | 완전한 기본 테스트 설정 조회/저장. 쓰기는 `expected_revision` 필수 |
| GET `/api/v1/admin/setup-status` | 3가지 운영 상태, 5단계 준비 상태, 키/실제 요청 구분 |
| GET `/api/v1/test-runs/{id}/clone-template` | 원래 설정, 원래/최신 공식 버전, 자원 유효성 |
| POST `/api/v1/test-runs/{id}/ground-truth-import/preview` | 신규/기존 대상 미리보기. 선택 문항 ID는 선택적이며 실행 소속 검증 |
| POST `/api/v1/test-runs/{id}/ground-truth-import/confirm` | `preview_token` + `idempotency_key`로 원자적 확정 |
| POST `/api/v1/validation-datasets/{id}/working/items/{case}/restore` | 기존 공식 버전으로 삭제 문항 복원 |
| POST `/api/v1/analyses` | Production-purpose 서비스 키에 한해 최상위 `initial_*` 접수 |
| GET `/api/v1/analyses` | `initial_comparison` 필터 및 목록/상세 `initial_assessment` |

`initial_verdict`는 true_positive/false_positive만 허용하며 유한한 0~1 `initial_probability`와 쌍으로 제출합니다. 선택적 모델 버전은 이 쌍 없이 제출할 수 없습니다. Test key/admin/Test/upload/정답 데이터/custom field 경로는 거부합니다. 이 메타데이터는 이벤트 fingerprint·extra fields·모델 입력과 분리됩니다. 재시도에는 보존되며 동일 이벤트의 None↔제공/값 변경은 409이고 조용히 덮어쓰지 않습니다. Production API 생성 문서에도 admission-only 필드로 반영했습니다.

추가 응답: Test Run의 `test_purpose`, 문항의 `ground_truth_source`, 데이터셋 요약/편집 중 데이터의 상태·출처, 공식 버전의 출처별 포함 수, 홈의 데이터셋별 확인 필요 수. 기존 필드와 API는 삭제하지 않았습니다.

## 문서와 현행 구조의 조정

1. R5 기능을 기존 FastAPI 서비스, SQLAlchemy 모델, React hash router, Dialog/DetailTabs/DataTable에 연결했습니다. 전체 UI/Agent를 새 framework로 바꾸지 않았습니다.
2. 과거 Test API의 암묵적 역할 배정은 호환성을 위해 유지합니다. 새 UI는 명시적 테스트 설정을 전송합니다. 새 기본 테스트 설정 저장이 과거 배정을 몰래 바꾸지는 않습니다.
3. 테스트 소속이 없는 과거 Test를 임의 실행으로 묶지 않고 `#evaluate/tests/items`에 둡니다. 공유 상세는 목적을 확인하고 올바른 영역으로 이동합니다.
4. 기존 개발 분석의 3×3/보류 지표 기능은 제거하지 않았습니다. R5 테스트 상세는 2×2 핵심 행렬과 별도 보류·제외/추가 지표로 제시합니다.
5. 기존 필드명과 보존된 역사적 API 내부의 candidate/promotion/approved 명칭은 호환성을 위해 유지합니다. 기본 탐색과 새 작업 흐름에서는 사용자 용어를 사용합니다.

## 검증 결과

- Backend 전체 회귀: **2,322 passed / 1 skipped**, 480.46초. 실제 PDF 렌더링을 켜서 실행했습니다. 건너뛴 1건은 별도 `/preview` 샘플 디렉터리가 필요한 선택적 PDF 레이아웃 묶음이며, 기본/긴 보고서 PDF 테스트는 통과했습니다. Starlette/anyio의 기존 deprecated alias 경고 1건이 있습니다.
- Frontend: 48개 테스트 파일, `--test-isolation=none` 기준 488개 테스트 통과.
- Vite production build 통과. 기존 단일 JS 번들의 500 kB 초과 경고는 남아 있습니다.
- 배포/Git 스크립트 단위 테스트 43개 통과. 실제 배포나 업로드는 하지 않았습니다.
- fresh DB 및 `0023 → 0024` upgrade, 재실행, 이전 열/값 보존, 외래키 검사, 새 기록이 있을 때 downgrade 차단 확인.
- ReportLab 실제 PDF 생성 테스트를 별도 opt-in으로 실행하여 통과. 운영 Chromium 의존성을 추가하지 않았습니다.
- `frontend/scripts/r5-final-smoke.mjs`: 합성 API를 주입한 실제 React 브라우저 검증. 모든 네트워크 차단, 실제 데이터/모델/운영 변경 없음.
- SK / Light / Dark × KR / EN: 홈, 정답 데이터, 테스트 평가 상세, 운영 반영 검토 스크린샷 생성/검토.
- 기본 설정 저장, 원래 정답 버전으로 복제, 가져오기 preview/confirm, Production-only 요청, Drawer/크게 보기/Back, 스키마 영향 확인과 중복 적용 방지 확인.
- Dialog Escape/원래 버튼으로 focus 복귀, 수동 활성화 탭의 화살표/Enter, selected+focus의 가독성, 언어 전환 시 입력값/선택 유지, reduced-motion 설정, 1280/1024 폭 overflow 확인.
- `git diff --check` 통과.

## IMPLEMENTATION_CHECKLIST self-review

원본 체크리스트는 수정하지 않았습니다. 다음은 구현·검증 범위와 남은 항목을 구분한 결과입니다.

| 영역 | 결과 / 근거 |
|---|---|
| IA, URL, Production/Test 구분 | 반영. 라우팅 단위 테스트 및 브라우저 Back/목적별 이동 |
| Login, 비밀값/전송 동의/원문 접근 감사 | 기존 구현 유지. 인증·egress·접근/암호화 회귀 테스트 |
| 기본 테스트 설정, 목적, 다시 테스트 | DB/API/UI 연결. optimistic conflict, 원본 누락 무대체, 버전 고정 테스트 |
| Test Detail와 평가 | 중복 상단 지표/팝업 제거, inline 평가, 행렬 분모와 보류 구분, 추가 지표 보존 |
| 정답 데이터 편집/발행/출처/가져오기 | 기존 working lifecycle 보존. 신규 preview/confirm, 만료/충돌/idempotency/심층 판정 비사용 테스트 |
| Production Review와 적용 | 5개 구성, 공식 테스트 목적, 실패 차단, 기준 stale/해시/검증/스키마 확인, 원자적 rollback 테스트 |
| 홈/첫 운영/업그레이드 상태 | 3가지 상태와 5단계, API 키≠트래픽, 동일 평가 기준/조건부 도표, 데이터셋별 확인 필요 |
| 설정/연동/Deployment | 기존 기능 보존. 운영 역할 read-only, 입력 스키마 이동, 기존 상세 진입 검증 |
| 1차 판정 | 경계 검증/예약 필드/중복 충돌/Agent 입력 분리/재시도 복사/비교 필터 |
| 테마/키보드/브라우저 | 위 주요 화면과 핵심 조작 검증. 정식 WCAG 전수 감사 및 모든 브라우저 검증은 아님 |
| 용어/KR·EN | R5 메뉴/새 핵심 흐름 정렬. 아래 기존 상세 문구의 전면 영문화는 잔여 항목 |

## 제한과 미검증 항목

- 기존 LLM 프로필·입력 스키마 편집·참고 라벨·Agent 단계·보고서 등의 일부 상세 문구/오류는 아직 한국어입니다. 새 R5 메뉴와 주요 흐름의 KR/EN 전환을 확인했지만 **전체 기존 화면의 영문화 완료로 간주하지 않습니다**. 모델 생성 분석 내용은 번역하지 않았습니다.
- 운영 데이터 규모에서의 대량 가져오기/모든 데이터셋 홈 집계 부하, 장시간 polling, 실환경 재배포, 실제 vLLM/OpenAI 호출은 미검증입니다. 화면 시험은 합성 API fixture이며 실제 배포 E2E와 구분해야 합니다.
- 실제 운영 DB 사본 migration, 운영 reverse proxy/HTTPS, 실제 키 일회 표시·삭제·복구 시나리오를 사용자 환경에서 실행하지 않았습니다. 기존 테스트와 구현을 보존했습니다.
- 브라우저는 설치된 로컬 Chromium으로 오프라인 QA만 했습니다. Firefox/Safari/스크린리더 전수 검증은 하지 않았고 운영 PDF는 계속 ReportLab입니다.
- 첫 운영 Header 진행률 shortcut은 필수 경로가 아닌 선택 기능으로 추가하지 않았습니다. 홈에 5단계와 다음 행동이 있습니다.

## 주요 화면

파일은 합성 검증 데이터로 만든 로컬 산출물이며 Git에는 자동 추가하지 않습니다. 스크립트를 다시 실행하면 생성할 수 있습니다.

- [일반 홈 · SK/KR](../../test-results/r5-final/home-KR-SK.png)
- [홈 · Dark/EN](../../test-results/r5-final/home-EN-Dark.png)
- [첫 운영](../../test-results/r5-final/home-unconfigured.png)
- [기존 운영 유지](../../test-results/r5-final/home-legacy_active.png)
- [정답 데이터 · Light/KR](../../test-results/r5-final/ground-truth-KR-Light.png)
- [테스트 평가 · Light/EN](../../test-results/r5-final/test-evaluation-EN-Light.png)
- [운영 반영 검토](../../test-results/r5-final/production-review-KR-SK.png)
- [운영 반영 차단 사유](../../test-results/r5-final/production-review-blocked.png)
- [기본 테스트 설정](../../test-results/r5-final/default-test-configuration.png)
- [새 공식 테스트](../../test-results/r5-final/new-official-test.png)
- [정답 가져오기 미리보기](../../test-results/r5-final/import-preview.png)
- [1차·심층 판정 비교](../../test-results/r5-final/initial-deep-comparison.png)
