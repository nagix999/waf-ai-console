# WAF AI Console — Final Review V5

## Decision

최종 IA는 다음 Lifecycle 축으로 고정한다.

```text
Overview / Configure / Evaluate / Promote / Operate / Connect
```

V5의 핵심 변경은 **Visual System**이다.

## Why previous visual approaches were rejected

- 모든 화면이 `제목 → 탭 → 흰 박스 → 표`로 반복됨
- Card/border가 지나치게 많아 admin template 느낌
- 서로 다른 업무가 같은 composition으로 보임
- SK Red가 반복되어 시선 분산
- 화면별 고유 작업이 충분히 드러나지 않음

## V5 resolution

```text
Overview             Narrative Dashboard
LLM Profiles         Registry
Agent Roles          Configuration Editor
Instructions         Versioned Editor
Tests                Resource List + Workflow
Ground Truth         Dataset Workbench
Production Eval      Evaluation Result
Promote              Comparison / Release Gate
Runtime              Investigation + Capacity
Inference History    Search/List
Activity             Audit History
Production API       Documentation
API Keys             Credential Registry
Input Schema         Versioned Contract Editor
vLLM Targets         Endpoint Registry
Inference Detail     Investigation Detail
```

## Theme

```text
SK      default, neutral shell + limited red/orange
Light   softer Mint/Emerald, minimal dark green area
Dark    Navy/Charcoal + Green
```

## Canonical visual source

`ui_mockups/canonical_v5/`

V4 및 그 이전 문서/목업과 생성형 UI 이미지는 canonical이 아니다. Reviewed package에는 V5 목업만 포함한다.


## V5 review result

최종 IA/Page Archetype을 유지하면서 V5는 다음 시각 문제를 해결한다.

```text
V4 issue: section/text/table 경계가 약함
V5: bounded Section Surface + explicit Data Surface

V4 issue: button hierarchy가 밋밋함
V5: Neutral Primary / Brand Mutation / Secondary / Ghost / Danger

V4 issue: split panes가 단순 나란한 영역처럼 보임
V5: shared workspace boundary + internal divider
```

V5 이후에는 IA 탐색보다 실제 구현/QA 단계로 이동한다.

# Final Visual Implementation Decision

V5 목업은 최종 IA와 화면 구조를 설명하는 canonical visual reference지만, 픽셀 단위의 최종 제품 디자인으로 취급하지 않는다.

최종 제품 디자인은 실제 React 구현에서 공통 Design System을 적용한 뒤 Overview / Ground Truth / Promote의 Browser Screenshot review를 통해 확정한다.

따라서 Astra는:

> V5 목업을 픽셀 단위로 복제하지 말 것. 목업은 IA, 정보 배치, Page Archetype 참고용이다. `UI_DESIGN_GUIDELINES.md`를 기반으로 reusable component를 구현하고, Overview / Ground Truth / Promote 세 화면을 먼저 production-quality로 완성하고 Browser Screenshot self-review로 공통 Design System을 교정한 뒤 나머지 화면으로 계속 확장할 것. 사용자가 단계별 승인을 명시하지 않았다면 중간에 멈춰 승인을 기다리지 말 것.

을 구현 원칙으로 삼는다.

## Handoff readiness

Reviewed Final에서는 다음 모호성을 제거했다.

- Light token 단일화 (`#7CCFB1 / #EFF9F5 / #F7FCFA`)
- 일반 Primary와 Brand Mutation의 역할 분리
- Promotion의 official Approved-only Ground Truth evaluation 필수화
- direct Production mutation backend boundary 차단
- Activity source를 `ChangeEvent`로 고정
- Theme/Locale control 및 storage key 고정
- route/legacy alias contract 고정
- legacy V4 mockup 제거

이 문서 세트 이후에는 IA/도메인 결정을 다시 해석하지 말고 구현/QA 단계로 진행한다.
