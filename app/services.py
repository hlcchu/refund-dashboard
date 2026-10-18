"""Refund workflow with maker-checker (four-eyes) controls.

Rules enforced here, independent of the UI:
  * Only the maker can raise or cancel a request; a request is never edited after submission.
  * A checker can never approve or reject a request they raised (even if they hold both roles).
  * Requests above the dual-approval threshold need two distinct checkers.
  * Admins tune limits but can't raise/decide refunds; an admin who also holds Maker or
    Checker is refused, so nobody can loosen the rules for their own refunds.
  * Every state change writes an audit event in the same DB transaction.
"""

from collections import defaultdict
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from app import audit
from app.auth import ROLE_CHECKER, ROLE_FINANCE, ROLE_MAKER, Principal
from app.config import Settings
from app.models import Approval, AppSetting, AuditEvent, RefundRequest, RefundStatus
from app.schemas import RefundCreate


class DomainError(Exception):
    status_code = 400

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class NotFoundError(DomainError):
    status_code = 404


class ForbiddenError(DomainError):
    status_code = 403


class ConflictError(DomainError):
    status_code = 409


class InvalidRequestError(DomainError):
    status_code = 422


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _to_minor(amount: Decimal) -> int:
    return int((amount * 100).to_integral_value())


def _snapshot(r: RefundRequest) -> dict[str, Any]:
    return {
        "customer_id": r.customer_id,
        "order_ref": r.order_ref,
        "amount_minor": r.amount_minor,
        "currency": r.currency,
        "status": r.status,
        "required_approvals": r.required_approvals,
        "approvals": len(r.approvals),
        "maker_oid": r.maker_oid,
        "version": r.version,
    }


@contextmanager
def _unit_of_work(session: Session) -> Iterator[None]:
    try:
        yield
        session.commit()
    except StaleDataError as exc:
        session.rollback()
        raise ConflictError("Refund was modified concurrently; reload and retry") from exc
    except IntegrityError as exc:
        session.rollback()
        raise ConflictError("Conflicting write; reload and retry") from exc
    except Exception:
        session.rollback()
        raise


def can_view_all(actor: Principal) -> bool:
    return actor.has(ROLE_CHECKER) or actor.has(ROLE_FINANCE)


def _load_visible(session: Session, actor: Principal, refund_id: str) -> RefundRequest:
    refund = session.get(RefundRequest, refund_id)
    if refund is None or (not can_view_all(actor) and refund.maker_oid != actor.oid):
        raise NotFoundError("Refund not found")
    return refund


def _check_version(refund: RefundRequest, expected_version: int | None) -> None:
    if expected_version is not None and expected_version != refund.version:
        raise ConflictError(
            f"Refund is at version {refund.version}, not {expected_version}; reload and retry"
        )


def _deny_self_check(session: Session, actor: Principal, refund: RefundRequest, op: str) -> None:
    if refund.maker_oid != actor.oid:
        return
    audit.append_event(
        session,
        actor=actor,
        action=f"refund.{op}.denied",
        entity_type="refund",
        entity_id=refund.id,
        payload={"reason": "segregation_of_duties", "detail": "maker cannot act as checker"},
    )
    session.commit()
    raise ForbiddenError(f"Segregation of duties: you cannot {op} a refund you raised")


def create_refund(
    session: Session,
    settings: Settings,
    actor: Principal,
    data: RefundCreate,
    idempotency_key: str | None,
) -> tuple[RefundRequest, bool]:
    amount_minor = _to_minor(data.amount)
    if idempotency_key:
        existing = session.scalar(
            select(RefundRequest).where(
                RefundRequest.maker_oid == actor.oid,
                RefundRequest.idempotency_key == idempotency_key,
            )
        )
        if existing is not None:
            same = (
                existing.customer_id == data.customer_id
                and existing.order_ref == data.order_ref
                and existing.amount_minor == amount_minor
                and existing.currency == data.currency
            )
            if not same:
                raise ConflictError("Idempotency-Key was already used for a different refund")
            return existing, False

    limits = get_limits(session, settings)
    if data.amount > limits.max_refund_amount:
        raise InvalidRequestError(
            f"Amount exceeds the maximum of {limits.max_refund_amount} per refund"
        )

    now = _now()
    with _unit_of_work(session):
        refund = RefundRequest(
            customer_id=data.customer_id,
            order_ref=data.order_ref,
            amount_minor=amount_minor,
            currency=data.currency,
            reason=data.reason,
            status=RefundStatus.PENDING.value,
            required_approvals=2 if data.amount > limits.dual_approval_threshold else 1,
            maker_oid=actor.oid,
            maker_name=actor.name,
            idempotency_key=idempotency_key,
            created_at=now,
            updated_at=now,
        )
        session.add(refund)
        session.flush()
        audit.append_event(
            session,
            actor=actor,
            action="refund.created",
            entity_type="refund",
            entity_id=refund.id,
            payload={**_snapshot(refund), "reason": refund.reason},
        )
    return refund, True


