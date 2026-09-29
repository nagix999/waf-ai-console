# Agent/UI 판정 의미 정합성 — 1차 구현 기록

기준: `ASTRA_AGENT_UI_SEMANTIC_ALIGNMENT_FINAL.md`의 **PR 1 — 현재 UI/표시 버그**. 작업 시작 commit은 `19380ae` (`Deploy current changes to main`)이다. 기존 SQLite 조회 최적화와 유효 기능을 보존했다.

이번 변경은 표시와 검색 의미를 맞춘 작업이다. 모델 출력 계약·프롬프트·판정 정책은 바꾸지 않았다. PR 2의 Agent 계약 변경과 별도 승인 항목을 완료한 것으로 보지 않는다. 운영 DB 적용, Docker 재배포, Git commit/push, 실제 LLM 호출도 하지 않았다.

## 구현 완료

| 파일 | 변경 |
| --- | --- |
| `frontend/src/decisionSemantics.js`, `backend/app/services/decision_semantics.py` | 판정별 근거 순서, Signature 관계 설명, 심각도 의미, WAF 관측 안내를 표시 전용 함수로 분리 |
| `frontend/src/AnalysisDecision.jsx`, `frontend/src/decisionDetail.js` | HOLD는 ‘판정 확정에 필요한 조건’, 확정 판정은 ‘후속 확인 · 선택사항’으로 분리. 확정 판정의 쟁점은 기술 해석으로 이동. 불일치 HOLD에서는 Primary 해석을 최종 해석처럼 보여주지 않음 |
| `frontend/src/App.jsx`, `frontend/src/GlobalSearchResults.jsx` | 상세·목록·검색 결과 제목은 이벤트명 또는 중립적인 기본 제목. Signature는 `WAF 탐지` 보조 정보 |
| `frontend/src/R5Evaluation.jsx`, `frontend/src/r5.css` | 공격→정상/보류, 정상→공격/보류, 보류 답안→정탐/오탐 확정의 6방향과 Coverage를 먼저 표시. 기존 이진 행렬·필터 이동·상세 지표 유지 |
| `frontend/src/GroundTruthWorkspace.jsx`, `backend/app/validation_data_schemas.py`, `backend/app/services/ground_truth_queries.py` | ‘답안 출처’ 필터와 표가 같은 `reference_origin`을 사용. SQL 필터·집계·페이지네이션 경로 유지 |
| `frontend/src/TestGroundTruthImport.jsx`, `frontend/src/groundTruthImport.js` | 신규 유답안/무답안을 중복 없는 숫자로 표시. 처리 중 상태, 미리보기·오류 초점 이동, 검색·대상 변경 시 이전 미리보기 초기화, 안전한 오류 안내, 명시적 저장과 재시도 키 유지 |
| `frontend/src/analysisReport.js`, `frontend/src/AnalysisReport.jsx`, `backend/app/services/analysis_exports.py`, `backend/app/services/report_pdf_layout.py` | 웹 보고서·Markdown·PDF·Excel에서 같은 판정 의미와 근거 순서를 사용. 기대 보류의 확정 방향은 평가 대상일 때만 표시 |

심각도 6종의 CSS class는 소문자로 맞췄다. ‘잠재 영향 기준 · 실제 공격 성공 여부와 별도’ 안내를 추가했다. 근거를 정렬해도 기존 근거 번호·원문 위치·관계는 바꾸지 않는다.

### Legacy Signature 처리

기록된 `relation` 값으로 짧은 관계 설명을 표시한다. 자유 서술 `explanation_ko`는 삭제·수정·추정하지 않고, UI에서 ‘기존 분석 설명’으로 접어 보존한다. 보고서에도 원래 설명이라는 구분과 주의 문구를 표시한다. `mismatch`를 오탐으로 바꾸지 않는다.

이는 **저장된 relation 자체의 정확도를 새로 검증한 것이 아니다.** 기존 자유 서술의 모순을 기록에서 지우거나, v2 구조로 backfill하지 않았다.

### Verifier 불일치

