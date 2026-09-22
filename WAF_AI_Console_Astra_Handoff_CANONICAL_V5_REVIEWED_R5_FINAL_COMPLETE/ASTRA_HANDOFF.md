# WAF AI Console — Astra Canonical Handoff V5 REVIEWED R5 FINAL — Solo Operations

- Repository: `nagix999/waf-ai-console`
- 기준 브랜치: `main`
- 대상 사용자: WAF AI 추론 시스템 개발·검증·승격·운영·디버깅 엔지니어
- 상태: **Canonical / Source of Truth**
- 이 문서는 현재 구현을 최종 Lifecycle IA와 Visual System으로 전환하기 위한 **독립적인 최종 기준**이다.
- **이 R5 FINAL 패키지 이전에 생성된 모든 설계·목업은 canonical이 아니다.** 생성형 UI 이미지는 구현 근거로 사용하지 않는다.

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
Brand Mutation          운영에 반영 only
Production mutation     Promotion transaction only
New Test UI             explicit Candidate Configuration always
Test defaults            full Candidate defaults: roles + Instructions + Input Schema
Ground Truth lifecycle  Working Draft → Publish Revision
Official evaluation     Published Ground Truth Revision only
Production state        unconfigured | legacy_active | promoted
Promotion review        terminal official Candidate Test부터 진입 가능; 실제 mutation은 preflight eligible일 때만
Initial assessment      Production service API admission metadata only; Agent-invisible
Activity source         immutable ChangeEvent
Deployment              read-only
Concurrency             Runtime setting; not Promotion snapshot
Router                   existing custom hash/browserHistory; no new router
```

`권장/가능`으로 해석해 다른 정책을 선택하지 않는다. 구현 세부만 코드 구조에 맞게 조정한다.

---

# 0.3 User-visible language contract

The domain model stays technical, but the UI copy does not expose that complexity.

User-visible terminology is defined in `UI_TERMINOLOGY.md`.

Canonical navigation labels:

```text
KR
홈 / 평가 / 설정 / 운영 / 연동

EN
Home / Evaluation / Configuration / Operations / Integrations
```

Examples:

```text
candidate_configuration  → 테스트 설정 / Test Configuration
promotion review         → 운영 반영 검토 / Production Review
preflight                → 적용 전 점검 / Readiness Checks
published revision       → 공식 버전 / Published Version
working draft            → 편집 중 / Draft
```

Backend/API identifiers remain unchanged.

Copy precedence:
`UI_TERMINOLOGY.md` overrides wording shown in older structural mockups.

# 0.4 Flow / visualization principle

The user should understand the product through this short mental model:

```text
정답 데이터 준비 → 테스트 → 결과 확인 → 수정 → 운영 반영 검토 → 운영에 반영 → 운영 확인
```

English:

```text
Prepare Ground Truth → Run a Test → Review Results → Fix What Is Needed → Production Review → Apply to Production → Observe Production
```

Do not turn this lifecycle into another persistent navigation layer.

Use visual structure only when it helps answer:

```text
What changed?
What is the distribution?
How is it trending?
What is blocking me?
What should I do next?
```

Detailed rules are in `UX_VISUALIZATION_GUIDE.md`.

# 0. Product Direction

이 콘솔은 SOC/관제 콘솔이 아니다. 주 사용자는 **혼자 WAF AI inference system을 개발·평가·구성·운영하는 엔지니어**다.

Sidebar는 개발 lifecycle을 그대로 복사하지 않고 **실제 작업 빈도와 인지 흐름**을 기준으로 한다.

```text
Home / 홈              현재 상태와 다음 행동 확인
Evaluation / 평가        Tests / 정답 데이터 / 운영 설정 평가
Configuration / 설정     모델 / Agent / 분석 지침 / 입력 스키마
Operations / 운영        실행 상태 / 분석 이력 / 변경 이력
Integrations / 연동      Production API / API 키 / vLLM 연결
```

Canonical route identity and visible labels are separate:

```text
Internal route identity
Overview → Evaluate → Configure → Operate → Connect

KR visible
홈 → 평가 → 설정 → 운영 → 연동

EN visible
Home → Evaluation → Configuration → Operations → Integrations
```

이 순서를 채택하는 이유:

- 이 제품의 핵심 반복 작업은 Candidate Test와 Ground Truth 평가다.
- Overview가 이미 Runtime 상태를 요약하므로 Operate를 두 번째에 둘 필요가 없다.
- Operate를 너무 앞에 두면 제품이 관제/SOC 콘솔처럼 보일 수 있다.
- Configure는 Evaluate 결과를 보고 조정하는 작업공간이므로 Evaluate 바로 뒤에 둔다.
- Input Schema는 Candidate/Promotion snapshot의 구성 요소이므로 Configure에 둔다.
- Connect는 Production API / credentials / model-server connection 같은 외부 시스템 경계만 모은다.

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

Sidebar has exactly five top-level destinations.

```text
KR
홈
평가
설정
운영
연동

