# SQLite 조회 최적화

기준 commit: `b987f321d8d8ae5535f7a281a967aaa1de70f673`.
요청 문서: `ASTRA_SQLITE_PERFORMANCE_OPTIMIZATION.md`.
SQLite/WAL을 유지한다. 운영 DB·실제 로그·LLM에는 접근하지 않았으며 재배포·Git 게시도 하지 않았다.

## 구현

- 분석 목록: 화면에 필요한 컬럼만 ORM에 적재하고 암호화 원문·스냅샷·결과 JSON·긴 오류는 제외한다. 최신 리뷰 상태는 단일 scalar 조회로 가져오며 리뷰 전체 이력은 상세에서 유지한다. 페이지의 평가 metadata를 이미 읽은 결과에서 재사용하고, 상세 라벨의 retry ancestry는 대상 분석에 한정한다.
- Ground Truth: 필터·검색·정렬·페이지 범위와 상태/변경/답안 출처 집계를 SQL로 처리한다. 삭제된 공식 문항도 기존처럼 목록에 포함하되 현재 초안의 분모에는 넣지 않는다. 검색의 Unicode casefold, 태그 표현, `%`·`_`의 문자 의미를 유지한다.
- GT 카탈로그·홈: 데이터셋마다 전체 `working.document()`를 만들지 않는다. 암호문을 제외한 집계 결과와 버전 metadata만 묶어서 조회한다. 개별 문항 조회도 해당 문항의 암호문만 읽는다.
- 홈의 운영 반영 안내: 최근 공식 평가의 완료/실패·공식 버전·구성 metadata만 확인하는 이동 안내다. **운영 반영 가능 여부를 승인하는 기능이 아니다.** Production Review와 Apply 트랜잭션의 full preflight는 변경하지 않았다. 최신 평가가 실패하면 과거 성공 평가로 안내를 대신하지 않는다.
- Test 상세: `stable_case_id`를 JOIN으로 읽어 항목별 `db.get()`을 제거한다. 처리 상태와 완료 시각을 함께 집계하고, 난이도/유형 목록·미분류 개수도 한 그룹 조회를 재사용한다. 재실행 계보, 최초 답안, 최신 답안, 저장 평가의 의미는 유지한다.
- Test polling: `pending`/`processing` 또는 `official_evaluation_pending`일 때만 5초 polling한다. 완료·실패 뒤에는 중지하며 수동 새로고침, 조건 변경, 답안 저장, 재실행 뒤에는 새로 조회한다.
- 홈 polling: 운영 설정·GT/평가 요약·키 목록의 반복 timer를 없앴다. 실행 상태는 활성 시 5초/대기 시 30초, 변경 이력은 60초다. 설정 변경, GT 저장·발행, 공식 평가 완료, 화면 재진입/복귀 또는 수동 새로고침에서 요약을 갱신한다. 백그라운드 완료는 변경 이력의 가벼운 revision 조회로 감지하므로 최대 약 60초 지연될 수 있다.

### 이전 데이터셋

Working State가 없는 이전 데이터에는 저장된 검증 결과가 없다. 답안 유무만 보고 입력이 유효하다고 추정하지 않는다.

최초 조회 시 200건 단위로 기존 검증을 실행하고, 불변 버전 ID별로 상태·문제 코드만 메모리에 보관한다. 원문·Cookie·comment·암호문은 cache에 남기지 않는다. crypto 인스턴스별 최대 10,000항목/64버전으로 제한하며 프로세스 재시작·eviction 뒤에는 다시 검증한다. 이후 목록 필터와 페이지네이션은 SQL에서 처리한다. GET으로 Working State를 생성하거나 과거 항목을 승인·공식 평가로 전환하지 않는다. 출처의 원본 삭제 표시 등 변경될 수 있는 metadata는 DB에서 읽는다.

**제한:** 이전 대형 데이터셋의 cold read는 여전히 전체 검증 비용이 든다. 이는 답안·검증 의미를 바꾸지 않고 migration 없는 조회 최적화를 적용하기 위한 절충이다.

## DB / API

