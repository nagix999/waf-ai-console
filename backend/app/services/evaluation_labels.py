"""Bounded reference-file attachment; no HTTP event ingestion or LLM execution."""
import hashlib
import json
import time
import uuid
from datetime import UTC, datetime

from itsdangerous import BadData, URLSafeTimedSerializer
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from ..evaluation_schemas import MAX_PREVIEW_TOKEN_CHARS, LabelConfirmResponse, LabelPreviewIssue, LabelPreviewResponse, LabelPreviewRow
from ..models import AccessAudit, Analysis, AnalysisLabel
from .evaluation import latest_labels

MAX_LABEL_FILE_BYTES = 2 * 1024 * 1024
MAX_LABEL_ROWS = 500
PREVIEW_TTL_SECONDS = 15 * 60
TOKEN_SALT = "waf-evaluation-label-preview-v1"


class LabelAttachmentError(ValueError):
    def __init__(self, code: str, status_code: int = 422):
        self.code, self.status_code = code, status_code
        super().__init__(code)


def validate_source(source_system: str, source_ref: str) -> None:
    if not 1 <= len(source_system) <= 120 or source_system != source_system.strip() or not source_system.isprintable():
        raise LabelAttachmentError("invalid_label_source_system")
    if (not 1 <= len(source_ref) <= 120 or source_ref != source_ref.strip()
            or not all(character.isalnum() or character in " ._-" for character in source_ref)):
        raise LabelAttachmentError("invalid_label_source_ref")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise LabelAttachmentError("duplicate_answer_json_key")
        result[key] = value
    return result


