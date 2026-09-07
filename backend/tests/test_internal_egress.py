from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import select

from app.database import Base, build_engine, build_session_factory
from app.internal_egress_schemas import InternalEgressCreate, InternalEgressUpdate
from app.models import AccessAudit, InternalEgressTarget, VLLMProfile, VLLMTestRun
from app.services.internal_egress import (
    InternalEgressError, allowed_targets_from_db, create_target, normalize_internal_ip, update_target,
)


BASE = "/api/v1/admin/internal-egress"


def login(client):
    assert client.post("/api/v1/auth/login", json={"username": "admin", "password": "test-password"}).status_code == 200


def payload(ip="10.0.0.10", port=8000, description="합성 내부 추론 서버"):
    return {"ip_address": ip, "port": port, "description": description}


def add_profile(client, *, status="draft", ip="10.0.0.10", port=8000, provider="vllm"):
    host = f"[{ip}]" if ":" in ip else ip
    with client.app.state.session_factory() as db:
        profile = VLLMProfile(name="synthetic-profile", provider=provider, status=status,
                              base_url=f"http://{host}:{port}/v1", model_name="synthetic-model")
        db.add(profile)
        db.flush()
        identity = profile.id
        db.add(VLLMTestRun(profile_id=identity, mode="quick", profile_fingerprint="a" * 64))
        db.commit()
    return identity


@pytest.mark.parametrize("ip,canonical", [
    ("10.0.0.1", "10.0.0.1"), ("10.255.255.254", "10.255.255.254"),
    ("172.16.0.1", "172.16.0.1"), ("172.31.255.254", "172.31.255.254"),
    ("192.168.1.10", "192.168.1.10"), ("fc00::1", "fc00::1"),
    ("FD12:3456:0000:0000:0000:0000:0000:0001", "fd12:3456::1"),
])
def test_only_rfc1918_or_ula_literals_are_normalized(ip, canonical):
    assert normalize_internal_ip(ip) == canonical


@pytest.mark.parametrize("ip", [
    "8.8.8.8", "192.0.2.1", "198.51.100.1", "203.0.113.1", "100.64.0.1", "172.15.0.1", "172.32.0.1",
    "127.0.0.1", "169.254.169.254", "0.0.0.0", "224.0.0.1", "255.255.255.255", "::", "::1",
    "fe80::1", "ff00::1", "2001:db8::1", "::ffff:10.0.0.1", "fd00::1%eth0", "[fd00::1]",
    "vllm.internal", "10.0.0.0/8", "10.1", "010.000.000.001", "0x0a000001", "167772161",
    "10.0.0.1 ", "\t10.0.0.1", "10.0.0.1\n", "http://10.0.0.1", "10.0.0.1:8000",
])
def test_non_private_or_ambiguous_targets_are_rejected_without_dns(client, monkeypatch, ip):
    monkeypatch.setattr("socket.getaddrinfo", lambda *args, **kwargs: pytest.fail("DNS must not run"))
    login(client)
    response = client.post(BASE, json=payload(ip))
    assert response.status_code == 422
    assert response.json() == {"detail": "internal_egress_ip_must_be_private"}
    assert client.get(BASE).json() == []


def test_admin_session_required_and_initial_list_empty_despite_environment(client, service_headers, monkeypatch):
    monkeypatch.setenv("WAF_VLLM_ALLOWED_TARGETS", "10.0.0.10:8000")
    for method, path, data in [
        ("get", BASE, None), ("post", BASE, payload()),
        ("put", BASE + "/synthetic", {**payload(), "expected_revision": 1}),
        ("delete", BASE + "/synthetic?expected_revision=1", None),
    ]:
        kwargs = {"json": data} if data is not None else {}
        assert getattr(client, method)(path, **kwargs).status_code == 401
        assert getattr(client, method)(path, headers=service_headers, **kwargs).status_code == 403
    login(client)
    assert client.get(BASE).json() == []
    with client.app.state.session_factory() as db:
        assert allowed_targets_from_db(db) == ""


def test_multiple_targets_crud_and_safe_audit(client):
    login(client)
    first = client.post(BASE, json=payload())
    second = client.post(BASE, json=payload("FD00:0000:0000:0000:0000:0000:0000:0001", 443, "  합성 IPv6  "))
    assert (first.status_code, second.status_code) == (201, 201)
    first, second = first.json(), second.json()
    assert first["revision"] == 1
    assert first["in_use_profiles"] == []
    assert first["created_at"].endswith("Z")
    assert second["ip_address"] == "fd00::1"
    assert second["description"] == "합성 IPv6"
    with client.app.state.session_factory() as db:
        assert allowed_targets_from_db(db) == "10.0.0.10:8000,[fd00::1]:443"
    updated = client.put(BASE + "/" + first["id"], json={**payload("10.0.0.11", 9000, "새 합성 대상"), "expected_revision": 1})
    assert updated.status_code == 200
    assert updated.json()["revision"] == 2
    assert updated.json()["created_at"] == first["created_at"]
    deleted = client.delete(BASE + "/" + first["id"] + "?expected_revision=2")
    assert deleted.status_code == 204 and deleted.content == b""
    assert len(client.get(BASE).json()) == 1
    with client.app.state.session_factory() as db:
        audits = db.scalars(select(AccessAudit).where(AccessAudit.resource_type == "internal_egress_target").order_by(AccessAudit.created_at)).all()
        assert [row.action for row in audits] == ["create_internal_egress", "create_internal_egress", "update_internal_egress", "delete_internal_egress"]
        assert all(row.actor_kind == "admin_session" and row.actor_id == "admin" for row in audits)
        assert all(row.resource_id in {first["id"], second["id"]} for row in audits)