- Alembic head: `0025_analysis_read_index` (`0024_r5_contract` 다음).
- 추가 index 하나: `analyses(created_at, id)` / `ix_analyses_created_id`.
- 테이블·컬럼·원문·답안·저장 평가·공식 버전은 변경하지 않는다. downgrade도 index만 제거한다.
- `EXPLAIN QUERY PLAN`: 최근 분석 조회의 `SCAN analyses` + `USE TEMP B-TREE FOR ORDER BY`가 `SEARCH analyses USING INDEX ix_analyses_created_id (created_at>?)`로 바뀐다.
- 목록/상세 API의 기존 응답 필드와 검색·평가 계약은 유지한다.
- 기존 관리자 `GET /api/v1/admin/activity`에 선택 query `include_home_revision=true`를 추가했다. 사용할 때만 `home_revision`이 응답에 추가된다. 변경 감지용 hash이며 권한이나 승인 token이 아니다. WAF 내용·정답 값·키를 포함하지 않는다.
- 운영 패키지 추가 없음. SQLite 표준 JSON/window 함수와 metadata용 Python scalar 함수를 사용한다. `MATERIALIZED` CTE를 사용하는 SQLite 3.35 이상이 필요하며, 현 Docker 기반인 Python 3.12/Bookworm 범위에서 지원된다.

적용 시 기존 배포 절차의 `alembic upgrade head`가 index를 만든다. 운영 DB 규모에서 index 생성 시간·디스크 사용·쓰기 대기는 검증하지 않았으므로 사전 백업과 작업 시간 확보가 필요하다. 이번 작업에서 실행 중인 DB에는 적용하지 않았다.

## 측정 방법

Python 3.12.12 / SQLite 3.50.4, 동일 로컬 환경·동일 SQLite 파일, 실제 FastAPI TestClient HTTP 응답으로 비교한다. 각 측정은 3회 중앙값이다. 분석 1,000/10,000건, GT 및 Test 각각 150/1,000/5,000건을 가상 데이터로 구성한다. 모델 호출·실제 사내 로그는 사용하지 않는다. 프로세스·디스크 cache와 다른 로컬 작업에 따른 시간 편차가 있으므로 운영 SLO나 부하 테스트 결과로 해석하지 않는다.

측정 항목은 응답 시간, SQL 횟수/누적/최대 시간, 응답 크기, 목록 개수, ORM 객체 수, 복호화 횟수/시간이다. SQL 시간은 cursor 실행 시간이며 row fetch·JSON 변환·HTTP 직렬화는 전체 응답 시간에 포함된다. 실행 계획에는 SQL template hash와 plan만 남기며 SQL 바인딩·검색어·원문·Cookie·API Key는 출력하지 않는다. 변경된 운영 DB에 자동 계측 middleware를 넣는 방식이 아니라 독립된 오프라인 재현 도구다.

GT snapshot과 working 항목을 모두 둔 fixture이며, 테스트는 완료 상태의 원본 실행 중심이다. 복잡한 retry·보류·오류·오염된 답안 등의 의미 보존은 별도 회귀 테스트에서 검사한다.

### 재현

backend 디렉터리에서 실행한다. 기존 DB를 덮어쓰지 않으며 `.env`의 서비스 DB 주소나 키를 쓰지 않는다. 출력은 지정한 가상 데이터 디렉터리에만 저장한다.

```bash
.venv/bin/python scripts/benchmark_sqlite_reads.py \
  --directory /tmp/waf-read-benchmark --seed --analyses 10000 --output before.json

# 코드 변경 뒤 같은 파일에서 다시 측정. 운영 DB가 아닌 위 fixture에만 index 추가.
.venv/bin/python scripts/benchmark_sqlite_reads.py \
  --directory /tmp/waf-read-benchmark --apply-read-index --output after.json

# 모든 삽입과 DDL을 rollback하는 가상 DB 전용 쓰기/실행 계획 비교
.venv/bin/python scripts/benchmark_sqlite_reads.py \
  --directory /tmp/waf-read-benchmark --index-probe-only --output index-probe.json
```

`before`는 반드시 변경 전 backend에서 측정한다. 현재 코드에서 두 명령을 연속 실행한 결과를 변경 전후 결과로 간주하면 안 된다. 과거 기준 commit을 통째로 reset할 필요는 없다.