EN
Home
Evaluation
Configuration
Operations
Integrations
```

Internal route names remain:

```text
Overview / Evaluate / Configure / Operate / Connect
```

Workspace labels:

```text
평가 / Evaluation
  테스트 / Tests
  정답 데이터 / Ground Truth
  운영 설정 평가 / Production Evaluation

설정 / Configuration
  LLM 프로필 / LLM Profiles
  Agent 역할 / Agent Roles
  분석 지침 / Analysis Instructions
  입력 스키마 / Input Schema

운영 / Operations
  실행 상태 / Runtime
  분석 이력 / Inference History
  변경 이력 / Activity

연동 / Integrations
  Production API
  API 키 / API Keys
  vLLM 연결 / vLLM Targets
```

## Promotion navigation

Promotion은 Sidebar 항목도 아니고 Operate의 상시 Tab도 아니다.

**운영 반영 검토 진입 가능 여부와 실제 Promote 가능 여부를 분리한다.**

```text
Evaluate > Tests > terminal official Candidate Test
  → 운영 반영 검토
  → contextual full-page Promotion workflow

Overview > 할 일 > promotion-ready Candidate
  → same Promotion workflow
```

Test Detail에서는 explicit Candidate Configuration으로 실행한 official Ground Truth Test가 terminal 상태가 되면 `운영 반영 검토`을 제공한다.
적용 전 점검 일부가 실패했더라도 운영 반영 검토 화면에는 들어갈 수 있어야 한다. 사용자는 여기서 막힌 이유를 확인한다.

실제 `운영에 반영` CTA는 다음 조건을 모두 만족할 때만 활성화한다.

```text
preflight eligible
+ production baseline not stale
+ required schema acknowledgement complete
```

Overview 할 일에는 noise를 줄이기 위해 실제 promotion-ready Candidate만 노출한다.

Promotion workflow는 full-page task다. 상단 Back link는 원래 Test로 돌아간다.
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
운영 반영 검토 = contextual full page
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
- SK-inspired Red = brand mark, active workspace indicator, `운영에 반영` 같은 high-consequence brand mutation
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

Overview는 Production 상태에 따라 다음 mode를 가진다.

```text
unconfigured  → First Production Setup
legacy_active → Normal Overview + legacy banner
promoted      → Normal Overview
```

`baseline` record 존재 여부만으로 Production 상태를 판단하지 않는다.

## 4.0 Production state semantics

### unconfigured

실제 Production 실행에 필요한 live configuration이 완성되지 않은 상태.

예:

```text
Production Primary 없음
필수 active instructions/schema 없음
필수 role/profile가 유효하지 않음
```

이 상태에서만 First Production Setup Guide를 보여준다.

### legacy_active

실제 사용 가능한 Production configuration은 존재하지만,
canonical Promotion workflow 도입 이후의 successful Promotion record가 없는 기존 설치 상태.

Normal Overview를 그대로 보여주고 상단에 작은 informational banner만 표시한다.

```text
기존 운영 설정
새 운영 반영 방식 도입 전에 적용된 설정입니다.
다음 변경부터 공식 테스트 → 운영 반영 검토 → 운영에 반영 흐름을 사용합니다.
```

`legacy_active`를 `Production 미구성`으로 오판해서 Setup Guide로 보내지 않는다.

### promoted

successful Production Promotion 이력이 존재하는 정상 상태.

Normal Overview를 보여준다.

---

## 4.1 First Production Setup mode

`production_state == unconfigured`일 때 일반 dashboard 대신 **첫 Production까지의 다음 행동을 알려주는 Setup Guide**를 보여준다.

별도 bootstrap mutation을 만들지 않는다.

```text
Test configuration resources
→ Published Ground Truth
→ Official Candidate Test
→ 운영 반영 검토
→ First Production Configuration
```

### Setup progress — 정확히 5단계

```text
1. 모델 연결
2. 기본 테스트 설정
3. 정답 데이터 공식 버전
4. 공식 테스트
5. 운영에 반영
```

각 단계는 server state에서 계산한다.

- 모델 연결: Test에 사용할 verified profile이 존재하고, vLLM이면 필요한 Target이 유효.
- 기본 테스트 설정: 최소 하나의 유효한 Primary/Verifier 조합, 선택 가능한 Evidence 설정, saved Analysis Instructions, saved Input Schema가 존재. Evidence Editor가 disabled이면 별도 profile을 요구하지 않는다.
- 정답 데이터 공식 버전: Ready case가 1건 이상 포함된 Published Revision 존재.
- 공식 테스트: Published Revision + explicit Candidate Configuration으로 실행한 terminal official Test 존재. Promotion 가능 여부 자체는 이 단계 완료 조건이 아니다.
- 운영에 반영: 운영 반영 검토의 preflight를 통과한 Candidate를 실제 Production에 승격.

`Candidate configuration`이라는 독립 리소스가 존재하는 것처럼 표현하지 않는다. Candidate snapshot은 Test 실행 시 고정된다.

UI는 큰 wizard로 강제하지 않는다.
**현재 완료 상태 + 다음 1개 action**을 가장 강하게 보여준다.

예:

```text
첫 운영 설정                         3 / 5