완료된 HOLD이고 `diagnostics.inconclusive_reasons`에 `verdict_disagreement`가 있는 경우에만 별도 표시한다. 최종 단일 기술 해석 대신 불일치 안내와 역할별 판정을 보여주고 양쪽 근거·실행 상세로 연결한다. 실제 저장된 역할별 기술 해석은 접힌 기록으로만 제공한다. 보고서는 역할별 판정만 허용하며 임의의 Agent 입출력 전체를 복사하지 않는다.

## API / DB / 운영 의존성

변경 API: 관리자 전용 `POST /api/v1/validation-datasets/{identifier}/working/search`의 요청에 선택 필드 추가.

```json
{
  "reference_origin": "reference_label",
  "offset": 0,
  "limit": 20
}
```

허용 값은 `published_ground_truth`, `reference_label`, `manual`, `none`이다. 생략/null이면 이 조건으로 제한하지 않는다. 허용하지 않은 값은 422로 거절한다. 기존 `source_kind` 필터도 유지하며 둘 다 지정하면 두 조건을 모두 적용한다. 목록에 표시하는 값과 같은 SQL 값으로 검색하고, 검색 때문에 암호화 원문을 읽지 않는다.

가져오기 API의 `new_count`는 기존대로 답안 없는 신규 건을 포함한다. UI는 `new_count - missing_reference_count`를 ‘신규 · 답안 있음’으로 표시해 나머지 네 분류와 중복되지 않게 했다. 미리보기는 사례를 저장하지 않는다. **‘편집 중 데이터에 추가’**를 눌러야 저장하며 공식 버전 발행은 별도다.

- DB migration 없음. head는 기존 `0025_analysis_read_index` 유지.
- 새 운영 패키지/브라우저 의존성 없음. PDF는 기존 ReportLab 사용.
- Agent 출력 계약·활성 프롬프트·Primary/Verifier 독립성·최종 판정 정책 변경 없음.
- 과거 Analysis/Agent 이력, Published 평가 snapshot 수정 없음.
- 운영 반영은 기존 공식 Test → Production Review → Apply 경로 유지.
- API/화면을 함께 빌드·배포해야 새 답안 출처 필터를 사용할 수 있다.

## 확인한 기존 문제와 회귀 검증

| 재현 조건 | 수정 후 확인 |
| --- | --- |
| `severity-CRITICAL` 등 대문자 class | 6종 모두 기존 소문자 CSS와 연결 |
| 이벤트명이 있는데 Signature가 제목 | 이벤트명 우선, Signature 보조 표시 |
| TP/FP에도 ‘판정 조건’ 제목 | 후속 확인은 선택사항, 판단 쟁점은 기술 해석 |
| Legacy `mismatch` + ‘명확히 일치합니다’ 설명 | 관계 요약은 mismatch 기준, 원래 설명은 별도 기록. TP 판정 유지 |
| Verifier 불일치 HOLD | Primary 기술 해석을 최종 해석처럼 노출하지 않음. 양쪽 근거와 기존 정책 유지 |
| FP에서도 TP 근거가 항상 먼저 | FP 근거 먼저, 반대 근거 다음. HOLD는 공격/정상 해석 |
| 기대 보류의 TP/FP 확정을 합산 | 11건/9건 가상 사례를 별도 표시. 실제 미탐·과탐과 구분 |
| ‘답안 출처’ 필터와 표의 필드 불일치 | 네 출처, 삭제된 문항 포함 조회, 기존 source_kind 호환과 422 검사 |
| 가져오기 신규 건 중복 집계 | 신규 유답안/무답안·중복·충돌·불가의 합이 원본 건수와 일치 |

### 실행 결과

