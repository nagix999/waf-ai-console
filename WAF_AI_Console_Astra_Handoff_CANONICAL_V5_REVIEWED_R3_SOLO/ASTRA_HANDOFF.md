# WAF AI Console — Astra Canonical Handoff V5 REVIEWED R3 — Solo Operations

- Repository: `nagix999/waf-ai-console`
- 기준 브랜치: `main`
- 대상 사용자: WAF AI 추론 시스템 개발·검증·승격·운영·디버깅 엔지니어
- 상태: **Canonical / Source of Truth**
- 이 문서는 현재 구현을 최종 Lifecycle IA와 Visual System으로 전환하기 위한 **독립적인 최종 기준**이다.
- 과거 V1~V4 문서·목업·생성형 이미지는 구현 근거가 아니다. 이 패키지에는 canonical V5 목업만 포함한다.

---


# 0.1 V5 Visual Lock

V5는 IA와 Backend 계약을 바꾸지 않고 **visual hierarchy와 component language를 고정**한다.

핵심 원칙:

```text
Overview        = narrative dashboard
Registry        = filter + explicit data table
Workbench       = list/detail split pane
Versioned data  = version rail + detail/editor
Promote         = staged comparison + preflight
Runtime         = operational status + failure investigation + capacity
Activity        = chronological change history + selected detail
Documentation   = TOC + readable document body
```

V5에서 금지:

```text
모든 페이지에 동일한 Card grid
본문과 Table이 시각적으로 같은 레벨
모든 Primary action을 SK Red로 표시
Border button 남발
Section title만 굵게 쓰고 경계를 없애는 평평한 화면
```

V5 component hierarchy:

```text
Page background
  └─ Section Surface
      ├─ Data Table / Editor / Code Surface
      └─ Inset / Metadata Surface
```

일반 Primary action은 Neutral Charcoal/Navy를 사용하고, SK Red는 `Promote` 같은 중요한 mutation과 brand identity에 제한한다.


# 0.2 Reviewed Final — No-discretion decisions

Astra가 임의로 선택하지 않고 그대로 따라야 하는 결정:

```text
Top-level IA            Overview / Evaluate / Configure / Operate / Connect
Operate tabs            Runtime / Inference History / Activity
Default theme           SK
Selectable themes       SK / Light / Dark
Default locale          ko
Locale options          ko / en
Normal Primary          neutral dark
Brand Mutation          Promote to Production only
Production mutation     Promotion transaction only
New Test UI             explicit Candidate Configuration always
Ground Truth lifecycle  Working Draft → Publish Revision
Official evaluation     Published Ground Truth Revision only
Activity source         immutable ChangeEvent
Deployment              read-only
Concurrency             Runtime setting; not Promotion snapshot
Router                   existing custom hash/browserHistory; no new router
```

`권장/가능`으로 해석해 다른 정책을 선택하지 않는다. 구현 세부만 코드 구조에 맞게 조정한다.

---

# 0. Product Direction

이 콘솔은 SOC/관제 콘솔이 아니다. 주 사용자는 **혼자 WAF AI inference system을 개발·평가·구성·운영하는 엔지니어**다.

Sidebar는 개발 lifecycle을 그대로 복사하지 않고 **실제 작업 빈도와 인지 흐름**을 기준으로 한다.

```text
Overview     현재 상태와 다음 행동 확인
Evaluate     Test / Ground Truth / Production Evaluation
Configure    모델 / Agent / Analysis Instructions
Operate      Runtime / Inference investigation / Activity
Connect      Production API / API Keys / Input Schema / vLLM Targets
```

Canonical sidebar order:

```text
Overview → Evaluate → Configure → Operate → Connect
```

이 순서를 채택하는 이유:

- 이 제품의 핵심 반복 작업은 Candidate Test와 Ground Truth 평가다.
- Overview가 이미 Runtime 상태를 요약하므로 Operate를 두 번째에 둘 필요가 없다.
- Operate를 너무 앞에 두면 제품이 관제/SOC 콘솔처럼 보일 수 있다.
- Configure는 Evaluate 결과를 보고 조정하는 작업공간이므로 Evaluate 바로 뒤에 둔다.
- Connect는 상대적으로 드문 시스템 경계 설정이라 마지막에 둔다.

개발 lifecycle 자체는 여전히:

```text
Configure → Evaluate → Promotion
```

이며 Sidebar 순서와 별개다.

1인 운영 원칙:

- Reviewer / Approver / 담당자 workflow를 만들지 않는다.
- 문항별 수동 승인 절차를 기본으로 하지 않는다.
- 저장할 때마다 revision을 만들지 않는다.
- 문제가 있는 항목만 사람이 확인한다.
- Production mutation safety / immutable published snapshot / audit은 유지한다.

# 1. Canonical Navigation

Sidebar는 정확히 다음 5개 top-level만 사용한다.

```text
Overview
Evaluate
Configure
Operate
Connect
```

Workspace Tabs:

```text
Evaluate
  Tests
  Ground Truth
  Production Evaluation

Configure
  LLM Profiles
  Agent Roles
  Analysis Instructions

Operate
  Runtime
  Inference History
  Activity

Connect
  Production API
  API Keys
  Input Schema
  vLLM Targets
```

## Promotion navigation

Promotion은 Sidebar 항목도 아니고 Operate의 상시 Tab도 아니다.

승격 가능한 Candidate가 존재하는 **맥락 안에서만** 진입한다.

```text
Evaluate > Tests > eligible Candidate
  → Promote to Production
  → contextual full-page Promotion workflow

Overview > Action Center > Candidate ready
  → same Promotion workflow
```

Promotion workflow는 full-page task이며 Sidebar에서 어떤 Workspace가 active인지 강조하지 않아도 된다. 상단 Back link는 원래 Test로 돌아간다.

Promotion History는 `Operate > Activity`의 Promotion filter에서 본다.

Drilldown 원칙:

```text
Tests
  Test = same workspace detail mode
  Case = Drawer
  Case full investigation = full detail page

Ground Truth
  Dataset = selector
  Case = split-pane selection

Inference Detail = full page
Activity Detail = split-pane selection
Promotion = contextual full page
```

# 2. V5 Visual Direction

V5의 시각 목표:

> **clean engineering shell + dense operational data**

과거의 `card grid / admin template`형 시각 문법을 사용하지 않는다.

## 2.1 Visual principles

1. **Overview만 dashboard composition을 허용**한다.
2. 다른 Workspace는 목적에 맞는 Registry / Workbench / Editor / Comparison / Investigation / Documentation layout을 사용한다.
3. 모든 section을 Card로 감싸지 않는다.
4. Border box보다 **spacing, typography, divider, alignment**로 구조를 만든다.
5. 넓은 data region을 우선하고 작은 box 반복을 피한다.
6. KPI card + sparkline을 default pattern으로 사용하지 않는다.
7. Primary CTA는 작업 영역당 원칙적으로 하나다.
8. SK Red/Orange는 브랜드/핵심 Action에만 제한한다.
9. Healthy/Success는 Green semantic color다.
10. 화면이 비어 보인다고 fake KPI, CPU/GPU, 비용, notification을 추가하지 않는다.

## 2.2 Surface hierarchy

```text
Canvas
  ↓
Section / data region     ← default
  ↓
Raised surface            ← selected/filter/editor support
  ↓
Popover / Dialog          ← overlay only
```

`Card inside Card inside Card` 금지.

---

# 3. Themes

정확히 3개:

```text
SK     ← default
Light
Dark
```

저장:

```text
waf-console-theme = sk | light | dark
```

저장값이 없으면 **SK**.

## 3.1 SK default

- neutral white/light gray canvas
- SK-inspired Red = brand mark, active workspace indicator, `Promote to Production` 같은 high-consequence brand mutation
- SK Orange = 작은 보조 brand accent에만 사용; 일반 버튼·Success·Warning 의미색으로 재사용하지 않음
- Soft Green = Healthy/Success/supportive selection
- full red surfaces 금지
- SK shieldus logo / building / corporate photo 금지

핵심 token:

```css
--bg: #F7F8FA;
--panel: #FFFFFF;
--text: #151B2B;
--muted: #6F7888;
--line: #E7E9ED;
--brand: #E63338;
--brand-alt: #F06A32;
--green: #5FB596;
--green-soft: #EEF8F4;
```

## 3.2 Light — softer Mint/Emerald

Light는 **진한 green 면적을 줄인다**.

