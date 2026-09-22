> Internal-domain note: `Candidate`, `Promotion`, `Preflight`, `Working Draft`, `Published Revision`
> may appear in this implementation checklist as code/domain terms.
> All rendered UI copy must follow `UI_TERMINOLOGY.md`.

# WAF AI Console — V5 REVIEWED R5 FINAL SOLO Implementation Checklist

## IA
- [ ] internal routes = Overview / Evaluate / Configure / Operate / Connect
- [ ] KR sidebar = 홈 / 평가 / 설정 / 운영 / 연동
- [ ] EN sidebar = Home / Evaluation / Configuration / Operations / Integrations
- [ ] persistent child sidebar 없음
- [ ] Evaluate tabs = Tests / Ground Truth / Production Evaluation
- [ ] Configure tabs = LLM Profiles / Agent Roles / Analysis Instructions / Input Schema
- [ ] Operate tabs = Runtime / Inference History / Activity
- [ ] Connect tabs = Production API / API Keys / vLLM Targets
- [ ] Promotion absent from Sidebar / Workspace Tabs
- [ ] terminal official Candidate Test → `Review Promotion` 진입 가능
- [ ] Overview Action Center는 promotion-ready Candidate만 `#promotion/{test_run_uuid}` 링크
- [ ] Promotion Review Back → originating Test
- [ ] blocked preflight에서도 Review 화면 진입 가능
- [ ] blocked checks expose remediation links
- [ ] Promotion Back restores origin + Source Test link
- [ ] actual Promote CTA만 eligibility + schema ack + stale baseline로 차단
- [ ] Promotion history → Operate > Activity

## Visual System
- [ ] Overview만 dashboard composition
- [ ] Card-grid default 금지
- [ ] Table = outer boundary + header surface + row dividers
- [ ] Section/Data/Inset surface 구분
- [ ] Neutral Primary / Brand Mutation / Secondary / Ghost / Icon / Danger 구분
- [ ] Promote CTA만 Brand Mutation
- [ ] visible Primary action 원칙적으로 1개
- [ ] split pane/version rail boundary 명확
- [ ] old V5/R2 mockup pixel tracing 금지

## Login
- [ ] username/password admin session only
- [ ] no invented SSO / OTP / social login
- [ ] no promotional/workflow explanation
- [ ] brand + form only
- [ ] auth error does not reveal unnecessary credential detail

## Overview
- [ ] Production health uses real readiness/heartbeat or Unknown
- [ ] Comparable Evaluation Trend implemented
- [ ] same comparison_key only
- [ ] x-axis = production config/time, not GT revision
- [ ] y-axis default 0–100%
- [ ] <2 comparable points → Current Official Evaluation fallback
- [ ] GT Ready / Needs attention / Excluded stacked bar
- [ ] Recent Comparable Tests = F1 mini bar + Accuracy + Coverage + compact configuration identity
- [ ] Action Center groups `Needs attention` / `Next steps`
- [ ] Promotion-ready Candidate is positive action, not error
- [ ] no fake KPI/chart fallback

## Evaluate / Tests
- [ ] Test name auto-generated; rename optional
- [ ] explicit Candidate Configuration always sent
- [ ] compact Evaluation = Accuracy / F1 / Coverage
- [ ] `평가지표 펼치기` = disclosure button + aria-expanded
- [ ] expanded = Accuracy / Precision / Recall / F1 / Coverage
- [ ] no evaluation = `—`, not 0%
- [ ] GT dataset/revision/sample visible
- [ ] different comparison_key direct delta 금지
- [ ] Test row → same workspace detail mode
- [ ] Case row → Drawer
- [ ] Drawer → `크게 보기`
- [ ] Full Detail = Result / Agent Trace / Input / Result JSON / Report
- [ ] return restores Test/Case/filter/search/scroll/metric mode
- [ ] terminal official Test → Review Promotion
- [ ] New Test purpose split = Official Evaluation / Development Test
- [ ] Test defaults visible prefill + explicit Candidate payload
- [ ] Run as new Test clones configuration/GT context without auto fallback
- [ ] Test list shows comparison-context reason
- [ ] GT-linked Case Drawer → matching Ground Truth Working Draft
- [ ] Test Detail `Add to Ground Truth…` supports create-new / append-existing
- [ ] Test→GT preview shows new/duplicate/conflict/missing-reference/unavailable counts
- [ ] deep assessment verdict never auto-becomes GT verdict

