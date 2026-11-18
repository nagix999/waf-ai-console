# Payload parser rules

This is an additive administrator workbench, not an automatic Production change.
The input remains the normalized event's `payload`; extracting event metadata
from device syslog is out of scope. The existing `generic-http-v3` and
`http-capture-v1` implementations remain available without modification.

## Rule contract

`parser-rule-proposal-v1` contains a name, bounded AND/OR format predicates,
one extraction operation, fixed capture-format options, a reason and limitations.
The complete closed schema lives in `app/parser_rule_schemas.py`.

- `raw_payload`: inspect the original request directly.
- `json_string`: resolve one string using a JSON Pointer inside payload only.
- `between_markers`: extract one uniquely bounded request region.
- `regex_capture`: use native RE2 and one nonempty named `request` capture.

Unknown properties, executable code, ambiguous captures, duplicate JSON keys,
unsupported regex syntax, and excessive pattern length or traversal are rejected.
No fallback to Python regex is permitted. Literal `/@/` data is not globally
removed or treated as an attack. There is no automatic field reconstruction,
arbitrary decoder pipeline, default protocol or invented header/body boundary.

The server assigns vendor identity, rule IDs and revisions, candidate state,
resource limits, source spans, artifact hashes and promotion state. A model
cannot assign selection priority, confidence, success or approval.

`vendor-parser-bundle-v1` fixes rule revisions and `vendor-rule-selection-v1`.
Encrypted definitions and the previous capture parser are stored with an
immutable `ParserVersion`. New bundles retain previous rule IDs unless they are
explicitly updated with a higher revision. Decrypted runtime attachments are
never serialized. Snapshot JSON contains ciphertext and safe identities only.
The v2 rule engine, schema and integration module are versioned artifacts.
Future engines must be added separately; adding a branch to the shared parser
dispatcher must not invalidate admitted v2 snapshots. A changed v2 artifact
fails closed and never silently reapproves stored candidates.

## Runtime selection

Only the event's normalized `waf_vendor` selects the vendor scope. Format checks
precede extraction. The selected candidate must have a recognizable request and
valid original source mapping. Equivalent candidates require the same decoded
request, original provenance and capture options. Materially different qualified
interpretations are ambiguous, not combined or selected by field count.

Unmatched, ambiguous and resource-limited inputs retain the pinned previous
capture parser as their fallback. Original payload is always preserved.
Extracted JSON/marker requests remain capture hints rather than a claim about
wire completeness. Parse status alone does not trigger Verifier for policies
that exclude `parser_incomplete`; old policies retain their original behavior.

Budgets are server constants in `parser_rule_engine.py`, not model output. They
bound source bytes, rule and predicate counts, JSON depth/nodes, regex memory
and the total selection deadline. A deadline is checked between bounded
operations; native RE2 is used for user-provided patterns.

## Authoring and validation

The workbench freezes a dataset revision or selected Test inputs. Before the
first model call, duplicate/related samples are assigned to authoring or held-out
groups. Only authoring payloads and format information may enter model input.
Reference verdicts, previous AI analyses and analyst opinions are excluded.

Source preview returns metadata only. Test previews read small metadata fields;
dataset previews decrypt bounded frozen events to obtain the vendor and record
raw-data access, without returning their payloads. Recommendations balance
vendors and recorded parser states and retain successful examples alongside
failed/partial cases. Missing parser records are shown as unrecorded, never
invented as success or failure. Users may add or exclude cases before freezing;
source membership is checked again when creating the workbench. A bounded
metadata scan is disclosed and is not a claim about the whole source.

Format discovery precedes human group confirmation and rule generation.
Generation and bounded correction use verified internal vLLM profiles only,
with explicit transmission confirmation, fresh profile/egress authorization,
shared model-call slots, job leases and revision fences. Viewing stored work
never starts a model call. There is no per-analysis rule-generation step.

Expected extraction values distinguish `present`, `absent` and `unknown`.
Only explicitly confirmed fields enter extraction-accuracy denominators. HTTP
headers and query values preserve repetitions. Model-derived drafts are not
parsing answers or WAF Ground Truth. Held-out raw input is hidden until the
first validation atomically locks the rules. After that, model discovery,
generation and rule edits are rejected. Expected fields may be corrected with
an audit record and require revalidation. Every added rule needs a held-out
case with jointly confirmed method and path values. If held-out data is used
outside this workflow to improve a rule, it is no longer independent and fresh
held-out data is needed.

