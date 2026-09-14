from typing import Annotated, Literal
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..agent.grounding_repair import DIAGNOSTIC_VERSIONS
from ..models import AccessAudit, AgentConfiguration, AgentRun, AgentStep, Analysis, VLLMProfile, utcnow
from ..security import Principal, require_scope
from ..services.agent_configuration import configuration_document, has_input_budget, validate_role_profile
from ..services.internal_egress import lock_egress_mutation
from ..services.vllm_profiles import TargetNotAllowedError
from .model_profiles import begin_test_read_snapshot, validate_profile_for_request

router = APIRouter(prefix="/admin/agent-settings", tags=["agent-settings"])
Db = Annotated[Session, Depends(get_db)]
Admin = Annotated[Principal, Depends(require_scope("admin"))]


class RoleSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    primary_profile_id: str | None = Field(max_length=36)
    # null is the explicit "same as Primary" selection, not a failure fallback.
    verifier_profile_id: str | None = Field(max_length=36)


class ConfigurationUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_state_token: str = Field(pattern=r"^[0-9a-f]{64}$")
    production: RoleSelection
    test: RoleSelection
    external_transfer_acknowledged: bool = False


@router.get("")
def get_configuration(db: Db, _admin: Admin, response: Response):
    response.headers["Cache-Control"] = "no-store"
    begin_test_read_snapshot(db)
    return configuration_document(db)


@router.get("/diagnostics")
def get_diagnostics(db: Db, _admin: Admin, response: Response,
                    days: Annotated[int, Query(ge=1, le=90)] = 7,
                    purpose: Literal["all", "production", "test"] = "all"):
    response.headers["Cache-Control"] = "no-store"
    begin_test_read_snapshot(db)
    value = lambda path: func.json_extract(Analysis.result_json, "$.diagnostics." + path)
    measured = value("version").in_(DIAGNOSTIC_VERSIONS) & (Analysis.status == "completed")
    count = lambda condition: func.coalesce(func.sum(case((condition, 1), else_=0)), 0)
    reasons = ("primary_evidence_rejected", "verifier_evidence_rejected", "primary_model_inconclusive",
               "verifier_model_inconclusive", "verifier_failed", "verdict_disagreement", "input_integrity_limited")
    fields = {"total": func.count(), "completed": count(Analysis.status == "completed"),
              "failed": count(Analysis.status == "failed"), "measured": count(measured),
              "inconclusive": count(measured & (Analysis.verdict == "inconclusive"))}
    for reason in reasons:
        fields[reason] = count(measured & value("inconclusive_reasons").contains('"' + reason + '"'))
    for role in ("primary", "verifier"):
        for field in ("repair_attempted", "repair_recovered", "grounding_downgraded"):
            fields[role + "_" + field] = count(measured & (value(f"roles.{role}.{field}") == 1))
    for signal in ("parser_incomplete", "input_truncated", "input_integrity_observed"):
        fields[signal] = count(measured & value("input_signals").contains('"' + signal + '"'))
    query = select(*(expression.label(name) for name, expression in fields.items())).select_from(Analysis).where(
        Analysis.created_at >= utcnow() - timedelta(days=days))
    if purpose != "all":
        query = query.where(Analysis.analysis_purpose == purpose)
    counts = dict(db.execute(query).mappings().one())
    last_run = select(AgentRun.id).where(AgentRun.analysis_id == Analysis.id).order_by(
        AgentRun.created_at.desc(), AgentRun.id.desc()).limit(1).correlate(Analysis).scalar_subquery()
    category = func.json_extract(AgentStep.metadata_json, "$.failure_category")
    failures = select(category, func.count()).select_from(Analysis).join(AgentStep, AgentStep.run_id == last_run).where(
        Analysis.created_at >= utcnow() - timedelta(days=days), AgentStep.status == "failed",
        AgentStep.step_type.in_(("llm_primary", "llm_verifier")),
        category.in_(("output_validation_failed", "output_incomplete", "model_refusal", "invocation_failed")))
    if purpose != "all":
        failures = failures.where(Analysis.analysis_purpose == purpose)
    failure_counts = dict(db.execute(failures.group_by(category)).all())
    return {"days": days, "purpose": purpose, "counts": counts,
            "llm_failure_counts": failure_counts,
            "inconclusive_rate": counts["inconclusive"] / counts["measured"] if counts["measured"] else None,
            "unmeasured_completed": counts["completed"] - counts["measured"],
            "reason_counts_overlap": True, "raw_data_read": False}


@router.put("")
def set_configuration(payload: ConfigurationUpdate, db: Db, admin: Admin, request: Request, response: Response):
    response.headers["Cache-Control"] = "no-store"
    lock_egress_mutation(db)
    if configuration_document(db)["state_token"] != payload.expected_state_token:
        raise HTTPException(409, "agent_configuration_changed")
    selected = {}
    from ..services.prompt_policies import PromptPolicyError, get_active_policy, read_policy_text
    try:
        policy_text = read_policy_text(get_active_policy(db, request.app.state.crypto), request.app.state.crypto)
    except PromptPolicyError as exc:
        raise HTTPException(exc.status_code, exc.code) from None
    for purpose in ("production", "test"):
        roles = getattr(payload, purpose)
        if roles.verifier_profile_id and not roles.primary_profile_id:
            raise HTTPException(422, "agent_primary_required")
        for role, identifier in roles.model_dump().items():
            if identifier:
                profile = db.get(VLLMProfile, identifier)
                try:
                    validate_role_profile(db, profile)
                except TargetNotAllowedError as exc:
                    raise HTTPException(409, str(exc)) from None
                validate_profile_for_request(profile, request, db)
                if not has_input_budget(profile, policy_text):
                    raise HTTPException(422, "agent_context_budget_too_small")
                if profile.provider == "openai" and not payload.external_transfer_acknowledged:
                    raise HTTPException(422, "agent_external_transfer_acknowledgement_required")
                selected[purpose, role] = profile
    config = db.get(AgentConfiguration, 1)
    if config is None:
        config = AgentConfiguration(id=1, revision=0)
        db.add(config)
    for profile in db.scalars(select(VLLMProfile)):
        if profile.status == "production":
            profile.status = "verified"
        profile.is_test = False
    db.flush()  # Respect the existing partial unique Primary-role indexes.
    for purpose in ("production", "test"):
        primary = selected.get((purpose, "primary_profile_id"))
        if primary:
            if purpose == "production":
                primary.status = "production"
            else:
                primary.is_test = True
        setattr(config, purpose + "_verifier_profile_id", getattr(payload, purpose).verifier_profile_id)
    config.revision += 1
    db.add(AccessAudit(actor_kind=admin.kind, actor_id=admin.username or "admin",
                       action="update_agent_configuration", resource_type="agent_configuration",
                       resource_id=str(config.revision)))
    db.commit()
    return configuration_document(db)