```css
--bg: #F7F9FA;
--panel: #FFFFFF;
--text: #172033;
--muted: #6C7A89;
--line: #E4ECE8;

--accent: #7CCFB1;
--accent-strong: #3F8F73;
--accent-soft: #EFF9F5;
--accent-pale: #F7FCFA;
```

- active background는 거의 white에 가까운 mint
- Light의 일반 Primary도 neutral dark를 기본으로 하고 Green은 active/select/status/text accent에 사용
- Green filled button은 해당 action의 의미상 필요할 때만 제한적으로 사용

## 3.3 Dark

```css
--bg: #0C141C;
--sidebar: #0A1219;
--panel: #111C25;
--raised: #17232D;
--text: #E8EEF4;
--muted: #9CABBA;
--line: #263642;
--accent: #43C995;
```

Dark 기본 accent는 Green. SK Red를 Dark 전체 accent로 쓰지 않는다.

---

# 3.4 Login

로그인 화면은 기능을 과장하지 않는다.

실제 인증 계약:

```text
username
password
→ admin session
```

UI:

```text
WAF AI Console brand
아이디
비밀번호
로그인
Theme selector
```

금지:

- SSO / OTP / Social Login을 임의로 추가
- Configure/Evaluate/Promote workflow 설명
- 홍보성 hero copy / illustration
- 인증되지 않은 기능/상태 정보 노출

로그인 실패는 credentials 존재 여부를 과도하게 노출하지 않는 일반 오류로 처리한다.

---

# 4. Overview

Overview만 Dashboard다.

읽는 순서:

```text
Production state / Runtime summary
→ Comparable Production Evaluation Trend
→ Ground Truth Working Draft distribution
→ Recent Comparable Tests
→ Action Center
→ Current Configuration
→ Recent Activity
```

편집 Form을 넣지 않는다. 각 block은 상세 Workspace 또는 contextual workflow로 drilldown한다.

Health는 실제 heartbeat/readiness가 없으면 `Unknown`.

Production Evaluation은 live production accuracy가 아니라 **Published Ground Truth Revision 기반 공식 evaluation snapshot**이다.

## 4.1 Comparable Production Evaluation Trend

- Accuracy / F1 / Coverage를 기본 series로 사용한다.
- 같은 Ground Truth dataset + 같은 published revision + 같은 scope/filter + 같은 metrics_version만 한 line으로 연결한다.
- X축은 Ground Truth revision이 아니라 `Production Configuration / Evaluated At`이다.
- 기본 Y축은 0–100%다. 좁은 확대축으로 차이를 과장하지 않는다.
- 비교 가능한 point가 2개 이상일 때만 trend line을 그린다.

비교 가능한 point가 2개 미만이면 빈 chart를 만들지 않는다. 다음 fallback을 사용한다.

```text
Current Official Evaluation
Accuracy / Precision / Recall / F1 / Coverage

비교 기준이 변경되어 이전 결과와 직접 비교하지 않습니다.
```

## 4.2 Ground Truth Working Draft Distribution

최신 공식 Production Evaluation이 사용한 Ground Truth dataset의 현재 Working Draft를 요약한다.

```text
Ready | Needs attention | Excluded
```

- horizontal stacked bar
- Ready = green
- Needs attention = amber
- Excluded = neutral gray
- Working changes count + latest Published Revision 표시
- latest Production Evaluation context가 없으면 다른 Dataset을 추측하지 않는다.

## 4.3 Recent Comparable Tests

latest official Production Evaluation과 동일한 comparison context의 최근 Test만 비교한다.

```text
Test name/time
F1 mini bar
Accuracy
Coverage
```

전체 Test 목록을 Overview에 복제하지 않는다.

## 4.4 Action Center

기존 `Needs Attention` 명칭은 사용하지 않는다. 문제가 아닌 action도 있기 때문이다.

```text
Action Center
⚠ Ground Truth cases need attention
⚠ Failed Test
⚠ Inference failures
✓ Candidate ready to promote
```

각 항목은 실제 destination으로 이동한다.

- GT issue → Evaluate > Ground Truth, Needs attention filter
- Failed Test → Evaluate > Tests
- Inference failure → Operate > Inference History
- Candidate ready → contextual Promotion workflow

협업형 reviewer queue / approval request / assigned-to-me는 만들지 않는다.

# 5. Evaluate

## 5.1 Tests — Flat Workspace

Tests는 다음 depth를 만들지 않는다.

