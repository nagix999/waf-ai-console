# WAF AI Console — V5 REVIEWED R3 SOLO Implementation Checklist

## IA
- [ ] Sidebar = Overview / Evaluate / Configure / Operate / Connect
- [ ] persistent child sidebar 없음
- [ ] Evaluate tabs = Tests / Ground Truth / Production Evaluation
- [ ] Configure tabs = LLM Profiles / Agent Roles / Analysis Instructions
- [ ] Operate tabs = Runtime / Inference History / Activity
- [ ] Connect tabs = Production API / API Keys / Input Schema / vLLM Targets
- [ ] Promotion absent from Sidebar / Workspace Tabs
- [ ] eligible Candidate / Action Center만 `#promotion/{test_run_uuid}` 진입
- [ ] Promotion Back → originating Test
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
- [ ] Recent Comparable Tests = F1 mini bar + Accuracy + Coverage
- [ ] `Action Center` naming
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
- [ ] eligible Test → contextual Promotion

## Evaluate / Ground Truth
- [ ] no Draft/Reviewed/Approved workflow in new UI
- [ ] Working Draft mutable
- [ ] item save does not create immutable item/dataset revision
- [ ] Ready/Needs attention = automatic validation
- [ ] Excluded = explicit user exclusion
- [ ] Working Changes = Added / Changed / Removed
- [ ] changed case can Revert to latest Published Revision
- [ ] Discard all working changes + destructive confirmation
- [ ] bulk Import / Include / Exclude / Tags / Category / Delete
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
- [ ] Production Agent roles read-only
- [ ] Test defaults editable
- [ ] Analysis Instructions versioned editor / working draft
- [ ] no Production direct Apply
- [ ] Agent Roles contextual link → Runtime Capacity

## Operate
- [ ] Runtime = real/unknown health + diagnostics + concurrency
- [ ] Inference History current filters/columns preserved
- [ ] Inference Detail 5 tabs preserved
- [ ] AgentHistory actual run/step structure preserved
- [ ] raw input access audit preserved
- [ ] Activity categories include Promotion / Runtime / Configuration / Integration / Deployment
- [ ] actor primarily in detail/audit metadata
- [ ] deployment read-only

## Connect
- [ ] Production API TOC/search/copy/Swagger/OpenAPI/PDF preserved
- [ ] API Key one-time raw display preserved
- [ ] Input Schema create/compare/validate preserved
- [ ] Input Schema direct Production apply/rollback removed
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
- [ ] 1440×900 calibration screenshots: Overview / Ground Truth / Contextual Promotion
- [ ] SK / Light / Dark visual QA
- [ ] KR / EN visual QA
- [ ] narrow/mobile-safe
- [ ] no synthetic production data hardcoded from mockups
