"""Explicit, allowlisted change summaries; never serialize arbitrary models."""
from ..models import ChangeEvent


def metadata(row, fields):
    """Only deliberate non-secret scalar fields are eligible for change history."""
    return {name: getattr(row, name) for name in fields} if row else None


def profile_metadata(row):
    return metadata(row, ("name", "provider", "model_name", "status", "is_test", "context_window",
        "max_output_tokens", "timeout_seconds", "test_concurrency", "tls_verify", "external_data_approved"))


def key_metadata(row):
    value = metadata(row, ("name", "purpose"))
    if value is not None:
        value["revoked"] = row.revoked_at is not None
        value["deleted"] = row.deleted_at is not None
    return value


def target_metadata(row):
    return metadata(row, ("ip_address", "port", "revision"))


def record_change(db, *, category, actor, action, resource_type, resource_id, before=None, after=None):
    event = ChangeEvent(category=category, actor=actor, action=action,
        resource_type=resource_type, resource_id=str(resource_id), before_json=before, after_json=after)
    db.add(event)
    return event