## Evaluate / Ground Truth
- [ ] no Draft/Reviewed/Approved workflow in new UI
- [ ] multiple Ground Truth Datasets supported
- [ ] Dataset selector searchable + Create Dataset
- [ ] selector shows published revision/case count/working changes/needs attention
- [ ] each Dataset has independent Working Draft + Published history
- [ ] Working Draft mutable
- [ ] item save does not create immutable item/dataset revision
- [ ] Ready/Needs attention = automatic validation
- [ ] Excluded = explicit user exclusion
- [ ] Working Changes = Added / Changed / Removed
- [ ] Added case can Discard addition
- [ ] Changed case can Revert to latest Published Revision
- [ ] Removed case can Restore from latest Published Revision
- [ ] Discard all working changes + destructive confirmation
- [ ] bulk Import / Include / Exclude / Tags / Category / Delete
- [ ] Test Run provenance preserved on imported cases
- [ ] Publish dialog shows Working total / Ready / Needs attention / Excluded / inclusion rate
- [ ] omitted items require one dataset-level acknowledgement
- [ ] Publish creates immutable Dataset Revision only once
- [ ] Published metadata stores inclusion counts/rate + working revision
- [ ] official evaluation uses Published Revision membership
- [ ] legacy revisions/evaluations preserved without rewrite
- [ ] stale working_revision conflict protection

## Production Evaluation / Promotion
- [ ] Production Evaluation is official snapshot, not live accuracy
- [ ] historical delta only same comparison context
- [ ] Promotion source = stored Test Run snapshot
- [ ] Primary / Verifier / Evidence / Instructions / Schema included
- [ ] Concurrency excluded
- [ ] official Published GT evaluation required
- [ ] stale production baseline check
- [ ] tested profile fingerprint check
- [ ] schema diff + explicit acknowledgement
- [ ] one atomic backend transaction
- [ ] direct Production mutation endpoints blocked for protected fields

## Configure
- [ ] LLM profile registry/validation features preserved
- [ ] Input Schema moved to Configure
- [ ] Input Schema version editor/compare/validate preserved
- [ ] Production Agent roles read-only
- [ ] Default Test Configuration editable from Evaluation > Tests
- [ ] Analysis Instructions versioned editor / working draft
- [ ] no Production direct Apply
- [ ] Agent Roles contextual link → Runtime Capacity

## Operate
- [ ] Runtime = real/unknown health + diagnostics + concurrency
- [ ] Inference History current filters/columns preserved
- [ ] Operations > Inference History lists Production analyses only
- [ ] no Test/All scope switch in Operations
- [ ] Test analyses remain under Evaluation > Tests
- [ ] initial mismatch uses compact badge, not extra column explosion
- [ ] Global Search routes Production Analysis → Operations, Test Analysis → matching Test/Case
- [ ] shared Inference Detail 5 tabs preserved with context-aware Back/breadcrumb
- [ ] AgentHistory actual run/step structure preserved
- [ ] raw input access audit preserved
- [ ] Activity categories include Promotion / Runtime / Configuration / Integration / Deployment
- [ ] actor primarily in detail/audit metadata
- [ ] deployment read-only

## Connect
- [ ] Production API TOC/search/copy/Swagger/OpenAPI/PDF preserved
- [ ] Production API → Configure/Input Schema contextual link
- [ ] API Key one-time raw display preserved
- [ ] vLLM target in-use/conflict rules preserved

## Accessibility / Theme
- [ ] SK default; Light/Dark selectable
- [ ] Light soft Mint tokens
- [ ] SK/Light focused tab text remains readable dark foreground
- [ ] focus-visible uses ring/outline, not white text mutation
- [ ] selected+focus-visible tested
- [ ] keyboard navigation / reduced motion / accessible Drawer/Dialog
- [ ] KR/EN preserves state

## URL / Security
- [ ] existing browserHistory/hash approach preserved
- [ ] no search/form/secret/raw payload in URL
- [ ] legacy routes remain safe aliases
- [ ] `#promote` never guesses a Candidate; goes to Tests selection
- [ ] OpenAI external approval/security preserved
- [ ] Activity ChangeEvent separate from AccessAudit

## QA
- [ ] backend tests
- [ ] frontend tests/build
- [ ] migrations tested on fresh + upgrade DB
- [ ] 1440×900 calibration screenshots: First Production / legacy_active / Normal Overview / Ground Truth / Test Detail / Promotion Review
- [ ] SK / Light / Dark visual QA
- [ ] KR / EN visual QA
- [ ] narrow/mobile-safe
- [ ] no synthetic production data hardcoded from mockups