- **Backend 전체:** 테스트 파일을 네 묶음으로 실행. 최초 2,344 passed / 1 failed / 3 skipped. 실패 1건은 이전 제목을 기대하던 `test_analyst_presentation.py`의 assertion이었다. 원문/역할 출력 비노출 검증은 유지하면서 새 제목으로 수정했고, 해당 파일을 포함한 최종 관련 회귀 **117건 통과**.
- 전체 검사 이후 기대 보류 방향의 평가 적격성 회귀 2건을 추가했다. 최신 의미 정합성 18건 + 실제 PDF 10건을 함께 실행해 **28건 통과**.
- 전체 검사에서 skipped였던 PDF renderer 연동 2건도 `WAF_TEST_REPORT_RENDERER=1`로 실제 실행해 통과했다. 별도 선택형 PDF golden sample 1건은 로컬 자료가 없어 미실행이다. 최종 코드에서 전체 suite를 다시 한 번 실행한 것은 아니며, 수정/추가된 범위는 위 재검사로 확인했다.
- **Frontend:** `node --test src/*.test.mjs` **530 passed, 0 failed**.
- **Build:** `npm run build`, `npm run build:report` 통과. 기존 JS 번들 500 kB 초과 경고는 남아 있다.
- **Browser:** `r5-final-smoke.mjs`, `analysis-decision-smoke.mjs`(50개 캡처), `semantic-alignment-smoke.mjs`(17개 캡처) 통과. 실제 React를 가상 API와 연결했으며 외부 요청은 차단했다. KR/EN, SK/Light/Dark, 390px 모바일, 키보드·초점 이동, 상태 초기화, 명시적 저장, 원문/Agent 이력의 필요 시 조회와 기존 URL 이동을 검증했다.
- 실제 ReportLab PDF에서 Light/Dark, 한글·긴 근거, 링크·스크립트 비활성화를 확인했다. 브라우저 다운로드 smoke의 응답은 가상 응답이므로 이 PDF 연동 검사와 구분한다.
- `git diff --check` 통과. 실제 사내 로그는 fixture에 넣지 않았다.

재현 명령(프런트엔드는 Node 24 환경 사용):

```bash
cd frontend
node --test src/*.test.mjs
npm run build
npm run build:report
node scripts/semantic-alignment-smoke.mjs
node scripts/analysis-decision-smoke.mjs
node scripts/r5-final-smoke.mjs
```

Browser smoke는 로컬에 준비된 Playwright/Chromium을 테스트에만 사용한다. 필요 시 `PLAYWRIGHT_MODULE`, `CHROMIUM_PATH`를 지정한다. 운영 PDF에 Chromium을 추가하지 않는다.

```bash
cd backend
.venv/bin/pytest
WAF_TEST_REPORT_RENDERER=1 .venv/bin/pytest tests/test_report_pdf.py tests/test_semantic_alignment.py -o addopts='' -q
```

### 주요 화면

가상 데이터 캡처이며 실제 모델 품질이나 운영 지표가 아니다. `test-results/`는 로컬 검증 산출물로 Git에 포함하지 않는다.

- [평가 · KR / Light](../test-results/semantic-alignment/evaluation-KR-Light.png)
- [판정 불일치 · EN / Dark](../test-results/semantic-alignment/disagreement-EN-Dark.png)
- [답안 출처 필터](../test-results/semantic-alignment/reference-origin-filter.png)
- [가져오기 미리보기](../test-results/semantic-alignment/import-preview.png)
- [모바일 가져오기](../test-results/semantic-alignment/import-mobile.png)
- [모바일 평가](../test-results/semantic-alignment/evaluation-mobile.png)

## 다음 단계와 미검증

**PR 2 — 아직 미구현:** `SignatureAssessment v2`, relation 구조 검증·repair, `AnalystCheck.purpose`, WAF D/A 프롬프트 규칙, purpose 검증, `recommended_checks` legacy-only 처리, Result Editor 호환.

이번 수정은 의미가 다른 표시를 분리했지만, 과거 자유 서술의 확인 사항을 새 목적 분류로 추정하지 않았다. 따라서 실제 모델이 WAF D/A나 공격 성공을 혼동한 질문을 생성하지 않는다고 보장할 수 없다. 이 부분은 PR 2에서 계약과 프롬프트를 바꾸고 별도 검증해야 한다. `SignatureAssessment v2`에 대한 회귀도 아직 대상이 아니다.

**별도 설계 승인 후:** Ground Truth 사람 검토 상태, Production 품질 정책/고정 합격선, Verifier A/B/C 전략 평가. 임의 스키마·정책·새 Agent를 추가하지 않았다.

**실제 모델 검증: 미검증.** Gemma/vLLM/OpenAI를 호출하지 않았다. 이번 단위·브라우저·PDF 결과를 실제 운영 로그에서의 판정 정확도 향상으로 해석하면 안 된다.
