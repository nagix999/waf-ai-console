# 0. User-visible labels

All visible labels must use `UI_TERMINOLOGY.md`.

KR default sidebar:

```text
홈
평가
설정
운영
연동
```

EN sidebar:

```text
Home
Evaluation
Configuration
Operations
Integrations
```

Routes/internal enum names stay unchanged.

Do not expose internal terms such as `Candidate`, `Promotion`, `Preflight`,
`Published Revision`, or `Working Draft` as the default KR/EN copy.


# WAF AI Console — UI Implementation Spec V5 REVIEWED R5 FINAL SOLO

Canonical IA:

```text
Overview
Evaluate
Configure
Operate
Connect
```

Promotion은 persistent navigation이 아닌 contextual full-page workflow다.

> Overview만 dashboard. 나머지는 task/result-oriented composition.

---

## Internal names vs visible copy

This document may use internal route/domain names in implementation notes.
Anything rendered to the user must follow `UI_TERMINOLOGY.md`.

Examples:

```text
Internal: candidate_configuration
Visible:  테스트 설정 / Test Configuration

Internal: preflight
Visible:  적용 전 점검 / Readiness Checks
```

# 1. Shell

```text
Sidebar 224px
Header 58px
Content horizontal padding 28~36px
```

Sidebar child menu 없음. Workspace Tabs는 content 상단 underline.

Header:

```text
Global Search | Production Health | KR/EN | Theme ▾ | Principal
```

Theme/Locale 변경 시 route/tab/filter/form/selection state 유지.

---

## Global Search routing

Global Search may find both Production and Test analyses, but routes by purpose:

```text
Production Analysis → 운영 > 분석 이력 / Detail
Test Analysis       → 평가 > 테스트 / matching Test Case
Test Run            → 평가 > 테스트 / Test Detail
```

Search results show a visible context badge:

```text
운영 분석 / Production
테스트 결과 / Test
```

Do not duplicate Test browsing inside Operations.

# 1.1 Composition Rules

- Page body는 Section Surface 단위로 읽혀야 한다.
- Table은 outer border + header surface + row separators.
- Registry에서는 Table이 가장 큰 시각적 면적.
- Workbench에서는 list/detail divider가 주요 구조.
- Versioned editor는 version rail + detail/editor.
- 일반 Primary = neutral dark.
- `운영에 반영 / Apply to Production`만 Brand Mutation.
- Page header 직접 action 1~2개, 나머지는 overflow.
- Card wall로 회귀하지 않는다.

권장:

```text
Button/Input/Select 38px
Button radius 9px
Table header 42px
Table row 48~50px
Section radius 14px
Section padding 20~24px
Section gap 22~28px
```

---

# 1.2 Login

Centered, minimal authentication screen.

```text
WAF AI Console
아이디
비밀번호
로그인
Theme ▾
```

- actual username/password admin session only
- no invented SSO/OTP/social login
- no workflow/marketing explanation
- SK default; Light/Dark selectable if theme control is shown

---

# 2. Overview

## 2.0 Production-state rendering

Backend `production_state`에 따라:

```text
unconfigured  → First Production Setup
legacy_active → Normal Overview + informational banner
promoted      → Normal Overview
```

### legacy_active banner

```text
기존 Production 구성
새 Promotion workflow 도입 이전의 운영 구성입니다.
다음 변경부터 Candidate Test → Promotion을 사용합니다.
```

Warning/Danger로 과장하지 않는다.

## 2.1 First Production Setup State

Setup Guide는 **5단계**다.

```text
모델 연결 / Model Connection
기본 테스트 설정 / Default Test Configuration
정답 데이터 공식 버전 / Published Ground Truth
공식 테스트 / Official Test
운영에 반영 / Apply to Production
```

- 동시에 여러 Primary CTA 금지
- 현재 next action 하나만 Primary
- 완료/blocked/unknown을 구분
- first Production은 Candidate Test → Promotion을 우회하지 않음
- `Candidate configuration`을 독립 저장 object처럼 표현하지 않음

