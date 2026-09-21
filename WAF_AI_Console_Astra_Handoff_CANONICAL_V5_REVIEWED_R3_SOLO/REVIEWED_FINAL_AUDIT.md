# V5 REVIEWED R3 SOLO — Final Audit

## Result

This package is internally aligned for Astra handoff after R3 corrections.

## Locked decisions

1. Sidebar = Overview / Evaluate / Configure / Operate / Connect.
2. Promotion is contextual full-page only; not Sidebar/Tab.
3. Production mutation remains Promotion transaction only.
4. New Tests explicitly pin Candidate Configuration.
5. Ground Truth UI = Working Draft → Publish Revision; no Draft/Reviewed/Approved workflow.
6. Working edits are mutable and recoverable; immutable history is created at Publish.
7. Official evaluation uses Published Dataset Revision membership.
8. Publish preview exposes inclusion rate and omitted counts; omissions require one dataset-level acknowledgement.
9. Overview visualizations use strict comparability and no fake trends.
10. Overview action list is named Action Center.
11. Test Case = Drawer + full-detail expansion.
12. Test metric expansion uses disclosure semantics.
13. SK/Light tab focus preserves readable foreground.
14. Legacy data/evaluation history is preserved without rewrite.
15. Old V5/R2 mockups are non-canonical unless explicitly copied into `mockups_r3/`.

## Remaining implementation discretion

Only code organization, component internals, optical CSS tuning, responsive breakpoints within the documented ranges, and other details that do not alter these product/data/workflow contracts.
