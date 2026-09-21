# WAF AI Console — Backend / Data Contract for Final UI

이 문서는 최종 UI를 실제 기능으로 구현하기 위해 필요한 Backend 정합성을 고정한다.

`AGENTS.md`에 따라 DB Migration과 운영 의존성 변경은 구현 전에 영향 범위를 명시한다.
이 handoff는 제품 요구 승인에 해당하지만, PR에는 Migration/Compatibility 설명을 남긴다.

---

# 1. 현재 구조에서 확인된 사실

현재 DB에는 이미 다음이 있다.

- `PromptPolicyState`: singleton active prompt
- `InputSchemaState`: singleton active schema
- Production/Test model assignment
- `TestRun`의 prompt/input-schema snapshots
- `ValidationDataset / ValidationDatasetVersion / ValidationDatasetItem`
- Test Run immutable membership / evaluation snapshots

따라서 최종 C안의 핵심 Gap은:

> Test Candidate가 **Production active Prompt/Input Schema를 바꾸지 않고도** 다른 saved version을 pin해서 실행할 수 있어야 한다.

---

# 2. Candidate Configuration

## Request concept

Test Run 생성 API에 `candidate_configuration`을 추가한다.

- **V5 UI는 모든 새 Test Run에서 이 객체를 명시적으로 전송해야 한다.**
- API 레벨에서 optional로 유지하는 것은 기존/legacy caller 호환만을 위한 것이다.
- V5 UI가 field 생략을 이용해 active Production Prompt/Schema에 암묵적으로 의존하면 안 된다.

```json
{
  "candidate_configuration": {
    "primary_profile_id": "uuid",
    "verifier_profile_id": "uuid-or-null",
    "evidence_editor_enabled": true,
    "evidence_editor_profile_id": "uuid-or-null",
    "prompt_policy_version_id": "uuid",
    "input_schema_version_id": "uuid"
  }
}
```

`null verifier_profile_id`는 Primary와 동일을 의미할 수 있다.

## Validation

- Profile 전체 검증/assignable 상태
- disabled 아님
- vLLM target allowed
- OpenAI external approval
- Prompt saved version 존재
- Input Schema saved version 존재
- Evidence Editor profile valid

## Snapshot

기존 TestRun fields를 최대한 재사용한다.

반드시 Promotion이 읽을 수 있도록 다음이 명시적으로 snapshot에 남아야 한다.

```text
Primary profile ID + fingerprint
Verifier profile ID + fingerprint
Evidence enabled/profile ID + fingerprint
Prompt policy version ID + content hash
Input schema version ID + content hash
```

반드시 다음을 추가한다.

```text
configuration_hash
```

Test 실행 중 snapshot을 바꾸지 않는다.

---

# 3. Prompt / Input Schema Selection

현재 TestRun service의 active singleton pin을 확장한다.

개념:

```python
pin_analysis_prompt(..., version_id=None)
pin_schema(..., version_id=None)
```

- `version_id is None`: 기존 default behavior
- explicit ID: saved version을 pin
- Production state는 바꾸지 않음

---

# 4. Production Mutation Rule

Production components:

```text
Primary
Verifier
Evidence Editor
Prompt
Input Schema
```

는 Promotion Transaction만 바꾼다.

기존 직접 mutation UI는 제거한다. Promotion endpoint가 도입된 뒤에는 browser-admin 경로에서 Production Primary/Verifier/Evidence/Prompt/Input Schema를 직접 변경할 수 없어야 한다.

기존 endpoint를 호환성 때문에 잠시 유지하더라도 Production field mutation은 거부하고, Test default 또는 비-Production 설정만 허용한다. Production mutation의 유일한 writable path는 Promotion transaction이다.

Concurrency는 예외이며 별도 Runtime 설정이다.

---

# 5. Promotion API

Canonical endpoint:

```text
POST /api/v1/admin/production-configurations/promote
```

Request:

```json
{
  "candidate_test_run_id": "uuid",
  "expected_production_configuration_hash": "sha256",
  "acknowledge_schema_change": true
}
```

Rules:

- `expected_production_configuration_hash` 불일치 → HTTP 409 `production_configuration_changed`; client는 최신 Production을 다시 조회하고 재검토해야 한다.
- schema가 변경됐는데 `acknowledge_schema_change != true` → HTTP 409 `schema_change_ack_required`.
- schema가 동일하면 acknowledgement 값은 승격 결과에 영향을 주지 않는다.
- client가 profile/prompt/schema ID를 별도로 보내 Promotion snapshot을 재구성하는 request shape은 만들지 않는다.

## Preflight

- candidate run completed
- snapshot complete
- tested fingerprints still match current profile definitions
- profiles not disabled
- external approval valid
- prompt/schema immutable versions exist
- baseline production hash not stale
- candidate snapshot에 연결된 **official Ground Truth evaluation이 반드시 존재**
- 해당 official evaluation은 pinned Ground Truth Dataset Revision의 `approved` item versions만 사용
- metric threshold를 자동으로 새로 만들지 않으며, 승격 여부 판단은 사용자에게 남긴다

## Transaction

한 DB Transaction에서:

1. Production primary 변경
2. Production verifier/evidence 변경
3. Prompt active version 변경
4. Input Schema active version 변경
5. Input schema activation history
6. promotion/configuration history
7. access audit
8. commit

실패 시 rollback.

Test defaults와 Concurrency는 변경하지 않는다.

---

# 6. Production Configuration ID / History

