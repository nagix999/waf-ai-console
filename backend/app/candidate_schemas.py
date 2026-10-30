"""Execution configuration is admin metadata, never a WAF event field."""
from typing import Annotated
from pydantic import BaseModel, ConfigDict, Field, model_validator

Identifier = Annotated[str, Field(min_length=1, max_length=36)]


class CandidateConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    primary_profile_id: Identifier
    verifier_profile_id: Identifier | None = None
    evidence_editor_enabled: bool = False
    evidence_editor_profile_id: Identifier | None = None
    prompt_policy_version_id: Identifier
    input_schema_version_id: Identifier
    # Missing in pre-parser clients means the immutable original parser, not
    # whichever version was most recently saved or applied to Production.
    parser_version_id: Identifier | None = None

    @model_validator(mode="after")
    def editor_requires_enable(self):
        if not self.evidence_editor_enabled and self.evidence_editor_profile_id is not None:
            raise ValueError("candidate_editor_disabled")
        return self

    def request_document(self):
        """Retain historical idempotency hashes when no parser was selected."""
        return self.model_dump(mode="json", exclude={"parser_version_id"} if self.parser_version_id is None else set())
