# WAF AI Console — UI Design Guidelines V5

V5 visual thesis:

> **Refined engineering console: clear boundaries, dense data, restrained brand accent.**

목표는 "예쁜 관리자 화면"이 아니라 **빠르게 스캔하고, 비교하고, 조사할 수 있는 engineering console**이다.

---

# 1. Hard Rules

1. Overview만 Dashboard.
2. Workspace마다 목적에 맞는 page archetype 사용.
3. 모든 section을 Card로 감싸지 않음.
4. KPI Card + Sparkline을 기본 템플릿으로 사용하지 않음.
5. Border는 경계가 실제로 필요한 곳에만.
6. 같은 디자인 시스템 ≠ 같은 page composition.
7. Fake health/resource/cost/notification 금지.
8. Primary Action은 작업 영역당 원칙적으로 하나.
9. Danger action은 명확히 분리.
10. 실제 기능·field·state는 Repository/Backend Contract가 source of truth.

---

# 1.1 V5 Component Hierarchy

V4에서 border를 너무 제거해 section/table/text가 평평해지는 문제가 있었다. V5는 **필요한 경계를 다시 도입하되 card wall로 돌아가지 않는다.**

## Surface levels

```text
Level 0  Page background
Level 1  Section Surface — 독립 업무 영역
Level 2  Data Surface — table/editor/code/split pane
Level 3  Inset Surface — helper/metadata/selected state
```

### Section Surface

- background: panel
- 1px subtle border
- radius: 13~14px
- padding: 20~24px
- 아주 약한 shadow 허용
- section gap: 22~28px
- section header 아래 divider 사용

모든 작은 항목을 각각 Card로 만들지 않는다. 한 업무 영역 전체를 하나의 Section Surface로 묶는다.

## Tables

Table은 일반 텍스트와 즉시 구분되어야 한다.

```text
outer border     1px
radius           10~12px
header height    40~42px
row height       48~52px
header bg        subtle gray / mint
row divider      visible but quiet
hover            pale mint / neutral
selected         pale mint + left indicator
```

Table header 10.5~11.5px / 700, body 12~13px. ID/hash/version에는 monospace를 적극 사용한다.

## Buttons

### Neutral Primary
일반 작업의 기본 Primary.

예:

```text
+ Add profile
Save defaults
New test
Issue key
Add target
```

SK/Light에서 Charcoal/Navy 또는 짙은 neutral을 사용한다.

### Brand Mutation
SK Red는 높은 중요도의 상태 전환에만 사용한다.

```text
Promote to Production
```

일반 Save/Add에 SK Red를 사용하지 않는다.

### Secondary
soft gray/neutral surface + subtle border. 흰색 border button을 반복하지 않는다.

### Ghost / Icon
Refresh, Back, Reset, Copy, More 등에 사용. 36~38px control height.

### Danger
삭제/파괴적 작업만 danger semantic color. Brand Red와 Danger Red는 의미를 분리한다.

## Tabs

- Workspace tabs: underline pattern
- Scope/status switching: segmented control
- 같은 화면에서 두 패턴을 임의로 섞지 않는다.
- active underline 2~3px, label 12~13px/650

## Split panes

Ground Truth, Agent Trace, Version Editor처럼 list/detail 관계가 핵심이면 `두 개의 카드`가 아니라 **하나의 bounded workspace + 내부 divider**로 표현한다.

## Section headers

Section header는 다음을 명확히 나눈다.

```text
Title
Short context/help
Right-side action/status
Divider
Content
```

일반 본문과 section title 사이에 충분한 간격을 둔다.

# 2. App Shell

Desktop target:

```text
1440×900
1536×1024
1600×1000
```

권장:

```text
Sidebar      216~228px
Top utility   56~60px
Content pad   28~36px
Max content  1560px
```

Sidebar에는 top-level 6개만:

```text
Overview
Configure
Evaluate
Promote
Operate
Connect
```

Active:

- soft neutral background
- 2~3px brand line
- text/icon strong
- 큰 filled pill 금지

Header:

```text
Global Search       Production Health   KR|EN   Theme   Admin
```

---

# 3. Theme Tokens

## 3.1 SK — Default

```css
--bg: #F7F8FA;
--sidebar: #FBFBFC;
--panel: #FFFFFF;
--raised: #F5F6F8;
--text: #151B2B;
--muted: #6F7888;
--line: #E7E9ED;
--line-strong: #D9DDE3;

--brand: #E63338;
--brand-alt: #F06A32;
--brand-soft: #FFF2F0;

--green: #5FB596;
--green-strong: #2E8166;
--green-soft: #EEF8F4;
--green-pale: #F7FBF9;
```

SK Red/Orange 사용:

```text
brand mark
active workspace line
primary mutation/action
critical promotion CTA
```

사용하지 않음:

```text
healthy status
success badge
large page background
all buttons
```

## 3.2 Light — Softer Green

사용자가 요청한 최종 Light 방향:

```css
--bg: #F7F9FA;
--sidebar: #FBFDFC;
--panel: #FFFFFF;
--raised: #F7FAF9;
--text: #172033;
--muted: #6C7A89;
--line: #E4ECE8;
--line-strong: #D7E4DE;

--accent: #7CCFB1;
--accent-strong: #3F8F73;
--accent-soft: #EFF9F5;
--accent-pale: #F7FCFA;
```

규칙:

- 진한 Green 면적 최소화
- active/select surface는 `#F7FCFA` / `#EFF9F5`
- medium Green은 Primary CTA/important text에만
- 화면 전체가 초록색으로 보이면 실패

## 3.3 Dark

```css
--bg: #0C141C;
--sidebar: #0A1219;
--panel: #111C25;
--raised: #17232D;
--text: #E8EEF4;
--muted: #9CABBA;
--line: #263642;
--line-strong: #354956;
--accent: #43C995;
--accent-soft: #143B2F;
```

---

# 4. Typography

```text
Page title      26~28 / 700
Section title   15~16 / 650
Body            12~14 / 400~500
Table body      12~13
Table header    10~11 / 620
Helper/meta     10~11
Metric          22~30 / 650
```

금지:

- 8px UI text
- 모든 label uppercase
- 과도한 bold

ID/hash/code는 monospace 허용.

---

# 5. Spacing / Divider

기본 scale:

```text
4  8  12  16  20  24  32  40
```

V5에서는 `section divider`가 주요 구조 도구다.

```text
Section
────────────────────
content

Section
────────────────────
content
```

Section마다 Card box를 만들지 않는다.

---

# 6. Buttons

의미 기준 6종으로 고정한다.

```text
Neutral Primary
Brand Mutation
Secondary
Ghost
Icon
Danger
```

- **Neutral Primary**: Add / Save / New Test / Issue Key / Add Target. SK·Light에서는 charcoal/navy 계열을 기본으로 한다.
- **Brand Mutation**: `Promote to Production` 전용 SK brand red. 일반 Save/Add에 사용하지 않는다.
- **Secondary**: 보조 작업. soft neutral surface + subtle border.
- **Ghost**: Back / Reset / Refresh / inline navigation.
- **Icon**: Copy / More / Close 등.
- **Danger**: Delete/revoke 등 파괴적 action. Brand Red와 다른 semantic danger token을 사용한다.

Primary 계열(Neutral Primary + Brand Mutation)은 한 action area에 원칙적으로 하나만 노출한다.
Visible action이 2~3개를 넘으면 낮은 우선순위를 `⋯` overflow로 이동한다.

---

# 7. Page Archetypes

## 7.1 Overview — Narrative Dashboard

작은 동일 Card를 여러 개 나열하지 않는다.

읽는 흐름:

```text
Production State
Runtime metrics strip
Evaluation + Attention
Current Configuration
Recent Activity
Connections
```

한 페이지가 하나의 이야기처럼 읽혀야 한다.

## 7.2 Registry / Resource List

대상:

```text
LLM Profiles
Tests
Inference History
API Keys
vLLM Targets
```

구조:

```text
Header + Primary Action
Search / Filter
Compact summary
Full-width table
Detail/action
Pagination
```

대형 KPI card 금지.

## 7.3 Workbench

대상:

```text
Ground Truth
```

구조:

```text
Dataset/Revision controls
Status scope
Case table | Case detail
```

사용자가 목록과 상세를 왕복하지 않고 검토할 수 있게 split pane 허용.

## 7.4 Versioned Editor

대상:

```text
Analysis Instructions
Input Schema
```

구조:

```text
Version rail | Selected version/editor
```

## 7.5 Comparison / Release Gate

대상:

```text
Promote
```

구조 자체가 비교여야 한다.

```text
Current → Candidate
Diff
Evaluation
Pre-flight
Mutation CTA
```

## 7.6 Investigation

대상:

```text
Runtime
Inference Detail
Activity
```

- 넓은 data region
- failure/step/time 중심
- tiny dashboard card 반복 금지

## 7.7 Documentation

대상:

```text
Production API
```

TOC + document body + code.

---

# 8. Tables

Table은 핵심 UI다.

```text
row 44~50px
header 10~11px
body 12~13px
```

- Header background는 매우 연하게
- row border는 1px divider
- hover는 subtle raised
- row action 0~1 visible + overflow
- sticky header 유지
- horizontal scroll 허용

Table 자체를 큰 Card로 한 번 더 감싸지 않아도 된다.

---

# 9. Metrics

Metric이 핵심 업무인 경우만 크게 보여준다.

허용:

```text
Overview runtime strip
Production Evaluation
Promote evaluation comparison
Runtime performance
```

금지:

```text
LLM Profiles count cards
Tests count cards
Ground Truth count cards
API Keys count cards
```

Count는 compact inline summary로.

---

# 10. Tabs

Workspace Tab:

- underline pattern
- box/pill tab 금지
- active brand line

Segmented Control:

