"""Seed pre-V5 states for worker/recovery tests, not an API or promotion bypass.

Tests of the release gate must use the real endpoint (test_production_promotion).
These helpers deliberately perform no evaluation, audit or model invocation.
"""
from sqlalchemy import select
from app.models import AgentConfiguration, VLLMProfile
from app.services.input_schemas import activate_schema_version, get_schema_state


def pin_legacy_v214(monkeypatch):
    """Exercise immutable old wire/check/editor contracts, not newest defaults."""
    from app.agent import legacy_prompts_v214, result_editor
    from app.services import prompt_snapshots, evidence_editor
    original = prompt_snapshots._build_snapshot
    def build_snapshot(version, crypto, *, origin="enqueue", rules=None):
        return original(version, crypto, origin=origin, rules=rules or legacy_prompts_v214)
    monkeypatch.setattr(prompt_snapshots, "_build_snapshot", build_snapshot)
    monkeypatch.setattr(evidence_editor, "VERSION", result_editor.VERSION)
    monkeypatch.setattr(evidence_editor, "INSTRUCTIONS", result_editor.INSTRUCTIONS)


def seed_production(client, profile):
    identifier = profile["id"] if isinstance(profile, dict) else profile
    with client.app.state.session_factory() as db:
        for current in db.scalars(select(VLLMProfile).where(VLLMProfile.status == "production")):
            current.status = "verified"
        db.flush()
        if identifier:
            db.get(VLLMProfile, identifier).status = "production"
        db.commit()


def seed_production_roles(client, primary, verifier=None, *, editor=None, editor_enabled=False):
    seed_production(client, primary)
    with client.app.state.session_factory() as db:
        config = db.get(AgentConfiguration, 1)
        if config is None:
            config = AgentConfiguration(id=1, revision=0)
            db.add(config)
        config.production_verifier_profile_id = verifier
        config.production_evidence_editor_enabled = editor_enabled
        config.production_evidence_editor_profile_id = editor
        config.revision += 1
        db.commit()


def seed_schema(client, identifier):
    with client.app.state.session_factory() as db:
        state = get_schema_state(db, client.app.state.crypto)
        activate_schema_version(db, client.app.state.crypto, identifier, state.revision, "legacy-fixture")
        db.commit()
