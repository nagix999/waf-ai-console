# WAF AI Console — Astra Canonical Handoff V5

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
Top-level IA            Overview / Configure / Evaluate / Promote / Operate / Connect
Default theme           SK
Selectable themes       SK / Light / Dark
Default locale          ko
Locale options          ko / en
Normal Primary          neutral dark
Brand Mutation          Promote to Production only
Production mutation     Promotion transaction only
New Test UI             explicit Candidate Configuration always
Promotion evidence      official Approved-only Ground Truth evaluation required
Activity source         immutable ChangeEvent
Deployment              read-only
Concurrency             Runtime setting; not Promotion snapshot
Router                   existing custom hash/browserHistory; no new router
```

`권장/가능`으로 해석해 다른 정책을 선택하지 않는다. 구현 세부만 코드 구조에 맞게 조정한다.

---

# 0. Product Direction

이 콘솔은 SOC/관제 콘솔이 아니다. 주 사용자는 WAF AI inference engineer다.

제품 Lifecycle:

```text
Configure
   ↓
Evaluate
   ↓
Promote
   ↓
Operate

Connect = system boundary
Overview = summary + drilldown
```

핵심 질문:

```text
Overview   지금 전체적으로 어떤 상태인가?
Configure  어떤 모델/Agent/지침으로 만들 것인가?
Evaluate   Candidate가 Ground Truth 기준으로 어떻게 동작하는가?
Promote    검증한 정확한 snapshot을 Production에 올릴 것인가?
Operate    실행 중 시스템과 개별 inference에서 무슨 일이 있었는가?
Connect    외부 시스템·모델 서버를 어떻게 연결하는가?
```

---

# 1. Canonical Navigation

Sidebar는 정확히 다음 6개 top-level만 사용한다.

```text
Overview
Configure
Evaluate
Promote
Operate
Connect
```

KR locale:

```text
Overview   → Overview
Configure  → 구성
Evaluate   → 평가
Promote    → 승격
Operate    → 운영
Connect    → 연동
```

상시 2단 Sidebar를 만들지 않는다. Workspace 내부 기능은 Page Tabs로 제공한다.

```text
Configure
  LLM Profiles
  Agent Roles
  Analysis Instructions

Evaluate
  Tests
  Ground Truth
  Production Evaluation

Promote
  Production Promotion workflow

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

Drilldown only:

```text
LLM Profile Detail
Test Detail
Inference Detail
Ground Truth Case Detail
Production Evaluation history/detail
Activity Detail
Deployment Detail
```

---

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

# 4. Overview

Overview만 Dashboard다.

읽는 순서:

```text
Production state
→ Runtime metrics
→ Official Production Evaluation
→ Attention
→ Current Configuration
→ Recent Activity
→ Connections
```

편집 Form을 넣지 않는다. 각 block은 상세 Workspace로 drilldown한다.

Health는 실제 heartbeat/readiness가 없으면 `Unknown`.

Production Evaluation은 live production accuracy가 아니라 Approved Ground Truth 공식 snapshot이다.

---

# 5. Configure

## 5.1 LLM Profiles — Registry

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

## 5.2 Agent Roles — Configuration Editor

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

## 5.3 Analysis Instructions — Versioned Editor

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

# 6. Evaluate

## 6.1 Tests — Resource List + Workflow

Landing은 Dashboard가 아니다.

```text
Search / Filter
compact status summary
Test Run table
+ New Test
```

New Test:

```text
Candidate Configuration
  Primary
  Verifier
  Evidence Editor
  Analysis Instructions Version
  Input Schema Version

Single | File | Ground Truth
```

Test Detail:

```text
Execution information
Official evaluation
Comparison
Case results
Failed retry
Promote CTA
```

## 6.2 Ground Truth — Dataset Workbench

일반 Q&A가 아니다. WAF Event / HTTP Request case다.

```text
Dataset / Revision
Review status scope
Case table | selected Case detail
```

Verdict:

```text
true_positive
false_positive
inconclusive
```

Review:

```text
Draft → Reviewed → Approved
```

Approved만 공식 평가에 포함.
Revision transition은 immutable.

## 6.3 Production Evaluation — Evaluation Result

Evaluate Workspace Tab.

표시:

```text
Production Configuration ID
Ground Truth Dataset / Revision
Evaluation Test
Evaluated At
Approved Sample Count
Accuracy / Precision / Recall / F1 / Coverage
Category results
Evaluation history
```

실시간 Production accuracy로 표현하지 않는다.

---

# 7. Promote

독립 Top-level Release Gate.

승격 단위:

```text
Primary
Verifier
Evidence Editor enabled/profile
Analysis Instructions Version
Input Schema Version
```

Concurrency 제외.

화면 composition 자체가 다음 비교가 되어야 한다.

```text
Current Production   →   Candidate
Configuration diff
Official evaluation
Pre-flight
Promote to Production
```

Frontend가 Candidate ID들을 다시 조립하지 않는다. Backend가 Test Run snapshot을 사용한다.

Schema contract change는 별도 acknowledgement가 필요하다.

---

# 8. Operate

## 8.1 Runtime — Investigation + Capacity

기존 `운영 상태 + Diagnostics + Concurrency`를 통합.

