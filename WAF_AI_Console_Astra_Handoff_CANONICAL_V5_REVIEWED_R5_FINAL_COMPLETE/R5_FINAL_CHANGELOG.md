# R5 FINAL Change Audit

## Changed from R4

### First Production
- Setup: 6 → 5 steps
- `Candidate configuration` → `Test configuration ready`
- API credentials/traffic moved to Normal Overview Action Center
- Production state added: unconfigured / legacy_active / promoted

### Promotion
- terminal official Candidate Test can always open Review Promotion
- preflight failures are inspectable
- actual mutation remains eligibility-gated

### Ground Truth
- Added → Discard addition
- Changed → Revert to published
- Removed → Restore from published

### Initial assessment
- v1 accepted only on Production-purpose service API `POST /analyses`
- reserved from Input Schema and generic extra fields
- explicit final-inconclusive warning

### Test Detail
- official evaluation copy must use Published Ground Truth terminology

## Preserved
- final IA
- contextual Promotion
- Working Draft / publish-only Ground Truth
- flat Tests workspace + Case Drawer/full detail
- Overview 3 visualizations
- SK/Light focus regression fix

## Usability consolidation

- Input Schema moved to Configure; old `#connect/input-schema` remains an alias.
- New Test: Official Evaluation vs Development Test.
- Candidate Configuration: visible Test-default prefill while keeping explicit request payload.
- Run as new Test clones source configuration and GT context.
- Test Detail can deep-link to matching Ground Truth Working Draft case.
- Promotion blocked checks expose remediation actions and preserve entry-origin Back behavior.
- Inference History defaults to Production; initial mismatch uses compact badge.
- Overview Recent Tests include compact configuration identity; Action Center groups attention vs next steps.
- Ground Truth explicitly supports multiple independent Datasets with richer selector metadata.
- Test → Ground Truth:
  - create new Dataset from Test
  - append all importable cases to existing Dataset
  - Preview → Confirm
  - duplicates/conflicts/missing reference reported
  - deep assessment verdict is never auto-used as Ground Truth



## Humanized language and visualization pass

- KR navigation: 홈 / 평가 / 설정 / 운영 / 연동
- EN navigation: Home / Evaluation / Configuration / Operations / Integrations
- Candidate Configuration → 테스트 설정 / Test Configuration
- Promotion Review → 운영 반영 검토 / Production Review
- Promote to Production → 운영에 반영 / Apply to Production
- Preflight → 적용 전 점검 / Readiness Checks
- Published Revision → 공식 버전 / Published Version
- Working Draft → 편집 중 / Draft
- Added UI_TERMINOLOGY.md
- Added UX_VISUALIZATION_GUIDE.md
- Added browser-viewable KO/EN simple user-flow references
- Added visualization budget to prevent chart/dashboard overuse
