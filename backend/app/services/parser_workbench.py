"""Encrypted, immutable parser samples and code-only human field validation.

Reference verdicts and AI results are deliberately absent from this workflow.
The workbench creates candidates, never changes an active parser configuration.
"""
from collections import Counter
from copy import deepcopy
import hashlib
import json
import re
import uuid
from types import SimpleNamespace

from sqlalchemy import case as sql_case, func, select
from sqlalchemy.orm import load_only

from ..models import (AgentRun, AgentStep, Analysis, ParserVersion, ParserWorkbench, ParserWorkbenchJob,
                      TestRun, TestRunItem, ValidationDataset, ValidationDatasetItem,
                      ValidationDatasetVersion, VLLMProfile, utcnow)
from ..parser_rule_schemas import RuleProposalV1
from .parser_engine import valid_source_spans
from .parser_management import MAX_SOURCE_BYTES, MAX_TOTAL_BYTES, audit, parse_version_payload, selected_sources
from .parser_rule_engine import ParserRuleError, parse_rule_bundle, validate_bundle
from .parser_rule_versions import (DOCUMENT_VERSION, RuntimeRuleSnapshot, apply_rule_snapshot,
    create_rule_bundle_version, decrypt_rule_document, read_rule_bundle_definition)
from .parser_versions import ParserVersionError, ensure_parser_state, get_version, version_summary
from .test_runs import write_lock

FIELDS = ("method", "uri", "protocol", "body", "headers", "query")
MAX_CASES = 100
MAX_GROUPS = 20
MAX_METADATA_SCAN = 1000
MAX_PREVIEW_CIPHERTEXT_BYTES = 2 * 1024 * 1024
MAX_STATE_BYTES = 8 * 1024 * 1024
SCHEMA = "parser-workbench-v1"


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def _decrypt(crypto, value):
    try:
        result = json.loads(crypto.decrypt_text(value))
        if not isinstance(result, dict):
            raise ValueError()
        return result
    except (ValueError, TypeError, UnicodeError):
        raise ParserVersionError("parser_workbench_data_unreadable") from None


def documents(row, crypto):
    return _decrypt(crypto, row.input_ciphertext), _decrypt(crypto, row.state_ciphertext)


def get_session(db, identifier):
    row = db.get(ParserWorkbench, identifier)
    if row is None:
        raise ParserVersionError("parser_workbench_not_found", 404)
    return row


def edit_session(db, identifier, revision):
    write_lock(db)
    row = get_session(db, identifier)
    if row.revision != revision:
        raise ParserVersionError("parser_workbench_revision_conflict")
    if db.scalar(select(ParserWorkbenchJob.id).where(ParserWorkbenchJob.workbench_id == row.id,
            ParserWorkbenchJob.status.in_(("pending", "running"))).limit(1)):
        raise ParserVersionError("parser_workbench_job_in_progress")
    return row


def store_state(row, crypto, state, *, status=None):
    text = canonical(state)
    if len(text.encode("utf-8")) > MAX_STATE_BYTES:
        raise ParserVersionError("parser_workbench_state_size_limit", 413)
    row.state_ciphertext = crypto.encrypt_text(text)
    row.revision += 1
    row.updated_at = utcnow()
    if status:
        row.status = status


def _family(raw, vendor):
    # Deterministic conservative near-duplicate family assignment before any
    # model call. Literal values in JSON/HTTP vary, but wrapper/field shape stays.
    def request_shape(text):
        text = text.replace("\\r\\n", "\n").replace("\\n", "\n").replace("\r\n", "\n")
        first, _, rest = text.partition("\n")
        first = re.sub(r"(?<=[=?&])[^ &]+", "VALUE", first)
        first = re.sub(r"\b[0-9a-f]{8}-[0-9a-f-]{27,}\b", "UUID", first, flags=re.I)
        first = re.sub(r"\d+", "N", first)
        head, boundary, body = rest.partition("\n\n")
        headers = [line.partition(":")[0].lower() for line in head.splitlines() if ":" in line]
        return [first, headers, shape_text(body) if boundary else None]

    def shape(value):
        if isinstance(value, dict):
            return {key: shape(item) for key, item in sorted(value.items())}
        if isinstance(value, list):
            return [shape(item) for item in value[:64]] + (["ARRAY_LIMIT"] if len(value) > 64 else [])
        if isinstance(value, str):
            if re.match(r"^[A-Z]+\s+(?:/|https?://|\*)", value):
                return request_shape(value)
            return "STRING"
        return type(value).__name__

    def shape_text(text):
        try:
            from ..agent.structured_json import normalize_structured_json
            return shape(json.loads(normalize_structured_json(text).content))
        except (ValueError, TypeError, RecursionError):
            return re.sub(r"\d+", "N", re.sub(r"\s+", " ", text.strip()))

    template = request_shape(raw) if re.match(r"^[A-Z]+\s+(?:/|https?://|\*)", raw) else shape_text(raw)
    return digest({"vendor": vendor.strip().casefold(), "template": template})