```text
Evaluate → Tests → Test → Case
```

사용자가 느끼는 navigation은 항상:

```text
Evaluate / Tests
```

이다.

### Test list

목록에서 evaluation을 바로 비교한다.

Compact default:

```text
TEST | CONFIGURATION | STATUS | EVALUATION | DURATION
```

`EVALUATION`은 기본적으로:

```text
Acc 94.6 · F1 94.1 · Cov 99.1
```

형태다.

Toolbar에 `평가지표 펼치기` disclosure/toggle button을 제공하고 `aria-expanded`로 상태를 표현한다.

Expanded state:

```text
TEST | STATUS | ACC | PREC | RECALL | F1 | COVERAGE | DURATION
```

Evaluation column은 하나의 grouped header로 보이게 한다.

- 평가되지 않은 Test는 `0%`가 아니라 `— / Not evaluated`.
- Ground Truth dataset/revision/sample count를 Test secondary metadata에 표시한다.
- 서로 다른 dataset/revision/scope/metrics_version의 Test는 delta/직접 비교 대상으로 표시하지 않는다.
- 펼침 상태는 URL이 아니라 local UI preference로 저장할 수 있다.

### Test naming

이름 입력은 필수가 아니다.

기본:

```text
Test 2026-09-21 13:42
```

사용자가 원할 때만 rename.

### New Test

```text
Candidate Configuration
  Primary
  Verifier
  Evidence Editor
  Analysis Instructions Version
  Input Schema Version

Single | File | Ground Truth
```

Ground Truth 공식 평가 Test는 Published Revision을 명시적으로 pin한다.

### Test detail mode

Test row 클릭 시 다른 Workspace로 이동하지 않고 같은 Tests page를 detail mode로 전환한다.

```text
← All tests
Test identity + Ground Truth context
metric strip
case result filters
case result table
```

### Case detail

Case row 클릭:

```text
default = right Drawer
```

Drawer에서:

```text
Result
Evidence
HTTP/Input summary
Agent Trace summary
⛶ 크게 보기
```

를 제공한다.

`크게 보기`는 full Inference Detail로 이동하며:

```text
Result | Agent Trace | Input | Result JSON | Report
```

를 모두 사용할 수 있다.

돌아올 때 반드시 복원:

```text
selected Test
selected Case
filters
search
table scroll
evaluation metric expanded state
```

## 5.2 Ground Truth — Solo Dataset Workbench

일반 Q&A가 아니라 WAF Event / HTTP Request case다.

협업 승인 workflow는 사용하지 않는다.

```text
Working Draft
  ↓ import / edit / bulk edit
Automatic validation
  ↓ Ready / Needs attention / Excluded
Publish Revision
  ↓ immutable Published rN
```

Verdict:

```text
true_positive
false_positive
inconclusive
```

Quality state:

- `Ready`: 현재 Working Draft가 자동 validation을 통과
- `Needs attention`: 누락/충돌/구조 오류/invalid verdict 등 사람 확인 필요
- `Excluded`: 사용자가 공식 평가에서 의도적으로 제외

기본 철학은 `모든 문항 검토`가 아니라 `문제가 있는 문항만 검토`다.

### Working Changes

Published Revision 이후 변경을 별도 summary로 제공한다.

```text
Published r19 · 252 working changes
Added 214 · Changed 38 · Removed 11
```

`Working changes`를 열면 변경된 문항만 필터링한다.

각 변경 Case에는 가능한 경우:

```text
Revert to published version
```

을 제공한다.

Dataset 전체에는 destructive action으로:

```text
Discard all working changes
```

를 제공하며 confirmation이 필요하다.

Working edit 저장은 revision을 생성하지 않는다.

### Bulk-first actions

```text
Import
Bulk include/exclude
Bulk tags/category
Bulk delete from Working Draft
Needs attention only
Verdict/source/search filters
```

### Publish Revision

Publish 시점에만 immutable Dataset Revision을 생성한다.

Publish dialog는 **평가 데이터가 얼마나 포함되는지** 반드시 보여준다.

```text
Working cases        2,000
Ready included       1,500
Needs attention        480
Excluded                20
Inclusion rate        75.0%
```

Needs attention이 있다고 Publish 자체를 막지는 않는다. 그러나 1건 이상 제외되면 다음 acknowledgement를 명시적으로 확인한다.

