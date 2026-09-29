"""Explicit live-model acceptance on synthetic cases in a disposable database.

Run inside the configured backend environment with the candidate code on
PYTHONPATH. Never reads production event payloads or changes role assignments.
The only writes to the source DB are the normal, bounded LLM concurrency leases.
"""
import argparse
import hashlib
import json
import logging
import tempfile
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import worker
from app.agent.prompts import FIXED_RULES_VERSION
from app.candidate_schemas import CandidateConfiguration
from app.config import get_settings
from app.database import Base, build_engine
from app.models import Analysis, AgentStep, InternalEgressTarget, VLLMProfile, VLLMTestRun
from app.services.agent_configuration import role_request_check, validate_role_profile
from app.services.crypto import CryptoService
from app.services.input_schemas import get_active_schema
from app.services.prompt_policies import get_active_policy
from app.services.test_runs import enqueue_named_run
from app.services.vllm_profiles import profile_fingerprint


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile-name", required=True)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--execute-model-calls", action="store_true", help="Explicit authorization; may incur API costs")
    args = parser.parse_args()
    if not args.execute_model_calls:
        parser.error("No calls made. Use --execute-model-calls only after authorization.")
    data = args.cases.read_bytes()
    if hashlib.sha256(data).hexdigest() != "96aead901321ce4cd8e1e61dd64346ee1ebb7c98b8e46e925c29930180780f13":
        parser.error("Only the reviewed synthetic acceptance_10.json is allowed; no calls made.")
    rows = json.loads(data)
    if len(rows) != 10 or any(row.get("test_category") != "followup-policy-v1" for row in rows):
        parser.error("Use the reviewed synthetic acceptance_10.json, not operating data.")
    settings = get_settings()
    live = build_engine(settings.database_url)
    crypto = CryptoService(settings.data_encryption_key, settings.encryption_key_version)
    # Do not expose configured keys, URLs, prompts or event bodies in console logs.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    with Session(live) as source:
        profile = source.scalar(select(VLLMProfile).where(VLLMProfile.name == args.profile_name))
        validate_role_profile(source, profile)
        profile_values = {column.name: getattr(profile, column.name) for column in VLLMProfile.__table__.columns}
        verification = source.scalar(select(VLLMTestRun).where(VLLMTestRun.profile_id == profile.id,
            VLLMTestRun.mode == "full", VLLMTestRun.status == "passed",
            VLLMTestRun.profile_fingerprint == profile_fingerprint(profile)).limit(1))
        proof = {key: getattr(verification, key) for key in ("id", "profile_id", "mode", "status", "profile_fingerprint", "created_at", "completed_at")}
        targets = [{column.name: getattr(row, column.name) for column in InternalEgressTarget.__table__.columns}
            for row in source.scalars(select(InternalEgressTarget))] if profile.provider == "vllm" else []
        live_check = role_request_check(live, profile)
    original_execute = worker.execute_structured_agent
    async def execute(**kwargs):
        # Respect the serving endpoint's live slot limit, not an independent pool.
        kwargs["concurrency_engine"] = live
        return await original_execute(**kwargs)
    worker.execute_structured_agent = execute
    args.output_dir.mkdir(parents=True, exist_ok=True)
    results = []
    with tempfile.TemporaryDirectory(prefix="waf-followup-acceptance-") as directory:
        engine = build_engine("sqlite+pysqlite:///" + str(Path(directory) / "acceptance.db"))
        Base.metadata.create_all(engine)
        local_settings = settings.model_copy(update={"agent_mode": "moduagent"})
        with Session(engine) as db:
            db.add(VLLMProfile(**profile_values))
            db.flush()
            db.add(VLLMTestRun(**proof))
            for target in targets:
                db.add(InternalEgressTarget(**target))
            db.commit()
            policy = get_active_policy(db, crypto)
            schema = get_active_schema(db, crypto)
            candidate = CandidateConfiguration(primary_profile_id=profile_values["id"],
                verifier_profile_id=profile_values["id"], evidence_editor_enabled=False,
                prompt_policy_version_id=policy.id, input_schema_version_id=schema.id)
            db.commit()
            run, _ = enqueue_named_run(db, crypto, local_settings, name="PR A 후속 확인 검증 10건",
                idempotency_key="followup-v214-acceptance-10", rows=rows, kind="upload",
                actor="synthetic-acceptance", filename=args.cases.name, candidate_configuration=candidate)
            analyses = list(db.scalars(select(Analysis).order_by(Analysis.event_id)))
            if len(analyses) != 10:
                raise RuntimeError("synthetic_acceptance_ingest_incomplete")
            print(json.dumps({"model": profile_values["model_name"], "provider": profile_values["provider"],
                "rules": FIXED_RULES_VERSION, "cases": len(analyses), "production_changed": False,
                "policy": "default editable instructions", "schema": "default input schema", "editor": False}), flush=True)
            for analysis, case in zip(analyses, rows, strict=True):
                try:
                    worker.process_moduagent(db, crypto, analysis, "", settings.verifier_confidence_threshold, request_check=live_check)
                except Exception as exc:
                    db.rollback()
                    # Never echo provider exceptions or raw invalid model output.
                    code = getattr(exc, "code", "acceptance_execution_failed")
                    results.append({"case": case["case_name"], "status": "failed", "error_code": code})
                    print(json.dumps(results[-1], ensure_ascii=False), flush=True)
                    continue
                result = analysis.result_json
                final = result.get("analyst_checks", [])
                tuned = result["tuning_recommendation"]["recommended"]
                denied_tp = result["verdict"] == "true_positive" and analysis.waf_action == "D"
                violations = []
                if denied_tp and any(check["purpose"] == "impact_followup" for check in final):
                    violations.append("denied_attack_has_impact_check")
                if not tuned and any(check["purpose"] == "tuning_validation" for check in final):
                    violations.append("tuning_check_without_proposal")
                if denied_tp and not tuned and final:
                    violations.append("denied_attack_without_tuning_has_checks")
                steps = list(db.scalars(select(AgentStep).where(AgentStep.run_id.in_(
                    select(worker.AgentRun.id).where(worker.AgentRun.analysis_id == analysis.id)))))
                record = {"case": case["case_name"], "event_id": analysis.event_id, "status": analysis.status,
                    "expected_verdict": case["expected_verdict"], "verdict": result["verdict"], "waf_action": analysis.waf_action,
                    "match": result["verdict"] == case["expected_verdict"], "tuning_recommended": tuned,
                    "final_checks": len(final), "purposes": [check["purpose"] for check in final],
                    "followup_policy": result["diagnostics"].get("followup_policy"), "violations": violations,
                    "verifier_executed": result["verifier"]["executed"],
                    "output_retries": sum(max(0, step.metadata_json.get("output_validation_retry", {}).get("attempt_count", 1) - 1) for step in steps)}
                results.append(record)
                # Synthetic final outputs only. Do not export prompts, credentials,
                # encrypted steps or any records from the source database.
                (args.output_dir / f"{analysis.event_id}.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
                print(json.dumps(record, ensure_ascii=False), flush=True)
                (args.output_dir / "summary.json").write_text(json.dumps(results, ensure_ascii=False, indent=2))
        engine.dispose()
    worker.execute_structured_agent = original_execute
    live.dispose()
    (args.output_dir / "summary.json").write_text(json.dumps(results, ensure_ascii=False, indent=2))
    # A working final filter is not proof that the expected HOLD cases ran as
    # HOLD. Report such quality/coverage failures separately from call failures.
    return 1 if any(row["status"] != "completed" or row.get("violations") or not row.get("match") for row in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