def split_cases(cases):
    families = {}
    for case in cases:
        family = _family(case["raw_payload"], case["waf_vendor"])
        case["family_id"] = family
        families.setdefault(family, []).append(case)
    if len(families) < 2:
        raise ParserVersionError("parser_split_requires_distinct_families", 422)
    # A family never appears on both sides. Stable order is independent of model
    # suggestions, labels, expected values and request arrival order.
    keys = sorted(families)
    holdout = set(keys[::3])
    if len(holdout) == len(keys):
        holdout.remove(keys[-1])
    for case in cases:
        case["role"] = "holdout" if case["family_id"] in holdout else "authoring"
    return {"authoring_count": sum(c["role"] == "authoring" for c in cases),
            "holdout_count": sum(c["role"] == "holdout" for c in cases), "family_count": len(families),
            "internal_only_count": sum(c["internal_only"] for c in cases),
            "strategy": "template-family-v1", "limitations": ["template_split_is_not_independent_quality_proof"]}


def _test_source_query(db, identifier):
    run = db.get(TestRun, identifier)
    if run is None:
        raise ParserVersionError("parser_test_run_not_found", 404)
    from .test_runs import run_cohort, test_read_context
    _, _, current, _ = test_read_context(db, run, cohort=run_cohort(run.id))
    query = select(current.c.analysis_id).join(TestRunItem, TestRunItem.id == current.c.item_id).where(
        TestRunItem.ingest_status == "accepted").order_by(TestRunItem.row_number, current.c.analysis_id)
    return run, query


def _observed_parser_statuses(db, identifiers):
    if not identifiers:
        return {}
    latest = select(AgentRun.id).where(AgentRun.analysis_id == Analysis.id).order_by(
        AgentRun.created_at.desc(), AgentRun.id.desc()).limit(1).correlate(Analysis).scalar_subquery()
    metadata = sql_case((func.json_valid(AgentStep.metadata_json) == 1, AgentStep.metadata_json), else_="{}")
    observed = select(func.json_extract(metadata, "$.parse_status")).where(AgentStep.run_id == latest,
        AgentStep.step_type == "parser").order_by(AgentStep.sequence.desc(), AgentStep.id.desc()).limit(1).correlate(Analysis).scalar_subquery()
    return {identifier: status if status in {"success", "partial", "failed"} else "unrecorded"
            for identifier, status in db.execute(select(Analysis.id, observed).where(Analysis.id.in_(identifiers)))}


def _source_inventory(db, crypto, source, actor, *, offset=0, limit=MAX_METADATA_SCAN):
    """Small, bounded metadata projection; no payload/label/model result output."""
    dataset_id = source.get("dataset_version_id")
    if dataset_id:
        version = db.get(ValidationDatasetVersion, dataset_id)
        parent = db.get(ValidationDataset, version.dataset_id) if version else None
        if version is None or parent is None or parent.deleted_at is not None:
            raise ParserVersionError("parser_dataset_version_not_found", 404)
        identifiers = version.item_version_ids
        selected = identifiers[offset:offset + limit]
        rows = {row.id: row for row in db.execute(select(ValidationDatasetItem.id, ValidationDatasetItem.case_name,
            ValidationDatasetItem.original_analysis_id, func.length(ValidationDatasetItem.event_ciphertext).label("cipher_length"))
            .where(ValidationDatasetItem.id.in_(selected)))}
        if len(rows) != len(selected):
            raise ParserVersionError("parser_dataset_version_unavailable", 409)
        readable, used = [], 0
        for identifier in selected:
            size = rows[identifier].cipher_length or 0
            if used + size <= MAX_PREVIEW_CIPHERTEXT_BYTES:
                readable.append(identifier)
                used += size
        ciphertexts = dict(db.execute(select(ValidationDatasetItem.id, ValidationDatasetItem.event_ciphertext)
            .where(ValidationDatasetItem.id.in_(readable))).all())
        if readable:
            audit(db, actor, "read_raw_for_parser_source_metadata", version.id, "validation_dataset_version")
        observations = _observed_parser_statuses(db, [row.original_analysis_id for row in rows.values() if row.original_analysis_id])
        items = []
        for index, identifier in enumerate(selected):
            row = rows[identifier]
            event = _decrypt(crypto, ciphertexts[identifier]) if identifier in ciphertexts else {}
            vendor = event.get("waf_vendor")
            if not isinstance(vendor, str):
                vendor = ""
            items.append({"case_id": identifier, "case_name": row.case_name or f"Sample {offset + index + 1}",
                "waf_vendor": vendor, "parser_status": observations.get(row.original_analysis_id, "unrecorded"),
                "vendor_recorded": bool(vendor),
                "vendor_unavailable_reason": None if vendor else "preview_byte_limit" if identifier not in ciphertexts else "source_vendor_unrecorded",
                "parser_status_origin": "source_analysis" if row.original_analysis_id else "unrecorded"})
        total = len(identifiers)
    else:
        if source.get("test_run_id"):
            _, query = _test_source_query(db, source["test_run_id"])
            total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
            selected = list(db.scalars(query.offset(offset).limit(limit)))
        else:
            identifiers = list(dict.fromkeys(source.get("analysis_ids") or []))
            total, selected = len(identifiers), identifiers[offset:offset + limit]
        rows = {row.id: row for row in db.execute(select(Analysis.id, Analysis.waf_vendor).where(Analysis.id.in_(selected)))}
        if len(rows) != len(selected):
            raise ParserVersionError("parser_analysis_not_found", 404)
        observations = _observed_parser_statuses(db, selected)
        items = [{"case_id": identifier, "case_name": f"Sample {offset + index + 1}", "waf_vendor": rows[identifier].waf_vendor,
            "parser_status": observations.get(identifier, "unrecorded"), "parser_status_origin": "source_analysis"}
                 for index, identifier in enumerate(selected)]
    audit(db, actor, "preview_parser_source_metadata", dataset_id or source.get("test_run_id") or "selection", "parser_workbench")
    return items, total