```text
☐ Needs attention / Excluded 항목은 이번 Published Revision에 포함되지 않음을 확인했습니다.
```

이 확인은 문항별 승인 절차가 아니라 Dataset snapshot 전체에 대한 단일 safety check다.

Published Revision metadata에는 최소한 다음을 남긴다.

```text
working_total_at_publish
included_ready_count
needs_attention_count
excluded_count
inclusion_rate
```

Official evaluation은 Published Revision membership만 사용한다.

### Layout

```text
Dataset selector / Published revision / Working changes
Ready | Needs attention | Excluded
filters + bulk actions
Case list 60~65% | selected Case detail 35~40%
Publish revision
```

Dataset과 Case를 별도 page depth로 만들지 않는다.

## 5.3 Production Evaluation — Evaluation Result

Evaluate Workspace Tab.

표시:

```text
Production Configuration ID
Ground Truth Dataset / Published Revision
Evaluation Test
Evaluated At
Sample Count
Accuracy / Precision / Recall / F1 / Coverage
Category results
Comparable evaluation history
```

실시간 Production accuracy로 표현하지 않는다.

Historical delta는 같은 dataset/revision/scope/metrics_version일 때만 계산한다.

# 6. Configure

## 6.1 LLM Profiles — Registry

기존 기능을 보존:

```text
등록
편집
Quick Validation
Full Validation
Enable / Disable
기술정보
Provider / Endpoint / Model / Context / Output limits
OpenAI 외부 전송 승인
```

화면은 `Search/Filter → compact summary → table → detail/action` 구조.
대형 KPI card 없음.

## 6.2 Agent Roles — Configuration Editor

```text
Production current roles  = read-only
Test defaults              = editable
```

Production:

```text
Primary
Verifier
Evidence Editor
```

Test Defaults도 동일.

Production 변경은 Promote에서만.

## 6.3 Analysis Instructions — Versioned Editor

```text
Version rail | selected version document
```

기능:

```text
New Version
Compare
Version Info
```

Production direct Apply 없음.

---

# 7. Operate

Tabs:

```text
Runtime | Inference History | Activity
```

## 7.1 Runtime — Investigation + Capacity

```text
Service / Worker / Queue state
Request rate / latency / failure / retry
Recent failures
Failure step/type
Capacity / Concurrency
Server call limits
```

Concurrency는 Promotion snapshot에 포함하지 않는 Runtime setting.

## 7.2 Inference History — Search/List

목적은 개별 inference를 찾는 것.
기존 검색/고급 필터 기능을 보존한다.
Dashboard chart는 넣지 않는다.

## 7.3 Inference Detail

5개 1급 Tab:

```text
Result
Agent Trace
Input
Result JSON
Report
```

기존 ResultView / AgentHistory / RawEventView / TextInspector / Decoding / JsonTree / AnalysisReport / Retry / PDF / Excel / audit 기능을 보존한다.

## 7.4 Activity — Audit/History

1인 운영이므로 Actor를 주요 컬럼으로 반복하지 않는다.

```text
Time / Type / Change
```

필터:

```text
All / Promotion / Runtime / Configuration / Integration / Deployment
```

Promotion history는 여기서 본다.
Deployment detail은 read-only다.

# 7.5 Contextual Promotion — Production Safety Gate

Promotion은 persistent navigation이 아니다.

진입:

```text
Evaluate > Tests > eligible Candidate
Overview > Action Center > Candidate ready
```

화면:

```text
← Back to Test
Candidate identity
Current Production → Candidate
Configuration diff
Official Published Ground Truth Evaluation
Pre-flight
Promote to Production
```

승격 단위:

```text
Primary
Verifier
Evidence Editor enabled/profile
Analysis Instructions Version
Input Schema Version
```

Concurrency는 제외한다.

Frontend가 Candidate ID들을 다시 조립하지 않는다. Backend가 Test Run의 exact tested snapshot을 사용한다.

Schema change acknowledgement / stale baseline / profile fingerprint / official evaluation / atomic transaction 검사를 모두 유지한다.

성공 후 Promotion record는 `Operate > Activity`에서 조회한다.

---

# 8. Connect

## 8.1 Production API — Documentation

현재 `ProductionApi.jsx` 구조를 최대한 유지:

```text
Search
TOC
Request / Response / Errors / Examples
Code copy
Swagger
OpenAPI JSON
PDF
Current Input Schema metadata
```

## 8.2 API Keys — Credential Registry

현재 실제 기능 유지:

```text
Issue
Production/Test purpose
source_system
scopes
raw key once
copy
rename
delete
last used
technical info
```

## 8.3 Input Schema — Versioned Contract Editor

```text
Version rail
Fields
New Version
Compare
Validate Input
Technical Info
```

현재 코드의 `운영 적용 / 복귀` UI는 제거.
Production 적용은 Promote만.

## 8.4 vLLM Targets — Endpoint Registry

현재 `InternalEgressSettings.jsx` 기능 유지:

```text
Add
Edit
Delete
In-use profiles
Conflict handling
Technical info
```

---

# 10. Production mutation invariant

직접 변경 금지:

```text
Production Primary
Production Verifier
Production Evidence Editor
Production Instructions Version
Production Input Schema Version
```

유일한 경로:

```text
Configure → Evaluate → contextual Promotion workflow
```

`tested configuration = promoted configuration`을 보장한다.

Concurrency만 Runtime에서 별도 관리.

---

# 11. Ground Truth invariants

- 여러 Dataset 지원
- Working Draft는 mutable
- 문항 저장마다 revision 생성하지 않음
- 사용자 workflow에 Draft/Reviewed/Approved 단계 없음
- `Ready / Needs attention`은 validation 결과, `Excluded`는 사용자 제외 상태
- Working Changes는 latest Published Revision과 diff로 계산/표시
- 개별 Case는 latest Published Revision으로 revert 가능
- Dataset Working Draft 전체 discard 가능 + confirmation
- Publish 시에만 immutable Dataset Revision 생성
- Published Revision membership만 official evaluation denominator
- Publish 시 included/needs-attention/excluded counts와 inclusion rate snapshot metadata 저장
- Needs attention/Excluded이 하나라도 빠지면 single publish acknowledgement 필요
- Reference Label ≠ Ground Truth
- Reference Label import → Working Draft + provenance
- 과거 Dataset Revision / Evaluation snapshot은 재작성하지 않음

# 12. Header / Locale / URL

Header:

```text
Global Search   Production Health   KR|EN   Theme ▾   Principal
```

실제 Notification 기능이 없으면 Bell을 추가하지 않는다.

Global Search:

```text
Analysis ID / Event ID / IP / Company / Signature
→ Operate > Inference History
```

URL에 넣지 않음:

```text
검색어
form draft
secret
raw payload
API response
```

Locale/theme 전환 시 route/tab/filter/form/selection state 유지.

저장 규칙:

```text
waf-console-theme  = sk | light | dark   (default: sk)
waf-console-locale = ko | en             (default: ko)
```

Theme은 Header의 단일 `Theme` button/popover에서 SK / Light / Dark를 선택한다. 세 테마 버튼을 Header에 상시 나열하지 않는다.
Authenticated principal의 실제 display name을 Header에 표시하고, 정보가 없을 때만 `Admin`을 fallback으로 사용한다.

---

# 13. Polling

```text
Running/Pending 존재   3~5s
Stable                  15~30s
Browser hidden          pause/backoff
```

`last updated` 제공.
항상 노출되는 큰 Refresh button 남발 금지.

---

# 14. Current code → V5 workspace

| 현재 | V5 |
|---|---|
| 대시보드 | Overview |
| 분석 결과 | Operate > Inference History |
| 분석 상세 | Inference Detail drilldown |
| 테스트 분석 | Evaluate > Tests > New Test |
| 테스트 결과 | Evaluate > Tests |
| 데이터 관리 | Evaluate > Ground Truth |
| 설정 > LLM 프로필 | Configure > LLM Profiles |
| 설정 > Agent > 모델 배정 | Configure > Agent Roles |
| 설정 > Agent > 공통 지침 | Configure > Analysis Instructions |
| 설정 > Agent > 동시 처리 | Operate > Runtime |
| 설정 > Agent > 처리 현황 | Operate > Runtime |
| Production API | Connect > Production API |
| 설정 > 서비스 API Key | Connect > API Keys |
| 설정 > 입력 스키마 | Connect > Input Schema |
| 설정 > 내부 연결 허용 | Connect > vLLM Targets |

---