## 성능 결과

분석 10,000건이 있는 동일 DB에서의 비교다. GT는 각 데이터셋의 첫 50건이며 카탈로그는 150/1,000/5,000건 데이터셋 3개다.

| Endpoint / 화면 | Before SQL | After SQL | Before ms | After ms |
|---|---:|---:|---:|---:|
| Analysis list 25 | 8 | 6 | 2832.4 | 1808.7 |
| Analysis list 50 | 8 | 6 | 2820.3 | 1618.2 |
| Analysis list 100 | 8 | 6 | 2679.4 | 2172.3 |
| Analysis detail | 6 | 6 | 260.7 | 75.3 |
| GT 150 | 8 | 6 | 69.6 | 65.5 |
| Test 150 page 25 | 39 | 10 | 534.4 | 341.8 |
| Test 150 page 50 | 64 | 10 | 457.2 | 355.1 |
| Test 150 page 100 | 114 | 10 | 493.6 | 312.0 |
| GT 1000 | 8 | 6 | 305.8 | 116.6 |
| Test 1000 page 25 | 39 | 10 | 809.9 | 557.4 |
| Test 1000 page 50 | 64 | 10 | 751.7 | 695.4 |
| Test 1000 page 100 | 114 | 10 | 835.0 | 572.0 |
| GT 5000 | 8 | 6 | 1751.7 | 364.5 |
| Test 5000 page 25 | 39 | 10 | 2305.0 | 1943.3 |
| Test 5000 page 50 | 64 | 10 | 2129.4 | 1938.8 |
| Test 5000 page 100 | 114 | 10 | 2446.9 | 1927.0 |
| Dataset catalog | 27 | 8 | 2660.0 | 372.8 |
| Overview | 55 | 31 | 2393.6 | 630.1 |
| Runtime 24h | 19 | 19 | 970.9 | 96.6 |
| Runtime 7d | 19 | 19 | 1067.7 | 395.8 |

분석 1,000건의 별도 작은 DB에서도 같은 방식으로 확인했다.

| Endpoint / 화면 | Before SQL | After SQL | Before ms | After ms |
|---|---:|---:|---:|---:|
| Analysis list 50 | 8 | 6 | 299.8 | 242.7 |
| Analysis detail | 6 | 6 | 51.7 | 44.6 |
| GT 150 | 8 | 6 | 59.5 | 74.5 |
| Test 150 page 50 | 64 | 10 | 282.6 | 155.4 |
| Dataset catalog | 11 | 8 | 76.6 | 41.3 |
| Overview | 43 | 31 | 127.5 | 96.5 |
| Runtime 24h | 19 | 19 | 123.4 | 49.1 |
| Runtime 7d | 19 | 19 | 158.1 | 77.3 |

- 5,000건 GT의 첫 50건: 기존 초안/원본 객체 10,000개 → 항목 ORM 객체 0개, SQL 결과로 50개 metadata만 전달. 목록 복호화는 0회다.
- Test 상세는 페이지 25/50/100건 모두 SQL 10회다. 이전은 각각 39/64/114회였다.
- 분석 목록은 리뷰 객체 0개이며 원문/스냅샷/결과 JSON을 Python 객체로 적재하지 않는다. SQL 내부의 평가 출처 확인은 유지한다.
- 홈 복호화 12회 → 6회는 같은 Production 구성을 다시 계산하던 것을 재사용한 결과다. 원문 복호화가 아니며 운영 지침/스키마 snapshot 확인은 유지한다.
- 분석/GT/Test/카탈로그 응답 크기는 이번 fixture에서 동일하다. Runtime의 시각 및 동일 생성 시각 항목의 순서에 따른 응답 크기 차이는 있을 수 있다.
- 인덱스 쓰기 probe: rollback 전용 1,000행 삽입 중앙값은 미적용 121.8ms / 적용 107.3ms였다. 작은 fixture와 cache 변동이 있으므로 쓰기가 빨라졌다는 의미가 아니며, 운영 동시 쓰기 비용을 보장하지 않는다.
- 작은 DB의 GT 150건은 59.5 → 74.5ms로 오히려 증가했다. SQL 구성의 고정 비용이 있어 모든 규모에서 지연 감소를 보장하지 않는다.
- 5,000건 Test 상세는 약 1.94초, 분석 목록은 약 1.62초가 남는다. 전체 평가 집계까지 상수 시간으로 바뀐 것은 아니다.