def _recommend(items, maximum):
    buckets = {}
    for item in items:
        buckets.setdefault((item["parser_status"], item["waf_vendor"].casefold()), []).append(item["case_id"])
    selected = []
    while len(selected) < maximum and buckets:
        for status in ("failed", "partial", "success", "unrecorded"):
            for key in sorted(key for key in buckets if key[0] == status):
                selected.append(buckets[key].pop(0))
                if not buckets[key]:
                    del buckets[key]
                if len(selected) == maximum:
                    return selected
    return selected


def source_preview(db, crypto, payload, actor):
    source = payload.source.model_dump(exclude_none=True)
    scanned, total = _source_inventory(db, crypto, source, actor)
    recommended = _recommend(scanned, payload.max_cases)
    page = scanned[payload.offset:payload.offset + payload.limit] if payload.offset + payload.limit <= len(scanned) else \
        _source_inventory(db, crypto, source, actor, offset=payload.offset, limit=payload.limit)[0]
    counts = Counter(item["parser_status"] for item in scanned)
    return {"items": [{**item, "recommended": item["case_id"] in recommended} for item in page],
        "total": total, "scanned_count": len(scanned), "scan_limited": total > len(scanned),
        "recommended_case_ids": recommended, "limit": payload.limit, "offset": payload.offset,
        "limits": {"max_cases": MAX_CASES, "scan_limit": MAX_METADATA_SCAN},
        "recommendation": {"strategy": "parser-status-vendor-balance-v1",
            "status_counts": {status: counts[status] for status in ("failed", "partial", "success", "unrecorded")},
            "unknown_status_count": counts["unrecorded"], "status_is_source_observation": True,
            "vendor_unrecorded_count": sum(not item["waf_vendor"] for item in scanned),
            "dataset_vendor_decryption_byte_limit": MAX_PREVIEW_CIPHERTEXT_BYTES}}


def _source_cases(db, crypto, source, maximum, actor):
    chosen = source.get("case_ids")
    if chosen is None:
        inventory, _ = _source_inventory(db, crypto, source, actor)
        chosen = _recommend(inventory, maximum)
    if not 2 <= len(chosen) <= maximum or len(set(chosen)) != len(chosen):
        raise ParserVersionError("parser_source_selection_invalid", 422)
    dataset_id = source.get("dataset_version_id")
    if dataset_id:
        version = db.get(ValidationDatasetVersion, dataset_id)
        parent = db.get(ValidationDataset, version.dataset_id) if version else None
        if version is None or parent is None or parent.deleted_at is not None:
            raise ParserVersionError("parser_dataset_version_not_found", 404)
        members = set(version.item_version_ids)
        if not set(chosen) <= members:
            raise ParserVersionError("parser_source_membership_changed")
        size = db.scalar(select(func.sum(func.length(ValidationDatasetItem.event_ciphertext)))
            .where(ValidationDatasetItem.id.in_(chosen))) or 0
        if size > MAX_PREVIEW_CIPHERTEXT_BYTES:
            raise ParserVersionError("parser_source_size_limit", 413)
        rows = {item.id: item for item in db.execute(select(ValidationDatasetItem.id, ValidationDatasetItem.event_ciphertext,
            ValidationDatasetItem.internal_only, ValidationDatasetItem.case_name).where(ValidationDatasetItem.id.in_(chosen)))}
        if len(rows) != len(chosen):
            raise ParserVersionError("parser_dataset_version_unavailable")
        items = [rows[identifier] for identifier in chosen]
        if not items:
            raise ParserVersionError("parser_selection_empty", 422)
        sources = []
        for item in items:
            event = _decrypt(crypto, item.event_ciphertext)
            raw, vendor = event.get("payload"), event.get("waf_vendor")
            if not isinstance(raw, str) or not isinstance(vendor, str) or not vendor.strip():
                raise ParserVersionError("parser_source_invalid", 422)
            sources.append({"raw_payload": raw, "waf_vendor": vendor, "internal_only": bool(item.internal_only),
                "source_ref": item.id, "case_name": item.case_name or f"Sample {len(sources) + 1}"})
        summary = {"dataset_version_id": version.id, "dataset_version_name": version.name,
            "dataset_revision": version.revision, "source_total": len(version.item_version_ids), "selected_count": len(sources),
            "selection_limited": len(version.item_version_ids) > len(sources)}
        kind = "dataset_version"
        audit(db, actor, "read_dataset_for_parser", version.id, "validation_dataset_version")
    else:
        if source.get("test_run_id"):
            _, query = _test_source_query(db, source["test_run_id"])
            cohort = query.order_by(None).subquery()
            allowed = set(db.scalars(select(cohort.c.analysis_id).where(cohort.c.analysis_id.in_(chosen))))
            total = db.scalar(select(func.count()).select_from(cohort)) or 0
        else:
            allowed, total = set(source.get("analysis_ids") or []), len(source.get("analysis_ids") or [])
        if not set(chosen) <= allowed:
            raise ParserVersionError("parser_source_membership_changed")
        size = db.scalar(select(func.sum(func.length(Analysis.payload_ciphertext))).where(Analysis.id.in_(chosen))) or 0
        if size > MAX_PREVIEW_CIPHERTEXT_BYTES:
            raise ParserVersionError("parser_source_size_limit", 413)
        selection = SimpleNamespace(analysis_ids=chosen, test_run_id=None, max_cases=maximum)
        sources, summary = selected_sources(db, crypto, selection, actor)
        for index, item in enumerate(sources):
            analysis = db.get(Analysis, item["analysis_id"])
            item.update(internal_only=bool(analysis.internal_only or analysis.analysis_purpose != "test"),
                        source_ref=analysis.id, case_name=f"Sample {index + 1}")
        kind = "test_run" if source.get("test_run_id") else "analyses"
        summary.update(selected_count=len(sources), source_total=total, selection_limited=total > len(sources))
        if source.get("test_run_id"):
            run = db.get(TestRun, source["test_run_id"])
            summary.update(test_run_id=run.id, test_run_name=run.name)
    used = 0
    cases = []
    for item in sources:
        try:
            size = len(item["raw_payload"].encode("utf-8"))
        except UnicodeError:
            raise ParserVersionError("parser_source_invalid", 422) from None
        used += size
        if size > MAX_SOURCE_BYTES or used > MAX_TOTAL_BYTES:
            raise ParserVersionError("parser_source_size_limit", 413)
        cases.append({"case_id": str(uuid.uuid4()), "case_name": item["case_name"], "raw_payload": item["raw_payload"],
            "waf_vendor": item["waf_vendor"], "internal_only": item["internal_only"], "source_ref": item["source_ref"]})
    summary.update(source_bytes=used)
    summary["selection_strategy"] = "manual" if source.get("case_ids") is not None else "parser-status-vendor-balance-v1"
    return kind, cases, summary