```text
Service / Worker / Queue state
Request rate / latency / failure / retry
Recent failures
Failure step/type
Capacity / Concurrency
Server call limits
```

Concurrency는 Promote snapshot에 포함하지 않는 Runtime setting.

## 8.2 Inference History — Search/List

목적은 개별 inference를 찾는 것.

기존 검색/고급 필터 기능을 보존한다.

표 기본 정보:

```text
판정 / 심각도
이벤트 / 요약
회사 / 연결
참고 답안 비교
전체 소요 시간
접수 시각
```

Dashboard chart 금지.

## 8.3 Inference Detail

5개 1급 Tab:

```text
Result
Agent Trace
Input
Result JSON
Report
```

기존 기능 보존:

- ResultView / AnalystEvidence / AdditionalChecks
- AgentHistory의 Run/Step/Output/Input/Metadata
- RawEventView / TextInspector / DecodingView
- JsonTree
- AnalysisReport
- Retry / PDF / Excel / refresh
- 접근 감사 및 원문 보안 규칙

## 8.4 Activity — Audit/History

기존 `Change History + Deployment Info` 통합.

```text
All / Promotion / Runtime / Configuration / Integration / Deployment
Time / Type / Resource / Before / After / Actor
```

Deployment drilldown은 image/git/db/deployment metadata만 Read-only 표시.
Deploy/Restart/Rollback 없음.

---

# 9. Connect

## 9.1 Production API — Documentation

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

## 9.2 API Keys — Credential Registry

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

## 9.3 Input Schema — Versioned Contract Editor

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

## 9.4 vLLM Targets — Endpoint Registry

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
Configure → Evaluate → Promote
```

`tested configuration = promoted configuration`을 보장한다.

Concurrency만 Runtime에서 별도 관리.

---

# 11. Ground Truth invariants

- 여러 Dataset
- immutable Dataset/Case revisions
- Draft / Reviewed / Approved
- Approved-only official denominator
- Reference Label ≠ Ground Truth
- Reference Label import → Draft
- Approved case edit → new Draft revision
- 과거 Evaluation snapshot은 이후 GT 변경으로 재작성하지 않음

---

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
#configure/llm-profiles
#configure/agent-roles
#configure/instructions
#evaluate/tests
#evaluate/ground-truth
#evaluate/production-evaluation
#promote
#operate/runtime
#operate/inference
#operate/activity
#connect/production-api
#connect/api-keys
#connect/input-schema
#connect/vllm-targets
```

UUID drilldown은 path segment로 허용한다.

```text
#operate/inference/{analysis_uuid}/{result|agent-trace|input|result-json|report}
#evaluate/tests/{test_run_uuid}
#evaluate/ground-truth/{dataset_uuid}
```

기존 hash는 가능한 한 alias로 유지한다.

```text
#dashboard                    → #overview
#analyses                     → #operate/inference
#analyses/production          → #operate/inference (Production scope)
#analyses/test                → #evaluate/tests
#analyses/{uuid}              → #operate/inference/{uuid}/result
#analyses/{uuid}/raw          → #operate/inference/{uuid}/input
#analyses/{uuid}/report       → #operate/inference/{uuid}/report
#test                         → #evaluate/tests (New Test entry)
#test-runs/{uuid}             → #evaluate/tests/{uuid}
#datasets                     → #evaluate/ground-truth
#datasets/{uuid}              → #evaluate/ground-truth/{uuid}
#production-api               → #connect/production-api
#settings/models              → #configure/llm-profiles
#settings/agents              → #configure/agent-roles
#settings/prompts             → #configure/instructions
#settings/schema              → #connect/input-schema
#settings/egress              → #connect/vllm-targets
#settings/keys                → #connect/api-keys
```

Alias 진입 후 search/filter/form drafts를 URL에 직렬화하지 않는다.

---

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

Sidebar/Workspace Tab을 중복 추가하지 않고, 서로 강하게 연결된 기능은 contextual link로 발견 가능하게 한다.

```text
Configure > LLM Profiles (vLLM provider)
  → Connect > vLLM Targets

Configure > Agent Roles
  → Operate > Runtime의 Concurrency/Capacity

Evaluate > Tests > New Test
  → Configure > Analysis Instructions
  → Connect > Input Schema

Connect > Production API
  → Connect > Input Schema

Promote schema diff
  → Connect > Input Schema version detail
```

Cross-link는 shortcut일 뿐 동일 기능을 복제하거나 다른 mutation path를 만들지 않는다.

---

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
Ground Truth review_status + immutable transition
Approved-only official evaluation
Runtime health/status API
Activity/Change metadata
Deployment metadata where available
```

상세는 `BACKEND_CONTRACT.md`.

---

# 16. Source precedence

```text
1. ASTRA_HANDOFF.md
2. BACKEND_CONTRACT.md
3. UI_IMPLEMENTATION_SPEC.md
4. UI_DESIGN_GUIDELINES.md
5. ui_mockups/canonical_v5/
6. 과거 문서/목업
```

V5 mockup은 HTML/CSS browser rendering 기반이다.
생성형 UI 이미지는 canonical source로 사용하지 않는다.

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
   - Promote
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