전체 수치(누적/최대 SQL 시간, 응답 크기, 복호화, 객체 수, index query plan)는 [측정 JSON](performance/sqlite-reads-2026-09-29.json)에 기록했다.

## 의미 보존 / 검증

최종 backend 전체를 파일별 네 개의 독립 pytest 프로세스로 나누어 실행: **2,329 passed / 3 skipped / 0 failed**. 각 분할 결과는 641 / 570 / 625 / 493 passed다. 최초 전체 실행에서 없어진 조회 함수에 연결된 WAL 테스트 hook 한 건이 실패해 새 조회 경로로 바꾼 뒤, 최종 전체를 다시 검증했다.

생략 3건은 기존 PDF release renderer 환경 2건과 준비된 PDF layout fixture 1건이다. 프런트엔드 전체 **505 passed**, production build 통과. 기존 500kB 초과 bundle 경고와 AnyIO deprecation 경고는 남아 있다. 신규/upgrade/index rollback migration 검증과 추가 read-path 회귀도 통과했다.

실행한 브라우저 스크립트는 `analysis-decision-smoke.mjs`, `r5-final-smoke.mjs`, `sqlite-polling-smoke.mjs`다. 가상 API와 실제 React를 사용했으며 실제 PDF renderer/LLM/운영 API를 호출하는 배포 검증은 아니다.

화면 자료: [데스크톱 홈](../test-results/sqlite-polling/home-desktop.png), [모바일 홈](../test-results/sqlite-polling/home-mobile.png), [판정 UI v2](../test-results/analysis-decision-v2/index.html). `test-results`는 로컬 생성 자료이며 Git 추적 대상이 아니다.

- GT 추가·변경·삭제·복원·제외·발행과 revision 충돌, 정확한 공식 membership, 검색·집계 비교를 검사한다.
- 리뷰 최신 상태, 참고 답안·retry 상속·오염 제외·저장 평가, WAL 읽기 일관성을 검사한다.
- 홈 안내가 떠도 변경/비활성 프로필은 실제 Review/Apply에서 차단됨을 검사한다.
- 원문/Agent 관리자 권한·AccessAudit, 암호화 저장 정책은 유지한다.
- 신규 SQLite 설치와 이전 head에서의 upgrade 및 index downgrade를 가상 DB로 검사한다.
- 브라우저: Decision UI v2, 네 탭, 원문/Agent lazy 조회, JSON·보고서, R5 흐름과 뒤로 가기/포커스, SK/Light/Dark와 KR/EN, 홈 데스크톱·모바일, 실제 요청 횟수 기반 polling 검증을 수행한다.

## 남은 범위

- 실제 운영 DB 복사본, 100,000건 이상, 장시간 동시 ingest, 실제 Production 배포는 미검증이다.
- 전체 평가 집계는 여전히 데이터 크기에 따른 계산이 필요하다. 조건부 지표의 의미를 유지하려고 평가 projection 테이블은 추가하지 않았다.
- Runtime은 기존 정확한 시간대 집계와 percentile 의미를 유지한다. hourly rollup은 추가하지 않았다.
- Agent step 상세 lazy API, FTS5, GT 영속 summary 테이블은 이번 측정으로 추가 필요성이 확정되지 않아 도입하지 않았다.
- raw/Agent 평문과 과거 지침·입력 스키마는 여전히 기존 권한·암호화·감사 기준으로 제공한다. UI를 줄이거나 기능을 삭제한 최적화가 아니다.

## 2026-10-14 Confusion Matrix 셀 조회

### 원인과 변경

시작 commit은 `2e92bcc`이며, 작업 중인 통계 AND/OR 필터 변경도 보존했다. 운영에서 보고된 504의 실제 서버 로그·실행 계획에는 접근하지 않았다. 대신 셀 선택과 같은 관리자 HTTP 요청을 가상 DB로 재현해 다음 병목을 확인했다.