def create_session(db, crypto, payload, actor):
    write_lock(db)
    state = ensure_parser_state(db)
    baseline = get_version(db, payload.baseline_version_id or state.active_version_id)
    source = payload.source.model_dump(exclude_none=True)
    kind, cases, summary = _source_cases(db, crypto, source, payload.max_cases, actor)
    split = split_cases(cases)
    document = {"schema_version": SCHEMA, "cases": cases, "source_summary": summary,
        "split_summary": split, "baseline_content_hash": baseline.content_hash}
    row = ParserWorkbench(name=payload.name or f"Parser {utcnow().strftime('%Y-%m-%d %H:%M')}", status="draft", revision=1,
        source_kind=kind, dataset_version_id=source.get("dataset_version_id"), baseline_version_id=baseline.id,
        input_ciphertext=crypto.encrypt_text(canonical(document)), state_ciphertext=crypto.encrypt_text(canonical({
            "groups": [], "groups_confirmed": False, "expectations": [], "validation": None})),
        encryption_key_version=crypto.key_version, metadata_json={"selected_count": len(cases), **split}, created_by=actor)
    db.add(row)
    db.flush()
    audit(db, actor, "create_parser_workbench", row.id, "parser_workbench")
    return row


def job_summary(row):
    return {"id": row.id, "kind": row.kind, "status": row.status, "session_revision": row.session_revision,
        "profile_id": row.profile_id, "error_code": row.error_code, "created_at": row.created_at,
        "started_at": row.started_at, "completed_at": row.completed_at, "summary": row.metadata_json}


def session_detail(db, crypto, identifier, actor):
    row = get_session(db, identifier)
    frozen, state = documents(row, crypto)
    baseline = get_version(db, row.baseline_version_id)
    baseline_bundle = read_rule_bundle_definition(baseline, crypto)
    validation = state.get("validation") or {}
    comparisons = {item["case_id"]: item for item in validation.get("items", [])}
    cases = []
    for case in frozen["cases"]:
        if case["role"] == "holdout" and not state.get("rules_locked"):
            cases.append({key: case[key] for key in ("case_id", "case_name", "waf_vendor", "role", "internal_only")}
                         | {"heldout_hidden": True})
            continue
        item = {key: value for key, value in case.items() if key not in {"source_ref", "family_id"}}
        item.update(comparisons.get(case["case_id"], {}))
        if "baseline" not in item:
            item["baseline"] = parse_version_payload(case["raw_payload"], baseline, crypto, waf_vendor=case["waf_vendor"])
        cases.append(item)
    audit(db, actor, "view_parser_workbench_raw", row.id, "parser_workbench")
    return {"id": row.id, "name": row.name, "status": row.status, "revision": row.revision,
        "baseline_version_id": row.baseline_version_id, "candidate_version_id": row.candidate_version_id,
        "baseline_rules": [{key: rule.get(key, rule["definition"]["name"] if key == "name" else None)
            for key in ("rule_id", "name", "waf_vendor", "rule_version")} for rule in (baseline_bundle or {}).get("rules", [])],
        "source_summary": frozen["source_summary"], "split_summary": frozen["split_summary"],
        "groups": state.get("groups", []), "groups_confirmed": bool(state.get("groups_confirmed")),
        "rules_locked": bool(state.get("rules_locked")),
        "holdout_revealed": bool(state.get("rules_locked")),
        "expectations": state.get("expectations", []), "validation_summary": validation.get("summary"),
        "cases": cases, "jobs": [job_summary(job) for job in db.scalars(select(ParserWorkbenchJob)
            .where(ParserWorkbenchJob.workbench_id == row.id).order_by(ParserWorkbenchJob.created_at, ParserWorkbenchJob.id))],
        "created_at": row.created_at, "updated_at": row.updated_at}


