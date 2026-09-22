# WAF AI Console — Backend / Data Contract V5 REVIEWED R5 FINAL SOLO

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

## 2.1 TestConfigurationDefaults

Default Test Configuration is a first-class server state.

```text
TestConfigurationDefaults
- primary_profile_id
- verifier_profile_id nullable
- evidence_editor_enabled
- evidence_editor_profile_id nullable
- prompt_policy_version_id
- input_schema_version_id
- revision
- updated_at
```

Conceptual API:
```text
GET   /api/v1/admin/test-configuration-defaults
PATCH /api/v1/admin/test-configuration-defaults
```

Rules:
- optimistic revision check
- validates profiles and saved versions
- never changes Production
- never changes existing TestRun snapshots
- UI entry point = Evaluation > Tests > Default Test Configuration

## 2.2 Visible prefill, explicit execution
```text
GET defaults
→ visible prefill
→ user may change values
→ explicit candidate_configuration submitted
→ immutable Test snapshot/hash
```
No hidden fallback to active Production values.

## 2.3 Test purpose
Every new TestRun stores:
```text
test_purpose = official_evaluation | development
```
Official:
```text
evaluation_mode = ground_truth
dataset_id required
dataset_revision_id required
```
Development:
- Single/File/Dataset allowed
- never Production Review evidence
- list/detail exposes test_purpose

Legacy ambiguous rows return `legacy_unknown`.

## 2.4 Clone / Run Again as New Test
```text
GET /api/v1/test-runs/{id}/clone-template
```
Response:
```text
candidate_configuration
test_purpose
evaluation_mode
source_dataset_id
source_dataset_revision_id
latest_published_dataset_revision_id
input_source_kind
resource_validity
```
Default selected revision = source_dataset_revision_id.
Never silently advance to latest.


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
- 해당 official evaluation은 pinned **Published Ground Truth Dataset Revision membership**만 사용
- metric threshold를 자동으로 새로 만들지 않으며, 승격 여부 판단은 사용자에게 남긴다

## Preflight remediation metadata

Each failed preflight check may expose a non-mutating remediation hint:

```json
{
  "code": "tested_profiles_still_valid",
  "passed": false,
  "remediation": {
    "kind": "open_llm_profile",
    "resource_id": "profile-id"
  }
}
```

Allowed conceptual kinds:

```text
open_llm_profile
open_instructions
open_input_schema
refresh_production
rerun_test
```

The hint is navigation guidance only; it never bypasses preflight or mutates Production.

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

# 8. Ground Truth Working Draft + Publish Model

1인 운영에서는 문항별 `Draft → Reviewed → Approved` workflow를 사용하지 않는다.

기존 immutable `ValidationDatasetVersion / ValidationDatasetItem`은 **Published snapshot/history**와 legacy compatibility 용도로 보존한다.

새 mutable working layer를 둔다.

개념:

```text
ValidationDatasetWorkingState
- dataset_id
- working_revision
- updated_at

ValidationDatasetWorkingItem
- id
- dataset_id
- stable_case_id
- event/schema snapshot fields
- reference_verdict nullable
- reference_origin: published_ground_truth | reference_label | manual | none
- reference_origin_ref_id nullable
- excluded boolean
- validation_state: ready | needs_attention
- validation_issues_json
- provenance/source fields
- comment/category/difficulty/case_name
- updated_at
```

UI quality state:

```text
excluded == true      → Excluded
else validation_state → Ready | Needs attention
```

`Ready / Needs attention`은 수동 승인 상태가 아니라 현재 Working Draft 데이터에 대한 validation 결과다.

Working item 저장은 같은 row를 update하고 `working_revision`만 증가시킨다. item revision이나 Dataset Revision은 생성하지 않는다.

## 8.1 Working Changes

Latest Published Revision과 Working Draft를 stable_case_id + normalized content hash로 비교하여:

```text
added
changed
removed
unchanged
```

를 계산한다.

UI/API는 최소:

```text
working_revision
latest_published_revision_id
added_count
changed_count
removed_count
```

을 제공한다.

## 8.2 Revert / Restore / Discard

Change type별 canonical behavior:

### Added

Published Revision에 없는 Working-only case.

```text
Discard addition
```

Working item을 제거하고 `working_revision`을 증가시킨다.

### Changed

Published Revision에도 존재하는 수정 case.

```text
POST /api/v1/validation-datasets/{dataset_id}/working/items/{case_id}/revert
```

