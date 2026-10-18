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
    ("alice", "CUST-1001", "ORD-58213", "19.99", "Item arrived damaged", "approve:bob"),
    ("alice", "CUST-1002", "ORD-58377", "120.00", "Duplicate charge on card", "approve:bob"),
    ("erin", "CUST-1003", "ORD-58410", "45.50", "Late delivery goodwill", None),
    ("alice", "CUST-1004", "ORD-58502", "250.00", "Order cancelled before dispatch", None),
    ("erin", "CUST-1005", "ORD-58611", "12.00", "Wrong size shipped", "reject:carol"),
    ("alice", "CUST-1006", "ORD-58720", "75.25", "Missing items in parcel", None),
    ("erin", "CUST-1007", "ORD-58799", "8.99", "Customer changed mind", "cancel"),
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
        services.approve_refund(session, _dev(user), refund.id, "Verified with order history", None)
    elif action == "reject":
        services.reject_refund(session, _dev(user), refund.id, "Outside refund policy", None)
    elif action == "cancel":
        services.cancel_refund(session, _dev(maker), refund.id)