def validate_groups(groups, cases):
    expected = {case["case_id"]: case for case in cases if case["role"] == "authoring"}
    seen, identifiers, result = set(), set(), []
    if not 1 <= len(groups) <= MAX_GROUPS:
        raise ParserVersionError("parser_groups_invalid", 422)
    for group in groups:
        group = deepcopy(group)
        gid, members, vendor = group["group_id"], group["case_ids"], group["waf_vendor"]
        if gid in identifiers or not members or len(set(members)) != len(members):
            raise ParserVersionError("parser_groups_invalid", 422)
        identifiers.add(gid)
        for identifier in members:
            case = expected.get(identifier)
            if case is None or identifier in seen or case["waf_vendor"].strip().casefold() != vendor.strip().casefold():
                raise ParserVersionError("parser_group_membership_invalid", 422)
            seen.add(identifier)
        if group.get("rule") is not None:
            group["rule"] = RuleProposalV1.model_validate(group["rule"]).model_dump(mode="json")
            validate_bundle(_bundle([{**group, "rule_id": gid}], validate=False))
        result.append(group)
    if seen != set(expected):
        raise ParserVersionError("parser_group_partition_required", 422)
    return result


def confirm_groups(db, crypto, identifier, payload, actor):
    row = edit_session(db, identifier, payload.expected_revision)
    frozen, state = documents(row, crypto)
    if state.get("rules_locked"):
        raise ParserVersionError("parser_holdout_rules_locked")
    if row.status == "draft":
        raise ParserVersionError("parser_format_discovery_required")
    groups = validate_groups([g.model_dump(mode="json", exclude_none=True) for g in payload.groups], frozen["cases"])
    baseline = get_version(db, row.baseline_version_id)
    previous = read_rule_bundle_definition(baseline, crypto)
    previous_rules = {rule["rule_id"]: rule for rule in (previous or {}).get("rules", [])}
    targets = set()
    for group in groups:
        target = group.get("target_rule_id")
        if target:
            old = previous_rules.get(target)
            if old is None or old["waf_vendor"].strip().casefold() != group["waf_vendor"].strip().casefold() or target in targets:
                raise ParserVersionError("parser_rule_target_invalid", 422)
            targets.add(target)
            group["rule_id"], group["rule_version"] = target, old["rule_version"] + 1
        else:
            group["rule_id"], group["rule_version"] = str(uuid.uuid5(uuid.UUID(row.id), group["group_id"])), 1
    state.update(groups=groups, groups_confirmed=True, groups_confirmed_by=actor,
                 groups_confirmed_at=utcnow().isoformat(), validation=None)
    store_state(row, crypto, state, status="generated" if all(g.get("rule") for g in groups) else "groups_confirmed")
    audit(db, actor, "confirm_parser_groups", row.id, "parser_workbench")
    return row


def save_expectations(db, crypto, identifier, payload, actor):
    row = edit_session(db, identifier, payload.expected_revision)
    frozen, state = documents(row, crypto)
    if len(canonical(payload.model_dump(mode="json")).encode("utf-8")) > MAX_PREVIEW_CIPHERTEXT_BYTES:
        raise ParserVersionError("parser_expectations_size_limit", 413)
    cases = {case["case_id"] for case in frozen["cases"]}
    if len({item.case_id for item in payload.items}) != len(payload.items):
        raise ParserVersionError("parser_expectations_invalid", 422)
    previous = {item["case_id"]: item for item in state.get("expectations", [])}
    for item in payload.items:
        if item.case_id not in cases:
            raise ParserVersionError("parser_expectation_case_invalid", 422)
        previous[item.case_id] = {"case_id": item.case_id, "fields": item.model_dump(mode="json")["fields"],
            "confirmed_by": actor, "confirmed_at": utcnow().isoformat()}
    if state.get("rules_locked"):
        state.setdefault("expectation_amendments", []).append({"previous": state.get("expectations", []),
            "amended_by": actor, "amended_at": utcnow().isoformat()})
    state.update(expectations=list(previous.values()), validation=None)
    store_state(row, crypto, state, status="generated" if state.get("groups") and all(g.get("rule") for g in state["groups"]) else row.status)
    audit(db, actor, "confirm_parser_expected_fields", row.id, "parser_workbench")
    return row


