# 참고 답안과 공통 테이블 개선

## 동작

- 웹의 테스트 목록·상세·문항 지표는 최신 참고 답안으로 표시한다. 개별/일괄 답안 저장 후 점수와 비교 결과를 새로 조회한다.
- 답안은 append-only로 보존한다. 접수 당시 답안과 저장한 평가 기록을 선택해 볼 수 있다. 이전 평가를 보고 답안을 수정하면 최신 답안 보기로 전환한다.
- ‘평가 기록 저장’은 테스트 전체의 현재 답안 선택을 고정한다. 접수 종료 및 분석 완료가 필요하며 이유를 화면에 표시한다. 모델을 호출하거나 AI 판정을 수정하지 않는다.
- 실행 간 비교는 기존의 같은 입력·같은 접수 당시 답안 기준을 유지하며 별도 화면에 명시한다. 최신 답안 지표와 동일한 비교 기준이라고 표시하지 않는다.
- 실패·미완료·stub·출처 미확인 등 평가 제외 조건 및 3×3 혼동 행렬 정책은 변경하지 않는다. 답안 제공 사실 자체가 독립적인 모델 정확도를 보장하지 않는다.

## 분석 접수

`POST /api/v1/analyses`의 기존 이벤트 JSON에 선택 항목을 추가할 수 있다.

```json
{"expected_verdict": "true_positive"}
```

위 코드는 추가할 항목만 보여준다. 실제 요청에는 기존 필수 이벤트 필드가 필요하다. Test·Production 키 모두 동일하며 `/uploads`의 각 JSON/CSV 행도 지원한다.

- 허용값: `true_positive`, `false_positive`, `inconclusive`. JSON 누락/null과 CSV 빈칸은 답안을 등록하지 않는다. 단건 JSON 빈 문자열·임의 문자열·잘못된 타입은 422다.
- 별도의 요청 모델에서 분리한 뒤 순수 이벤트 검증을 수행한다. 이벤트 fingerprint·원문·추론 입력·사용자 입력 스키마에는 답안을 넣지 않는다.
- 응답 `evaluation.reference_label`에서 등록된 답안을 즉시 확인할 수 있다. 파일 응답은 `label_attached`/`label_unchanged`를 제공한다.
- 서비스 키 답안은 `reference` 출처, `analysis-request:expected_verdict`, AI 결과 열람 여부 미확인으로 기록한다. 관리자의 기존 테스트 파일 출처는 유지한다.
- 같은 답안 재전송은 중복 등록하지 않는다. 최신 답안과 충돌하면 덮어쓰지 않고 409로 안내한다. 자동 Test 실행에는 기존 요청 전체 멱등성 검사도 적용된다. 답안 정정은 웹 또는 별도 답안 API로 한다.
- 입력 스키마 버전은 바뀌지 않는다. Swagger와 온라인 API 정의서/PDF에는 선택 요청 항목을 별도로 반영한다. DB 마이그레이션은 없다.

## 조회 API와 테이블

- Test 조회에 `reference_basis=latest|initial`을 추가했다. 기존 API 호출의 기본값 `initial`은 호환성을 위해 유지하고 웹에서 `latest`를 명시한다.
- 상세 `evaluation_id`는 지정한 저장 평가를 우선한다. 응답 `reference_basis`는 `latest`, `initial`, `saved` 중 하나다.
- 분석 목록 정렬: `sort_by=created_at|company_name|src_ip|dest_ip|status`, `sort_order=asc|desc`.
- 테스트 목록 정렬: `created_at|name`. 테스트 문항 정렬: `row_number|case_name|status`.
- 정렬은 서버가 전체 검색 결과에 적용한 후 페이지를 자른다. 정렬 변경 시 첫 페이지로 이동하고 선택을 초기화한다. 지표의 분모에는 정렬/페이지가 영향을 주지 않는다.
- 프런트엔드 의존성은 `@tanstack/react-table@9.2.4`를 추가했다. React/Vite와 기존 CSS 구조는 유지한다. 주요 목록은 공통 DataTable, 문서·행렬·고정 순서 표는 공통 Table로 구성한다.
- 좁은 화면은 표 영역 안에서 가로 스크롤한다. 원문·긴 요약은 전체 데이터를 잘라 저장하지 않고 화면 미리보기만 줄인다. 체크박스는 현재 페이지 선택이며 부분 선택 상태를 표시한다.

## 검증

자동화된 테스트는 인공 입력과 격리된 DB를 사용한다. 브라우저 검증은 모든 API를 가상 응답으로 대체하고 외부 네트워크를 차단한다. 실제 모델 품질 평가가 아니다.

최종 결과: 관련 백엔드 회귀 테스트 **412건 통과**, 프런트엔드 **39개 테스트 파일 통과**, 브라우저 **8개 시나리오 통과**, Vite 빌드 성공. 브라우저의 외부 요청·페이지 오류는 0건이다. 빌드에는 기존 단일 번들 구조의 500 kB 권고 초과 경고가 남으며 생성된 JS는 약 603 kB(압축 전)다. Docker 재배포·Git 게시·실제 LLM 호출은 수행하지 않았다.

- 백엔드: `tests/test_request_references.py` 및 기존 접수·테스트 실행·평가·입력 스키마 회귀 테스트.
- 프런트엔드: `node --test src/*.test.mjs`, `npm run build`.
- 브라우저: 기존 Playwright/Chromium 환경에서 `node scripts/evaluation-table-smoke.mjs`. 별도 설치 위치는 `PLAYWRIGHT_MODULE`·`CHROMIUM_PATH`로 지정할 수 있다.

의존성 확인에서 기존 Vite 6.1.0/esbuild의 npm audit 경고 2개 패키지(high 1, moderate 1)가 확인됐다. TanStack 관련 경고는 없었다. 이번 기능 변경에서 도구 체인 업그레이드는 수행하지 않았으며 별도 보완이 필요하다.