첫 Promotion 성공 즉시 Normal Overview로 전환한다.

Production API는 Setup progress에 넣지 않고 할 일 / Action Center에서 별도로 표시한다.

## 2.2 Normal Overview

```text
Production Health + Configuration ID
Runtime metric strip
Comparable Production Evaluation Trend | 정답 데이터 상태 / Ground Truth Status
최근 동일 기준 테스트 / Recent Tests on the Same Baseline (configuration identity 포함)
할 일 / Action Center (Needs attention / Next steps 그룹)
Current Configuration | Recent Activity
```

할 일 / Action Center는 다음을 구분한다.

```text
Production API credentials: ready / missing / unknown
Production API traffic: observed / not_observed / unknown
```

API Key만 있다고 `connected`라고 쓰지 않는다.

Trend comparability / 0–100% axis / fallback 규칙은 Handoff를 따른다.


## 2.3 Conditional visualization rule

Home uses **up to three** visual summaries, not three permanent empty slots.

```text
운영 평가 추세
정답 데이터 상태
최근 동일 기준 테스트
```

Rules:

- Trend requires >=2 comparable Production evaluation points.
- With 0–1 comparable points, show a compact current evaluation summary.
- If Ground Truth has no working changes/attention/exclusions, show compact `변경 없음`.
- If no comparable Tests exist, show a compact empty state instead of an empty mini-bar area.

# 3. Evaluate

Tabs:

```text
Tests | Ground Truth | Production Evaluation
```

## 3.1 Tests

Landing:

```text
Search / Status / Ground Truth filters                         + New Test
평가지표 펼치기 ▾
Test Run table
```

Compact columns:

```text
Test / Configuration / Status / Evaluation / Duration
```

Compact Evaluation:

```text
Acc 94.6 · F1 94.1 · Cov 99.1
```

`평가지표 펼치기`는 disclosure/toggle button이며 `aria-expanded`를 사용한다. Checkbox semantics를 사용하지 않는다.

Expanded:

```text
Test / Status / Accuracy / Precision / Recall / F1 / Coverage / Duration
```

Rules:

- 평가 없음 = `—`, 0% 금지
- GT dataset/revision/sample count secondary metadata
- different comparison_key면 delta/직접 비교 금지
- Test name 기본 자동 생성, rename optional
- same-baseline previous Test 대비 config change가 있으면 row에서 변경된 항목을 먼저 표시:
  `변경됨 · 분석 지침 v19 → v20`
- full config identity is secondary/detail metadata

### Default Test Configuration

Placement:
```text
평가 > 테스트
[기본 테스트 설정]   [+ 새 테스트]
```

Fields:
```text
주 분석 모델 · Primary
검증 모델 · Verifier
근거 정리 모델 · Evidence Editor
분석 지침
입력 스키마
```

This is a New Test convenience default, not a Production editor.

### New Test — purpose first

Visual section order:

```text
1. 테스트 종류 / Test Type
2. 테스트 설정 / Test Configuration
3. 평가 데이터 / Evaluation Data
```

This is one form, not a blocking wizard.

```text
공식 테스트 / Official Test
개발 테스트 / Development Test
```

공식 테스트 / Official Test:
- Published Ground Truth Dataset + version required
- can be used for Production Review

개발 테스트 / Development Test:
- Single / File / Dataset
- clearly Development only
- cannot be used for Production Review

테스트 설정 / Test Configuration is visibly prefilled from Test defaults, remains editable, and is explicitly submitted.

### 이 설정으로 다시 테스트 / Run Again as New Test

Source Test's 테스트 설정 / Test Configuration and Ground Truth context are prefilled.
Unavailable resources are shown as missing; never silently replaced.

### Test → Ground Truth

Test Detail More menu:

```text
정답 데이터에 추가… / 테스트 사례를 정답 데이터에 추가 / Add Test Cases to Ground Truth
```

Choose:

