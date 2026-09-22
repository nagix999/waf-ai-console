# WAF AI Console — V5 REVIEWED R5 FINAL

This is the single canonical implementation handoff.

## Navigation
```text
KR: 홈 / 평가 / 설정 / 운영 / 연동
EN: Home / Evaluation / Configuration / Operations / Integrations
```

## Responsibility split
```text
평가 > 테스트
= Test Run / Test Case / evaluation / rerun / Ground Truth reuse / Production Review

운영 > 분석 이력
= Production analyses only
```

## Final user mental model
```text
정답 데이터 준비
→ 테스트
→ 결과 확인
→ 필요한 것 수정
→ 운영 반영 검토
→ 운영에 반영
→ 운영 확인
```

## Final decisions
- Default Test Configuration lives under Evaluation > Tests
- Official/Development purpose is persisted
- Ground Truth visible states = 포함 가능 / 확인 필요 / 제외
- `포함 가능` means automatic validation passed, not approval
- Test import wording = `테스트 사례를 정답 데이터에 추가`
- Clone keeps source Ground Truth version by default
- Current Production evaluation Dataset/Version is visibly marked
- Confusion Matrix uses explicit class axes
- `실행 실패` is distinct from evaluation mismatch
- Home visualizations are conditional, maximum three
- Operations Inference History is Production-only
- Global Search routes Test analyses to their Test/Case
- KR/EN render one language at a time

## Read order
1. ASTRA_HANDOFF.md
2. UI_TERMINOLOGY.md
3. BACKEND_CONTRACT.md
4. UI_IMPLEMENTATION_SPEC.md
5. UI_DESIGN_GUIDELINES.md
6. UX_VISUALIZATION_GUIDE.md
7. IMPLEMENTATION_CHECKLIST.md
8. USER_WORKFLOW.md
9. MOCKUP_INDEX.md