latest Published snapshot으로 Working item을 복구한다.

### Removed

Published Revision에는 있지만 Working Draft에서 삭제된 case.

```text
POST /api/v1/validation-datasets/{dataset_id}/working/items/{case_id}/restore
```

latest Published snapshot을 이용해 Working item을 다시 생성한다.

모든 action은 `expected_working_revision`을 검사한다.
published snapshot이 없거나 case가 해당 Published Revision에 없으면 명확한 404/409를 반환한다.

전체 Working Draft:

```text
POST /api/v1/validation-datasets/{dataset_id}/working/discard
```

- latest Published Revision을 Working Draft로 복원
- destructive confirmation은 UI에서 수행
- server도 expected_working_revision 검사

## 8.3 Publish transaction

Canonical concept:

```text
POST /api/v1/validation-datasets/{dataset_id}/publish
```

Request 최소:

```json
{
  "expected_working_revision": 42,
  "acknowledge_exclusions": true
}
```

Publish 시:

1. Working Draft 전체 재검증
2. `excluded=false AND validation_state=ready` 항목만 include
3. Working total / Ready / Needs attention / Excluded / inclusion rate 계산
4. Needs attention 또는 Excluded가 1건 이상인데 acknowledgement가 false면 409 `ground_truth_exclusion_ack_required`
5. 포함 항목을 immutable `ValidationDatasetItem` snapshot으로 생성
6. 새 `ValidationDatasetVersion` 생성 + immutable membership/hash 고정
7. publish metadata 저장
8. ChangeEvent 기록
9. commit

Published metadata 최소:

```text
working_total_at_publish
included_ready_count
needs_attention_count
excluded_count
inclusion_rate
working_revision_at_publish
included_reference_origin_counts
```

`Needs attention`이 존재한다고 publish 자체를 막지는 않는다. 다만 사용자가 Dataset 수준에서 제외 사실을 한 번 확인해야 한다.

## 8.4 Legacy compatibility

기존 `ValidationDatasetItem.review_status`와 과거 revisions/evaluations은 삭제하거나 재작성하지 않는다.

신규 published snapshot이 기존 table constraint를 재사용해야 한다면 `review_status='approved'`를 **storage compatibility 값**으로 기록할 수 있다. 이 값은 신규 UI/API의 사용자 workflow가 아니며 official evaluation selection 조건으로 사용하지 않는다.

신규 UI/API에는 Draft/Reviewed/Approved transition을 노출하지 않는다.

새 official evaluation은 새 Publish flow로 생성된 immutable Dataset Revision membership을 기준으로 한다. 과거 저장 evaluation snapshot은 기존 의미 그대로 보존한다.

# 8.5 Multiple Ground Truth Datasets

System supports multiple independent `ValidationDataset` records.

Each Dataset owns:

```text
ValidationDatasetWorkingState
ValidationDatasetWorkingItem set
working_revision
Published ValidationDatasetVersion history
```

No global singleton Ground Truth Dataset is introduced.

Dataset list/search response should include enough UI metadata:

```text
id
name
latest_published_revision
published_case_count
working_revision
working_change_count
ready_count
needs_attention_count
excluded_count
updated_at
```

Official Test pins exactly one `dataset_id + dataset_revision_id`.

Comparison context always includes Dataset identity; same revision number in two different Datasets is not comparable.

# 8.6 Test → Ground Truth Import

A terminal Test can be reused as Ground Truth Working Draft input.

Two targets:

```text
create_new_dataset
append_to_existing_dataset
```

This operation **never publishes automatically** and **never treats the Test's model verdict as Ground Truth**.

## Preview

Conceptual endpoint:

```text
POST /api/v1/test-runs/{test_run_id}/ground-truth-import/preview
```

Request:

```json
{
  "target": "create_new_dataset | append_to_existing_dataset",
  "dataset_id": "uuid-or-null",
  "new_dataset_name": "optional",
  "expected_working_revision": 42
}
```

For a new Dataset, dataset_id/working_revision are omitted.
For an existing target, preview binds the current working revision.

Preview response minimum:

```text
preview_token
expires_at
source_test_run_id
source_total
importable
new_count
duplicate_count
reference_conflict_count
missing_reference_count
unavailable_count
target_dataset_summary
```

## Confirm

Conceptual endpoint:

```text
POST /api/v1/test-runs/{test_run_id}/ground-truth-import/confirm
```

Request:

```json
{
  "preview_token": "...",
  "idempotency_key": "..."
}
```

Confirm verifies:

```text
same source Test snapshot
same target Dataset
same expected working_revision for existing target
preview not expired
item-limit/capacity still valid
```

Existing target changed since preview → 409 `ground_truth_working_changed`.

One confirmed bulk import increments target `working_revision` once and records one ChangeEvent summary.

## Importable cases

Only Test items with an available accepted Analysis/event can be copied.

Rejected/unavailable Test rows are counted and skipped; do not fabricate events.

Copied event content is the exact stored Analysis observation.
Source Candidate schema/configuration is provenance; target Working Draft runs normal validation and may mark a case `Needs attention`.

Internal-only restrictions are preserved.

## Reference verdict resolution

Never use `Analysis.verdict` / deep assessment verdict as `reference_verdict`.

Use this order:

1. Source Test item pinned to Published Ground Truth
   → copy fixed verdict; `reference_origin=published_ground_truth`
2. Else TestRunItem has fixed Reference Label
   → copy fixed verdict + provenance; `reference_origin=reference_label`
3. Else `reference_verdict = null`; `reference_origin=none`
   - import the case input
   - mark `Needs attention`
   - issue `reference_verdict_missing`

The source deep assessment verdict may be displayed only as read-only provenance/supporting metadata such as:

```text
source_analysis_verdict
```

It must not make the case ready by itself and must not become an official answer automatically.
If the user edits/sets the verdict in Ground Truth, set `reference_origin=manual`.

## Duplicate/conflict

If same target input identity already exists:

```text
same fixed reference meaning → duplicate skip
different reference verdict  → conflict; never overwrite automatically
```

Conflict is returned in the import result and resolved in Ground Truth.

## Provenance

Working item/import provenance retains at least:

```text
source_test_run_id
source_test_run_item_id
source_analysis_id
source_dataset_id
source_dataset_revision_id
source_dataset_item_version_id
source_reference_label_id
source_kind
imported_at
```

Creating a new Dataset from a Test creates Dataset + Working State + imported Working Items in one transaction, but **no Published Revision**.

# 9. Published Revision Official Evaluation

새 official Ground Truth evaluation denominator:

```text
selected Published Dataset Revision의 immutable membership
```

이다.

다음을 기준으로 추가 필터링하지 않는다.

```text
review_status
actor
manual approval count
```

Test Run은 최소한 다음 provenance를 고정한다.

```text
dataset_id
dataset_revision_id
dataset_hash
metrics_version
evaluation scope/filter definition
```

각 TestRunItem의 `dataset_item_version_id`로 실제 case membership을 추적한다.

기존 `approved_item_version_ids`는 legacy run 호환용으로 유지할 수 있으나 신규 run의 canonical source로 사용하지 않는다.

공식 평가에 Working Draft를 직접 사용하지 않는다.

비공식 개발 Test에서 Working Draft 지원이 필요하면 결과에 `unpublished`를 명시하고 Promotion evidence로 사용할 수 없게 한다.

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

# 12.1 Test List Evaluation Summary

Tests 목록이 detail 진입 없이 비교 가능하도록 list API에 evaluation summary를 포함한다.

최소:

```text
accuracy
precision
recall
f1
coverage
sample_count
dataset_id
dataset_revision_id
metrics_version
evaluation_scope_hash
comparison_key
```

`comparison_key`는 최소:

```text
dataset_revision_id + metrics_version + normalized scope/filter definition
```

을 반영한다.

같은 comparison_key가 아닌 Test끼리는 UI가 delta/winner처럼 직접 비교하지 않는다.

평가가 없으면 metrics는 `null`; 0으로 치환하지 않는다.

---

# 12.2 Overview Visualization Data

기존 `/api/v1/dashboard/summary`를 backward-compatible하게 확장하거나 별도 admin overview endpoint를 제공한다.

Overview에는 실제 데이터가 있을 때만 다음을 반환한다.

## Comparable Production Evaluation Trend

- latest official Production Evaluation의 comparison_key 기준
- 동일 comparison_key인 historical Production Configuration evaluation만 포함
- x축 identity = production_configuration_id / evaluated_at
- points < 2이면 UI는 chart 대신 current official evaluation summary + comparison context changed 상태

## Ground Truth Working Distribution

latest official Production Evaluation이 사용한 dataset에 대해:

```text
latest_published_revision
working_change_count
ready_count
needs_attention_count
excluded_count
```

