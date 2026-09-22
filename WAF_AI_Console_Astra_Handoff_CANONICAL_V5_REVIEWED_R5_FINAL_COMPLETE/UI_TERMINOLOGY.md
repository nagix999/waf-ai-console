# UI Terminology Contract — R5 FINAL

This file is the single source of truth for user-visible KR/EN copy.
Backend/API/domain identifiers remain unchanged.

## Navigation
| Internal | 한국어 | English |
|---|---|---|
| Overview | 홈 | Home |
| Evaluate | 평가 | Evaluation |
| Configure | 설정 | Configuration |
| Operate | 운영 | Operations |
| Connect | 연동 | Integrations |

## Workspaces
| Internal | 한국어 | English |
|---|---|---|
| Tests | 테스트 | Tests |
| Ground Truth | 정답 데이터 | Ground Truth |
| Production Evaluation | 운영 설정 평가 | Production Evaluation |
| LLM Profiles | LLM 프로필 | LLM Profiles |
| Agent Roles | Agent 역할 | Agent Roles |
| Analysis Instructions | 분석 지침 | Analysis Instructions |
| Input Schema | 입력 스키마 | Input Schema |
| Runtime | 실행 상태 | Runtime |
| Inference History | 분석 이력 | Inference History |
| Activity | 변경 이력 | Activity |
| API Keys | API 키 | API Keys |
| vLLM Targets | vLLM 연결 | vLLM Targets |

## Operations > Inference History
KR:
```text
운영 목적으로 처리된 분석만 표시합니다.
테스트 결과는 평가 > 테스트에서 확인합니다.
```
EN:
```text
Shows Production analyses only.
Test results are available under Evaluation > Tests.
```

## Test / Ground Truth / Production
| Internal | 한국어 | English |
|---|---|---|
| Candidate Configuration | 테스트 설정 | Test Configuration |
| TestConfigurationDefaults | 기본 테스트 설정 | Default Test Configuration |
| Official Evaluation | 공식 테스트 | Official Test |
| Development Test | 개발 테스트 | Development Test |
| Working Draft | 편집 중 | Draft |
| Published Revision | 공식 버전 | Published Version |
| Publish Revision | 공식 버전 만들기 | Publish Version |
| Promotion Review | 운영 반영 검토 | Production Review |
| Promote to Production | 운영에 반영 | Apply to Production |
| Preflight | 적용 전 점검 | Readiness Checks |
| Production Configuration | 현재 운영 설정 | Current Production Configuration |
| Run as new Test | 이 설정으로 다시 테스트 | Run Again as New Test |
| Ready | 포함 가능 | Ready to Include |
| Needs attention | 확인 필요 | Needs Attention |
| Excluded | 제외 | Excluded |
| Reference Label | 참고 라벨 | Reference Label |
| Initial Assessment | 1차 판정 | Initial Assessment |
| Deep Assessment | 심층 판정 | Deep Assessment |

### Ground Truth quality meaning
```text
포함 가능 / Ready to Include
= 자동 검증을 통과하여 다음 공식 버전에 포함할 수 있음
= 사람이 정답을 승인했다는 의미가 아님

확인 필요 / Needs Attention
= 누락·충돌·형식 문제 등 사람 확인 필요

제외 / Excluded
= 사용자가 의도적으로 공식 버전에서 제외
```

## Ground Truth actions
```text
Test Run bulk:
KR 테스트 사례를 정답 데이터에 추가
EN Add Test Cases to Ground Truth

Linked case:
KR 정답 데이터에서 열기
EN Open in Ground Truth

Single case import:
KR 이 사례를 정답 데이터에 추가
EN Add This Case to Ground Truth
```

Do not say `테스트 결과를 정답 데이터에 추가`.
The model/deep-assessment verdict is never automatically copied as Ground Truth.

## Home titles
| Internal | 한국어 | English |
|---|---|---|
| Comparable Production Evaluation Trend | 운영 평가 추세 | Production Evaluation Trend |
| Ground Truth Working Distribution | 정답 데이터 상태 | Ground Truth Status |
| Recent Comparable Tests | 최근 동일 기준 테스트 | Recent Tests on the Same Baseline |
| Action Center | 할 일 | Action Center |

## Current Production evaluation context
```text
KR 현재 운영 평가 기준
EN Current Production Evaluation
```

Official Test suggestion:
```text
KR 현재 운영과 동일한 평가 기준
EN Same evaluation baseline as current Production
```
This is a visible suggestion, not a hidden automatic choice.

## Test execution wording
```text
KR 처리 완료 1,013 · 실행 실패 7
EN Processed 1,013 · Execution failures 7
```
Evaluation mismatch is separate:
```text
KR 불일치 55
EN Mismatches 55
```

## Agent roles
KR:
```text
주 분석 모델 · Primary
검증 모델 · Verifier
근거 정리 모델 · Evidence Editor
```
EN:
```text
Primary
Verifier
Evidence Editor
```

## Help text
Ground Truth:
```text
KR 모델 판정을 평가할 때 기준으로 사용하는 WAF 요청 사례입니다.
EN WAF request cases used as the reference for model evaluation.
```

Official Test:
```text
KR 정답 데이터의 공식 버전으로 평가합니다. 운영 반영 검토에 사용할 수 있습니다.
EN Evaluates against a published Ground Truth version. Can be used for Production Review.
```

Development Test:
```text
KR 빠른 실험과 디버깅용입니다. 운영 반영 근거로 사용할 수 없습니다.
EN For quick experiments and debugging. Cannot be used for Production Review.
```

## Copy rules
Do not expose as default KR UI:
`Candidate / Promotion / Preflight / Revision / Working Draft / Comparable`.

English prefers:
`Test Configuration / Production Review / Apply to Production / Readiness Checks / Published Version / Draft`.

Do not use `Deploy` for configuration application.

## Internal names are not UI copy
Valid internally:
```text
candidate_configuration
production_promotion
promotion_ready
preflight
working_draft
working_revision
published_revision
comparison_key
analysis_purpose
test_purpose
reference_origin
```