Operate > Runtime UI가 안정적으로 표시할 Configuration ID가 필요하다.

필수 immutable record:

```text
ProductionPromotion
- id
- source_test_run_id
- previous_configuration_hash
- configuration_hash
- snapshot_json (IDs/hashes only, no secrets/payload)
- actor_id
- created_at
```

기존 execution tables는 실제 active state의 source of truth로 유지할 수 있지만, `ProductionPromotion`은 immutable audit/configuration snapshot으로 반드시 남긴다.

Runtime의 `Configuration ID`는 **latest successful `ProductionPromotion.id`**를 사용한다.

---

# 7. Input Schema Promotion Safety

Candidate Schema가 Production active schema와 다르면 Promotion preflight response에:

```text
schema_changed
field_diff
compatibility_warnings
```

를 포함한다.

UI에서 API Contract 변경을 명확히 보여준다.

Breaking change를 자동으로 안전하다고 가정하지 않는다.

---

# 8. Ground Truth Review Status

기존 Validation Dataset 구조를 유지한다.

`ValidationDatasetItem` immutable revision에:

```text
review_status
```

추가.

Allowed:

```text
draft
reviewed
approved
```

DB CHECK constraint를 추가한다.

## Transition

Row를 update하지 않는다.

```text
old item version
→ new item version with new review_status
→ new dataset revision
```

## Rules

- Draft: verdict nullable
- Reviewed: verdict required
- Approved: verdict required
- Approved edit → new Draft revision
- Reference Label import → Draft
- pre-migration item → Draft
- old historical evaluation unchanged

---

# 9. Approved-only Official Evaluation

Ground Truth Dataset Revision에서:

```text
review_status == approved
```

인 item version만 official metric denominator에 포함한다.

Draft/Reviewed는 UI에서 관리하지만 official Accuracy/Precision/Recall/F1/Coverage에는 포함하지 않는다.

Test Run은 다음 provenance를 고정한다.

```text
dataset_id
dataset_revision
approved_item_version_ids
```

기존 `dataset_version_id` / immutable item IDs를 활용한다.

---

# 10. Reference Label

`AnalysisLabel`은 계속 별도 개념이다.

```text
Reference Label ≠ Ground Truth
```

Import 시 provenance 유지:

```text
source_kind
source_ref
ai_visible
created_by
original_analysis_id
```

Official Ground Truth가 되어도 provenance를 삭제하지 않는다.

---

# 11. Production Evaluation

Evaluate > Production Evaluation는 live request label accuracy가 아니다.

Source:

```text
현재 Production Configuration과 연결된 latest official Ground Truth Evaluation Snapshot
```

Promotion record가 `source_test_run_id`를 가지면 해당 공식 evaluation을 기본 표시할 수 있다.

추후 같은 Production config 재평가가 있으면 최신 것을 표시한다.

---

# 12. Baseline Comparison

Delta 계산은:

```text
same dataset ID
same dataset revision
same scope
same metric semantics
```

일 때만.

다르면 backend/client 모두 `comparable=false` 처리.

---

# 13. Runtime Status API

Mockup과 같은 Runtime 화면을 실제 데이터로 만들려면 다음 형태가 필요하다.

개념:

```text
GET /api/v1/admin/runtime/status?window=1h
```

Response 영역:

```text
production_configuration
health
queue
request_volume_series
latency_summary
latency_series(optional)
outcome_summary
recent_analyses
updated_at
```

Health:

```text
API
Analysis Worker heartbeat/readiness
Model Worker / assigned model 상태는 explicit readiness/heartbeat 또는 timestamp가 있는 documented recent-success window로만 계산
```

근거가 없으면 `unknown`, 절대 fake healthy 금지.

Window:

```text
1h
6h
24h
7d
```

Production Evaluation의 평가 범위와 분리할 수 있다.

---

# 14. Worker Heartbeat

현재 실제 heartbeat 근거가 없다면 별도 minimal heartbeat 저장/조회 설계를 추가한다.

Payload/PROMPT/민감 데이터는 heartbeat/metrics에 넣지 않는다.

---

# 15. Activity / Change History

현재 `AccessAudit`는 action/resource/time 중심이다.

최종 `Operate > Activity` UI의 source는 별도 immutable `ChangeEvent`로 고정한다.
`AccessAudit`는 보안/접근 감사 용도로 유지하고 Activity의 before/after history와 혼용하지 않는다.

`ChangeEvent` 포함:

```text
actor
action
resource
before summary
after summary
created_at
```

Secret/API key/raw payload는 기록하지 않는다.

---

# 16. Deployment v1

Read-only.

인프라 제어 API는 만들지 않는다.

가능한 Metadata만 제공:

```text
frontend version/image
api version/image
worker version/image
git commit
db schema revision
last deployment
```

없으면 `미제공`, fake data 금지.

---

# 17. Notification / Cost / Resource

현재 최종 v1 필수 아님.

Mockup에 보이더라도 아래는 Backend 데이터가 실제로 존재하기 전 구현하지 않는다.

```text
Notification badge
System alerts panel
CPU/GPU cards
Cost card
Token chart
```

P2 backlog로 유지.

---

# 18. Security

기존 원칙 유지:

- raw WAF payload/Cookie 마스킹하지 않음
- payload/agent I/O encrypted at rest
- payload를 log/metric에 쓰지 않음
- raw/agent access audit
- external OpenAI approval
- no real internal logs in fixtures

i18n/theme/runtime telemetry 추가가 이 원칙을 우회하면 안 된다.