✓ 모델 연결
✓ 기본 테스트 설정
✓ 정답 데이터 공식 버전
○ 공식 테스트
○ 운영에 반영

Next
정답 데이터의 공식 버전으로 첫 공식 테스트를 실행하세요.
                                      공식 테스트 실행 →
```

규칙:

- 완료 단계는 generic `View`가 아니라 `LLM Profiles →`, `정답 데이터 →`처럼 목적지를 표시한다.
- blocked step은 하위 조건을 펼쳐 원인을 보여줄 수 있다.
  예: Primary ✓ / Verifier ✓ / Instructions ✓ / Input Schema ✕
- 미완료 단계 여러 개에 Primary CTA를 동시에 두지 않음.
- Backend 상태 확인 불가 → `Unknown`.
- Production이 없는데 fake runtime/evaluation chart 금지.
- `unconfigured` 동안 Header에 작은 `첫 운영 설정 · 3/5` shortcut을 둘 수 있다.
- 첫 Promotion 성공 후 shortcut은 사라진다.

### Setup step UI state

Setup 단계는 `완료/미완료` 두 값으로 단순화하지 않는다.

```text
not_started
in_progress
needs_attention
ready
unknown
```

예:

```text
● 공식 테스트
  Running · 742 / 1,020

또는

! 공식 테스트
  Completed with 7 failed cases
  Review failed cases →
```

`in_progress`는 실행/검증 중인 실제 작업에만 사용하고,
`needs_attention`은 사용자가 해결해야 다음 단계로 갈 수 있는 상태다.

## 4.2 After first promotion

첫 Promotion 성공 즉시 Normal Overview로 전환한다.

Production API 연결은 Setup 5단계에 포함하지 않는다.
대신 할 일에서 **credentials와 실제 traffic을 분리**하여 안내한다.

```text
Production API credentials
  ready | missing | unknown

Production API traffic
  observed | not_observed | unknown
```

예:

```text
✓ Production configuration ready
⚠ Production API credentials missing
  → Issue Production API Key

또는

✓ Production API credentials ready
○ No Production API traffic observed since promotion
  → Open Production API docs
```

API Key 존재만으로 `Production API connected`라고 표현하지 않는다.

실제 외부 caller 연결은:

```text
Connect > API Keys
Connect > Production API
```

에서 수행한다.

## 4.3 Normal Operations dashboard

읽는 순서:

```text
Production state / Runtime summary
→ Comparable Production Evaluation Trend
→ Ground Truth Working Draft distribution
→ 최근 동일 기준 테스트
→ 할 일
→ Current Configuration
→ Recent Activity
```

편집 Form은 넣지 않는다.
Health는 실제 heartbeat/readiness가 없으면 `Unknown`.

Production Evaluation은 live accuracy가 아니라 **Published Ground Truth Revision 기반 공식 evaluation snapshot**이다.

### Comparable Production Evaluation Trend

- Accuracy / F1 / Coverage
- same Ground Truth dataset + same Published Revision + same scope/filter + same metrics_version만 연결
- X축 = Production Configuration / Evaluated At
- Y축 기본 = 0–100%
- comparable point >= 2일 때만 line chart
- point < 2이면 빈 차트 대신 compact current evaluation summary

### 정답 데이터 상태

latest official Production Evaluation이 사용한 Dataset의 현재 Working Draft:

```text
준비됨 | 확인 필요 | 제외
```

- horizontal stacked bar
- Working changes count + latest Published Revision
- Production Evaluation context 없으면 Dataset 임의 선택 금지
- Working Draft 변경이 0이고 확인 필요/제외도 0이면 큰 chart 대신 compact `변경 없음` summary

### 최근 동일 기준 테스트

- comparable Test가 없으면 빈 mini-bar 영역을 만들지 않고 compact empty-state를 표시

latest official Production Evaluation과 동일 comparison context의 최근 Test만 보여준다.

```text
Test name/time
Primary / Instructions / Schema identity
F1 mini bar | Accuracy | Coverage
```

직전 동일 기준 Test 대비 달라진 구성이 있으면 **전체 config identity보다 변경된 항목을 우선** 표시한다.

```text
변경됨 · 분석 지침 v19 → v20
```

전체 config identity는 secondary metadata/detail에서 확인한다.

```text
Changed: Instructions v19 → v20
```

자동 생성 Test명만 보고 성능 변화 원인을 추측하게 하지 않는다.

### 할 일

하나의 Surface 안에서 `Needs attention`과 `Next steps`를 분리한다.

```text
할 일

Needs attention
────────────────
⚠ 23 Ground Truth cases · 3 datasets
   WAF-General 3 · SQL-Injection 12 · FP-Hard-Cases 8
⚠ Failed Test
⚠ Inference failures

