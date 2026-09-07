"""Local vLLM egress permissions. No DNS, network I/O, or environment fallback."""
import ipaddress
from urllib.parse import urlsplit

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from ..internal_egress_schemas import InternalEgressCreate, InternalEgressProfile, InternalEgressResponse, InternalEgressUpdate
from ..models import InternalEgressTarget, ModelProfileStatus, VLLMProfile, utcnow


PRIVATE_NETWORKS = tuple(ipaddress.ip_network(value) for value in (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fc00::/7",
))


class InternalEgressError(ValueError):
    def __init__(self, code: str, status_code: int = 409):
        self.code = code
        self.status_code = status_code
        super().__init__(code)


def normalize_internal_ip(value: str) -> str:
    # is_private also includes other special ranges; only RFC1918/ULA qualify.
    try:
        if not isinstance(value, str) or value != value.strip() or "%" in value:
            raise ValueError
        address = ipaddress.ip_address(value)
    except ValueError:
        raise InternalEgressError("internal_egress_ip_must_be_private", 422) from None
    if not any(address.version == network.version and address in network for network in PRIVATE_NETWORKS):
        raise InternalEgressError("internal_egress_ip_must_be_private", 422)
    return address.compressed


def lock_egress_mutation(db: Session) -> None:
    """Serialize egress/profile mutations in SQLite; callers own the transaction.

    Call before reading mutable policy or profiles. A no-op write also takes a
    writer lock if the caller has already opened an explicit transaction.
    """
    connection = db.connection()
    if connection.dialect.name == "sqlite":
        if not connection.connection.driver_connection.in_transaction:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
        else:
            connection.exec_driver_sql("UPDATE internal_egress_targets SET revision = revision WHERE 0")


def allowed_targets_from_db(db: Session) -> str:
    targets = db.execute(select(InternalEgressTarget.ip_address, InternalEgressTarget.port)
                         .order_by(InternalEgressTarget.ip_address, InternalEgressTarget.port)).all()
    # Read fresh columns, never ORM identity-map snapshots. Revalidate persisted
    # values so malformed direct database changes fail closed before transport.
    entries = []
    for ip_address, port in targets:
        ip = normalize_internal_ip(ip_address)
        if ip != ip_address or type(port) is not int or not 1 <= port <= 65535:
            raise InternalEgressError("internal_egress_configuration_invalid", 503)
        entries.append(f"[{ip}]:{port}" if ":" in ip else f"{ip}:{port}")
    return ",".join(entries)


def in_use_profiles(db: Session, ip_address: str, port: int) -> list[InternalEgressProfile]:
    rows = db.execute(select(VLLMProfile.id, VLLMProfile.name, VLLMProfile.status, VLLMProfile.base_url)
                      .where(VLLMProfile.provider == "vllm", VLLMProfile.status != ModelProfileStatus.disabled.value)
                      .order_by(VLLMProfile.name)).all()
    matches = []
    for row in rows:
        try:
            parsed = urlsplit(row.base_url)
            target_ip = normalize_internal_ip(parsed.hostname)
            target_port = parsed.port if parsed.port is not None else {"http": 80, "https": 443}.get(parsed.scheme)
        except (ValueError, TypeError):
            continue
        if (target_ip, target_port) == (ip_address, port):
            matches.append(InternalEgressProfile(id=row.id, name=row.name, status=row.status))
    return matches


def to_egress_response(db: Session, target: InternalEgressTarget) -> InternalEgressResponse:
    return InternalEgressResponse(
        id=target.id, ip_address=target.ip_address, port=target.port,
        description=target.description, revision=target.revision,
        created_at=target.created_at, updated_at=target.updated_at,
        in_use_profiles=in_use_profiles(db, target.ip_address, target.port),
    )


def get_target(db: Session, target_id: str) -> InternalEgressTarget:
    target = db.get(InternalEgressTarget, target_id, populate_existing=True)
    if target is None:
        raise InternalEgressError("internal_egress_not_found", 404)
    return target


def create_target(db: Session, payload: InternalEgressCreate) -> InternalEgressTarget:
    ip = normalize_internal_ip(payload.ip_address)
    lock_egress_mutation(db)
    target = InternalEgressTarget(ip_address=ip, port=payload.port, description=payload.description)
    db.add(target)
    db.flush()
    return target


def update_target(db: Session, target_id: str, payload: InternalEgressUpdate) -> InternalEgressTarget:
    ip = normalize_internal_ip(payload.ip_address)
    lock_egress_mutation(db)
    target = get_target(db, target_id)
    if target.revision != payload.expected_revision:
        raise InternalEgressError("internal_egress_changed")
    target_changed = (ip, payload.port) != (target.ip_address, target.port)
    if target_changed and in_use_profiles(db, target.ip_address, target.port):
        raise InternalEgressError("internal_egress_target_in_use")
    result = db.execute(update(InternalEgressTarget)
                        .where(InternalEgressTarget.id == target_id, InternalEgressTarget.revision == payload.expected_revision)
                        .values(ip_address=ip, port=payload.port, description=payload.description,
                                revision=payload.expected_revision + 1, updated_at=utcnow())
                        .execution_options(synchronize_session=False))
    if result.rowcount != 1:
        raise InternalEgressError("internal_egress_changed")
    db.refresh(target)
    return target


def delete_target(db: Session, target_id: str, expected_revision: int) -> None:
    lock_egress_mutation(db)
    target = get_target(db, target_id)
    if target.revision != expected_revision:
        raise InternalEgressError("internal_egress_changed")
    if in_use_profiles(db, target.ip_address, target.port):
        raise InternalEgressError("internal_egress_target_in_use")
    result = db.execute(delete(InternalEgressTarget)
                        .where(InternalEgressTarget.id == target_id, InternalEgressTarget.revision == expected_revision)
                        .execution_options(synchronize_session=False))
    if result.rowcount != 1:
        raise InternalEgressError("internal_egress_changed")