def _bundle(groups, *, validate=True):
    result = {"schema_version": "vendor-parser-bundle-v1", "selection_policy_version": "vendor-rule-selection-v1",
        "rules": [{"rule_id": group.get("rule_id", group["group_id"]), "rule_version": group.get("rule_version", 1),
                   "waf_vendor": group["waf_vendor"], "name": group["name"], "definition": group["rule"]} for group in groups]}
    return validate_bundle(result) if validate else result


def candidate_bundle(state, baseline, crypto):
    incoming = _bundle(state["groups"])
    previous = read_rule_bundle_definition(baseline, crypto)
    if previous:
        old = {rule["rule_id"]: rule for rule in previous["rules"]}
        targets = {group.get("target_rule_id") for group in state["groups"] if group.get("target_rule_id")}
        for rule in incoming["rules"]:
            if rule["rule_id"] in old and old[rule["rule_id"]] != rule:
                current = old[rule["rule_id"]]
                if (rule["rule_id"] not in targets or current["waf_vendor"].casefold() != rule["waf_vendor"].casefold()
                        or rule["rule_version"] != current["rule_version"] + 1):
                    raise ParserVersionError("parser_rule_revision_conflict")
            old[rule["rule_id"]] = rule
        incoming["rules"] = list(old.values())
    return validate_bundle(incoming)


def field_value(parsed, field):
    value = parsed.get(field)
    if field in {"headers", "query"}:
        if isinstance(value, dict):
            return [{"name": str(key), "value": str(item)} for key, values in value.items()
                    for item in (values if isinstance(values, list) else [values])]
        return [{"name": str(pair[0]), "value": str(pair[1])} if isinstance(pair, (tuple, list)) else pair
                for pair in value or []]
    return value


def _compare_fields(parsed, expectations):
    items = []
    for field, expected in expectations.items():
        actual = field_value(parsed, field)
        status = "unknown"
        if expected["state"] == "present":
            status = "match" if actual == expected.get("value") else "mismatch"
        elif expected["state"] == "absent":
            status = "match" if actual in (None, "", []) else "mismatch"
        span_key = "request_target" if field == "uri" else field
        spans = [{"start": span[0], "end": span[1]} for span in parsed.get("raw_source_spans", {}).get(span_key, [])]
        items.append({"field": field, "expected": expected, "actual": actual, "status": status, "source_spans": spans})
    return items


def _parse_candidate(raw, bundle, baseline, crypto, vendor):
    if baseline.engine_version == "vendor-http-v2":
        fallback = decrypt_rule_document(baseline.options_json, crypto)["fallback"]
    else:
        fallback = {"engine_version": baseline.engine_version, "options": baseline.options_json,
                    "content_hash": baseline.content_hash}
    document = {"schema_version": DOCUMENT_VERSION, "bundle": bundle, "fallback": fallback}
    # Use the actual runtime selector and frozen fallback, including its warning
    # merge and diagnostic metadata, not a second execution of old vendor rules.
    snapshot = RuntimeRuleSnapshot({"options": {"definition_hash": digest(document)}}, document)
    return apply_rule_snapshot(raw, snapshot, waf_vendor=vendor)


def _counts(items):
    count = Counter(item["status"] for item in items)
    total = count["match"] + count["mismatch"]
    return {"field_total": total, "field_matches": count["match"], "field_mismatches": count["mismatch"],
            "field_accuracy": count["match"] / total if total else None}