Candidate saving does not activate a parser. The exact baseline/candidate
comparison and confirmed held-out extraction checks must qualify before the
existing approved Published Ground Truth official evaluation and administrator
Production Review can apply the complete configuration. Successful parsing does
not itself establish correct extraction or accurate security judgment.

## Storage, API and deployment

Migration `0031_parser_rule_workbench` adds encrypted workbench and job records.
Existing parser versions and analysis, retry and Test snapshots are not rewritten.
The new administrator API uses `/api/v1/admin/parser-workbench`. Legacy
`/api/v1/admin/parsers` comparisons remain available; direct activation remains
blocked. Raw-data reads require administrator authorization and access auditing.

| Method | Relative path | Purpose |
| --- | --- | --- |
| GET | `/catalog` | Rule, profile, source and recent-work metadata |
| POST | `/source-preview` | Bounded recommendations and source membership metadata |
| POST | `/sessions` | Freeze selected source cases and the baseline |
| GET | `/sessions/{id}` | Audited work details; held-out raw hidden before rule lock |
| POST | `/sessions/{id}/discover` | Enqueue internal format discovery |
| POST | `/sessions/{id}/groups` | Confirm groups and explicit optional rule-revision targets |
| POST | `/sessions/{id}/generate` | Enqueue rules for the confirmed groups |
| PUT | `/sessions/{id}/expectations` | Save human-confirmed parsed fields |
| POST | `/sessions/{id}/validate` | Lock rules, reveal holdout and compare locally |
| POST | `/sessions/{id}/save-candidate` | Save a qualified, inactive parser version |
| GET | `/sessions/{id}/jobs` | Read fixed job statuses; never invoke a model |

All session mutations carry `expected_revision`. Discovery and generation also
require a verified internal `profile_id` and the literal boolean confirmation
`internal_data_confirmed: true`. `source.case_ids` optionally selects cases from
the chosen `dataset_version_id` or `test_run_id`; foreign/stale members fail.
Human group confirmation may set `target_rule_id` from the same baseline and
vendor. The server retains that ID and increments its revision while preserving
all other rules. Leaving the target empty creates a new rule. Model input uses
short sample aliases, never server-assigned rule IDs or revision controls.

### Rule-generation failures and correction

Workbench model jobs use the selected profile's `max_output_tokens` instead of
the former 1,536-token cap. The same configured output allowance is reserved in
the existing conservative input/context estimate; the response-byte limit is
unchanged. A higher profile limit is not an automatic guarantee of valid output.

New jobs distinguish incomplete/refused model responses, malformed JSON, schema
errors, RE2/rule validation errors and failed extraction on authoring samples.
Correction includes the prior complete proposal, safe schema field/type errors
and up to eight failing authoring aliases. It never includes validation-only
samples, verdict labels, arbitrary exception messages or upstream HTTP bodies.
Prior proposals stay in memory, are bounded to 16 KiB, and are omitted if needed
to respect the same input budget. Incomplete/refused text is never reused as a
proposal. Every request repeats profile, egress, shared-slot and lease checks.

Rules identify stable wrapper structure, not changing IPs, timestamps, HTTP
methods, request paths or body values. The authoring instructions explicitly
describe original-payload matching, literal prefixes, RE2 search and all/any.
For `parser_rule_not_matched`, the workbench explains which conditions failed
using the unchanged pinned engine's predicates. `attempts[].match_checks`
contains at most 64 records (eight authoring aliases and eight conditions), with
zero-based condition positions, kinds, booleans and fixed codes only. Correction
feedback selects at most 12 failed checks and reduces them further if needed
to fit the input budget. Predicate literals, raw fragments and held-out data are
never added to plaintext diagnostics. This explanatory helper is not part of
runtime rule selection; frozen engine files and approved artifact hashes remain
unchanged. Conditions are not automatically dropped, relaxed or changed from
all to any to force a pass.

