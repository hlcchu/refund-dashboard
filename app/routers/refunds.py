import csv
import io

from fastapi import APIRouter, Depends, Header, Query, Response, status
from sqlalchemy.orm import Session

from app import services
from app.auth import ROLE_CHECKER, ROLE_FINANCE, ROLE_MAKER, Principal, require_any_role
from app.config import Settings
from app.deps import get_app_settings, get_session
from app.models import RefundStatus
from app.schemas import (
    ApproveBody,
    AuditEventOut,
    PublicLimits,
    RefundCreate,
    RefundOut,
    RejectBody,
    StatusSummary,
)

router = APIRouter(prefix="/api/refunds", tags=["refunds"])

maker = require_any_role(ROLE_MAKER)
checker = require_any_role(ROLE_CHECKER)
finance = require_any_role(ROLE_FINANCE)
reader = require_any_role(ROLE_MAKER, ROLE_CHECKER, ROLE_FINANCE)

EXPORT_COLUMNS = (
    "id",
    "created_at",
    "customer_id",
    "order_ref",
    "amount",
    "currency",
    "status",
    "reason",
    "maker_name",
    "approvers",
    "rejection_reason",
    "decided_at",
)


def _csv_safe(value: object) -> str:
    text = "" if value is None else str(value)
    # Neutralise spreadsheet formula injection.
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


@router.post("", response_model=RefundOut, status_code=status.HTTP_201_CREATED)
def create_refund(
    body: RefundCreate,
    response: Response,
    idempotency_key: str | None = Header(default=None, max_length=128),
    actor: Principal = Depends(maker),
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_app_settings),
) -> RefundOut:
    refund, created = services.create_refund(session, settings, actor, body, idempotency_key)
    if not created:
        response.status_code = status.HTTP_200_OK
    return RefundOut.of(refund)


@router.get("", response_model=list[RefundOut])
def list_refunds(
    status_filter: RefundStatus | None = Query(default=None, alias="status"),
    limit: int = Query(default=100, ge=1, le=500),
    actor: Principal = Depends(reader),
    session: Session = Depends(get_session),
) -> list[RefundOut]:
    return [RefundOut.of(r) for r in services.list_refunds(session, actor, status_filter, limit)]


@router.get("/limits", response_model=PublicLimits)
def limits(
    _actor: Principal = Depends(reader),
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_app_settings),
) -> PublicLimits:
    current = services.get_limits(session, settings)
    return PublicLimits(
        dual_approval_threshold=f"{current.dual_approval_threshold:.2f}",
        max_refund_amount=f"{current.max_refund_amount:.2f}",
    )


@router.get("/summary", response_model=dict[str, StatusSummary])
def summary(
    actor: Principal = Depends(reader),
    session: Session = Depends(get_session),
) -> dict:
    return services.summarize(session, actor)


@router.get("/export.csv", response_class=Response)
def export_csv(
    status_filter: RefundStatus | None = Query(default=None, alias="status"),
    actor: Principal = Depends(finance),
    session: Session = Depends(get_session),
) -> Response:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(EXPORT_COLUMNS)
    for r in services.list_refunds(session, actor, status_filter, limit=100_000):
        out = RefundOut.of(r)
        row = {
            **out.model_dump(),
            "approvers": "; ".join(a.checker_name for a in out.approvals),
        }
        writer.writerow([_csv_safe(row[c]) for c in EXPORT_COLUMNS])
    return Response(
        buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="refunds.csv"'},
    )


@router.get("/{refund_id}/history", response_model=list[AuditEventOut])
def history(
    refund_id: str,
    actor: Principal = Depends(reader),
    session: Session = Depends(get_session),
) -> list[AuditEventOut]:
    return [AuditEventOut.of(e) for e in services.refund_history(session, actor, refund_id)]


@router.get("/{refund_id}", response_model=RefundOut)
def get_refund(
    refund_id: str,
    actor: Principal = Depends(reader),
    session: Session = Depends(get_session),
) -> RefundOut:
    return RefundOut.of(services.get_refund(session, actor, refund_id))


@router.post("/{refund_id}/approve", response_model=RefundOut)
def approve_refund(
    refund_id: str,
    body: ApproveBody | None = None,
    actor: Principal = Depends(checker),
    session: Session = Depends(get_session),
) -> RefundOut:
    body = body or ApproveBody()
    refund = services.approve_refund(session, actor, refund_id, body.comment, body.expected_version)
    return RefundOut.of(refund)


@router.post("/{refund_id}/reject", response_model=RefundOut)
def reject_refund(
    refund_id: str,
    body: RejectBody,
    actor: Principal = Depends(checker),
    session: Session = Depends(get_session),
) -> RefundOut:
    refund = services.reject_refund(session, actor, refund_id, body.reason, body.expected_version)
    return RefundOut.of(refund)


@router.post("/{refund_id}/cancel", response_model=RefundOut)
def cancel_refund(
    refund_id: str,
    actor: Principal = Depends(maker),
    session: Session = Depends(get_session),
) -> RefundOut:
    return RefundOut.of(services.cancel_refund(session, actor, refund_id))