- 셀의 판정·비교 결과 조건이 붙으면 SQLite가 재실행 계보/window와 답안 비교를 여러 번 펼쳐 계산했다. 고정 답안 LEFT JOIN에서는 분석마다 답안 집합을 반복 탐색하는 실행 계획도 확인했다.
- 문항 수와 페이지 조회가 같은 복잡한 조건을 각각 실행했다. 작은 목록을 표시해도 전체 비교 비용이 중복되었다.

선택한 테스트·난이도·유형의 현재 분석 ID, 답안 출처, 비교 결과 등 작은 metadata만 `MATERIALIZED` CTE에 둔다. 고정 답안의 LEFT JOIN은 **답안 있는 행 INNER JOIN + 답안 없는 행의 합집합**으로 구성해 나쁜 반복 탐색을 피한다. `UNION ALL`을 사용하며 답안 중복·NULL·평가 제외 순서는 기존 평가 함수를 그대로 따른다. 건수와 페이지는 한 SQL 문에서 같은 경량 후보 집합을 재사용하고, `LIMIT/OFFSET` 이후 표시할 Analysis/TestRunItem만 적재한다. 전체 결과 JSON·입력 암호문을 중간 테이블이나 목록 ORM에 복사하지 않는다. 비교 계산은 여전히 DB 안에서 필요한 결과 provenance를 확인한다.

공식 답안·최초/최신 참고 판정·저장된 평가·재실행 계보 및 snapshot 시점, 보류·중지·오염·stub 제외, 지표 공식은 유지한다. 요청 간 cache를 추가하지 않아 답안 변경 뒤 오래된 결과를 제공하지 않는다. **DB migration·새 운영 의존성·API 경로/응답 형식 변경은 없다.** 기존 SQLite 3.35 이상 요구사항을 사용하며 `0026_test_run_stop` head는 그대로다. 프록시 timeout을 늘리는 방식은 적용하지 않았다.

셀 목록에 동일 분석을 가리키는 **중복 접수 행**이 같이 표시되던 기존 오류도 수정했다. 참고/AI 판정/비교 결과가 모두 지정된 셀 조회는 평가 대상인 accepted 문항만 표시한다. 일반 목록과 전체 Excel의 중복·접수 거부 기록은 유지하며 데이터를 삭제하지 않는다. 프런트엔드에서 셀을 선택하면 문항 목록에 복사된 이전 통계 집계/AND·OR 조건은 해제하되, 통계 화면의 적용 설정과 평가 범위/답안 기준은 보존한다.

### 재현과 성능

Python 3.12.12 / SQLite 3.50.4의 오프라인 FastAPI TestClient로 같은 가상 DB·같은 25건 페이지 요청을 비교했다. 분석 결과 JSON에 가상 padding 16KiB를 포함하고, 세 판정의 아홉 조합을 구성했다. 변경 전은 1회, 최종 변경 후는 2회 중앙값이다. 로컬 cache와 함께 실행한 회귀 테스트의 부하가 포함되므로 운영 SLA나 동시 부하 결과가 아니다.

| 1,000건 공식 답안 / 999건 최신 참고 판정 | 변경 전 HTTP ms | 변경 후 HTTP ms |
|---|---:|---:|
| 공식 답안 · 정탐 일치 (TP) | 15,733.6 | 1,058.1 |
| 공식 답안 · 과탐 (FP) | 12,818.6 | 1,123.9 |
| 공식 답안 · 미탐 (FN) | 14,044.7 | 845.6 |
| 공식 답안 · 오탐 일치 (TN) | 12,261.8 | 1,145.7 |
| 최신 참고 판정 · 정탐 일치 (TP) | 14,025.8 | 1,193.4 |
| 최신 참고 판정 · 과탐 (FP) | 14,266.4 | 859.6 |
| 최신 참고 판정 · 미탐 (FN) | 10,446.7 | 679.9 |
| 최신 참고 판정 · 오탐 일치 (TN) | 12,267.8 | 726.6 |

