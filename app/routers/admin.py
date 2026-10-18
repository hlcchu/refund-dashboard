from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app import services
from app.auth import ALL_ROLES, ROLE_ADMIN, Principal, require_any_role
from app.config import Settings
from app.deps import get_app_settings, get_session
from app.schemas import KnownUserOut, LimitsOut, LimitsUpdate

router = APIRouter(prefix="/api/admin", tags=["admin"])

admin = require_any_role(ROLE_ADMIN)


def _out(limits: services.Limits) -> LimitsOut:
    return LimitsOut(
        dual_approval_threshold=f"{limits.dual_approval_threshold:.2f}",
        max_refund_amount=f"{limits.max_refund_amount:.2f}",
        updated_at=limits.updated_at,
        updated_by=limits.updated_by,
    )


@router.get("/settings", response_model=LimitsOut)
def get_settings(
    _actor: Principal = Depends(admin),
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_app_settings),
) -> LimitsOut:
    return _out(services.get_limits(session, settings))


@router.put("/settings", response_model=LimitsOut)
def update_settings(
    body: LimitsUpdate,
    actor: Principal = Depends(admin),
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_app_settings),
) -> LimitsOut:
    return _out(
        services.update_limits(
            session, settings, actor, body.dual_approval_threshold, body.max_refund_amount
        )
    )


@router.get("/users", response_model=list[KnownUserOut])
def users(
    _actor: Principal = Depends(admin),
    session: Session = Depends(get_session),
) -> list[KnownUserOut]:
    return [KnownUserOut(**u) for u in services.known_users(session)]


@router.get("/roles")
def roles(_actor: Principal = Depends(admin)) -> list[str]:
    return sorted(ALL_ROLES)
