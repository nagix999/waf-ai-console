# WAF AI Console — Final Review V5 REVIEWED R3 SOLO

## Final decision

R3 is the recommended canonical handoff.

### Sidebar

```text
Overview
Evaluate
Configure
Operate
Connect
```

This is intentionally not a lifecycle diagram. It prioritizes the solo engineer's repeated work: evaluate candidates and Ground Truth, then adjust configuration, while operational investigation remains directly available without making the product feel like a SOC console.

### Promotion

Promotion is not a persistent Sidebar item or Workspace Tab.

```text
Eligible Test / Overview Action Center
→ Contextual Promotion full page
→ Back to originating Test
```

The safety model remains unchanged: exact tested snapshot, official Published GT evaluation, preflight, stale-baseline protection, schema acknowledgement, atomic transaction, immutable history.

### Ground Truth

The solo workflow remains:

```text
Working Draft → automatic validation → Publish Revision
```

But R3 adds safety and recoverability:

- Working Changes (Added / Changed / Removed)
- per-case Revert to latest Published Revision
- Discard all working changes
- publish inclusion-rate preview
- one dataset-level acknowledgement when items are omitted
- immutable publish metadata

This avoids per-item approval bureaucracy without making silent dataset shrinkage easy.

### Tests

- Test stays in same Tests workspace detail mode
- Case opens Drawer
- `크게 보기` opens full Inference Detail
- return restores context
- list shows compact Accuracy / F1 / Coverage
- `평가지표 펼치기` is a disclosure button, not a checkbox

### Overview

Three visualizations remain:

1. Comparable Production Evaluation Trend
2. Ground Truth Working Draft Distribution
3. Recent Comparable Tests

R3 corrections:

- trend default y-axis = 0–100%
- if fewer than 2 comparable evaluations, show Current Official Evaluation summary instead of an empty/fake trend
- `Needs Attention` becomes `Action Center`
- Promotion-ready Candidate is treated as an action, not a warning

### Accessibility

SK/Light internal tab focus must never turn foreground white on a light background. Focus is indicated by `focus-visible` ring/outline while selected/inactive foreground semantics remain intact.

## Handoff readiness

R3 removes the remaining R2 contradictions: stale Approved-only audit language, old standalone Promote mockup references, and canonical references to obsolete V5 mockups.