def test_canonical_duplicates_and_update_collision_preserve_rows(client):
    login(client)
    first = client.post(BASE, json=payload("fd00::1")).json()
    duplicate = client.post(BASE, json=payload("FD00:0:0:0:0:0:0:1"))
    assert duplicate.status_code == 409
    assert duplicate.json() == {"detail": "internal_egress_target_exists"}
    second = client.post(BASE, json=payload("10.0.0.10")).json()
    update = client.put(BASE + "/" + second["id"], json={**payload("fd00::1"), "expected_revision": 1})
    assert update.status_code == 409
    assert update.json() == {"detail": "internal_egress_target_exists"}
    assert {row["id"]: row for row in client.get(BASE).json()} == {first["id"]: first, second["id"]: second}


@pytest.mark.parametrize("changes", [{"port": 0}, {"port": 65536}, {"port": True}, {"port": "8000"},
                                      {"description": "x" * 501}, {"description": "x\ny"},
                                      {"description": "\u202eunsafe"}, {"ip_address": None}, {"extra": "ignored?"}])
def test_schema_limits_and_no_unknown_fields(client, changes):
    login(client)
    assert client.post(BASE, json={**payload(), **changes}).status_code == 422
    assert client.get(BASE).json() == []


def test_stale_or_missing_revision_and_unknown_id(client):
    login(client)
    created = client.post(BASE, json=payload()).json()
    path = BASE + "/" + created["id"]
    assert client.put(path, json=payload()).status_code == 422
    assert client.delete(path).status_code == 422
    assert client.put(path, json={**payload(), "expected_revision": True}).status_code == 422
    assert client.delete(path + "?expected_revision=0").status_code == 422
    assert client.put(path, json={**payload(description="new"), "expected_revision": 1}).status_code == 200
    for response in [client.put(path, json={**payload(), "expected_revision": 1}), client.delete(path + "?expected_revision=1")]:
        assert response.status_code == 409
        assert response.json() == {"detail": "internal_egress_changed"}
    for response in [client.put(BASE + "/missing", json={**payload(), "expected_revision": 1}), client.delete(BASE + "/missing?expected_revision=1")]:
        assert response.status_code == 404
        assert response.json() == {"detail": "internal_egress_not_found"}


@pytest.mark.parametrize("status", ["draft", "verified", "production"])
@pytest.mark.parametrize("ip", ["10.0.0.10", "fd00::1"])
def test_live_profile_blocks_target_change_and_delete_but_description_allowed(client, status, ip):
    login(client)
    target = client.post(BASE, json=payload(ip)).json()
    profile_id = add_profile(client, status=status, ip=ip)
    current = client.get(BASE).json()[0]
    assert current["in_use_profiles"] == [{"id": profile_id, "name": "synthetic-profile", "status": status}]
    path = BASE + "/" + target["id"]
    for response in [
        client.put(path, json={**payload("10.0.0.12"), "expected_revision": 1}),
        client.put(path, json={**payload(ip, 8001), "expected_revision": 1}),
        client.delete(path + "?expected_revision=1"),
    ]:
        assert response.status_code == 409
        assert response.json() == {"detail": "internal_egress_target_in_use"}
    assert client.get(BASE).json()[0]["revision"] == 1
    changed = client.put(path, json={**payload(ip, description="설명만 변경"), "expected_revision": 1})
    assert changed.status_code == 200
    assert changed.json()["revision"] == 2
    with client.app.state.session_factory() as db:
        assert db.get(VLLMProfile, profile_id).status == status
        assert len(db.scalars(select(VLLMTestRun)).all()) == 1


@pytest.mark.parametrize("status,provider,port", [("disabled", "vllm", 8000), ("draft", "openai", 8000), ("draft", "vllm", 8001)])
def test_disabled_unrelated_provider_and_other_port_do_not_block(client, status, provider, port):
    login(client)
    target = client.post(BASE, json=payload()).json()
    profile_id = add_profile(client, status=status, provider=provider, port=port)
    assert client.get(BASE).json()[0]["in_use_profiles"] == []
    assert client.delete(BASE + "/" + target["id"] + "?expected_revision=1").status_code == 204
    with client.app.state.session_factory() as db:
        assert db.get(VLLMProfile, profile_id) is not None
        assert len(db.scalars(select(VLLMTestRun)).all()) == 1


def test_helpers_never_commit_and_reads_notice_database_changes(client):
    with client.app.state.session_factory() as db:
        create_target(db, InternalEgressCreate(**payload()))
        assert allowed_targets_from_db(db) == "10.0.0.10:8000"
        db.rollback()
        assert allowed_targets_from_db(db) == ""


def test_concurrent_target_edits_do_not_overwrite_each_other(tmp_path):
    engine = build_engine(f"sqlite+pysqlite:///{tmp_path / 'egress-concurrent.db'}")
    Base.metadata.create_all(engine)
    factory = build_session_factory(engine)
    with factory() as db:
        target = create_target(db, InternalEgressCreate(**payload()))
        target_id = target.id
        db.commit()
    barrier = Barrier(2)

    def writer(description):
        with factory() as db:
            barrier.wait(timeout=5)
            try:
                update_target(db, target_id, InternalEgressUpdate(**payload(description=description), expected_revision=1))
                db.commit()
                return "updated"
            except InternalEgressError as exc:
                db.rollback()
                return exc.code

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            assert sorted(pool.map(writer, ["first", "second"])) == ["internal_egress_changed", "updated"]
        with factory() as db:
            assert db.get(InternalEgressTarget, target_id).revision == 2
    finally:
        engine.dispose()