Next steps
────────────────
✓ 운영 반영 검토할 공식 테스트
○ Production API credentials missing
○ Production API traffic not observed
```

Candidate ready와 API traffic 미관측은 error로 표현하지 않는다.

Ground Truth Distribution은 **현재 Production Evaluation context의 한 Dataset**만 시각화한다.
반면 할 일의 Ground Truth needs-attention은 **모든 Dataset의 global work queue**다.
Dataset별 count/link를 제공하고 클릭 시 해당 Dataset이 선택된 Ground Truth Workspace를 연다.

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
- Test name 바로 아래에 purpose badge를 표시한다.
  - `Official · {dataset} r{revision}`
  - `Development · {Single|File|Dataset}`
- Ground Truth dataset/revision/sample count를 Official Test secondary metadata에 표시한다.
- 개발 테스트 metric은 항상 `비공식 / Development` context로 표시하고 Production delta를 계산하지 않는다.
- 서로 다른 dataset/revision/scope/metrics_version의 Official Test는 delta/직접 비교 대상으로 표시하지 않는다.
- 각 Test에 comparison context를 작게 표시한다.
  - `Comparable to Production`
  - `Different GT revision`
  - `Different Dataset`
  - `Different evaluation scope`
- Compare 진입 후 비교 불가라면 양쪽 context 값을 함께 보여주고 이유를 설명한다.
- 펼침 상태는 URL이 아니라 local UI preference로 저장할 수 있다.

### Test naming

이름 입력은 필수가 아니다.

기본:

```text
Test 2026-09-21 13:42
```

사용자가 원할 때만 rename.

### 기본 테스트 설정 위치

`기본 테스트 설정`은 `평가 > 테스트`의 secondary action이다.

```text
[기본 테스트 설정]   [+ 새 테스트]
```

It includes model roles + Analysis Instructions + Input Schema.
It only affects New Test prefill, never Production or existing Test snapshots.

New Test shows:
```text
기본 테스트 설정에서 불러옴
기본 설정 편집 →
```

### New Test

먼저 **평가 목적**을 선택한다. 입력 형태와 평가 목적을 같은 control에 섞지 않는다.

```text
공식 테스트
  Published Ground Truth 기준의 공식 평가
  운영 반영 검토 근거로 사용 가능

개발 테스트
  빠른 개발/디버깅용
  Promotion 근거로 사용 불가
```

#### 테스트 설정 — explicit but prefilled

매번 빈 form에서 고르게 하지 않는다.
`평가 > 테스트 > 기본 테스트 설정`의 **full Test Configuration Defaults**를 초기값으로 명시적으로 화면에 채워서 보여준다.

Test Defaults는 다음 전체를 포함한다.

```text
Primary
Verifier
Evidence Editor enabled/profile
Analysis Instructions version
Input Schema version
```

역할 3개만 저장하는 부분 기본값과 혼동하지 않는다.

```text
Based on: Test defaults

Primary                  Gemma-12B
Verifier                 GPT-4o
Evidence Editor          Disabled / selected profile
Analysis Instructions    v19
Input Schema             v8
```

사용자가 그대로 실행해도 Backend에는 모든 ID/version을 명시적으로 전송한다.

```text
implicit hidden default      X
explicit visible prefill     O
```

조회 실패 시 다른 설정으로 자동 fallback하지 않는다.

#### 공식 테스트

```text
Candidate Configuration
Published Ground Truth Dataset
Published Revision
```

Dataset selector에는 최소 다음을 표시한다.

```text
WAF-General
Published r12 · 1,842 cases · 17 working changes

SQL-Injection
Published r8 · 420 cases · No working changes

FP-Hard-Cases
Published r4 · 186 cases · 8 needs attention
```

한 Test는 **정확히 하나의 Dataset + 하나의 Published Revision**을 pin한다.

#### 개발 테스트

입력 방식:

```text
Single
File
Dataset
```

Working Draft나 비공식 reference를 사용하는 경우 `Unpublished / Development only`를 명확히 표시하고 Promotion evidence로 사용할 수 없게 한다.

### 이 설정으로 다시 테스트

Test Detail에 `이 설정으로 다시 테스트`를 제공한다.

기존 Test의 다음 값을 그대로 prefill한다.

```text
Primary / Verifier / Evidence
Analysis Instructions
Input Schema
Ground Truth Dataset / Revision (있다면)
Test purpose
```

사용자는 한두 항목만 바꿔 반복 실험할 수 있다.

source Test의 Ground Truth Revision과 현재 latest Published Revision이 다르면 자동 변경하지 않는다.

```text
Ground Truth · WAF-General

● r19 — Same as source Test
○ r20 — Latest published
```

기본값은 source Test의 r19다.
사용자가 명시적으로 latest를 선택해야 r20으로 바뀐다.

새 Test는 새 immutable snapshot/hash를 만든다.

### Test detail mode

Test row 클릭 시 다른 Workspace로 이동하지 않고 같은 Tests page를 detail mode로 전환한다.

상단 Header는 **테스트 identity / 실행 상태 / 구성 / Ground Truth context / 처리 건수 / 소요 시간**만 보여준다.

금지:

```text
상단 Accuracy / Precision / Recall / F1 / Coverage MetricStrip
상단 평가 요약 card
```

평가 정보는 아래 `평가 상세` Tab 한 곳에서만 보여준다.

```text
← All tests
Test identity + purpose badge + Candidate Configuration + Ground Truth context
Status / accepted / completed / failed / duration

[이 설정으로 다시 테스트] [운영 반영 검토] [More ▾]