latest Production Evaluation context가 없으면 dataset을 임의로 추측하지 않는다.

## Recent Comparable Tests

latest official Production Evaluation과 같은 comparison_key의 최근 Test 최대 5개.

```text
test_run_id
name
created_at
accuracy
f1
coverage
sample_count
primary_profile_name_or_id
prompt_policy_version
input_schema_version
changed_components_from_previous_comparable (optional)
```

fake/synthetic runtime data를 Overview fallback으로 만들지 않는다.

## Action Center

Overview API는 가능한 경우 다음 actionable counts/links를 제공한다.

```text
ground_truth_needs_attention_total
ground_truth_needs_attention_dataset_count
ground_truth_needs_attention_datasets[]
  - dataset_id
  - name
  - count
failed_tests
recent_inference_failures
promotion_ready_candidates
```

Promotion-ready Candidate는 problem/error가 아니라 action item이다. UI가 Needs Attention으로 오표현하지 않는다.

---

# 12.3 First Production / Production State

Overview가 기존 운영 설치를 오판하지 않도록 Production state를 server가 명시적으로 분류한다.

개념 endpoint:

```text
GET /api/v1/admin/setup-status
```

Response concept:

```json
{
  "production_state": "unconfigured|legacy_active|promoted",
  "steps": {
    "model_connection": {"state": "not_started|in_progress|needs_attention|ready|unknown"},
    "test_configuration": {"state": "not_started|needs_attention|ready|unknown", "missing": []},
    "ground_truth_published": {"state": "not_started|needs_attention|ready|unknown", "dataset_id": null, "dataset_revision_id": null},
    "official_candidate_test": {
      "state": "not_started|in_progress|needs_attention|ready|unknown",
      "test_run_id": null,
      "processed": null,
      "execution_failures": null,
      "total": null,
      "warning_count": null
    },
    "first_promotion": {"state": "not_started|needs_attention|ready|unknown", "test_run_id": null}
  },
  "production_api_credentials": "ready|missing|unknown",
  "production_api_traffic": {"status": "observed|not_observed|unknown", "last_observed_at": null, "since": null},
  "next_action": {"kind": "run_official_candidate_test", "target": "evaluate_tests", "resource_id": null}
}
```

State meanings:
```text
not_started
in_progress
needs_attention
ready
unknown
```

Official Test can be `ready` when terminal + official evaluation snapshot exists even if some Case executions failed; expose those as warnings.
A failed Test Run or missing official evaluation snapshot is `needs_attention`.
No metric threshold is invented here.

## Production state classification

### promoted

successful `ProductionPromotion(kind='promotion')`이 하나 이상 존재.

Promotion 이후 live configuration drift는 별도 `drifted` state로 표현하며 `legacy_active`로 되돌리지 않는다.

### legacy_active

successful promotion record는 없지만 **현재 live Production configuration이 실제 실행 가능한 완성 상태**다.

최소 조건:

```text
Production Primary profile assigned and exists
required verifier/evidence role resolution valid
active Analysis Instructions exists
active Input Schema exists
required profiles are not disabled
```

baseline record 존재만으로 판단하지 않는다.
과거 Production traffic 유무도 필수 조건으로 사용하지 않는다.

### unconfigured

위 두 상태가 아니며 live Production configuration이 완성되지 않음.

**First Production Setup Guide는 이 상태에서만 표시한다.**

## Setup step semantics — 5 steps

```text
model_connection
test_configuration
ground_truth_published
official_candidate_test
first_promotion
```

`test_configuration`은 독립 Candidate object 존재 여부가 아니다.
Test에서 explicit Candidate Configuration을 구성할 수 있는 required resources가 모두 존재/유효한지 뜻한다.

`official_candidate_test.state=ready` means a terminal Official Test with an official evaluation snapshot exists.
Case-level execution failures are warnings unless the Test Run/evaluation itself failed.
Production Review eligibility is calculated separately.

## Production API readiness after promotion

Production API는 setup 5단계에 포함하지 않는다.

### production_api_credentials

`ready`는 non-revoked / non-deleted Production-purpose Service API Key가 최소 1개 있다는 뜻이다.
이 상태만으로 caller가 연결됐다고 표현하지 않는다.

### production_api_traffic

가능하면 실제 Production service API ingress를 관측한다.

`promoted` 상태에서는 최신 promotion `created_at` 이후:

```text
analysis_purpose = production
ingest_channel = service_api
```

