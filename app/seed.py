"""Demo data for local runs (REFUND_SEED_DEMO_DATA=true, dev auth only)."""

from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app import services
from app.auth import DEV_USERS, Principal
from app.config import Settings
from app.models import RefundRequest
from app.schemas import RefundCreate


def _dev(user: str) -> Principal:
    name, roles = DEV_USERS[user]
    return Principal(oid=f"dev-{user}", name=name, roles=frozenset(roles))


DEMO_REFUNDS = [
    ("alice", "CUST-1001", "SUB-58213", "9.99", "Pro plan billed twice this month", "approve:bob"),
    (
        "alice",
        "CUST-1002",
        "SUB-58377",
        "119.88",
        "Annual Premium plan cancelled within 14-day cooling-off period",
        "approve:bob",
    ),
    ("erin", "CUST-1003", "RWD-58410", "45.50", "Cashback reward not credited for March", None),
    ("alice", "CUST-1004", "DSP-58502", "250.00", "Card dispute: merchant did not deliver", None),
    ("erin", "CUST-1005", "RWD-58611", "12.00", "Referral bonus claim", "reject:carol"),
    ("alice", "CUST-1006", "DSP-58720", "75.25", "Unrecognised card transaction", None),
    ("erin", "CUST-1007", "SUB-58799", "4.99", "Trial auto-renewed after cancellation", "cancel"),
]


def seed_demo_data(factory: sessionmaker, settings: Settings) -> None:
    with factory() as session:
        if session.scalar(select(func.count()).select_from(RefundRequest)):
            return
        for maker, customer, order, amount, reason, outcome in DEMO_REFUNDS:
            _create_and_decide(session, settings, maker, customer, order, amount, reason, outcome)


def _create_and_decide(
    session: Session,
    settings: Settings,
    maker: str,
    customer: str,
    order: str,
    amount: str,
    reason: str,
    outcome: str | None,
) -> None:
    refund, _ = services.create_refund(
        session,
        settings,
        _dev(maker),
        RefundCreate(
            customer_id=customer,
            order_ref=order,
            amount=Decimal(amount),
            currency="USD",
            reason=reason,
        ),
        None,
    )
    if outcome is None:
        return
    action, _, user = outcome.partition(":")
    if action == "approve":
        services.approve_refund(
            session, _dev(user), refund.id, "Verified against billing history", None
        )
    elif action == "reject":
        services.reject_refund(session, _dev(user), refund.id, "Outside refund policy", None)
    elif action == "cancel":
        services.cancel_refund(session, _dev(maker), refund.id)