def list_refunds(
    session: Session, actor: Principal, status: RefundStatus | None, limit: int
) -> list[RefundRequest]:
    stmt = select(RefundRequest).order_by(RefundRequest.created_at.desc()).limit(limit)
    if status is not None:
        stmt = stmt.where(RefundRequest.status == status.value)
    if not can_view_all(actor):
        stmt = stmt.where(RefundRequest.maker_oid == actor.oid)
    return list(session.scalars(stmt))


def get_refund(session: Session, actor: Principal, refund_id: str) -> RefundRequest:
    return _load_visible(session, actor, refund_id)


def approve_refund(
    session: Session,
    actor: Principal,
    refund_id: str,
    comment: str | None,
    expected_version: int | None,
) -> RefundRequest:
    refund = _load_visible(session, actor, refund_id)
    _deny_self_check(session, actor, refund, "approve")
    with _unit_of_work(session):
        _check_version(refund, expected_version)
        if refund.status != RefundStatus.PENDING.value:
            raise ConflictError(f"Refund is {refund.status}, not PENDING")
        if any(a.checker_oid == actor.oid for a in refund.approvals):
            raise ConflictError("You already approved this refund; a different checker is needed")

        now = _now()
        refund.approvals.append(
            Approval(
                checker_oid=actor.oid,
                checker_name=actor.name,
                comment=comment,
                created_at=now,
            )
        )
        refund.updated_at = now
        final = len(refund.approvals) >= refund.required_approvals
        if final:
            refund.status = RefundStatus.APPROVED.value
            refund.decided_at = now
        session.flush()
        audit.append_event(
            session,
            actor=actor,
            action="refund.approved" if final else "refund.approval_recorded",
            entity_type="refund",
            entity_id=refund.id,
            payload={**_snapshot(refund), "comment": comment},
        )
    return refund


def reject_refund(
    session: Session,
    actor: Principal,
    refund_id: str,
    reason: str,
    expected_version: int | None,
) -> RefundRequest:
    refund = _load_visible(session, actor, refund_id)
    _deny_self_check(session, actor, refund, "reject")
    with _unit_of_work(session):
        _check_version(refund, expected_version)
        if refund.status != RefundStatus.PENDING.value:
            raise ConflictError(f"Refund is {refund.status}, not PENDING")
        now = _now()
        refund.status = RefundStatus.REJECTED.value
        refund.rejection_reason = reason
        refund.updated_at = now
        refund.decided_at = now
        session.flush()
        audit.append_event(
            session,
            actor=actor,
            action="refund.rejected",
            entity_type="refund",
            entity_id=refund.id,
            payload={**_snapshot(refund), "rejection_reason": reason},
        )
    return refund


def cancel_refund(session: Session, actor: Principal, refund_id: str) -> RefundRequest:
    refund = _load_visible(session, actor, refund_id)
    if refund.maker_oid != actor.oid:
        raise ForbiddenError("Only the maker who raised a refund can cancel it")
    with _unit_of_work(session):
        if refund.status != RefundStatus.PENDING.value:
            raise ConflictError(f"Refund is {refund.status}, not PENDING")
        now = _now()
        refund.status = RefundStatus.CANCELLED.value
        refund.updated_at = now
        refund.decided_at = now
        session.flush()
        audit.append_event(
            session,
            actor=actor,
            action="refund.cancelled",
            entity_type="refund",
            entity_id=refund.id,
            payload=_snapshot(refund),
        )
    return refund


@dataclass(frozen=True)
class Limits:
    dual_approval_threshold: Decimal
    max_refund_amount: Decimal
    updated_at: datetime | None = None
    updated_by: str | None = None


