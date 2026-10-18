from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit
from app.auth import ROLE_FINANCE, Principal, require_any_role
from app.deps import get_session
from app.models import AuditEvent
from app.schemas import AuditEventOut, ChainVerificationOut

router = APIRouter(prefix="/api/audit", tags=["audit"])

auditor = require_any_role(ROLE_FINANCE)


@router.get("/events", response_model=list[AuditEventOut])
def list_events(
    entity_id: str | None = None,
    after_seq: int = Query(default=0, ge=0),
    limit: int = Query(default=200, ge=1, le=1000),
    _actor: Principal = Depends(auditor),
    session: Session = Depends(get_session),
) -> list[AuditEventOut]:
    stmt = select(AuditEvent).where(AuditEvent.seq > after_seq).order_by(AuditEvent.seq)
    if entity_id:
        stmt = stmt.where(AuditEvent.entity_id == entity_id)
    return [AuditEventOut.of(e) for e in session.scalars(stmt.limit(limit))]


@router.get("/verify", response_model=ChainVerificationOut)
def verify(
    _actor: Principal = Depends(auditor),
    session: Session = Depends(get_session),
) -> ChainVerificationOut:
    result = audit.verify_chain(session)
    return ChainVerificationOut(
        valid=result.valid,
        events_checked=result.events_checked,
        head_hash=result.head_hash,
        first_invalid_seq=result.first_invalid_seq,
        error=result.error,
    )