```text
Create new Ground Truth Dataset
Add all cases to existing Ground Truth Dataset
```

Preview before confirm shows:
- importable
- new
- duplicates
- reference conflicts
- missing reference verdict
- unavailable/rejected

Preview counts are inspectable.
Clicking duplicates/conflicts/missing/unavailable filters the preview item list and shows the reason.

No automatic Publish.
Deep assessment verdict never becomes Ground Truth automatically.

### Test Detail Mode

Test row → same `Evaluate / Tests` detail mode.

Header:

```text
Back / Test name / status
테스트 설정 / Test Configuration + Ground Truth context
accepted / processed / execution failures / duration
Compare / Execution info / Promote CTA when eligible
```

**Header 아래의 evaluation MetricStrip은 제거한다.**

Detail Tabs:

```text
문항별 결과 | 평가 상세
```

`평가 상세` Tab은 compact overview가 아니라 detailed evaluation을 바로 inline render한다.
공식 Ground Truth mode에서는 `승인 답안` 같은 legacy 문구를 사용하지 않고 `Published Ground Truth / Ground Truth 기준`으로 표현한다.

```text
Evaluation context
Accuracy / Precision / Recall / F1 / Coverage
Confusion Matrix
Additional metrics
Counts / excluded / abstention information
```

`평가 상세` secondary button과 Dialog는 제거한다.
Test Detail에서 `EvaluationOverview`의 compact → dialog 패턴을 사용하지 않는다.

Confusion Matrix cell labels must not use bare TP/FP/FN/TN.
Use Ground Truth class × Deep Assessment class axes.
Inconclusive/excluded counts remain outside the 2×2 matrix.
Cell selection switches to `문항별 결과` with the class-pair filter.

Case row → right Drawer → `크게 보기` → Full Inference Detail.
GT-linked case는 `Working Draft에서 열기 →`로 동일 Dataset/case를 바로 연다.
Return state:

```text
selected Test / Case / Tab
filters / search / scroll
list metric-expanded preference
```

### 운영 반영 검토 / Production Review Entry

explicit 테스트 설정 / Test Configuration으로 실행한 official Ground Truth Test가 terminal 상태라면
실제 preflight eligibility와 무관하게 `운영 반영 검토 / Production Review` CTA를 노출한다.

```text
운영 반영 검토 / Production Review →
#promotion/{test_run_uuid}
```

Promotion Review 화면에서 blocked preflight reason을 확인할 수 있어야 한다.
실제 `운영에 반영 / Apply to Production` button만 eligibility/schema acknowledgement/stale baseline 조건으로 disable/enable한다.

## 3.2 Ground Truth

```text
Dataset selector
Published Revision + Working Changes
준비됨 / 확인 필요 / 제외
filters + bulk actions
Case table 60~65% | selected Case 35~40%
Publish Revision
```

### Multiple Datasets

Ground Truth Workspace supports multiple independent Datasets.

```text
[Dataset search/select] [Create Dataset]
Published rN · cases · working changes · needs attention
```

Each Dataset owns an independent Working Draft / working_revision / Published history.

Test-origin imported cases retain Test provenance and then behave like normal Working Draft cases.

### Current Production evaluation context

Ground Truth selector can show `현재 운영 평가 기준`.

New Official Test may visibly preselect the same Dataset/Published Version when current Production Evaluation context exists, labeled `현재 운영과 동일한 평가 기준`.

User may change it. Never guess if no context exists.

### Working Draft

- Draft/Reviewed/Approved UI 없음
- item save마다 immutable revision 생성 없음
- automatic validation = Ready / Needs attention
- user exclusion = Excluded

### Working Changes

```text
Added / Changed / Removed
```

change type별 action:

```text
Added   → Discard addition
Changed → Revert to published version
Removed → Restore from published revision
```

- changes filter
- stale working revision protection
- dataset → `Discard all working changes` + destructive confirmation

### Bulk