def validate_session(db, crypto, identifier, payload, actor):
    row = edit_session(db, identifier, payload.expected_revision)
    frozen, state = documents(row, crypto)
    if not state.get("groups_confirmed") or not state.get("groups") or not all(g.get("rule") for g in state["groups"]):
        raise ParserVersionError("parser_generated_rules_required")
    baseline = get_version(db, row.baseline_version_id)
    if baseline.content_hash != frozen["baseline_content_hash"]:
        raise ParserVersionError("parser_baseline_changed")
    bundle = candidate_bundle(state, baseline, crypto)
    expectations = {item["case_id"]: item["fields"] for item in state.get("expectations", [])}
    items, all_fields, role_fields = [], [], {"authoring": [], "holdout": []}
    regressions, source_errors, cross_checks, cross_matches = 0, 0, 0, 0
    covered_rules = set()
    for case in frozen["cases"]:
        raw, vendor = case["raw_payload"], case["waf_vendor"]
        before = parse_version_payload(raw, baseline, crypto, waf_vendor=vendor)
        after = _parse_candidate(raw, bundle, baseline, crypto, vendor)
        fields = _compare_fields(after, expectations.get(case["case_id"], {}))
        old_fields = {item["field"]: item for item in _compare_fields(before, expectations.get(case["case_id"], {}))}
        regressions += sum(item["status"] == "mismatch" and old_fields[item["field"]]["status"] == "match" for item in fields)
        valid = valid_source_spans(raw, after)
        source_errors += not valid
        confirmed = expectations.get(case["case_id"], {})
        if (case["role"] == "holdout" and valid
                and all(confirmed.get(field, {}).get("state") == "present" for field in ("method", "uri"))
                and all(item["status"] == "match" for item in fields if item["field"] in {"method", "uri"})
                and after.get("capture_selection", {}).get("status") in {"matched", "equivalent"}):
            covered_rules.update(item["rule_id"] for item in after["capture_selection"].get("candidates", [])
                                 if item.get("reason_code") == "parser_rule_qualified")
        # A different format's rule can legitimately match the same extraction.
        # Record cross-format matches, but never invent a negative label merely
        # because the vendor also exports the same standard wire format.
        own_group = next((group["group_id"] for group in state["groups"] if case["case_id"] in group["case_ids"]), None)
        for group in state["groups"]:
            if group["group_id"] == own_group or group["waf_vendor"].casefold() != vendor.casefold():
                continue
            cross = parse_rule_bundle(raw, _bundle([group]), waf_vendor=vendor)
            cross_checks += 1
            cross_matches += cross.get("capture_selection", {}).get("status") in {"matched", "equivalent"}
        if not valid:
            for item in fields:
                item["source_spans"] = []
        all_fields.extend(fields)
        role_fields[case["role"]].extend(fields)
        qualified_ids = {candidate["rule_id"] for candidate in after.get("capture_selection", {}).get("candidates", [])
                         if candidate.get("reason_code") == "parser_rule_qualified"}
        group_ids = [group["group_id"] for group in state["groups"] if group["rule_id"] in qualified_ids]
        if after.get("capture_selection", {}).get("status") not in {"matched", "equivalent"}:
            group_ids = []
        items.append({"case_id": case["case_id"], "baseline": before, "candidate": after, "comparisons": fields,
            "group_ids": group_ids,
            "valid_source_spans": valid, "changed_fields": [field for field in FIELDS if before.get(field) != after.get(field)]})
    # Shared boundary probes contain only fixed synthetic data. Success is not
    # required for malformed probes; any reported provenance must remain valid.
    edge_raw = ("", "not an HTTP request", "GET /edge HTTP/1.1\r\nHost: example.invalid\r\n\r\n", '{"request":null}',
                'prefix GET /fake HTTP/1.1', '{"request":"GET /a HTTP/1.1","request":"GET /b HTTP/1.1"}')
    edge_failures = 0
    for raw in edge_raw:
        for vendor in {group["waf_vendor"] for group in state["groups"]}:
            parsed = _parse_candidate(raw, bundle, baseline, crypto, vendor)
            edge_failures += not valid_source_spans(raw, parsed)
    summary = {"completed": True, **_counts(all_fields),
        **{role: _counts(fields) for role, fields in role_fields.items()}, "regression_count": regressions,
        "source_error_count": source_errors, "negative_format_checks": cross_checks,
        "negative_format_matches": cross_matches,
        "edge_case_count": len(edge_raw) * len({group["waf_vendor"] for group in state["groups"]}),
        "edge_case_failures": edge_failures, "baseline_content_hash": baseline.content_hash,
        "bundle_hash": digest(bundle), "human_confirmed_fields_only": True}
    required_rules = {group["rule_id"] for group in state["groups"]}
    summary["holdout_rule_count"] = len(covered_rules & required_rules)
    summary["required_rule_count"] = len(required_rules)
    summary["minimum_holdout_fields_confirmed"] = required_rules <= covered_rules
    summary["expectations_amended_after_validation"] = bool(state.get("expectation_amendments"))
    summary["ready"] = bool(summary["field_total"] and summary["holdout"]["field_total"]
        and summary["minimum_holdout_fields_confirmed"]
        and not summary["field_mismatches"] and not regressions and not source_errors and not edge_failures)
    state["validation"] = {"summary": summary, "items": items, "bundle": bundle,
        "expectations_hash": digest(state.get("expectations", [])), "validated_by": actor, "validated_at": utcnow().isoformat()}
    # Once holdout results are exposed, freeze this candidate's rule definitions.
    # A new rule attempt requires a new frozen session, not iterative holdout fit.
    state["rules_locked"] = True
    store_state(row, crypto, state, status="validated")
    row.metadata_json = {**row.metadata_json, "validation_ready": summary["ready"], "confirmed_field_count": summary["field_total"]}
    audit(db, actor, "validate_parser_workbench", row.id, "parser_workbench")
    return row


def save_candidate(db, crypto, identifier, payload, actor):
    row = edit_session(db, identifier, payload.expected_revision)
    frozen, state = documents(row, crypto)
    validation = state.get("validation") or {}
    summary = validation.get("summary") or {}
    baseline = get_version(db, row.baseline_version_id)
    if (not summary.get("ready") or summary.get("baseline_content_hash") != baseline.content_hash
            or validation.get("expectations_hash") != digest(state.get("expectations", []))
            or summary.get("bundle_hash") != digest(candidate_bundle(state, baseline, crypto))):
        raise ParserVersionError("parser_workbench_validation_required")
    if payload.parent_id and payload.parent_id != baseline.id:
        raise ParserVersionError("parser_workbench_parent_mismatch")
    version = create_rule_bundle_version(db, crypto, validation["bundle"], name=payload.name,
        parent_id=baseline.id, actor=actor)
    row.candidate_version_id = version.id
    state["validation"]["summary"]["candidate_content_hash"] = version.content_hash
    store_state(row, crypto, state, status="saved")
    row.metadata_json = {**row.metadata_json, "candidate_content_hash": version.content_hash,
        "baseline_content_hash": baseline.content_hash, "validation_ready": True, "review_revision": row.revision}
    audit(db, actor, "save_parser_workbench_candidate", row.id, "parser_workbench")
    return row