_LIMIT_KEYS = ("dual_approval_threshold", "max_refund_amount")


def get_limits(session: Session, settings: Settings) -> Limits:
    rows = {
        r.key: r for r in session.scalars(select(AppSetting).where(AppSetting.key.in_(_LIMIT_KEYS)))
    }
    # SQLite drops tzinfo on read; all timestamps are stored as UTC.
    latest = max(rows.values(), key=lambda r: r.updated_at.replace(tzinfo=None), default=None)

    def value(key: str) -> Decimal:
        return Decimal(rows[key].value) if key in rows else getattr(settings, key)

    return Limits(
        dual_approval_threshold=value("dual_approval_threshold"),
        max_refund_amount=value("max_refund_amount"),
        updated_at=latest.updated_at if latest else None,
        updated_by=latest.updated_by_name if latest else None,
    )


def update_limits(
    session: Session,
    settings: Settings,
    actor: Principal,
    dual_approval_threshold: Decimal,
    max_refund_amount: Decimal,
) -> Limits:
    if actor.has(ROLE_MAKER) or actor.has(ROLE_CHECKER):
        audit.append_event(
            session,
            actor=actor,
            action="settings.update.denied",
            entity_type="settings",
            entity_id="limits",
            payload={"reason": "segregation_of_duties", "detail": "admin also holds maker/checker"},
        )
        session.commit()
        raise ForbiddenError(
            "Segregation of duties: admins who also hold Maker or Checker can't change limits"
        )
    if dual_approval_threshold >= max_refund_amount:
        raise InvalidRequestError("dual_approval_threshold must be below max_refund_amount")

    before = get_limits(session, settings)
    now = _now()
    new_values = {
        "dual_approval_threshold": dual_approval_threshold,
        "max_refund_amount": max_refund_amount,
    }
    with _unit_of_work(session):
        for key, val in new_values.items():
            row = session.get(AppSetting, key)
            if row is None:
                row = AppSetting(key=key)
                session.add(row)
            row.value = f"{val:.2f}"
            row.updated_at = now
            row.updated_by_oid = actor.oid
            row.updated_by_name = actor.name
        session.flush()
        audit.append_event(
            session,
            actor=actor,
            action="settings.updated",
            entity_type="settings",
            entity_id="limits",
            payload={
                "before": {k: f"{getattr(before, k):.2f}" for k in _LIMIT_KEYS},
                "after": {k: f"{v:.2f}" for k, v in new_values.items()},
            },
        )
    return get_limits(session, settings)


def known_users(session: Session) -> list[dict[str, Any]]:
    """Users seen in the audit trail with the roles their token carried on their latest action.

    Authoritative role assignments live in Entra ID (enterprise app > Users and groups).
    """
    latest: dict[str, AuditEvent] = {}
    for ev in session.scalars(select(AuditEvent).order_by(AuditEvent.seq)):
        latest[ev.actor_oid] = ev
    return [
        {
            "oid": ev.actor_oid,
            "name": ev.actor_name,
            "roles": [r for r in ev.actor_roles.split(",") if r],
            "last_seen": ev.occurred_at,
            "last_action": ev.action,
        }
        for ev in sorted(latest.values(), key=lambda e: e.actor_name)
    ]


def summarize(session: Session, actor: Principal) -> dict[str, Any]:
    stmt = select(RefundRequest.status, RefundRequest.currency, RefundRequest.amount_minor)
    if not can_view_all(actor):
        stmt = stmt.where(RefundRequest.maker_oid == actor.oid)
    counts: dict[str, int] = defaultdict(int)
    totals: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for status, currency, amount_minor in session.execute(stmt):
        counts[status] += 1
        totals[status][currency] += amount_minor
    return {
        s.value: {
            "count": counts.get(s.value, 0),
            "totals": {c: f"{Decimal(v) / 100:.2f}" for c, v in totals.get(s.value, {}).items()},
        }
        for s in RefundStatus
    }


def refund_history(session: Session, actor: Principal, refund_id: str) -> list[AuditEvent]:
    _load_visible(session, actor, refund_id)
    return list(
        session.scalars(
            select(AuditEvent).where(AuditEvent.entity_id == refund_id).order_by(AuditEvent.seq)
        )
    )
