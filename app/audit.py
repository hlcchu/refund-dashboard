"""Append-only, hash-chained audit trail.

Each event stores sha256(prev_hash + canonical(event)). Any edit, deletion or reordering
of past rows breaks the chain and is reported by `verify_chain`. The database also
blocks UPDATE/DELETE (SQLite triggers in dev, Azure SQL append-only ledger table in prod).
"""

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import Principal
from app.models import AuditEvent

GENESIS_HASH = "0" * 64


def canonical_json(data: Any) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def compute_hash(
    *,
    prev_hash: str,
    seq: int,
    occurred_at: str,
    actor_oid: str,
    actor_name: str,
    actor_roles: str,
    action: str,
    entity_type: str,
    entity_id: str,
    payload: str,
) -> str:
    material = canonical_json(
        {
            "seq": seq,
            "occurred_at": occurred_at,
            "actor_oid": actor_oid,
            "actor_name": actor_name,
            "actor_roles": actor_roles,
            "action": action,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "payload": payload,
        }
    )
    return hashlib.sha256((prev_hash + material).encode("utf-8")).hexdigest()


def append_event(
    session: Session,
    *,
    actor: Principal,
    action: str,
    entity_type: str,
    entity_id: str,
    payload: dict[str, Any],
) -> AuditEvent:
    """Add an event in the caller's transaction, so audit and state change commit together."""
    last = session.execute(
        select(AuditEvent).order_by(AuditEvent.seq.desc()).limit(1).with_for_update()
    ).scalar_one_or_none()
    seq = 1 if last is None else last.seq + 1
    prev_hash = GENESIS_HASH if last is None else last.hash
    occurred_at = datetime.now(timezone.utc).isoformat(timespec="microseconds")
    payload_json = canonical_json(payload)
    actor_roles = ",".join(sorted(actor.roles))
    event = AuditEvent(
        seq=seq,
        occurred_at=occurred_at,
        actor_oid=actor.oid,
        actor_name=actor.name,
        actor_roles=actor_roles,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        payload=payload_json,
        prev_hash=prev_hash,
        hash=compute_hash(
            prev_hash=prev_hash,
            seq=seq,
            occurred_at=occurred_at,
            actor_oid=actor.oid,
            actor_name=actor.name,
            actor_roles=actor_roles,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            payload=payload_json,
        ),
    )
    session.add(event)
    session.flush()
    return event


@dataclass(frozen=True)
class ChainVerification:
    valid: bool
    events_checked: int
    head_hash: str
    first_invalid_seq: int | None = None
    error: str | None = None


def verify_chain(session: Session) -> ChainVerification:
    prev_hash = GENESIS_HASH
    expected_seq = 1
    count = 0
    for ev in session.scalars(select(AuditEvent).order_by(AuditEvent.seq)).yield_per(500):
        count += 1
        if ev.seq != expected_seq:
            return ChainVerification(False, count, prev_hash, ev.seq, "sequence gap")
        if ev.prev_hash != prev_hash:
            return ChainVerification(False, count, prev_hash, ev.seq, "prev_hash mismatch")
        recomputed = compute_hash(
            prev_hash=ev.prev_hash,
            seq=ev.seq,
            occurred_at=ev.occurred_at,
            actor_oid=ev.actor_oid,
            actor_name=ev.actor_name,
            actor_roles=ev.actor_roles,
            action=ev.action,
            entity_type=ev.entity_type,
            entity_id=ev.entity_id,
            payload=ev.payload,
        )
        if recomputed != ev.hash:
            return ChainVerification(False, count, prev_hash, ev.seq, "hash mismatch")
        prev_hash = ev.hash
        expected_seq += 1
    return ChainVerification(True, count, prev_hash)