def workbench_parser_review_ready(db, candidate_id, baseline_id):
    """Safe metadata gate; field review is not an official verdict evaluation."""
    candidate, baseline = db.get(ParserVersion, candidate_id), db.get(ParserVersion, baseline_id)
    if candidate is None or baseline is None:
        return False
    rows = db.scalars(select(ParserWorkbench).where(ParserWorkbench.candidate_version_id == candidate_id,
        ParserWorkbench.baseline_version_id == baseline_id, ParserWorkbench.status == "saved"))
    return any(row.metadata_json.get("validation_ready") is True
        and row.metadata_json.get("review_revision") == row.revision
        and row.metadata_json.get("confirmed_field_count", 0) > 0
        and row.metadata_json.get("candidate_content_hash") == candidate.content_hash
        and row.metadata_json.get("baseline_content_hash") == baseline.content_hash for row in rows)


def catalog(db, crypto, actor):
    configuration = ensure_parser_state(db)
    versions = list(db.scalars(select(ParserVersion).order_by(ParserVersion.created_at.desc(), ParserVersion.id).limit(200)))
    datasets = list(db.scalars(select(ValidationDatasetVersion).join(ValidationDataset,
        ValidationDataset.id == ValidationDatasetVersion.dataset_id).where(ValidationDataset.deleted_at.is_(None))
        .order_by(ValidationDatasetVersion.created_at.desc()).limit(200)))
    runs = list(db.scalars(select(TestRun).options(load_only(TestRun.id, TestRun.name, TestRun.created_at))
        .order_by(TestRun.created_at.desc()).limit(200)))
    run_counts = dict(db.execute(select(TestRunItem.test_run_id, func.count()).where(TestRunItem.test_run_id.in_(
        [run.id for run in runs])).group_by(TestRunItem.test_run_id)).all())
    profiles = []
    from .parser_jobs import approved_profile
    for profile in db.scalars(select(VLLMProfile).where(VLLMProfile.provider == "vllm")):
        try:
            approved_profile(db, profile.id)
            profiles.append({"id": profile.id, "name": profile.name, "model_name": profile.model_name})
        except ParserVersionError:
            pass
    latest_rule_version = db.scalar(select(ParserVersion).where(ParserVersion.engine_version == "vendor-http-v2")
        .order_by(ParserVersion.created_at.desc(), ParserVersion.id).limit(1))
    rule_versions = {configuration.active_version_id: get_version(db, configuration.active_version_id)}
    if latest_rule_version is not None:
        rule_versions[latest_rule_version.id] = latest_rule_version
    rules = []
    for version in rule_versions.values():
        bundle = read_rule_bundle_definition(version, crypto)
        for rule in (bundle or {}).get("rules", []):
            rules.append({"rule_id": rule["rule_id"], "name": rule.get("name", rule["definition"]["name"]),
                "waf_vendor": rule["waf_vendor"], "method": rule["definition"]["extract"]["kind"],
                "rule_version": rule["rule_version"], "version_id": version.id, "version_name": version.name,
                "in_production": version.id == configuration.active_version_id})
    sessions = list(db.scalars(select(ParserWorkbench).options(load_only(ParserWorkbench.id, ParserWorkbench.name,
        ParserWorkbench.status, ParserWorkbench.revision, ParserWorkbench.created_at, ParserWorkbench.updated_at))
        .order_by(ParserWorkbench.updated_at.desc(), ParserWorkbench.id).limit(100)))
    audit(db, actor, "list_parser_workbench", "all", "parser_workbench")
    dataset_names = dict(db.execute(select(ValidationDataset.id, ValidationDataset.name).where(
        ValidationDataset.id.in_([item.dataset_id for item in datasets]))).all())
    return {"dataset_versions": [{"id": item.id, "dataset_id": item.dataset_id, "name": item.name,
        "dataset_name": dataset_names.get(item.dataset_id, item.name),
        "revision": item.revision, "item_count": len(item.item_version_ids), "published": item.is_published} for item in datasets],
        "test_runs": [{"id": run.id, "name": run.name, "item_count": run_counts.get(run.id, 0)} for run in runs],
        "parser_versions": [version_summary(version) for version in versions], "model_profiles": profiles, "rules": rules,
        "active_version_id": configuration.active_version_id,
        "sessions": [{"id": row.id, "name": row.name, "status": row.status, "revision": row.revision,
                      "created_at": row.created_at, "updated_at": row.updated_at} for row in sessions],
        "limits": {"max_cases": MAX_CASES, "max_groups": MAX_GROUPS, "source_bytes": MAX_SOURCE_BYTES,
                   "total_source_bytes": MAX_TOTAL_BYTES}}
