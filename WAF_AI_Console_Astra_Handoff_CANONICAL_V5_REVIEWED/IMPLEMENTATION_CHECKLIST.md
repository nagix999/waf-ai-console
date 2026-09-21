# WAF AI Console — V5 Implementation Checklist

## IA
- [ ] Sidebar = Overview / Configure / Evaluate / Promote / Operate / Connect
- [ ] persistent child sidebar 없음
- [ ] Workspace tabs 사용
- [ ] Drilldown 화면을 Sidebar에 추가하지 않음

## Visual V5
- [ ] Overview만 dashboard composition
- [ ] Card grid default 제거
- [ ] Registry pages = filters + table
- [ ] Ground Truth = workbench split pane
- [ ] Versioned pages = version rail + detail
- [ ] Promote = comparison/release gate
- [ ] Runtime = investigation + capacity
- [ ] Activity = timeline/list + detail
- [ ] Production API = TOC + document
- [ ] visible Primary Action 1개 원칙


## Visual V5 hierarchy
- [ ] Section Surface가 본문과 명확히 구분됨
- [ ] Table outer border/header background/row divider 적용
- [ ] 일반 text block과 data table이 즉시 구분됨
- [ ] 일반 Primary = neutral dark
- [ ] Promote = brand mutation color
- [ ] Danger = semantic danger color
- [ ] Button height/radius/padding token 통일
- [ ] Workspace tabs / segmented controls 구분
- [ ] Ground Truth/Agent Trace/version editor split boundary 명확
- [ ] Section header divider/context/action hierarchy 확인
- [ ] Card wall로 회귀하지 않음

## Theme
- [ ] SK default
- [ ] Light selectable
- [ ] Dark selectable
- [ ] no saved theme => SK
- [ ] Light = softer mint `#7CCFB1`, soft `#EFF9F5`, pale `#F7FCFA`
- [ ] Dark = green accent
- [ ] SK red/orange limited to brand/primary mutation
- [ ] Success remains green
- [ ] no SK logo/photo/building

## Configure
- [ ] LLM Profiles actual registry/validation features preserved
- [ ] Production Agent roles read-only
- [ ] Test defaults editable
- [ ] Analysis Instructions versioned editor
- [ ] no Production direct Apply

## Evaluate
- [ ] Tests list/new/detail unified
- [ ] explicit Candidate Configuration
- [ ] Ground Truth WAF cases only
- [ ] Draft/Reviewed/Approved
- [ ] Approved-only official evaluation
- [ ] Production Evaluation clearly official snapshot, not live accuracy

## Promote
- [ ] source = stored Test Run snapshot
- [ ] Primary/Verifier/Evidence/Instructions/Schema included
- [ ] Concurrency excluded
- [ ] baseline stale check
- [ ] fingerprint check
- [ ] schema diff + acknowledgement
- [ ] atomic backend transaction

## Operate
- [ ] real/unknown health
- [ ] Runtime includes diagnostics + concurrency
- [ ] Inference History retains current filters/columns
- [ ] Inference Detail 5 tabs
- [ ] AgentHistory actual structure preserved
- [ ] Raw input access audit preserved
- [ ] Activity includes promotion/runtime/config/integration/deployment
- [ ] deployment read-only

## Connect
- [ ] ProductionApi existing TOC/search/copy/Swagger/OpenAPI/PDF preserved
- [ ] API Key one-time raw display preserved
- [ ] Input Schema create/compare/validate preserved
- [ ] Input Schema direct apply/rollback removed
- [ ] vLLM target conflict/in-use logic preserved

## Backend
- [ ] explicit candidate prompt/schema version selection
- [ ] candidate configuration hash
- [ ] transactional promotion
- [ ] production configuration history
- [ ] GT review_status immutable transition
- [ ] runtime API
- [ ] activity change metadata

## UX
- [ ] selection bulk actions hidden at 0 selected
- [ ] Dialog close icon, footer cancel/save
- [ ] Danger delete
- [ ] polling backoff when hidden
- [ ] last updated
- [ ] URL privacy preserved
- [ ] KR/EN switch preserves state
- [ ] keyboard/focus/reduced motion

## QA
- [ ] backend tests
- [ ] frontend build/tests
- [ ] 1600×1000 + 1440×900 screenshots
- [ ] SK / Light / Dark
- [ ] KR / EN
- [ ] narrow/mobile-safe

## Visual Quality Gate

- [ ] V5 PNG를 pixel-perfect tracing하지 않음
- [ ] 공통 Design Primitive를 먼저 구현
- [ ] Overview를 실제 React로 production-quality 수준까지 polish
- [ ] Ground Truth를 실제 React로 production-quality 수준까지 polish
- [ ] Promote를 실제 React로 production-quality 수준까지 polish
- [ ] 대표 3화면 1440×900 Browser Screenshot review 완료
- [ ] 대표 3화면 self-review 전 나머지 화면에 page-specific CSS를 대량 적용하지 않음
- [ ] Button hover / pressed / focus / disabled 확인
- [ ] Table header / row / hover / selected state 확인
- [ ] Section / Data Surface / Inset Surface 구분 확인
- [ ] SK / Light / Dark 동일 component architecture 사용
- [ ] KR / EN에서 control width 및 wrapping 확인
- [ ] 목업 샘플 데이터를 하드코딩하지 않음
- [ ] 실제 기존 기능을 목업과 맞추기 위해 삭제하지 않음

## Ambiguity locks
- [ ] V5 UI always sends explicit `candidate_configuration` for new tests
- [ ] promotion requires an official Approved-only Ground Truth evaluation snapshot
- [ ] browser-admin direct Production mutation paths are disabled after Promotion endpoint lands
- [ ] Activity uses immutable `ChangeEvent`; `AccessAudit` remains security/access audit
- [ ] Runtime Configuration ID = latest successful `ProductionPromotion.id`
- [ ] Theme control = one popover/menu; default SK
- [ ] Locale storage = `waf-console-locale`, default `ko`
- [ ] no new router/i18n/UI/chart runtime dependency by default
- [ ] old hash routes remain aliases; search/form/secret values never enter URL
- [ ] root `WAF_AI_Analysis_Architecture_v0.1.md` is read despite stale path in `AGENTS.md`
- [ ] contextual cross-links 제공: LLM→vLLM Targets, Agent Roles→Runtime Capacity, New Test→Instructions/Schema, Production API→Schema
- [ ] cross-link가 duplicate mutation UI를 만들지 않음