```text
Import
Include / Exclude
Tags / Category
Delete from Working Draft
Needs attention only
Verdict / Source / Search
```

### Publish Dialog

반드시 표시:

```text
Working total
Ready included
Needs attention omitted
Excluded omitted
Inclusion rate
```

Omitted item이 1건 이상이면 dataset-level acknowledgement 1회 필요.
Publish 시에만 immutable Dataset Revision 생성.

## 3.3 Production Evaluation

```text
Production Configuration ID
Ground Truth Dataset / Published Revision
Evaluation Test / Evaluated At / Sample Count
Accuracy / Precision / Recall / F1 / Coverage
Category results
Comparable evaluation history
```

Live Production accuracy로 표현 금지.

---

# 4. Configure

Tabs:

```text
LLM Profiles | Agent Roles | Analysis Instructions | Input Schema
```

## 4.1 LLM Profiles

```text
Search / Provider / Validation                         + Add profile
compact summary
full-width registry table
```

기존 Quick/Full Validation, enable/disable, provider/model/context/output/timeout, OpenAI external approval 보존.

## 4.2 Agent Roles

```text
현재 운영 역할 / Current Production roles
Read-only
```

Show:
```text
주 분석 모델 · Primary
검증 모델 · Verifier
근거 정리 모델 · Evidence Editor
```

Production roles are not directly mutable here.

## 4.3 Analysis Instructions

```text
Version rail 245~280px | selected version/editor
```

New Version / Compare / Working Draft. Production direct Apply 없음.

## 4.4 Input Schema

```text
Version rail | field definitions
Validate / Compare / More
```

Input Schema는 테스트 설정 / Test Configuration의 일부이므로 Configure에 둔다.
No Production Apply / Rollback.
Production API docs에서 contextual link로 진입한다.

---

# 5. Operate

Tabs:

```text
Runtime | Inference History | Activity
```

## 5.1 Runtime

```text
Service/worker/queue state
Request/latency/failure strip
Recent failures | Capacity / Concurrency
```

실제 근거 없으면 `Unknown`, fake healthy 금지.

## 5.2 Inference History

`운영 > 분석 이력 / Operations > Inference History` is Production-only.

Helper:

```text
KR: 운영 목적으로 처리된 분석만 표시합니다.
    테스트 결과는 평가 > 테스트에서 확인합니다.

EN: Shows Production analyses only.
    Test results are available under Evaluation > Tests.
```

No `Production / Test / All` scope control.

```text
search/filter toolbar
result count
full-width Production analysis table
pagination
```

기존 filters/columns/bulk functions 보존. Dashboard chart 금지.

## 5.3 Activity

```text
filters
Timeline/List 65~70% | selected change detail 30~35%
```

기본 컬럼은 Time / Type / Change. Actor는 detail metadata.
Promotion history는 Promotion filter에서 제공.
Deployment는 read-only.

---

# 5.4 Initial Assessment Comparison

v1 입력 surface:

```text
Production-purpose service API → POST /api/v1/analyses only
```

Reserved metadata:

```text
initial_verdict
initial_probability
initial_model_version
```

- generic extra_fields / Input Schema field로 사용 금지
- Test/upload/GT 경로에서는 reject
- Agent input에는 절대 포함하지 않음
- terminology = `1차 판정 / 심층 판정`

Result warning:

```text
different
  ⚠ 1차 판정과 심층 판정이 다릅니다

final_inconclusive
  ⚠ 심층 판정이 보류되었습니다
```

둘 다 Amber.
`match`는 조용한 metadata.

Inference History:

```text
1차 판정 비교
전체 / 일치 / 불일치 / 심층 판정 보류 / 1차 판정 없음
```

probability와 심층 판정 confidence를 같은 calibration scale의 경쟁 수치처럼 비교하지 않는다.


# 6. Contextual Promotion Full Page

# 6. Contextual Promotion Full Page

Sidebar/Workspace Tab에는 없다.

Entry:

```text
Terminal official Candidate Test → 운영 반영 검토 / Production Review
Overview 할 일 / Action Center → promotion-ready Candidate
```

Layout:

```text
← Back to Test
Candidate identity
Current Production → Candidate
Configuration diff
Official Published GT Evaluation
Pre-flight
운영에 반영 / Apply to Production
```

- Promotion Review 진입은 blocked preflight에서도 가능
- failed check마다 remediation action 제공
- origin-aware Back (`Overview` 또는 `Test`) + Source Test link
- actual `운영에 반영 / Apply to Production` CTA만 Brand Mutation + eligibility-gated
- exact tested snapshot만 허용
- schema acknowledgement
- stale baseline detection
- atomic backend transaction
- success → Activity record

---

# 7. Inference Detail

```text
compact execution summary
Result | Agent Trace | Input | Result JSON | Report
```

Result: evidence/analysis 중심.
Agent Trace: run selector + step rail + detail.
Input: raw content + schema/decoding metadata.
Result JSON: tree/json/search/wrap/copy.
Report: readable paper layout + export controls.

---

# 8. Connect

Tabs:

```text
Production API | API Keys | vLLM Targets
```

## Production API
TOC + document + search + code copy + Swagger + OpenAPI JSON + PDF + schema metadata.

## API Keys
Issue / purpose / source_system / scopes / one-time raw key / rename / delete / last used.

## vLLM Targets
Search/usage + endpoint registry + add/edit/delete + in-use conflict handling.

---

# 9. Theme / Tab Focus

Default theme = SK. Selectable = SK / Light / Dark.

SK/Light 내부 Tab focus에서 글자가 white로 바뀌면 안 된다.

```text
inactive        muted foreground
hover           strong foreground
selected        strong foreground + indicator
focus-visible   foreground 유지 + ring/outline
selected+focus  selected foreground 유지 + ring/outline
```

Required QA:

```text
SK / Light / Dark
× inactive / hover / selected / focus-visible / selected+focus-visible
```

---

# 10. Responsive

```text
>=1180 desktop full
700~1179 compact shell
<700 mobile-safe
```

- Workspace tabs horizontal scroll
- tables local horizontal scroll
- split panes stack
- forms one column
- Drawer → full-width sheet on narrow screens

---

# 11. Visual Implementation Gate

먼저 공통 primitive 구현:

```text
Tokens / Typography / Surface
Button / Input / Select
Tabs / Table / Badge
Dialog / Drawer
Sidebar / Header
```

Calibration screens:

```text
1. Overview — First Production / legacy_active / Normal
2. Ground Truth — Added/Changed/Removed recovery
3. Test Detail — 평가 상세
4. Promotion Review — blocked + eligible
```

1440×900 Browser Screenshot self-review 후 공통 component/token 문제를 먼저 수정하고 전체 화면으로 확장한다. 사용자가 단계 승인을 요청하지 않았다면 중간에 멈추지 않는다.

R5 FINAL mockup은 pixel specification이 아니다. IA / Page Archetype / 정보 우선순위를 보존하면서 실제 React에서 더 높은 완성도로 polish한다.


# 7. Visualization usage matrix

| Surface | Visual | Rule |
|---|---|---|
| Home / first setup | 5-step progress | one next action; not a wizard |
| Home / normal | line + stacked bar + mini bars | exactly the three canonical summaries |
| New Test | numbered sections | no flowchart or wizard |
| Tests list | table | no dashboard charts |
| Test detail | metrics + confusion matrix | one evaluation surface only |
| Ground Truth | compact stacked distribution | counts/table remain primary |
| Publish Version dialog | provenance stacked bar + counts | show source composition |
| Production Review | side-by-side diff + readiness checklist | no historical trend chart |
| Runtime | real time-series only | Unknown if unavailable |
| Operations > Inference History | Production-only table | Test browsing belongs to Evaluation > Tests |
| Activity | chronological list | no chart |
| Initial vs Deep | two-row comparison | no chart |

Do not add visualizations merely to fill empty space.
