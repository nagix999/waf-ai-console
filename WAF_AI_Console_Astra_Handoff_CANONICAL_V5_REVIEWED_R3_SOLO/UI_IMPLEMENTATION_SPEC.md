# WAF AI Console — UI Implementation Spec V5 REVIEWED R3 SOLO

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

# 1.1 Composition Rules

- Page body는 Section Surface 단위로 읽혀야 한다.
- Table은 outer border + header surface + row separators.
- Registry에서는 Table이 가장 큰 시각적 면적.
- Workbench에서는 list/detail divider가 주요 구조.
- Versioned editor는 version rail + detail/editor.
- 일반 Primary = neutral dark.
- `Promote to Production`만 Brand Mutation.
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

```text
Production Health + Configuration ID
Runtime metric strip

Comparable Production Evaluation Trend | Ground Truth Working Distribution
Recent Comparable Tests
Action Center
Current Configuration | Recent Activity
```

## 2.1 Comparable Production Evaluation Trend

- Accuracy / F1 / Coverage
- same comparison_key만 연결
- x-axis = Production Configuration / evaluated_at
- y-axis default = 0–100%
- 다른 GT revision을 하나의 line으로 연결 금지
- comparable point < 2이면 chart 대신 Current Official Evaluation metrics + `비교 기준이 변경되어 이전 결과와 직접 비교하지 않습니다.`

## 2.2 Ground Truth Distribution

```text
Ready | Needs attention | Excluded
```

Latest official Production Evaluation이 사용한 Dataset의 Working Draft만 표시.

## 2.3 Recent Comparable Tests

```text
Test / F1 mini bar / Accuracy / Coverage
```

same comparison_key의 최근 Test만 비교.

## 2.4 Action Center

```text
Ground Truth issues
Failed Test
Inference failures
Promotion-ready Candidate
```

Candidate는 warning/error가 아니라 positive action item으로 표시.

---

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

### Test Detail Mode

Test row 클릭 → 다른 hierarchy가 아니라 같은 `Evaluate / Tests` workspace의 detail mode.

```text
← All tests
Test identity + Candidate Configuration + GT context
metric strip
case filters
case result table
```

### Case Drawer

Case row 클릭 → right Drawer.

```text
Result
Evidence
HTTP/Input summary
Agent Trace summary
⛶ 크게 보기
```

`크게 보기` → Full Inference Detail:

```text
Result | Agent Trace | Input | Result JSON | Report
```

돌아오면 반드시 복원:

```text
selected Test / Case
filters / search
scroll position
evaluation metric expanded state
```

### Candidate Promotion Entry

Eligible Candidate인 경우 Test Detail에서 `Promote to Production` CTA를 노출한다.

```text
#promotion/{test_run_uuid}
```

로 contextual full page 진입.

## 3.2 Ground Truth

```text
Dataset selector
Published Revision + Working Changes
Ready / Needs attention / Excluded
filters + bulk actions
Case table 60~65% | selected Case 35~40%
Publish Revision
```

### Working Draft

- Draft/Reviewed/Approved UI 없음
- item save마다 immutable revision 생성 없음
- automatic validation = Ready / Needs attention
- user exclusion = Excluded

### Working Changes

```text
Added / Changed / Removed
```

- changes filter
- changed Case → `Revert to published version`
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
LLM Profiles | Agent Roles | Analysis Instructions
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
Production current roles | Test defaults
Read-only                | Editable
```

Production Primary / Verifier / Evidence는 direct mutation 금지.
Concurrency는 여기서 빼고 Operate > Runtime.

## 4.3 Analysis Instructions

```text
Version rail 245~280px | selected version/editor
```

New Version / Compare / Working Draft. Production direct Apply 없음.

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

```text
scope/search/filter toolbar
result count
full-width table
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

# 6. Contextual Promotion Full Page

Sidebar/Workspace Tab에는 없다.

Entry:

```text
Eligible Test detail
Overview Action Center Candidate
```

Layout:

```text
← Back to Test
Candidate identity
Current Production → Candidate
Configuration diff
Official Published GT Evaluation
Pre-flight
Promote to Production
```

- actual CTA만 Brand Mutation
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
Production API | API Keys | Input Schema | vLLM Targets
```

## Production API
TOC + document + search + code copy + Swagger + OpenAPI JSON + PDF + schema metadata.

## API Keys
Issue / purpose / source_system / scopes / one-time raw key / rename / delete / last used.

## Input Schema
Version rail + field definitions + Validate / Compare / More. Production Apply/Rollback 없음.

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
1. Overview
2. Ground Truth
3. Contextual Promotion
```

1440×900 Browser Screenshot self-review 후 공통 component/token 문제를 먼저 수정하고 전체 화면으로 확장한다. 사용자가 단계 승인을 요청하지 않았다면 중간에 멈추지 않는다.

R3 mockup은 pixel specification이 아니다. IA / Page Archetype / 정보 우선순위를 보존하면서 실제 React에서 더 높은 완성도로 polish한다.
