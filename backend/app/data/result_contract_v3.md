# Security analysis result v3

This is a result contract and candidate-instruction change, not an automatic
Production configuration upgrade. The implementation starts from `eea2527`.

## New executions

Candidates pinned to `waf-system-v2.15` or `waf-system-v2.16` generate
`waf-analysis-v3`. Approved
Production instructions, already queued jobs and failed-job retries retain
their encrypted pinned instructions. In particular, v2.14 remains available
unchanged; the v2.15 prompt module is also frozen byte-for-byte. Test and
Production share the selected instruction content; changing
Production still requires an approved official evaluation and administrator
review/apply.

Every object in v3 has a declared model with `additionalProperties: false`.
Worker finalization and optional evidence organization validate the complete
result before storage. The API also rejects malformed v3 records without
falling through to its historical-object reader. Input Schema extensions are
not affected by this output restriction.

`result.evidence` is the only canonical evidence list. Each item has
`evidence_id`, `field`, `excerpt`, `interpretation_ko`, required `supports`,
`origins` and `source_spans`. Supports values are:

| Value | Korean label | Meaning |
| --- | --- | --- |
| `attack_request` | 공격 요청 근거 | Supports an attack-attempt/security-policy-violation interpretation; not proof of compromise |
| `normal_request` | 정상 요청 근거 | Supports a normal application/business-data interpretation; not automatically a false-positive verdict |
| `context` | 참고 내용 | Relevant observation without a directional conclusion |

Each role can select up to five source candidates. Both roles contribute up to
ten source-validated items, including opposing interpretations. The established
final-policy subset remains at most five items and is recorded by reference in
`policy.decision_evidence_ids`. Role records use `evidence_ids`, not duplicate
evidence collections. No majority vote, WAF-action inference or keyword
classification is introduced. Source occurrence, field and submitted continuous
span checks remain mandatory.

`decision_issues` contains recorded `point_ko`, `evidence_ids`,
`missing_condition_ko` and role origins. Invalid/disconnected issues are not
revived. Missing prerequisites are null for decisive role outputs. Technical
holds retain server diagnostics instead of inventing missing business data.

`summary_ko` describes request observations and security meaning, without a
verdict declaration or a generic follow-up checklist. Incomplete, conflicting
or unverified analyses use a conservative neutral summary; the original role
interpretations remain in encrypted trace. The UI and exports show the final
hold reason separately. They never promote a reference verdict or a single
role's disputed interpretation to the final verdict.

The model's v3 wire schema places evidence before `verdict`. The frozen v2.15
order puts all analysis first; v2.16 keeps signature comparison, evidence and
summary first but moves verdict-dependent severity and decision conditions
after the verdict. This changes no pinned legacy schema or analyst-facing
analysis-then-conclusion layout.

## Removed generation work

V3 does not generate `tuning_recommendation`, `analyst_checks`,
`recommended_checks`, `conflicting_evidence`, or the duplicated
`analyst_assessment` collection. Actual hold conditions remain in
`decision_issues`; collection/validation/execution problems remain in
`diagnostics`. The evidence editor organizes evidence only; it cannot change
meaning, support direction, source text, severity or verdict.

`interpretation_conflict` is an explicit boolean for genuinely incompatible
interpretations. It replaces the free-text conflict-list trigger, not a test
for the presence of both support directions. The policy version is
`security-analysis-policy-v1`. The retired advisory-only trigger
`tuning_recommended` is recorded in `policy.retired_verifier_triggers`.
All other existing verifier triggers and failure/disagreement hold rules remain.
Performance comparisons must report call-count changes separately from per-call
generation or local processing improvements.

## Service API transition

Existing service keys default to a **v2 compatibility response** at
`POST /api/v1/analyses` and `GET /api/v1/analyses/{id}`. To request the canonical
new response, pass `result_version=v3`. `result_version=v2` explicitly requests
compatibility. Administrator UI requests default to canonical v3. This setting
changes serialization only; it neither selects instructions nor reruns a model.
Stored legacy results retain their original version even if v3 is requested.

The compatibility response has the old five-item policy evidence shape and a
read-only `analyst_assessment` projection of the ten-item classified collection.
Its supports values map to `true_positive/false_positive/context`. Retired
check lists are empty and its tuning recommendation is disabled. These are
documented compatibility placeholders, not additional LLM output. The projection
is never persisted, evaluated, promoted or fed to an Agent. Do not treat the
version label alone as proof that old and new wire shapes are interchangeable.