# 14.1 Required reading path correction

작업 시작 시 `AGENTS.md`, `README.md`, architecture, agent README를 읽는다.

현재 `main`의 `AGENTS.md`는 architecture 경로를 `docs/WAF_AI_Analysis_Architecture_v0.1.md`로 적고 있지만 실제 파일은 repository root의:

```text
WAF_AI_Analysis_Architecture_v0.1.md
```

이다. Astra는 root 파일을 읽는다. `backend/app/agent/README.md`도 함께 읽는다.

---

# 14.2 Route / URL contract

기존 custom `browserHistory`/hash routing을 유지하고 새 router dependency를 추가하지 않는다.
검색어·form draft·secret·raw payload·API response는 URL에 넣지 않는다.

Canonical hash routes:

```text
#overview
#evaluate/tests
#evaluate/ground-truth
#evaluate/production-evaluation
#configure/llm-profiles
#configure/agent-roles
#configure/instructions
#operate/runtime
#operate/inference
#operate/activity
#connect/production-api
#connect/api-keys
#connect/input-schema
#connect/vllm-targets
```

Contextual promotion route:

```text
#promotion/{test_run_uuid}
```

이 route는 Sidebar item이 아니다. Back destination은 `#evaluate/tests/{test_run_uuid}`.

UUID drilldown:

```text
#operate/inference/{analysis_uuid}/{result|agent-trace|input|result-json|report}
#evaluate/tests/{test_run_uuid}
#evaluate/tests/{test_run_uuid}/case/{analysis_uuid}
```

Legacy aliases:

```text
#dashboard                    → #overview
#promote                      → #evaluate/tests (eligible Candidate를 선택하도록 안내; 자동 Candidate 추측 금지)
#analyses                     → #operate/inference
#analyses/production          → #operate/inference (Production scope)
#analyses/test                → #evaluate/tests
#analyses/{uuid}              → #operate/inference/{uuid}/result
#analyses/{uuid}/raw          → #operate/inference/{uuid}/input
#analyses/{uuid}/report       → #operate/inference/{uuid}/report
#test                         → #evaluate/tests (New Test entry)
#test-runs/{uuid}             → #evaluate/tests/{uuid}
#datasets                     → #evaluate/ground-truth
#datasets/{uuid}              → #evaluate/ground-truth (dataset selected in page state)
#production-api               → #connect/production-api
#settings/models              → #configure/llm-profiles
#settings/agents              → #configure/agent-roles
#settings/prompts             → #configure/instructions
#settings/schema              → #connect/input-schema
#settings/egress              → #connect/vllm-targets
#settings/keys                → #connect/api-keys
```

Test Case full-detail 이동 후 browser back/return은 원래 Test/Case/filter/scroll/metric-expanded state를 복원한다.

# 14.3 Frontend dependency policy

기본 구현은 현재의 작은 dependency set을 유지한다.

```text
No new router library
No new i18n library
No new UI component library
No new chart library by default
```

- routing: 기존 `browserHistory.js` / `appRoutes.js` 확장
- i18n: lightweight locale dictionary + React context/hook
- charts: 기존 SVG/CSS 기반 구현 우선
- icons: 기존 `Icon.jsx` 확장
- menus/popovers: accessible local component 구현

새 runtime dependency가 정말 필요한 경우에만 `AGENTS.md`에 따라 이유·bundle/maintenance 영향·대안을 먼저 설명한다.

---


# 14.4 Contextual cross-links

```text
Overview > Action Center > Candidate ready
  → #promotion/{test_run_uuid}

Evaluate > Tests > eligible Candidate
  → #promotion/{test_run_uuid}

Configure > LLM Profiles (vLLM provider)
  → Connect > vLLM Targets

Configure > Agent Roles
  → Operate > Runtime의 Concurrency/Capacity

Evaluate > Tests > New Test
  → Configure > Analysis Instructions
  → Connect > Input Schema

Connect > Production API
  → Connect > Input Schema

Promotion schema diff
  → Connect > Input Schema version detail
```

Cross-link는 shortcut일 뿐 동일 기능을 복제하거나 다른 mutation path를 만들지 않는다.

# 15. Backend changes required

Frontend-only 완료로 간주하지 않는다.

필수:

```text
Explicit Candidate Configuration selection
Prompt Version override for Test
Input Schema Version override for Test
Candidate configuration_hash
Transactional Production Promotion endpoint
Production configuration history/hash

Ground Truth Working Draft storage
Ground Truth automatic validation state
Publish-only Dataset Revision creation
Published-revision official evaluation

Test list evaluation summary + comparison context
Overview comparable evaluation trend
Overview Ground Truth working distribution
Overview recent comparable tests

Runtime health/status API
Activity/Change metadata
Deployment metadata where available
```

상세는 `BACKEND_CONTRACT.md`.

# 15.1 Workspace Tab Focus/Contrast Rule

SK/Light에서 keyboard focus가 들어왔을 때 tab text가 white로 바뀌어 배경과 섞이는 현상을 금지한다.

State 원칙:

```text
inactive        muted foreground
hover           strong foreground
selected        strong foreground + indicator
focus-visible   foreground 유지 + focus ring
selected+focus  selected foreground 유지 + focus ring
```

`focus-visible`은 **text color mutation이 아니라 ring/outline**으로 표현한다.

SK / Light:

```text
tab active text   #172033 계열
tab inactive      #667085 계열
focus ring        pale mint/green
```

Dark:

```text
tab active text   near-white
tab inactive      muted light gray
focus ring        green
```

금지:

```css
.tab:focus { color: white; }
button:focus { color: white; } /* tab에 전역 상속 */
```

Workspace Tab, Inference Detail Tab, segmented scope 모두 keyboard focus + selected 조합을 SK/Light/Dark에서 테스트한다.

# 16. Source precedence

```text
1. ASTRA_HANDOFF.md
2. BACKEND_CONTRACT.md
3. UI_IMPLEMENTATION_SPEC.md
4. UI_DESIGN_GUIDELINES.md
5. mockups_r3/ (R3에서 실제 제공된 화면만)
6. 과거 V5/V4/V3 문서·목업
```

R3에 포함되지 않은 화면은 과거 mockup을 canonical로 사용하지 않는다.
과거 mockup은 visual inspiration만 가능하며, IA/Navigation/Ground Truth/Promotion 동작은 반드시 R3 문서를 따른다.
생성형 UI 이미지는 canonical source가 아니다.

# Mockup Fidelity / Implementation Quality Rule

V5 목업은 **픽셀 단위 복제 대상이 아니다**.

목업의 역할은 다음으로 제한한다.

- 최종 IA
- 정보의 포함 관계
- Page Archetype
- 화면별 주요 기능과 정보 배치
- Drilldown 구조
- 상대적인 정보 우선순위

실제 React 구현에서는 `UI_DESIGN_GUIDELINES.md`를 기준으로 reusable component를 구현하고, 브라우저에서 실제 typography, spacing, interaction state, table density, focus/hover/pressed 상태를 조정한다.

특히 다음 순서로 구현한다.

1. 공통 Visual System / Design Components 구현
   - Typography
   - Spacing
   - Surface
   - Button
   - Input / Select
   - Tabs
   - Table
   - Badge / Status
   - Dialog / Drawer
   - Sidebar / Header
2. 대표 화면 3개를 먼저 production-quality로 완성
   - Overview
   - Ground Truth
   - Promotion contextual full-page
3. 1440×900 실제 브라우저 Screenshot Review
   - 목업과의 기계적인 일치보다 실제 가독성·위계·밀도·상호작용 품질을 우선
   - 문제가 있으면 공통 component/token을 먼저 수정
4. 대표 화면 3개에 대해 checklist 기반 self-review와 Browser Screenshot QA를 완료한 뒤 동일한 Design System을 나머지 화면으로 확장. 사용자가 명시적으로 단계별 승인을 요청하지 않았다면 여기서 멈추지 말고 계속 구현한다.

금지:

- V5 PNG를 그대로 HTML/CSS로 tracing하는 구현
- 목업의 샘플 수치·샘플 문자열을 제품 데이터로 하드코딩
- 목업에 없는 실제 기존 기능을 삭제
- 목업과 맞추기 위해 접근성/URL privacy/보안 동작을 훼손
- 각 페이지마다 독자적인 버튼·테이블·탭 스타일을 새로 만드는 것

즉 구현 목표는 다음이다.

> **V5 목업과 동일한 정보 구조를 유지하되, 실제 코드에서는 목업보다 더 완성도 높은 production UI를 만든다.**
