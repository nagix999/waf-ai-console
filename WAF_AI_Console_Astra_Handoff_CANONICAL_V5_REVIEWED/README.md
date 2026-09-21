# WAF AI Console — Astra Canonical Handoff V5

## Read order

1. `ASTRA_HANDOFF.md`
2. `BACKEND_CONTRACT.md`
3. `UI_IMPLEMENTATION_SPEC.md`
4. `UI_DESIGN_GUIDELINES.md`
5. `IMPLEMENTATION_CHECKLIST.md`
6. `FINAL_REVIEW.md`
7. `ui_mockups/canonical_v5/` — structural visual reference only

## Final IA

```text
Overview
Configure
Evaluate
Promote
Operate
Connect
```

## Final visual direction

- Overview only dashboard
- refined engineering shell
- explicit section/table hierarchy
- dense operational data
- restrained section surfaces instead of card wall
- redesigned button hierarchy
- SK default
- Light softer Mint/Emerald
- Dark Green accent

## Locked product rules

- Admin-only v1
- KR/EN
- Ground Truth = WAF Event/HTTP Request
- Draft → Reviewed → Approved
- Approved-only official evaluation
- Reference Label ≠ Ground Truth
- Production model/agent/instructions/schema changed only by Promote
- Candidate Prompt/Schema tested without Production activation
- Concurrency excluded from Promotion and managed in Runtime
- Deployment read-only

## Important visual implementation rule

V5 목업은 pixel-perfect 구현 대상이 아닙니다. IA / Page Archetype / 정보 우선순위를 유지하면서 실제 React component를 production-quality로 polish합니다. 먼저 `Overview`, `Ground Truth`, `Promote` 3개 화면을 구현·Screenshot review한 뒤 나머지 화면으로 확장합니다.

## Reviewed-final locks

- only `canonical_v5` mockups are included; V4 and older visuals are excluded
- V5 UI sends explicit Candidate Configuration for every new Test
- Promotion requires official Approved-only Ground Truth evaluation
- Production model/agent/instructions/schema writes are Promotion-only at UI **and backend admin mutation boundary**
- Activity source is immutable `ChangeEvent`, separate from `AccessAudit`
- Theme default `sk`; locale default `ko`
- existing custom hash router is extended; no new router/i18n/UI/chart dependency by default