## Display and downloads

Web, Markdown, PDF and Excel use the same canonical evidence interpretation.
Historical supports values receive an explicit display-only mapping; missing
classification remains unclassified. Stored historical JSON is never rewritten.
Full HTTP, Agent Trace access controls, audit, URL state, retry and themes are
preserved.

New Excel column names are `evidence_attack_request`,
`evidence_normal_request`, `evidence_context`. Old selected names
`evidence_true_positive` and `evidence_false_positive` remain accepted as input
aliases but produce canonical English headers. Each group remains Top 5,
separated by `\n---\n`; selected membership, complete input fields and the column
guide sheet remain unchanged.

## Local processing optimization

- Completion waiting polls status only, with the same source/purpose visibility;
  full reviews/evaluation/retry metadata is loaded once for the response.
- Queue selection reads candidate IDs; only the claimed row loads large blobs.
  Empty-queue preflight avoids the writer lock. The actual claim and shared
  capacity check still run under the original lock.
- Payload fitting reuses signature search; bounded source searches avoid large
  substring copies. Candidate/hint lengths are accumulated with final exact JSON
  budget validation. Decoder Unicode-marker checks short-circuit only when
  logically impossible; unmarked Base64 is still examined.
- No cross-request raw cache, reduced crypto/audit, changed decoder limits,
  weakened durability, bypassed source validation or cached egress permission.

HTTP client sharing across roles and adaptive shared-slot polling remain deferred:
their loop/thread ownership and latency/DB-load tradeoffs need measurements before
changing them. No new operating dependency, DB column or migration is introduced.
Existing fresh-database and upgrade/data-preservation migration tests passed in
isolated containers. No migration was run against the deployed database.

## Focused remediation candidate — v2.16

This candidate changes no public v3 output fields. The v2.15 wire schema stays
frozen. After the focused diagnosis below identified dependent-field
inconsistencies, v2.16 uses a separate wire model: signature comparison,
evidence and request summary precede the verdict; severity and the missing
decision condition follow it. The existing value/severity/condition validators
remain mandatory. Error-specific recovery feedback applies only to the new
candidate and does not tell the model to change its verdict to pass validation.
This is an evidence-informed mitigation, not proof that field ordering caused
the earlier failures or that the mitigation improves model behavior.

Only executions explicitly pinned to v2.16 receive `request-integrity-v3`:

- A bounded, local JSON inspection distinguishes identical scalar values,
  differing scalar values and comparisons that cannot be established. It
  checks complete syntax, object scope and JSON types, without executing,
  decoding or retaining values in diagnostic metadata.
- Unsupported/incomplete framing, unverifiable byte lengths, inspection limits,
  invalid suffixes or partial model submission cannot produce an equality or
  difference claim. Numeric representation changes and containers remain
  uncomparable. Full-capture proof spans must be submitted to the model.
- The new observations are hints, not normal-verdict restrictions or automatic
  attack/normal decisions. Existing guard codes, bounded resources, encryption,
  grounding, independent Verifier and failure/disagreement policy are unchanged.
- Short instructions distinguish structure anomalies from malicious intent,
  reject ordinary-header/test-cue presence as normal evidence, and require
  Korean signature-comparison descriptions while preserving source quotations.

The default integrity helper remains v2. Frozen v2.15 and older pinned
executions/retries continue their earlier preprocessing and instructions.
No new model stage, result field, dependency or migration is added. A candidate
instruction improvement is not a measured model-quality improvement.

## Validation boundaries

Offline synthetic tests and fake provider transports verify contracts and control
flow, not actual Gemma/Qwen/OpenAI quality or production speed. Compare the same
input/model/concurrency: processing and queue times, server-slot wait, output
tokens, retry/verifier/editor rates, failures, holds and false-negative/positive
outcomes. Existing profiles must be exercised with the new actual schema before
the formal Production promotion. Paid/external calls and internal-data transfer
require separate authorization.

`backend/scripts/compare_security_analysis_v3.py` provides an opt-in, resumable
comparison of the same 30 repository-whitelisted synthetic inputs (10 each of
Medium, Hard and Parser Stress) against v2.14 and the current candidate. Both use the same
profile, editable policy, conditional Verifier and enabled Evidence Editor.
Default execution is a no-network plan, without reading runtime configuration.
Actual calls require `--execute-model-calls`; the source database remains
OS read-only and query-only and execution uses an in-memory scratch database.
Source profile/approval/verification/egress authority is reread before every
request, including retries. Credentials are never written into output files.
The tool checks for live server idleness but cannot atomically reserve shared
live slots; its times are diagnostic comparisons, not a concurrency benchmark.
Synthetic reference labels are not approved expert Ground Truth and cannot
authorize Production promotion.

