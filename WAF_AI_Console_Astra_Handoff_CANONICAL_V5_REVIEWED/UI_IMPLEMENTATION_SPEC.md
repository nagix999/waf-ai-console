# WAF AI Console — UI Implementation Spec V5

Canonical IA:

```text
Overview
Configure
Evaluate
Promote
Operate
Connect
```

Visual rule:

> Overview만 dashboard. 나머지는 task/result-oriented composition.

---

# 1. Shell

```text
Sidebar 224px
Header 58px
Content 28~36px
```

Sidebar child menu 없음. Workspace Tabs는 content 상단 underline.

Header:

```text
Global Search | Production Health | KR/EN | Theme ▾ | Principal
```

---


# 1.1 V5 Composition Rules

- Page body는 `Section Surface` 단위로 읽혀야 한다.
- Table은 반드시 outer border + header surface + row separators를 가진다.
- Registry 페이지에서는 Table이 가장 큰 시각적 면적을 가져야 한다.
- Workbench 페이지에서는 list/detail divider가 주요 구조가 된다.
- Versioned editor는 version rail과 detail/editor를 하나의 bounded workspace로 묶는다.
- 일반 Primary는 neutral dark, Production Promote만 brand mutation color를 사용한다.
- Page header action은 1~2개만 직접 노출하고 나머지는 overflow로 이동한다.
- Helper text와 primary data 사이의 contrast를 명확히 한다.

권장 control:

```text
Button/Input/Select height  38px
Button radius                9px
Table header                 42px
Table row                    50px
Section radius               14px
Section padding              20~24px
Section gap                  22~28px
```

# 1.2 Action semantics

```text
Neutral Primary = ordinary create/save/run actions
Brand Mutation  = Promote to Production only
Danger          = delete/revoke/destructive actions
```

Brand Mutation is reserved for Production promotion and MUST NOT be reused for normal Save/Add.

---

# 2. Overview

```text
Production Health + Configuration ID
Runtime metric strip

Official Production Evaluation   | Attention
Current Configuration            | Connections
Recent Activity
```

동일 크기 Card grid 금지.

---

# 3. Configure

Tabs:

```text
LLM Profiles | Agent Roles | Analysis Instructions
```

## LLM Profiles

```text
Search / Provider / Validation                         + Add profile
12 profiles · 9 validated · 2 failed

Full-width registry table
Profile / Provider-Model / Context / Validation / Assigned Role / Last Check / More
```

## Agent Roles

2-column composition:

```text
Production current roles     | Test defaults
Read-only                    | Editable
```

## Analysis Instructions

```text
Version rail 245~280px | document/editor
```

---

# 4. Evaluate

Tabs:

```text
Tests | Ground Truth | Production Evaluation
```

## Tests

```text
Search / filters                                               + New Test
compact status summary
Test Run table
```

No KPI cards.

## Ground Truth

```text
Dataset / Revision / summary / + New Case
Status tabs / filters
Case table 65~70% | selected Case detail 30~35%
```

## Production Evaluation

```text
Official context
5 metrics in one horizontal strip
Category results | Evaluation history
```

---

# 5. Promote

```text
Candidate identity
Current Production → Candidate
Configuration diff inline
Official Evaluation comparison
Pre-flight two-column list
Promote CTA bottom-right
```

Schema change acknowledgement near Pre-flight, not hidden in modal only.

---

# 6. Operate

Tabs:

```text
Runtime | Inference History | Activity
```

## Runtime

```text
Service state strip
Request/latency/failure metric strip
Recent failures table | Capacity
```

No fake health.

## Inference History

```text
scope/search/filter toolbar
compact result count
full-width table
pagination
```

No dashboard charts.

## Activity

```text
filters
Timeline/List 65~70% | Selected change detail 30~35%
```

---

# 7. Inference Detail

```text
compact execution summary
Result | Agent Trace | Input | Result JSON | Report
```

Result:

```text
analysis/evidence 65~70% | reference/event 30~35%
```

Agent Trace:

```text
run selector
step rail 260~300px | detail
```

Input:

```text
raw content 65~70% | schema/decoding metadata
```

Result JSON:

```text
tree/json/search/wrap/copy
```

Report:

```text
report controls
reading paper max-width ~900px
```

---

# 8. Connect

Tabs:

```text
Production API | API Keys | Input Schema | vLLM Targets
```

## Production API

```text
TOC 210~230px | document
```

## API Keys

```text
filters                                                + Issue Key
security note
credential table/list
```

## Input Schema

```text
Version rail | field definitions
Validate / Compare / More
```

No Production Apply.

## vLLM Targets

```text
Search/Usage                                           + Add target
endpoint registry table
```

---

# 9. Theme

Default:

```js
const saved = localStorage.getItem("waf-console-theme");
const theme = ["sk","light","dark"].includes(saved) ? saved : "sk";
```

Light uses softer Mint/Emerald tokens from `UI_DESIGN_GUIDELINES.md`.

---

# 10. Responsive

```text
>= 1180 desktop full
700~1179 compact shell
<700 mobile-safe
```

- Workspace tabs horizontal scroll
- tables horizontal scroll
- split panes stack
- forms one column

---

# 11. Mockup Rule

`ui_mockups/canonical_v5/`가 Visual reference.

문서와 이미지 충돌 시:

```text
Handoff/Backend Contract > UI Spec > Design Guidelines > Mockup
```

# Visual Implementation Gate

화면을 한꺼번에 1:1 변환하지 않는다.

## Stage A — Design primitives

먼저 구현:

- theme tokens
- Button variants
- Input / Select
- Tabs
- Table
- Section Header
- Surface / Inset
- Status / Badge
- Dialog / Drawer
- Sidebar / Utility Header

## Stage B — Calibration screens

다음 순서로 실제 React 화면을 완성한다.

1. Overview
2. Ground Truth
3. Promote

각 화면은 1440×900 기준으로 Browser Screenshot을 남긴다.

Review 기준:

- 목업 구조를 따르는가
- 실제 콘텐츠에서 hierarchy가 유지되는가
- Table과 일반 text surface가 명확히 구분되는가
- Primary / Secondary / Ghost / Brand Mutation / Danger가 즉시 구분되는가
- 과도한 border/card가 다시 생기지 않았는가
- 빈 공간이 단순 낭비가 아닌 hierarchy로 작동하는가
- KR/EN 전환 후 layout이 유지되는가
- SK / Light / Dark 모두 같은 component system을 사용하는가

대표 3화면의 Browser Screenshot self-review와 공통 component 수정이 끝나면 나머지 Workspace로 계속 확장한다. 사용자가 단계별 승인을 명시하지 않았다면 중간 승인 대기를 위해 작업을 중단하지 않는다.

## Mockup precedence

목업은 page composition의 기준이지만 최종 pixel specification은 아니다.
실제 browser에서 typography rendering, content length, native control behavior, focus state 때문에 더 나은 배치가 필요하면 `UI_DESIGN_GUIDELINES.md` 범위 안에서 조정한다.

단, 다음을 변경해서는 안 된다.

- 기능 삭제
- 데이터 의미 변경
- Workflow 변경
- Production mutation 경로 변경
- Ground Truth semantics 변경
- Backend contract 변경