## R5 First Production / Upgrade State
- [ ] backend classifies `unconfigured / legacy_active / promoted`
- [ ] Setup Guide appears only for `unconfigured`
- [ ] `legacy_active` gets Normal Overview + informational banner
- [ ] Setup steps exactly 5 = model / test configuration ready / published GT / official test / first promotion
- [ ] exactly one Next Action primary CTA
- [ ] first Production cannot bypass Candidate Test + Promotion
- [ ] Production API credentials are separate from Production traffic
- [ ] key exists != connected wording
- [ ] post-promotion missing credentials / no observed traffic surfaced in Action Center
- [ ] unconfigured Header may show `First Production · n/5` shortcut
- [ ] Setup generic `View` replaced by destination-specific labels

## R5 Test Detail Evaluation
- [ ] remove top Test Detail MetricStrip
- [ ] tabs = 문항별 결과 / 평가 상세
- [ ] 평가 상세 renders detailed evaluation inline
- [ ] remove 평가 상세 button/Dialog from Test Detail
- [ ] official 평가 상세 copy uses Published Ground Truth terminology, not 승인 답안 legacy wording
- [ ] confusion-matrix cell can filter item results
- [ ] return preserves selected detail tab

## R5 Initial Assessment
- [ ] initial_verdict + initial_probability validated as a pair
- [ ] initial assessment stored outside Agent-visible extra_fields
- [ ] initial_* names reserved from Input Schema/custom fields
- [ ] v1 accepts initial_* only on Production-purpose service API POST /analyses
- [ ] test/upload/GT paths reject initial_*
- [ ] never passed to Primary / Verifier / Editors
- [ ] mismatch warning uses 1차 판정 / 심층 판정 terminology
- [ ] final_inconclusive warning = 심층 판정이 보류되었습니다
- [ ] Inference History mismatch filter
- [ ] retry preserves initial assessment
- [ ] duplicate None↔provided or changed initial assessment does not silently overwrite


## UI terminology
- [ ] KR top nav = 홈 / 평가 / 설정 / 운영 / 연동
- [ ] EN top nav = Home / Evaluation / Configuration / Operations / Integrations
- [ ] UI copy follows UI_TERMINOLOGY.md
- [ ] Candidate/Promotion/Preflight/Revision/Working Draft are not default user-facing terms
- [ ] `Deploy` is not used for Production configuration application
- [ ] route/API/internal enum names remain unchanged

## Visual comprehension
- [ ] First Production uses compact 5-step progress, not blocking wizard
- [ ] New Test uses 3 numbered form sections, not multi-page flow
- [ ] Test→GT import = target → preview → confirm
- [ ] GT publish shows provenance distribution
- [ ] Production Review uses current→tested diff + readiness checks
- [ ] Initial vs Deep uses two-row comparison, not chart
- [ ] registries/history/config forms do not gain decorative charts
- [ ] visualizations answer trend/distribution/change/blocker/next-action


## Final user-centered gates
- [ ] `Production Evaluation` visible KR label = `운영 설정 평가`
- [ ] ready visible KR label = `포함 가능`; explain automatic validation, not human approval
- [ ] Home visual titles = 운영 평가 추세 / 정답 데이터 상태 / 최근 동일 기준 테스트
- [ ] Home visualizations render conditionally, maximum three; no empty chart slots
- [ ] Test list prioritizes changed config over full config identity
- [ ] bulk Test action = `테스트 사례를 정답 데이터에 추가`
- [ ] Test→GT preview duplicate/conflict/missing/unavailable counts are inspectable
- [ ] Ground Truth page has one-line help explaining what the dataset is
- [ ] UI Design Guidelines contains no obsolete sidebar order
- [ ] UI Implementation Spec contains no duplicate headings
- [ ] generic badge examples do not reintroduce Approved GT workflow


## Final contract closure
- [ ] TestConfigurationDefaults first-class backend state/API
- [ ] `test_purpose` persisted for new Test Runs
- [ ] clone template returns source + latest Ground Truth revision; source remains default
- [ ] `reference_origin` stored and publish metadata includes origin counts
- [ ] setup-status backend states match not_started/in_progress/needs_attention/ready/unknown
- [ ] Action Center backend returns per-Dataset Ground Truth attention counts
- [ ] Confusion Matrix uses explicit Ground Truth class × Deep Assessment class axes
- [ ] Test Detail distinguishes `실행 실패` from evaluation `불일치`
- [ ] current Production-evaluation Dataset/Version is visibly marked
- [ ] KR locale does not show English nav beside Korean nav
- [ ] Default Test Configuration is under Evaluation > Tests, not Agent Roles