The existing total budget remains two calls for discovery or N + 2 calls for N
rule groups (two shared corrections). Restarting a worker does not reset it.
Rules must still pass all authoring extraction/source-span checks; partial
groups are not published after a failure. Unsupported inputs are not made valid
by weakening HTTP structure or provenance checks.

`GET /sessions/{id}/jobs` and the session detail retain their existing shape.
Job `summary.diagnostics` adds bounded attempts, stages, fixed error codes,
correction counts, group numbers and safe numeric usage/finish metadata when
available. Prior rules and raw model/source text are never stored in this
unencrypted metadata. Existing tables suffice; no DB migration is required.
The UI shows current job failures separately from earlier attempts and provides
fixed KR/EN explanations with safe technical details. Historic
`parser_workbench_invalid_rule` records cannot recover missing diagnostics
retroactively and are explicitly displayed as an unspecified earlier failure.

Public `waf-analysis-v3` field structure does not grow. Existing parser metadata
can report the new `vendor-http-v2` engine identifier; encrypted options and
cross-vendor routing definitions are not exposed in service results.

The pinned `google-re2` dependency is installed while building the backend
image. Offline servers receive that complete image, not a runtime package
download. Older images may continue running old parsers but cannot execute new
rules without the matching engine and dependency artifact. Compose starts
`app.worker_entrypoint_parsers`; its outer coordinator imports the unchanged
versioned verdict runners and retains the independent evidence-display lane.
The old and new parser jobs share the single administration lane, alternating
with model qualification jobs. All model calls also use shared profile slots.

New selection and extraction diagnostics remain in the encrypted parser-stage
output and workbench comparisons. Historical execution-statistics allowlists
do not name every new warning and may classify them as other/omit details;
historical verdict code is not changed to extend that list.

Synthetic automated and mocked-transport tests do not establish real-model
generation quality, real WAF format coverage or Production performance. Those
must be evaluated separately with authorized internal data and profiles.

## Rule-correction verification (2026-11-18)

- The initial correction batch passed 192 related backend tests: authoring, safe diagnostics, rule engine,
  worker lane, runtime integration and fresh/existing-DB migration coverage.
  The final engine-reason feedback change was rechecked in three focused
  authoring tests. This is a targeted regression run, not the full backend suite.
- The subsequent condition-feedback batch passed 86 backend tests covering
  match diagnostics, existing workbench corrections and immutable parser
  versions. The final 18-test rerun covered all 17 match-feedback cases and the
  frozen-engine byte check, including feedback budget reduction. No pinned
  runtime engine, schema or version file was modified.
- All 783 frontend tests and the Vite production build passed. The existing
  large-chunk build warning remains. This change was not browser-reviewed.
- All model transports were mocked and samples synthetic. No real-model calls,
  operational-data transfer, Production setting change, live-DB migration,
  deployment or Git mutation was performed. Actual internal model generation
  quality and success rate still require an explicitly requested model run.

## Earlier verification status (2026-11-16)

- The final parser and migration batch passed 205 tests, including all 178 new
  engine, encrypted snapshot, workbench, runtime and migration cases. Fresh
  installation, existing-DB upgrade and non-destructive history checks passed.
- Frontend verification passed 711 tests and the Vite production build. The
  existing large-chunk warning remains. Synthetic browser checks covered 15 new
  and 14 legacy captures, SK/Light/Dark, KR/EN, desktop/mobile, keyboard focus,
  consent, source selection, rule lock, source spans and Back/Forward navigation.
- An earlier full backend run reported 4,596 passed, 34 failed and 7 skipped.
  Eleven failures were stale current-Alembic-head expectations; their assertions
  were updated to 0031 without weakening data/FK/history checks and passed in
  the final batch. The full suite was not rerun after that targeted correction.
- The other 23 failures also reproduced with the changed application files
  replaced read-only by their original `5f6351d` versions: 12 archived prompt
  expectations, 10 legacy result/promotion cases and one order-dependent log
  capture case. The latter passes alone and fails after a migration test. They
  are not declared fixed, and the full backend suite is not fully green.
- No live LLM, operational-data transfer, active configuration change, live-DB
  migration, deployment or Git mutation was performed for this work.

Review captures are kept locally in `frontend/test-results/parser-workbench`
and `frontend/test-results/parser-management`; they contain synthetic data only.