`--diagnostic-ten` instead runs exactly ten unique inputs against frozen v2.15
only, with its fixed-instruction and strict wire-schema hashes checked before
reading the source database or making calls. It includes the five changed
cases from the earlier comparison, an English-description case and four stable
controls. This is a post-hoc cause investigation, not an unbiased quality set.
It records recovery kinds (`schema_correction`, `compact_output`,
`timeout_recovery` or unrecorded), safe validation paths/types, retry duration,
mandatory-stage failures and technical holds. Nested citation-repair attempts
are counted once. Provider text, invalid values, exception context and runtime
credentials are not exported. Earlier checkpoints are never overwritten;
resume requires an identical code/configuration/selection manifest and summaries
are immutable, content-addressed revisions.

The model-output field ordering and closed-object schema are consistent with
the [official OpenAI Structured Outputs guide](https://developers.openai.com/api/docs/guides/structured-outputs).

## Observed validation — 2026-10-28

The authorized diagnostic comparison used `gpt-5.4-mini` with the same 30
synthetic inputs, current preprocessing, profile and editable policy for both
instruction versions. It used scratch storage only and made no Production/Test
assignment changes. The references contain 12 attack, 12 normal and six hold
labels; they are not approved expert Ground Truth.

| Observed measure | v2.14 | v2.15 candidate |
| --- | ---: | ---: |
| Completed / failed analyses | 30 / 0 | 30 / 0 |
| Reference matches | 23 / 30 | 23 / 30 |
| Median processing time (seconds; no queue) | 13.5635 | 17.5355 |
| P95 processing time (seconds; no queue) | 16.373 | 29.401 |
| Conditional Verifier executions | 27 | 26 |
| Failed Verifier stages (safely held) | 0 | 1 |
| Evidence Editor model executions | 26 | 25 |
| Structured-output recovery retries | 3 | 40 |
| Citation repairs | 0 | 0 |
| Reported input tokens, including retries | 321,719 | 415,666 |
| Reported output tokens, including retries | 50,764 | 66,599 |

This run does **not** demonstrate a latency or accuracy improvement. The shorter
candidate instructions were offset by additional recovery generations. The old
checkpoint field is named `schema_retries`, but it also counts compact-output
and timeout recovery; those checkpoint files do not distinguish the causes.
It would be incorrect to call all 40 retries schema-validation errors. The
recorded total retry counts are exact for this run because no citation repair occurred;
older comparison-harness checkpoints with citation repairs can undercount nested
schema retries. Provider token counters are not a billing statement. Both
versions use the optimized current preprocessing, so this comparison does not
measure the local-code optimization against the original implementation.

The candidate retained the new summary/classification contract in all 30 saved
results, but there was one normal-reference request classified as an attack.
Its identical duplicate JSON values were interpreted as malicious parser
confusion; exact source validation cannot validate that semantic conclusion.
The old version held this particular case because its roles disagreed. This
single non-deterministic comparison cannot establish which change caused it.
Another normal-reference request was held after its Verifier exhausted output
validation retries. An analysis marked completed does not imply that every
model stage succeeded. Two candidate signature-comparison descriptions also
contain English-only sentences, and some normal-request evidence overuses
ordinary headers or synthetic fixture cues; these remain quality-review items.
All six reference-hold cases still require expert review: each version gave a
decisive answer to five of them. A smaller hold count is not automatically an
improvement.

Production remains on its approved pinned instructions. The candidate needs
retry-cause analysis and focused semantic evaluation before formal promotion;
these synthetic results are not permission to activate it.

### Focused retry diagnosis — frozen v2.15, ten cases

The separately authorized `gpt-5.4-mini` run completed all ten selected synthetic
cases with no failed mandatory stage and no technical hold. Its recorded 12
additional output generations were all `schema_correction`, with these safe
validation codes:

| Recorded constraint | Occurrences |
| --- | ---: |
| Decisive verdict also supplied `decision_issue.missing_condition_ko` | 10 |
| Inconclusive verdict used severity other than `UNKNOWN` | 2 |
| Compact-output / timeout recovery | 0 / 0 |
| Citation repairs | 0 |

There were seven conditional Verifier and seven Evidence Editor calls. Median
processing time was 16.524 seconds and P95 was 26.121 seconds; additional output
recovery call intervals totaled 51.356 seconds. That total excludes initial
failed-call time and is not an exact measure of wasted time. Reported usage was
125,736 input and 19,261 output tokens, not a billing statement.

Eight final verdicts matched the synthetic references. The two mismatches were
a normal reference held by role disagreement, and a hold reference classified
normal. This targeted, outcome-informed sample is not an accuracy estimate.
The identical-duplicate-value case now classified normal under the same frozen
v2.15 rules, illustrating generation variability rather than a measured v2.16
improvement. These observations do not retroactively identify every cause of
the earlier 40 recoveries.

Validation issues are safely bounded to twelve per attempt; generic unknown
validation errors remain unknown rather than reconstructing provider text.
The original 30-case comparison was not overwritten. Diagnostic records and
their immutable summary are in
`/tmp/waf-security-v215-diagnostic.ZFGu58/run/summary-0779025336f2873d5678.json`.
The diagnostic used the frozen v2.15 schema and preprocessing, not the new
v2.16 candidate. The latter still needs an authorized actual-model comparison;
no additional candidate calls or Production/Test changes were made.

## Initial v3 implementation checks

- The full backend suite ran in four disjoint groups. Three historical
  prompt-literal assertions were corrected to test the frozen v2.14 rules;
  the affected modules and latest result/export contracts were rerun together
  (131 passed, two release-only PDF checks skipped in that offline batch).
- The final PDF/layout/result-contract batch passed 67 checks with real local
  Light/Dark PDF generation. One optional prepared-sample test was excluded
  because its separate `/preview` fixtures were not mounted. No Chromium,
  external fonts or downloads were used for PDF generation.
- The hardened comparison harness passed eight offline tests, including
  read-only source enforcement, metadata redaction, nested retry accounting,
  resume configuration checks and immutable summary revisions. No additional
  paid calls were made for this harness update.
- Frontend: 582 tests passed; application and shared report builds passed.
  The existing large JavaScript-chunk warning remains.
- Browser: 50 synthetic screenshots/checks passed across SK/Light/Dark,
  KR/EN, desktop/mobile, keyboard/focus, source links and reports. Desktop,
  mobile and generated PDF images were visually reviewed.
- No new operating dependencies or DB migrations; no deployment, Git commit,
  push or Production configuration changes were made.

## Focused remediation checks — 2026-10-28

- The targeted candidate/frozen-schema/provider-transport/input-integrity/harness
  regression batch passed 99 checks. The v2.15 strict wire-schema hash and
  historical recovery-message golden hashes remain unchanged. Both providers
  were exercised with fake transports, not actual candidate model calls.
- The complete backend suite ran in four disjoint groups: initially 2,962
  passed, one failed and one optional prepared-PDF-fixture check skipped.
  Real local Light/Dark PDF rendering was enabled. Fresh and existing-database
  preservation migration tests also passed independently.
- The single failure was the mocked 150-case profile validation: 149 completed
  and one failed. It passed an isolated rerun before any code change. Its
  original per-case exception was not retained, so the cause is not proven.
  The fixture did share one in-memory SQLite/StaticPool DBAPI connection
  between analysis and heartbeat threads, which permits transaction interference.
  This threaded test module now uses separate file-backed WAL connections like
  deployment; its full 19 tests passed, including all 150 analyses with a
  heartbeat every 0.1 seconds and an explicit connection-isolation check.
  Only that test module changed after the full run; no production DB/worker
  behavior was changed for this test-environment correction. Final unique
  coverage is 2,964 passing backend cases and one optional fixture skip, across
  the initial run and affected-module rerun, not a second complete-suite run.
- Frontend: all 582 tests, application build and shared report build passed.
  The pre-existing large JavaScript-chunk warning remains. This follow-up
  changes no frontend source; the earlier browser checks were not rerun.
- Actual calls were limited to the separately approved frozen-v2.15 ten-case
  diagnosis above. The v2.16 candidate's model quality, retry reduction and
  processing-time effect remain unverified, including on Gemma/Qwen.
- No public result fields, DB migrations, dependencies, Production/Test
  assignments, deployments or Git commits/pushes were added by this follow-up.
