import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class RefundStatus(str, enum.Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


class RefundRequest(Base):
    __tablename__ = "refund_requests"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    customer_id: Mapped[str] = mapped_column(String(64))
    order_ref: Mapped[str] = mapped_column(String(64))
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3))
    reason: Mapped[str] = mapped_column(String(1000))
    status: Mapped[str] = mapped_column(String(16), default=RefundStatus.PENDING.value, index=True)
    required_approvals: Mapped[int] = mapped_column(Integer)
    maker_oid: Mapped[str] = mapped_column(String(64), index=True)
    maker_name: Mapped[str] = mapped_column(String(256))
    idempotency_key: Mapped[str | None] = mapped_column(String(128))
    rejection_reason: Mapped[str | None] = mapped_column(String(1000))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False)

    approvals: Mapped[list["Approval"]] = relationship(
        back_populates="refund", order_by="Approval.id", lazy="selectin"
    )

    __mapper_args__ = {"version_id_col": version}
    __table_args__ = (
        CheckConstraint("amount_minor > 0", name="ck_refund_amount_positive"),
        CheckConstraint("required_approvals >= 1", name="ck_refund_required_approvals"),
        Index(
            "uq_refund_maker_idempotency",
            "maker_oid",
            "idempotency_key",
            unique=True,
            sqlite_where=text("idempotency_key IS NOT NULL"),
            mssql_where=text("idempotency_key IS NOT NULL"),
        ),
    )


class Approval(Base):
    __tablename__ = "refund_approvals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    refund_id: Mapped[str] = mapped_column(ForeignKey("refund_requests.id"), index=True)
    checker_oid: Mapped[str] = mapped_column(String(64))
    checker_name: Mapped[str] = mapped_column(String(256))
    comment: Mapped[str | None] = mapped_column(String(1000))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    refund: Mapped[RefundRequest] = relationship(back_populates="approvals")

    __table_args__ = (UniqueConstraint("refund_id", "checker_oid", name="uq_approval_checker"),)


class AppSetting(Base):
    """Runtime-tunable limits (e.g. dual-approval threshold). Changed only by Refund.Admin."""

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(String(64))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_by_oid: Mapped[str] = mapped_column(String(64))
    updated_by_name: Mapped[str] = mapped_column(String(256))
    version: Mapped[int] = mapped_column(Integer, nullable=False)

    __mapper_args__ = {"version_id_col": version}


class AuditEvent(Base):
    """Append-only, hash-chained audit record. Never updated or deleted."""

    __tablename__ = "audit_events"

    seq: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    occurred_at: Mapped[str] = mapped_column(String(40))
    actor_oid: Mapped[str] = mapped_column(String(64), index=True)
    actor_name: Mapped[str] = mapped_column(String(256))
    actor_roles: Mapped[str] = mapped_column(String(256))
    action: Mapped[str] = mapped_column(String(64))
    entity_type: Mapped[str] = mapped_column(String(32))
    entity_id: Mapped[str] = mapped_column(String(64), index=True)
    payload: Mapped[str] = mapped_column(Text)
    prev_hash: Mapped[str] = mapped_column(String(64), unique=True)
    hash: Mapped[str] = mapped_column(String(64), unique=True)