- 좁은 scope (`All / Production / Test`, review status)
- light raised background

두 패턴 외 임의 Tab style 추가 금지.

---

# 11. Forms

Configuration screen에서는 label/value row를 적극 사용한다.

```text
Label              Control / Value
──────────────────────────────────
```

2-column을 기본으로 하되 대형 form을 4-column grid로 쪼개지 않는다.

Production read-only와 Test editable을 시각적으로 구분한다.

---

# 12. Status / Badge

Color badge는 의미 상태에만.

```text
Validated
Running
Failed
Approved
Warning
Healthy
```

Neutral metadata는 text 또는 neutral pill.

색만으로 상태 전달 금지.

---

# 13. Dialog / Drawer

Dialog:

```text
Title                         ×
───────────────────────────────
Body
───────────────────────────────
                     Cancel Action
```

- Header close = icon
- nested panel 금지
- destructive action = Danger

Ground Truth Case는 desktop에서 right detail pane를 우선하고 필요할 때 Drawer.

---

# 14. Inference Detail

공통 summary는 compact하게 한 번만.

```text
Analysis ID
Verdict
Severity
Total elapsed
Purpose
```

Tabs:

```text
Result
Agent Trace
Input
Result JSON
Report
```

각 Tab은 서로 다른 page composition 허용.

Agent Trace:

```text
Run selector
Step rail | Step detail
Output / Input / Metadata
```

Input:

```text
Raw / Extra Fields / Decoding
Search / Wrap / Copy
```

Report는 reading document처럼 보여야 한다.

---


## Locale / Theme control

Header control은 다음으로 고정한다.

```text
KR | EN        Theme ▾        Principal
```

- Locale은 2-item segmented control
- Theme은 하나의 button + accessible popover/menu (`SK`, `Light`, `Dark`)
- theme 3개를 Header에 inline button 3개로 상시 표시하지 않음
- `waf-console-theme`: `sk|light|dark`, missing → `sk`
- `waf-console-locale`: `ko|en`, missing → `ko`
- 전환 시 route/tab/filter/form/selection 유지

# 15. Theme Selector

Header의 단일 `Theme` button을 누르면 accessible popover/menu로 `SK / Light / Dark`를 선택한다.

Theme 전환 시 route/tab/filter/form state를 초기화하지 않는다.

---

# 16. Accessibility

필수:

```text
focus visible
keyboard navigation
aria-current
aria-selected
aria-expanded
focus trap
reduced motion
```

최소 contrast 확보.
Semantic state를 색만으로 전달하지 않는다.

---

# 17. Don't

- 모든 화면을 Card Grid로 만들기
- 모든 Section에 icon box 넣기
- LLM/Test/GT에 KPI 카드 만들기
- SK Red를 success/healthy에 사용
- Light를 진한 green UI로 만들기
- fake CPU/GPU/cost/notification
- 생성형 이미지의 임의 field/action 구현
- Production direct Apply shortcut
- second persistent sidebar

# Production UI Polish Rule

정적 목업은 interaction이 없는 구조 참고물이다. 실제 UI 품질은 브라우저 구현에서 확정한다.

## 목업 사용 원칙

목업에서 그대로 보존:

- IA
- Page Archetype
- 기능의 존재 여부
- 주요 정보의 상대적 우선순위
- List / Split View / Version Rail / Comparison / Documentation 등의 구조

목업에서 그대로 복제하지 않음:

- 픽셀 위치
- 고정 폭
- 정적 button shape의 미세 값
- 샘플 문자열 길이에 맞춘 임의 여백
- 샘플 데이터에 종속된 row/column 폭

## Browser-first visual review

다음 3개 화면을 Design System calibration screen으로 사용한다.

### Overview
- 전체 제품의 첫 인상
- Section hierarchy
- 상태/평가/Attention/Activity의 우선순위
- SK / Light / Dark theme balance

### Ground Truth
- Dense table 가독성
- Split pane 경계
- Filter / status / selection
- Drawer/detail hierarchy

### Promote
- Current vs Candidate 비교
- Diff readability
- Preflight hierarchy
- 위험 Action의 시각적 중요도

세 화면의 Browser Screenshot에 대해 checklist 기반 self-review를 먼저 완료하고 공통 token/component 문제를 수정한 뒤 나머지 페이지를 polish한다. 사용자가 명시적으로 단계별 승인을 요청하지 않았다면 human approval을 기다리며 중단하지 않는다.

## Component-first correction

시각 문제가 발견되면 페이지별 CSS override보다 다음 순서로 수정한다.

1. Token
2. Primitive component
3. Composite pattern
4. Page-specific layout

## Interaction states are part of the design

정적 PNG에 없는 다음 상태를 반드시 실제 구현에서 설계한다.

- hover
- pressed
- focus-visible
- selected
- disabled
- loading
- empty
- error
- destructive confirmation

정적인 모양이 아니라 **실제 상호작용까지 포함한 UI 품질**이 최종 기준이다.