More
  Compare tests
  Execution info
  정답 데이터에 추가…

문항별 결과 | 평가 상세
```

#### 문항별 결과 Tab

- case filters
- case result table
- Case row → right Drawer
- Drawer → `크게 보기` → Full Inference Detail
- source Ground Truth case가 있으면 `Working Draft에서 열기 →` 제공
  - target Dataset 자동 선택
  - same stable_case_id 선택
  - Published Revision 자체를 수정하지 않고 현재 Working Draft case를 연다

Test Detail의 반복 작업 action:

```text
이 설정으로 다시 테스트
운영 반영 검토 (terminal official Candidate)
More …
  Compare tests
  Execution info
  정답 데이터에 추가…
```

visible action은 2개 정도로 제한하고 나머지는 overflow로 이동한다.

#### Confusion Matrix labeling

`true_positive` / `false_positive` are domain verdict classes.
Do not use ambiguous TP/FP/FN/TN error-cell labels.

Use explicit axes:

```text
                          심층 판정
                       TP class   FP class
정답 데이터 TP class      472       31
            FP class       24      486
```

Show separately:
```text
심층 판정 보류 8
평가 제외 7
불일치 55
```

#### 평가 상세 Tab

기존 `평가 지표` Tab 이름을 **`평가 상세`**로 변경한다.

Tab 내부에서 별도 버튼/Dialog를 거치지 않고 상세 평가 전체를 inline으로 보여준다.

```text
Evaluation context
Accuracy / Precision / Recall / F1 / Coverage
Confusion Matrix
Coverage / abstention / additional metrics
평가 대상 / 일치 / 불일치 / 보류 / 제외 counts
필요 시 source/breakdown 상세
```

**`평가 상세` 버튼은 제거한다.**
`EvaluationOverview → 버튼 → Dialog` 방식으로 중첩하지 않는다.

공식 평가라면 context는:

```text
Published Ground Truth dataset / revision / sample count / metrics version
```

을 명확히 표시한다.

참고 답안 비교라면 `비공식`임을 명시한다.

Confusion Matrix cell을 선택하면 `문항별 결과` Tab으로 이동하면서 해당 cell filter를 적용할 수 있다.

### Case detail

Case row 클릭 → right Drawer.
Drawer는 빠른 확인용이며 `⛶ 크게 보기`로 Full Inference Detail에 진입한다.

돌아올 때 복원:

```text
selected Test
selected Case
selected detail Tab
filters / search / table scroll
evaluation list metric-expanded preference
```

### Test → Ground Truth reuse

Test 전체를 Ground Truth Working Draft 재료로 재사용할 수 있다.

Test Detail의:

```text
정답 데이터에 추가…
```

에서 두 가지를 제공한다.

```text
Create new Ground Truth Dataset from this Test
Add all importable cases to existing Ground Truth Dataset
```

#### Create new Dataset

기본 이름 예:

```text
{Test name} Ground Truth
```

새 Dataset과 Working Draft를 만들고 import 가능한 Test case를 추가한다.
**자동 Publish하지 않는다.**

#### Add to existing Dataset

Dataset selector에서 target의:

```text
name
latest Published Revision
Working changes count
Ready / Needs attention count
```

를 함께 보여준다.

적용 전에 Preview를 반드시 보여준다.

```text
Test cases                 500
Importable accepted cases  486
New cases                  440
Duplicates skipped          34
Reference conflicts          7
Missing reference verdict    5
Rejected/unavailable        14
```

사용자가 확인한 뒤에만 편집 중 데이터에 반영한다.

#### Preview count interaction

`중복 / 충돌 / 정답 없음 / 가져올 수 없음` count는 클릭해서 해당 항목과 이유를 확인할 수 있어야 한다.

```text
중복              34  보기
충돌               7  보기
정답 없음           5  보기
가져올 수 없음      14  보기
```

Preview는 숫자만 보여주는 confirmation이 아니라 **무엇이 제외/충돌하는지 검사할 수 있는 단계**다.

#### Reference verdict rule

**심층 판정 결과를 Ground Truth 정답으로 자동 복사하지 않는다.**

정답 우선순위:

1. source Test가 Published Ground Truth item에 고정돼 있으면 그 **고정 Ground Truth verdict**를 복사
2. 그렇지 않고 TestRunItem에 고정된 Reference Label이 있으면 그 **고정 reference verdict + provenance**를 복사
3. 둘 다 없으면 `reference_verdict = null`
   - case input은 import
   - `Needs attention`
   - issue = `reference_verdict_missing`
   - 심층 판정은 read-only `참고: 심층 판정` metadata로 볼 수 있으나 자동 답안이 아니다

따라서:

```text
Test result verdict ≠ Ground Truth
```

이다.

#### Duplicate / conflict

target Working Draft에서 같은 input identity가 이미 존재하면:

- 동일 reference → duplicate로 skip
- reference가 다름 → 기존 item을 덮어쓰지 않고 conflict로 보고
- 사용자가 Ground Truth에서 conflict를 직접 해결

#### Provenance

imported Working item은 최소 다음 출처를 보존한다.

```text
source_test_run_id
source_test_run_item_id
source_analysis_id
source_dataset_id / source_dataset_revision_id (있다면)
source_reference_label_id (있다면)
source_kind
imported_at
```

Internal-only restriction도 그대로 보존한다.

이 기능은 Dataset Working Draft를 만드는 편의 기능이며,
**Published Revision 생성이나 official evaluation을 자동 실행하지 않는다.**

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

### Multiple Ground Truth Datasets

Ground Truth는 **여러 Dataset을 만드는 것이 정상 사용 방식**이다.

예:

```text
WAF-General
SQL-Injection
XSS
False-Positive-Hard-Cases
Customer-A-Sample
```

각 Dataset은 서로 독립적인:

```text
Working Draft
working_revision
Ready / Needs attention / Excluded
Working Changes
Published Revision history
```

를 가진다.

Dataset selector는 검색 가능해야 하고 최소 다음 metadata를 보여준다.

```text
Dataset name
latest Published Revision
Published case count
Working changes count
Needs attention count
```

`Create Dataset`은 Dataset selector 근처의 명확한 action으로 제공한다.

다른 Dataset의 Working Draft나 Published history를 암묵적으로 합치지 않는다.

Official Test는 하나의 Dataset + 하나의 Published Revision을 pin한다.
서로 다른 Dataset/Revision의 Test는 직접 delta 비교하지 않는다.

### 현재 운영 평가 기준 표시

Ground Truth selector shows the Dataset used by current Production Evaluation:

```text
WAF-General
현재 운영 평가 기준
공식 버전 r19
```

For New Official Test:
- if current Production Evaluation context exists, visibly suggest the same Dataset/Version
- label: `현재 운영과 동일한 평가 기준`
- user may change it
- never guess when no Production Evaluation context exists

### Working Changes

Published Revision 이후 변경을 별도 summary로 제공한다.

```text
Published r19 · 252 working changes
Added 214 · Changed 38 · Removed 11
```

`Working changes`를 열면 변경된 문항만 필터링한다.

Working Changes의 change type별 복구 action을 제공한다.

```text
Added   → Discard addition
Changed → Revert to published version
Removed → Restore from published revision
```

`Removed`는 Working Draft에서 row가 없어도 latest Published Revision snapshot을 이용해 복구할 수 있어야 한다.
복구/폐기 action은 `expected_working_revision`으로 stale edit를 차단한다.

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

Publish dialog에는 포함 수량뿐 아니라 **포함되는 verdict provenance**도 보여준다.

```text
Included verdict provenance

