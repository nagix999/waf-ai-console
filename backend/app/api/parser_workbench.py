"""Admin-only encrypted parser authoring. No direct Production activation."""
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import ParserWorkbenchJob
from ..parser_rule_schemas import RuleProposalV1
from ..security import Principal, require_scope
from ..services import parser_workbench as service
from ..services.parser_workbench_jobs import enqueue_job
from .parsers import errors

router = APIRouter(prefix="/admin/parser-workbench", tags=["parser-workbench"])
DbSession = Annotated[Session, Depends(get_db)]
Admin = Annotated[Principal, Depends(require_scope("admin"))]


class Closed(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Source(Closed):
    dataset_version_id: str | None = Field(default=None, min_length=1, max_length=36)
    test_run_id: str | None = Field(default=None, min_length=1, max_length=36)
    analysis_ids: list[str] | None = Field(default=None, min_length=2, max_length=100)
    case_ids: list[str] | None = Field(default=None, min_length=2, max_length=100)

    @model_validator(mode="after")
    def one_source(self):
        if sum(value is not None for value in (self.dataset_version_id, self.test_run_id, self.analysis_ids)) != 1:
            raise ValueError("parser_source_required")
        if self.analysis_ids and any(not value or len(value) > 36 for value in self.analysis_ids):
            raise ValueError("parser_source_invalid")
        if self.case_ids and any(not value or len(value) > 36 for value in self.case_ids):
            raise ValueError("parser_source_invalid")
        return self


class Create(Closed):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    source: Source
    baseline_version_id: str | None = Field(default=None, min_length=1, max_length=36)
    max_cases: StrictInt = Field(default=100, ge=2, le=100)


class Revision(Closed):
    expected_revision: StrictInt = Field(ge=1)


class Preview(Closed):
    source: Source
    max_cases: StrictInt = Field(default=100, ge=2, le=100)
    limit: StrictInt = Field(default=50, ge=1, le=100)
    offset: StrictInt = Field(default=0, ge=0, le=100000)


class JobCreate(Revision):
    profile_id: str = Field(min_length=1, max_length=36)
    internal_data_confirmed: StrictBool

    @model_validator(mode="after")
    def confirmed(self):
        if self.internal_data_confirmed is not True:
            raise ValueError("parser_internal_data_confirmation_required")
        return self


class Group(Closed):
    group_id: str = Field(min_length=1, max_length=36, pattern=r"^[a-zA-Z0-9_-]+$")
    name: str = Field(min_length=1, max_length=120)
    waf_vendor: str = Field(min_length=1, max_length=120)
    case_ids: list[str] = Field(min_length=1, max_length=100)
    target_rule_id: str | None = Field(default=None, min_length=1, max_length=36)
    rule: RuleProposalV1 | None = None


class Groups(Revision):
    groups: list[Group] = Field(min_length=1, max_length=20)


class Pair(Closed):
    name: str = Field(max_length=65536)
    value: str = Field(max_length=65536)


class Expected(Closed):
    state: Literal["present", "absent", "unknown"]
    value: str | list[Pair] | None = None

    @model_validator(mode="after")
    def present_value(self):
        if self.state == "present" and self.value is None:
            raise ValueError("parser_expected_value_required")
        if self.state != "present" and self.value is not None:
            raise ValueError("parser_unexpected_value")
        if isinstance(self.value, str) and len(self.value) > 65536:
            raise ValueError("parser_expected_value_limit")
        if isinstance(self.value, list) and len(self.value) > 512:
            raise ValueError("parser_expected_value_limit")
        return self


class Expectation(Closed):
    case_id: str = Field(min_length=1, max_length=36)
    fields: dict[str, Expected] = Field(max_length=6)

    @model_validator(mode="after")
    def typed_fields(self):
        if set(self.fields) - set(service.FIELDS):
            raise ValueError("parser_expected_field_invalid")
        for key, expected in self.fields.items():
            if expected.state == "present" and (isinstance(expected.value, list) != (key in {"headers", "query"})):
                raise ValueError("parser_expected_type_invalid")
        return self


class Expectations(Revision):
    items: list[Expectation] = Field(max_length=100)


class Save(Revision):
    name: str = Field(min_length=1, max_length=120)
    parent_id: str | None = Field(default=None, min_length=1, max_length=36)


def _actor(principal):
    return principal.username or "unknown"


def _response(db, request, row, principal):
    db.flush()
    result = service.session_detail(db, request.app.state.crypto, row.id, _actor(principal))
    db.commit()
    return result


@router.get("/catalog")
def catalog(request: Request, db: DbSession, principal: Admin):
    with errors(db):
        result = service.catalog(db, request.app.state.crypto, _actor(principal))
        db.commit()
        return result


@router.post("/sessions", status_code=201)
def create(payload: Create, request: Request, db: DbSession, principal: Admin):
    with errors(db):
        row = service.create_session(db, request.app.state.crypto, payload, _actor(principal))
        return _response(db, request, row, principal)


@router.post("/source-preview")
def preview(payload: Preview, request: Request, db: DbSession, principal: Admin):
    with errors(db):
        result = service.source_preview(db, request.app.state.crypto, payload, _actor(principal))
        db.commit()
        return result


@router.get("/sessions/{identifier}")
def detail(identifier: str, request: Request, db: DbSession, principal: Admin):
    with errors(db):
        row = service.get_session(db, identifier)
        return _response(db, request, row, principal)


@router.post("/sessions/{identifier}/discover", status_code=202)
def discover(identifier: str, payload: JobCreate, request: Request, db: DbSession, principal: Admin):
    with errors(db):
        row = enqueue_job(db, request.app.state.crypto, identifier, payload, _actor(principal), "format_discovery")
        result = service.job_summary(row)
        db.commit()
        return result


@router.post("/sessions/{identifier}/groups")
def groups(identifier: str, payload: Groups, request: Request, db: DbSession, principal: Admin):
    with errors(db):
        row = service.confirm_groups(db, request.app.state.crypto, identifier, payload, _actor(principal))
        return _response(db, request, row, principal)


@router.post("/sessions/{identifier}/generate", status_code=202)
def generate(identifier: str, payload: JobCreate, request: Request, db: DbSession, principal: Admin):
    with errors(db):
        row = enqueue_job(db, request.app.state.crypto, identifier, payload, _actor(principal), "rule_generation")
        result = service.job_summary(row)
        db.commit()
        return result


@router.put("/sessions/{identifier}/expectations")
def expectations(identifier: str, payload: Expectations, request: Request, db: DbSession, principal: Admin):
    with errors(db):
        row = service.save_expectations(db, request.app.state.crypto, identifier, payload, _actor(principal))
        return _response(db, request, row, principal)


@router.post("/sessions/{identifier}/validate")
def validate(identifier: str, payload: Revision, request: Request, db: DbSession, principal: Admin):
    with errors(db):
        row = service.validate_session(db, request.app.state.crypto, identifier, payload, _actor(principal))
        return _response(db, request, row, principal)


@router.post("/sessions/{identifier}/save-candidate", status_code=201)
def save(identifier: str, payload: Save, request: Request, db: DbSession, principal: Admin):
    with errors(db):
        row = service.save_candidate(db, request.app.state.crypto, identifier, payload, _actor(principal))
        return _response(db, request, row, principal)


@router.get("/sessions/{identifier}/jobs")
def jobs(identifier: str, request: Request, db: DbSession, principal: Admin):
    with errors(db):
        service.get_session(db, identifier)
        items = [service.job_summary(row) for row in db.scalars(select(ParserWorkbenchJob)
            .where(ParserWorkbenchJob.workbench_id == identifier).order_by(ParserWorkbenchJob.created_at, ParserWorkbenchJob.id))]
        service.audit(db, _actor(principal), "list_parser_workbench_jobs", identifier, "parser_workbench")
        db.commit()
        return {"items": items}