인 Analysis가 하나라도 있으면 `observed`.

없으면 `not_observed`.

`legacy_active`는 최신 Production service API observation timestamp를 제공하되, current configuration과 정확히 연결할 근거가 없으면 `since=null`과 함께 단순 informational observation으로만 사용한다.

`unknown`은 필요한 관측 데이터/쿼리가 신뢰 가능하게 계산되지 않을 때 사용한다.

UI는:

```text
credentials ready
traffic not observed
```

를 `connected` 하나로 합치지 않는다.

## Promotion Review availability

Test Detail에서 Promotion Review 진입은 실제 Promote eligibility와 분리한다.

Review 진입 가능 조건:

```text
explicit Candidate Configuration snapshot 존재
evaluation_mode == ground_truth
Test가 terminal state
```

preflight check 실패가 있어도 Review 화면은 열려야 한다.

실제 `Promote to Production` mutation은 기존 preflight `eligible == true`와 schema acknowledgement / stale baseline 검사를 모두 통과해야 한다.


# 13.1 Production Analysis History / Global Search Routing

`Operations > Inference History` is a Production-only UI surface.

The backend may retain generic analysis query capability, but the Operations list request must constrain:

```text
analysis_purpose = production
```

Test analyses are retrieved through their Test Run / TestRunItem relationships for `Evaluation > Tests`.

Global Search may search both contexts. Search result concept:

```json
{
  "kind": "analysis",
  "analysis_id": "uuid",
  "context": "production|test",
  "test_run_id": "uuid-or-null",
  "test_run_item_id": "uuid-or-null"
}
```

Frontend routing:

```text
context=production → Operations > Inference History / Detail
context=test       → Evaluation > Tests / matching Test Case
```

Never force a Test Analysis into Operations just because it shares the `Analysis` table/model.

The analysis detail serializer/component may remain shared.

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

# 18. Initial Assessment Metadata

외부 BERT 기반 1차 판정은 event evidence와 분리된 **server-reserved admission metadata**다.

## Accepted surface — v1

v1에서는 다음 경로에만 허용한다.

```text
POST /api/v1/analyses
authenticated by Production-purpose service API key
```

다음 경로에서는 reject한다.

```text
Test-purpose service API
Admin /test-analyses
Production/Test file uploads
Ground Truth import/edit
generic extra_fields
Input Schema custom field definitions
```

Reserved names:

```text
initial_verdict
initial_probability
initial_model_version
```

Input Schema field registry / extra-field policy는 이 이름을 사용자 필드로 등록할 수 없게 해야 한다.

Request:

```text
initial_verdict       true_positive | false_positive | null
initial_probability   float 0.0..1.0 | null
initial_model_version string | null
```

Validation:

- verdict와 probability는 둘 다 없거나 둘 다 있어야 한다.
- model_version은 verdict/probability pair가 있을 때만 허용한다.
- probability는 **제공된 initial_verdict class의 confidence**다.
- NaN / Infinity / 0..1 범위 밖 거부.
- first-class `Analysis` columns에 저장하고 `extra_fields`에는 넣지 않는다.
- `AnalysisRequest.event_input()` / Agent event document / prompt snapshot에서 제외한다.
- event fingerprint의 inference evidence identity에는 포함하지 않는다.
- duplicate admission은 기존 저장 metadata와 **정확히 동일할 때만** duplicate로 허용한다.
  - `None → provided`
  - `provided → None`
  - verdict/probability/model_version 값 차이
  모두 `409 initial_assessment_conflict`.
- retry는 original initial assessment snapshot을 그대로 복사한다.
- Ground Truth / Reference Label / official evaluation answer로 자동 사용하지 않는다.

Response concept:

```json
{
  "initial_assessment": {
    "verdict": "false_positive",
    "probability": 0.93,
    "model_version": "bert-waf-v3.4",
    "comparison": "different"
  }
}
```

comparison은 저장하지 않고 현재 final verdict에서 계산한다.

```text
unavailable
pending
match
different
final_inconclusive
```

`AnalysisSummary` / `AnalysisDetail`에는 필요한 initial assessment metadata와 derived comparison을 제공한다.
Inference History server query에는 다음 filter를 추가한다.

```text
initial_comparison =
  match | different | final_inconclusive | unavailable
```

Agent independence가 최우선이다.
Primary / Verifier / Evidence Editor / Result Editor 어디에도 initial assessment를 주입하지 않는다.