Published GT inherited     1,100
Reference Label              350
Manual Working Draft          50
```

`Reference Label` 기반 Ready case가 포함되더라도 별도 문항별 승인 workflow를 추가하지 않는다.
대신 사용자가 공식 Dataset으로 Publish하려는 source composition을 한 번에 인지할 수 있게 한다.

Working item은 현재 reference verdict의 origin을 명시적으로 가진다.

```text
reference_origin =
  published_ground_truth
  reference_label
  manual
  none
```

사용자가 reference verdict를 직접 수정하면 `manual`로 전환한다.

Published Revision metadata에는 최소한 다음을 남긴다.

```text
working_total_at_publish
included_ready_count
needs_attention_count
excluded_count
inclusion_rate
included_reference_origin_counts
```

Official evaluation은 Published Revision membership만 사용한다.

### Layout

```text
Dataset selector / Published revision / Working changes
준비됨 | 확인 필요 | 제외
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
Test Defaults              = editable
```

Production roles:

```text
Primary
Verifier
Evidence Editor
```

Test Defaults는 **완전한 Candidate 기본값**이다.

```text
Models
  Primary
  Verifier
  Evidence Editor enabled/profile

Versions
  Analysis Instructions
  Input Schema
```

Production 변경은 Promote에서만.
Test Defaults 변경은 새 Test form의 prefill만 바꾸며 기존 Test snapshot과 Production에는 영향을 주지 않는다.

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

## 6.4 Input Schema — Versioned Contract Editor

Input Schema는 외부 API 문서의 일부이기도 하지만,
Candidate Configuration / Test snapshot / Promotion 단위에 포함되는 **승격 가능한 시스템 구성**이므로 Configure에 둔다.

```text
Version rail
Fields
New Version
Compare
Validate Input
Technical Info
```

Production direct Apply / rollback UI는 없다.
Production 적용은 Promotion transaction만 사용한다.

Connect > Production API에서는 `Current Input Schema →` contextual link로 이 화면을 연다.

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

## 7.2 Inference History — Production-only Search/List

`운영 > 분석 이력`은 **Production 분석 전용 목록**이다.

목적:

```text
운영 목적으로 처리된 Production 분석 검색/조사
```

Do not provide:

```text
Production | Test | All
```

scope switching here.

Test Run / Test Case browsing belongs only to:

```text
평가 > 테스트
```

Shared analysis-detail components may be reused, but the list ownership and navigation context remain separate.

Production Analysis row:

```text
운영 > 분석 이력
→ Analysis Detail
```

Test Case row:

```text
평가 > 테스트
→ Test Detail
→ Case
→ Full Analysis Detail
```

The detail component may share:

```text
Result / Agent Trace / Input / Result JSON / Report
```

but Back/breadcrumb follows the origin.

기존 Production 검색/고급 필터 기능을 보존한다.
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
Evaluate > Tests > terminal official Candidate Test → 운영 반영 검토
Overview > 할 일 > promotion-ready Candidate
```

