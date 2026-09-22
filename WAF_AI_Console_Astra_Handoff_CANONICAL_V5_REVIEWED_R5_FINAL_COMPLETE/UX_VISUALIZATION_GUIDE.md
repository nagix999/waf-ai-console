# UX Flow & Visualization Guide — R5 FINAL

The UI should be understandable without learning the backend/domain model.

## 1. Product mental model

The user should be able to summarize the product as:

```text
정답 데이터 준비
→ 테스트
→ 결과 확인
→ 필요한 것 수정
→ 운영 반영 검토
→ 운영에 반영
→ 운영 결과 확인
```

English:

```text
Prepare Ground Truth
→ Run a Test
→ Review Results
→ Fix What Is Needed
→ Production Review
→ Apply to Production
→ Observe Production
```

This lifecycle is documentation/onboarding help, **not a persistent navigation element**.

## 2. Where flow visualization helps

### A. First Production Setup — YES

Use a compact five-step vertical/linear progress view:

```text
모델 연결
→ 기본 테스트 설정
→ 정답 데이터 공식 버전
→ 공식 테스트
→ 운영에 반영
```

Show only one strongest next action.

Do not make this a blocking wizard.

### B. New Test — LIGHTWEIGHT

Do not use a multi-page wizard.

Use numbered section headings to make the form self-explanatory:

```text
1. 테스트 종류
2. 테스트 설정
3. 평가 데이터
```

For Development Test, section 3 becomes `입력 데이터`.

### C. Test iteration — CONTEXTUAL FLOW

Do not draw a large diagram in the Test Detail page.

Use contextual actions:

```text
이 설정으로 다시 테스트
정답 데이터에서 열기
운영 반영 검토
```

The next action should be visible where the user needs it.

### D. Test → Ground Truth import — YES

Use a 3-stage dialog/progress pattern:

```text
대상 선택 → 미리보기 → 추가
```

Preview counts for duplicate/conflict/missing/unavailable are clickable filters so the user can inspect why items will not be imported.

Preview is the important stage:

```text
새 항목
중복
충돌
정답 없음
가져올 수 없음
```

This prevents a bulk action from feeling opaque.

### E. Production Review — YES

Use a left-to-right comparison:

```text
현재 운영 설정  →  테스트한 설정
```

Under it, use a readiness checklist.

Do not use a generic process diagram here.

## 3. Where quantitative visualization helps

### Home

Use up to three primary visual summaries when the data supports them:

1. Comparable Production Evaluation Trend
   - line chart
   - Accuracy / F1 / Coverage
   - only comparable evaluation context

2. Ground Truth Draft Distribution
   - one horizontal stacked bar
   - Ready to Include / Needs Attention / Excluded
   - only the Dataset tied to current Production Evaluation context

3. 최근 동일 기준 테스트 / Recent Tests on the Same Baseline
   - compact list + F1 mini bar
   - show changed configuration first, full identity second
   - Accuracy / Coverage support

### Home empty/low-data behavior

Do not reserve empty chart slots.

```text
Trend has <2 comparable points
→ compact current evaluation summary

Ground Truth has no draft changes/attention/exclusions
→ compact `변경 없음 / No changes`

No same-baseline Tests
→ compact empty state
```

### Ground Truth

Useful:
- compact Ready to Include / Needs Attention / Excluded stacked bar
- Working changes: Added / Updated / Removed as counts
- Publish source/provenance breakdown as a small horizontal stacked bar + counts

Avoid:
- pie/donut dashboards
- per-Dataset KPI cards
- charts for simple counts that a table/list explains better

### Test Detail

Useful:
- Accuracy / Precision / Recall / F1 / Coverage
- Confusion Matrix with Ground Truth class × Deep Assessment class axes
- coverage / abstention counts

Avoid:
- duplicate top metric strip
- a second summary dialog
- decorative charts

### Production Review

Useful:
- current-vs-tested diff
- official test metric summary
- readiness checklist

Avoid:
- historical trend chart on the review page
- visual scoring or "winner" framing

### Runtime

Useful only with real time-series data:
- request volume trend
- latency trend
- failure/outcome distribution

If the data source is unavailable, show `Unknown`; do not synthesize a chart.

## 4. Where visualization does NOT help

Use tables/lists instead of charts:

```text
LLM Profiles
Agent Roles
Analysis Instructions
Input Schema
Operations > Inference History (Production-only)
Activity
API Keys
vLLM Targets
```

Reason: these pages are for finding, editing, comparing, or investigating specific records.

## 5. Initial vs Deep Assessment

Do not create a chart.

Use a simple two-row comparison:

```text
1차 판정   False Positive · 93%
심층 판정  True Positive
```

If different, show one amber warning.
If deep assessment is inconclusive, show a separate amber message.

## 6. Help / learning support

Do not add a permanent tutorial sidebar.

Recommended:

- First Production progress on Home only while unconfigured.
- Small `?` helper for unfamiliar concepts:
  - 정답 데이터 / Ground Truth
  - 공식 테스트 / Official Test
  - 운영 반영 검토 / Production Review
- Empty states tell the user the next action.
- Contextual deep links replace instructional prose where possible.

## 7. Visualization budget

Every visualization must answer one of:

```text
What changed?
What is the distribution?
How is it trending?
What is blocking me?
What should I do next?
```

If it answers none of these, use text/table instead.


## 8. Evaluation vs Operations

```text
Evaluation > Tests
→ Test browsing and evaluation

Operations > Inference History
→ Production analysis investigation only
```

The same detail component may be reused; the lists are not duplicated.