def parse_answer_file(content: bytes, filename: str | None) -> list:
    if len(content) > MAX_LABEL_FILE_BYTES:
        raise LabelAttachmentError("label_file_too_large", 413)
    if not filename or not filename.lower().endswith(".json"):
        raise LabelAttachmentError("label_file_must_be_json")
    try:
        document = content.decode("utf-8-sig")
        depth, in_string, escaped = 0, False, False
        for character in document:
            if in_string:
                if escaped:
                    escaped = False
                elif character == "\\":
                    escaped = True
                elif character == '"':
                    in_string = False
            elif character == '"':
                in_string = True
            elif character in "[{":
                depth += 1
                if depth > 32:
                    raise LabelAttachmentError("label_json_too_deep")
            elif character in "]}":
                depth -= 1
        values = json.loads(document, object_pairs_hook=_unique_object, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (UnicodeError, ValueError, RecursionError) as exc:
        if isinstance(exc, LabelAttachmentError):
            raise
        raise LabelAttachmentError("invalid_label_json") from None
    if not isinstance(values, list) or not values:
        raise LabelAttachmentError("label_file_requires_nonempty_array")
    if len(values) > MAX_LABEL_ROWS:
        raise LabelAttachmentError("too_many_label_rows")
    return values


def _same_label(row, verdict: str, source_kind: str, source_ref: str, ai_visible: bool | None) -> bool:
    return row is not None and all(row[field] == value for field, value in {
        "verdict": verdict, "source_kind": source_kind, "source_ref": source_ref, "ai_visible": ai_visible,
    }.items())


def preview_labels(db: Session, *, answers: list, source_system: str, source_kind: str,
                   source_ref: str, ai_visible: bool | None, actor: str, secret: str) -> LabelPreviewResponse:
    validate_source(source_system, source_ref)
    errors, normalized, seen = [], [], set()
    for number, answer in enumerate(answers, 1):
        if not isinstance(answer, dict):
            errors.append(LabelPreviewIssue(row_number=number, code="answer_row_must_be_object"))
            continue
        event_id, verdict = answer.get("event_id"), answer.get("expected_verdict")
        if not isinstance(event_id, str) or not 1 <= len(event_id) <= 255 or event_id != event_id.strip() or not event_id.isprintable():
            errors.append(LabelPreviewIssue(row_number=number, code="invalid_answer_event_id", field="event_id"))
            continue
        if not isinstance(verdict, str) or verdict not in {"true_positive", "false_positive", "inconclusive"}:
            errors.append(LabelPreviewIssue(row_number=number, code="invalid_reference_verdict", field="expected_verdict"))
            continue
        if verdict == "inconclusive" and source_kind != "synthetic_expected":
            errors.append(LabelPreviewIssue(row_number=number, code="inconclusive_requires_synthetic_expected", field="expected_verdict"))
            continue
        if event_id in seen:
            errors.append(LabelPreviewIssue(row_number=number, code="duplicate_answer_event_id", field="event_id"))
            continue
        seen.add(event_id)
        normalized.append((number, event_id, verdict))
    analyses = db.execute(select(Analysis.id, Analysis.event_id).where(
        Analysis.source_system == source_system, Analysis.event_id.in_([row[1] for row in normalized]),
    )).mappings()
    by_event = {row["event_id"]: row["id"] for row in analyses}
    labels = latest_labels()
    current = {row["analysis_id"]: row for row in db.execute(select(labels).where(labels.c.analysis_id.in_(list(by_event.values())))).mappings()}
    rows = []
    for number, event_id, verdict in normalized:
        analysis_id = by_event.get(event_id)
        if analysis_id is None:
            errors.append(LabelPreviewIssue(row_number=number, code="analysis_not_found_in_source", field="event_id"))
            continue
        label = current.get(analysis_id)
        rows.append(LabelPreviewRow(
            row_number=number, event_id=event_id, analysis_id=analysis_id,
            current_revision=label["revision"] if label is not None else 0,
            current_label=label["verdict"] if label is not None else None, proposed_label=verdict,
            change=not _same_label(label, verdict, source_kind, source_ref, ai_visible),
        ))
    can_confirm = not errors and len(rows) == len(answers)
    expires_at, token = None, None
    if can_confirm:
        expiry = int(time.time()) + PREVIEW_TTL_SECONDS
        expires_at = datetime.fromtimestamp(expiry, UTC)
        document = {
            "v": 1, "attachment_id": str(uuid.uuid4()), "actor": actor, "expires": expiry,
            "source_system": source_system, "source_kind": source_kind, "source_ref": source_ref,
            "ai_visible": ai_visible, "rows": [row.model_dump() for row in rows],
        }
        token = URLSafeTimedSerializer(secret, salt=TOKEN_SALT).dumps(document)
        if len(token) > MAX_PREVIEW_TOKEN_CHARS:
            raise LabelAttachmentError("label_preview_too_large", 413)
    return LabelPreviewResponse(
        preview_token=token, expires_at=expires_at, can_confirm=can_confirm, total_rows=len(answers),
        matched_count=len(rows), unchanged_count=sum(not row.change for row in rows),
        change_count=sum(row.change for row in rows), source_system=source_system, source_kind=source_kind,
        source_ref=source_ref, ai_visible=ai_visible, rows=rows, errors=sorted(errors, key=lambda issue: issue.row_number),
    )


def confirm_labels(db: Session, *, token: str, actor: str, secret: str) -> LabelConfirmResponse:
    if len(token) > MAX_PREVIEW_TOKEN_CHARS:
        raise LabelAttachmentError("invalid_label_preview", 422)
    try:
        document = URLSafeTimedSerializer(secret, salt=TOKEN_SALT).loads(token)
    except (BadData, ValueError, RecursionError):
        raise LabelAttachmentError("invalid_label_preview", 422) from None
    if not isinstance(document, dict) or document.get("v") != 1 or document.get("actor") != actor:
        raise LabelAttachmentError("invalid_label_preview", 422)
    rows = document["rows"]
    digest = hashlib.sha256(token.encode()).hexdigest()
    changed = [row for row in rows if row["change"]]
    attachment_id = document["attachment_id"]
    # End a prior read snapshot before taking the SQLite write lock. The route
    # owns this session and has performed no writes before confirmation.
    db.rollback()
    try:
        if db.get_bind().dialect.name == "sqlite":
            db.execute(text("BEGIN IMMEDIATE"))
        else:
            db.execute(select(Analysis.id).where(Analysis.id.in_([row["analysis_id"] for row in rows])).order_by(Analysis.id).with_for_update())
        existing = list(db.scalars(select(AnalysisLabel).where(AnalysisLabel.attachment_id == attachment_id)))
        if existing:
            if len(existing) != len(changed) or any(row.token_digest != digest for row in existing):
                raise LabelAttachmentError("label_attachment_conflict", 409)
            db.rollback()
            return LabelConfirmResponse(attachment_id=attachment_id, applied_count=len(changed), unchanged_count=len(rows)-len(changed), duplicate=True)
        if time.time() > document["expires"]:
            raise LabelAttachmentError("label_preview_expired", 409)
        labels = latest_labels()
        current = {row["analysis_id"]: row for row in db.execute(select(labels).where(
            labels.c.analysis_id.in_([row["analysis_id"] for row in rows]),
        )).mappings()}
        analyses = {row["id"]: row for row in db.execute(select(Analysis.id, Analysis.event_id, Analysis.source_system).where(
            Analysis.id.in_([row["analysis_id"] for row in rows]),
        )).mappings()}
        for row in rows:
            analysis = analyses.get(row["analysis_id"])
            label = current.get(row["analysis_id"])
            if (analysis is None or analysis["source_system"] != document["source_system"]
                    or analysis["event_id"] != row["event_id"]
                    or (label["revision"] if label is not None else 0) != row["current_revision"]):
                raise LabelAttachmentError("label_preview_stale", 409)
        for row in changed:
            db.add(AnalysisLabel(
                analysis_id=row["analysis_id"], revision=row["current_revision"] + 1,
                verdict=row["proposed_label"], source_kind=document["source_kind"], source_ref=document["source_ref"],
                ai_visible=document["ai_visible"], created_by=actor, attachment_id=attachment_id, token_digest=digest,
            ))
        if changed:
            db.add(AccessAudit(
                actor_kind="admin_session", actor_id=actor, action="attach_evaluation_labels",
                resource_type="evaluation_label_attachment", resource_id=attachment_id,
            ))
            db.commit()
        else:
            db.rollback()
        return LabelConfirmResponse(attachment_id=attachment_id, applied_count=len(changed), unchanged_count=len(rows)-len(changed), duplicate=False)
    except IntegrityError:
        db.rollback()
        raise LabelAttachmentError("label_preview_stale", 409) from None
    except OperationalError:
        db.rollback()
        raise LabelAttachmentError("label_confirmation_busy", 503) from None
    except Exception:
        db.rollback()
        raise