Test Detail의 `운영 반영 검토`은 **preflight가 완전히 통과하기 전에도** 열 수 있다.
운영 반영 검토는 단순 mutation 화면이 아니라 blocked reason을 확인하는 진단/검토 화면이기도 하다.

화면:

```text
← Back to Test
Candidate identity
Current Production → Candidate
Configuration diff
Official Published Ground Truth Evaluation
Pre-flight
운영에 반영
```

preflight는 각각의 check 상태와 blocked reason을 읽을 수 있게 보여준다.

각 blocked check에는 가능한 경우 **바로 해결할 수 있는 contextual action**을 제공한다.

```text
Profile changed        → Open LLM Profile
Instructions missing   → Open Analysis Instructions
Schema issue           → Open Input Schema
External approval      → Open LLM Profile
Baseline stale         → Refresh Production comparison
Retest required        → 이 설정으로 다시 테스트
```

운영 반영 검토의 Back destination은 진입 origin을 복원한다.

```text
Overview에서 진입 → ← Overview
Test Detail에서 진입 → ← Test
```

항상 `Source Test →` 링크는 별도로 제공한다.

실제 `운영에 반영` 버튼은:

```text
eligible == true
AND baseline hash current
AND schema acknowledgement complete when required
```

일 때만 활성화한다.

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

# 7.6 Initial Assessment vs Deep Assessment

Production **service API**가 optional admission metadata로 다음을 받을 수 있다.

```text
initial_verdict       true_positive | false_positive
initial_probability   0.0 ~ 1.0
initial_model_version optional/recommended
```

v1에서는 다음 경로에만 허용한다.

```text
POST /api/v1/analyses
+ Production-purpose service API principal
```

다음에서는 reject한다.

```text
Test-purpose API
Admin test input
File upload / Test upload
Ground Truth import
Input Schema custom field
```

`initial_verdict`, `initial_probability`, `initial_model_version`은 **server-reserved admission metadata names**다.
사용자 Input Schema 필드나 generic `extra_fields` 이름으로 등록/저장할 수 없다.

Validation:

```text
initial_verdict + initial_probability
→ 둘 다 없음 또는 둘 다 있음

initial_model_version
→ verdict/probability pair가 있을 때만 허용
```

probability는 제공된 initial_verdict class의 confidence다.

UI 용어:

```text
initial_* 결과 = 1차 판정
현재 Agent 결과 = 심층 판정
```

중요 invariant:

```text
1차 판정은 Primary / Verifier / Evidence Editor / Result Editor 입력에 절대 포함하지 않는다.
1차 판정 ≠ Ground Truth
1차 판정 ≠ Reference Label
```

비교 상태:

```text
unavailable
pending
match
different
final_inconclusive
```

Result UI:

### different

```text
⚠ 1차 판정과 심층 판정이 다릅니다
1차 판정   False Positive · 93%
심층 판정  True Positive
```

### final_inconclusive

```text
⚠ 심층 판정이 보류되었습니다
1차 판정   True Positive · 91%
심층 판정  Inconclusive
```

둘 다 Amber warning을 사용한다.
`match`는 metadata 수준으로 조용히 표시한다.

두 시스템의 confidence 값을 동일 calibration scale처럼 직접 비교하거나 winner를 표현하지 않는다.

Operate > Inference History에는 `1차 판정 비교` filter를 제공한다.

기본 table column을 과도하게 늘리지 않는다.
판정 column에서 mismatch/inconclusive일 때만 작은 badge를 추가한다.

```text
True Positive
⚠ 1차 판정 불일치
```

상세 화면에서만 전체 1차 판정/확률을 보여준다.


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

## 8.3 vLLM Targets — Endpoint Registry

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
- Ready item은 `reference_origin`을 보존하고 Publish summary에서 origin breakdown을 보여줌
- 과거 Dataset Revision / Evaluation snapshot은 재작성하지 않음

### Locale rendering rule

KR locale shows Korean navigation/copy only.
EN locale shows English navigation/copy only.

Do not render bilingual top-level navigation side by side.

KR Agent role labels may use:
```text
주 분석 모델 · Primary
검증 모델 · Verifier
근거 정리 모델 · Evidence Editor
```

# 12. Header / Locale / URL

Header:

```text
Global Search   Production Health   KR|EN   Theme ▾   Principal
```

실제 Notification 기능이 없으면 Bell을 추가하지 않는다.

Global Search searches identifiers across Production and Test data, then routes by context.

```text
Production Analysis
→ 운영 > 분석 이력 / Analysis Detail

Test Analysis
→ 평가 > 테스트 / matching Test + Case

Test Run
→ 평가 > 테스트 / Test Detail
```

Search result rows must make the context explicit:

```text
운영 분석 / Production
테스트 결과 / Test
```

Do not send a Test Analysis to Operations merely because it has an Analysis ID.

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
| Production 분석 결과 | 운영 > 분석 이력 |
| Production 분석 상세 | 운영 > 분석 이력 drilldown |
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
| 설정 > 입력 스키마 | Configure > Input Schema |
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
#configure/input-schema
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
  → Production Analysis only

#evaluate/tests/{test_run_uuid}
#evaluate/tests/{test_run_uuid}/case/{analysis_uuid}/{result|agent-trace|input|result-json|report}
  → Test Analysis only
```

Legacy aliases:

```text
#dashboard                    → #overview
#promote                      → #evaluate/tests (official Candidate Test를 선택하도록 안내; 자동 Candidate 추측 금지)
#analyses                     → #operate/inference
#analyses/production          → #operate/inference (Production scope)
#analyses/test                → #evaluate/tests
#analyses/{uuid}              → resolve purpose; Production → Operations, Test → matching Test/Case
#analyses/{uuid}/raw          → same purpose-aware resolver, Input tab
#analyses/{uuid}/report       → same purpose-aware resolver, Report tab
#test                         → #evaluate/tests (New Test entry)
#test-runs/{uuid}             → #evaluate/tests/{uuid}
#datasets                     → #evaluate/ground-truth
#datasets/{uuid}              → #evaluate/ground-truth (dataset selected in page state)
#production-api               → #connect/production-api
#settings/models              → #configure/llm-profiles
#settings/agents              → #configure/agent-roles
#settings/prompts             → #configure/instructions
#connect/input-schema          → #configure/input-schema (R5 usability alias)
#settings/schema              → #configure/input-schema
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
Overview > 할 일 > Candidate ready
  → #promotion/{test_run_uuid}

Evaluate > Tests > terminal official Candidate Test
  → #promotion/{test_run_uuid} (운영 반영 검토)

Configure > LLM Profiles (vLLM provider)
  → Connect > vLLM Targets

Configure > Agent Roles
  → Operate > Runtime의 Concurrency/Capacity

Test Detail > Ground Truth-linked Case
  → Evaluate > Ground Truth / matching Dataset + Working Draft case

Test Detail > 이 설정으로 다시 테스트
  → same Candidate/GT context prefilled

Evaluate > Tests > New Test
  → Configure > Analysis Instructions
  → Configure > Input Schema

Connect > Production API
  → Configure > Input Schema

Promotion schema diff
  → Configure > Input Schema version detail
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
Production state classification: unconfigured / legacy_active / promoted
Production API credentials state + traffic observation state
운영 반영 검토 blocked-reason visibility
Initial assessment reserved admission metadata + comparison filter
Multiple Ground Truth Dataset selector/search metadata
Test → Ground Truth preview/confirm import
Test clone/prefill contract
Promotion preflight remediation targets
Recent comparable Test configuration identity

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
5. UI_TERMINOLOGY.md
6. UX_VISUALIZATION_GUIDE.md
7. IMPLEMENTATION_CHECKLIST.md
8. USER_WORKFLOW.md
9. visuals/
10. mockups_reconciled/ (layout reference only)
11. 과거 R5/R4/R3 및 이전 문서·목업
```

R5 FINAL에 포함되지 않은 화면은 과거 mockup을 canonical로 사용하지 않는다.
과거 mockup은 visual inspiration만 가능하며, IA/Navigation/Ground Truth/Promotion 동작은 반드시 R5 FINAL 문서를 따른다.
생성형 UI 이미지는 canonical source가 아니다.

# Mockup Fidelity / Implementation Quality Rule

R5 FINAL 목업은 **픽셀 단위 복제 대상이 아니다**.

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
2. 대표 화면을 먼저 production-quality로 완성
   - Overview — First Production / legacy_active / Normal
   - Ground Truth
   - Test Detail — 평가 상세
   - 운영 반영 검토 — blocked + eligible states
3. 1440×900 실제 브라우저 Screenshot Review
   - 목업과의 기계적인 일치보다 실제 가독성·위계·밀도·상호작용 품질을 우선
   - 문제가 있으면 공통 component/token을 먼저 수정
4. 대표 화면에 대해 checklist 기반 self-review와 Browser Screenshot QA를 완료한 뒤 동일한 Design System을 나머지 화면으로 확장. 사용자가 명시적으로 단계별 승인을 요청하지 않았다면 여기서 멈추지 말고 계속 구현한다.

금지:

- R5 FINAL PNG를 그대로 HTML/CSS로 tracing하는 구현
- 목업의 샘플 수치·샘플 문자열을 제품 데이터로 하드코딩
- 목업에 없는 실제 기존 기능을 삭제
- 목업과 맞추기 위해 접근성/URL privacy/보안 동작을 훼손
- 각 페이지마다 독자적인 버튼·테이블·탭 스타일을 새로 만드는 것

즉 구현 목표는 다음이다.

> **V5 목업과 동일한 정보 구조를 유지하되, 실제 코드에서는 목업보다 더 완성도 높은 production UI를 만든다.**