- 해당 8개 요청은 **전체 응답 JSON hash가 변경 전후 동일**하다. 중복 접수 수정은 별도의 중복 fixture로 검사한다.
- 공식 답안 SQL 11회 → 10회, 최신 참고 판정 10회 → 9회다. 횟수보다 반복 계산과 JOIN 실행 계획을 줄인 효과가 크다.
- 5,000건 공식 답안에서는 최종 HTTP 약 **4.3~5.7초**, 4,999건 최신 참고 판정에서는 **2.7~3.4초**다. 각 페이지의 Analysis/TestRunItem 객체는 25개씩이며 원문 복호화는 0회다.
- 원래 5,000건 조회는 측정 중 완료되지 않아 중단했으며 정확한 변경 전 수치는 없다. 이 크기의 응답 hash는 중간 최적화와 최종 구현 사이에서 동일함을 확인한 것이며 원래 구현과의 전체 비교로 주장하지 않는다.
- 전체 평가 집계는 아직 문항 수에 비례하는 작업이 필요하다. 모든 조회가 상수 시간으로 바뀐 것은 아니다.

숫자와 조건, 응답 일치 검사는 [셀 조회 측정 JSON](performance/matrix-drilldown-2026-10-14.json)에 남겼다. 원문·SQL 바인딩·서비스 키는 포함하지 않는다. 재현 도구는 `backend/scripts/benchmark_test_matrix.py`다. 아래 명령은 새 가상 디렉터리에서만 실행하며 서비스 DB와 `.env`를 사용하지 않는다. 변경 전 수치가 필요하면 반드시 **변경 전 backend 코드**에서 먼저 측정해야 한다.

```bash
# backend 디렉터리, 사용 가능한 프로젝트 Python 환경에서 실행
python scripts/benchmark_test_matrix.py \
  --directory /tmp/waf-matrix-new-fixture --seed --rows 1000 \
  --repetitions 1 --output before.json

# 같은 fixture를 코드 변경 후 다시 측정
python scripts/benchmark_test_matrix.py \
  --directory /tmp/waf-matrix-new-fixture \
  --repetitions 2 --output after.json
```

### 검증과 남은 항목

최종 backend의 서로 겹치지 않는 두 테스트 묶음은 **342 passed + 69 passed = 411 passed**다. 테스트 목록·중지·재실행·비교·1차 판정·공식 평가·후보 구성·평가 데이터셋·답안·평가 계산·브라우저 보안·SQLite 조회와 통계/AND·OR 필터를 포함한다. 신규 셀 회귀는 아홉 셀의 문항/건수, 중복 접수, stub/미라벨/실패/대기 제외, 정렬·빈 페이지·범위 밖 페이지, 경량 중간 집합과 한 문장 count/page, 목록 복호화 금지를 검사한다. backend 전체 suite를 실행했다는 의미는 아니다.

프런트엔드 **565 passed**, production build 통과(기존 500kB 초과 bundle 경고 유지). 실제 React와 가상 API의 브라우저 검증은 **29 screenshots**, 외부 요청 0회, page error/unknown API 0건이다. SK/Light/Dark·KR/EN·모바일과 keyboard/Back/Forward·AND/OR 집계·Excel 요청·셀 선택 시 이전 조건 해제·선택 셀에 맞는 25건/마지막 2건 페이지를 확인했다. [셀 이동 화면](../test-results/test-statistics/matrix-drilldown.png)은 Git에 포함하지 않는 로컬 자료다. SQL 성능은 별도 실제 HTTP backend 측정이며 브라우저 fixture가 실제 DB를 조회한 것은 아니다.

DB schema와 migration 파일은 변경하지 않았으며 이번 최적화를 위한 fresh/upgrade migration은 필요하지 않다. 실제 운영 DB·동시 접수/분석 부하·nginx 경유 504 재현·100,000건 이상·LLM 호출은 미검증이다. Docker 재배포와 Git 게시는 하지 않았다. 배포 후 동일 테스트에서 아홉 셀과 페이지 이동, 답안 수정/재실행 뒤 반영, 다른 요청을 함께 처리할 때의 응답 시간을 확인해야 한다.
