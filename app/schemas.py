import json
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models import AuditEvent, RefundRequest

_ID_PATTERN = r"^[A-Za-z0-9._:\-]+$"


class RefundCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer_id: str = Field(min_length=1, max_length=64, pattern=_ID_PATTERN)
    order_ref: str = Field(min_length=1, max_length=64, pattern=_ID_PATTERN)
    amount: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    reason: str = Field(min_length=5, max_length=1000)

    @field_validator("reason")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = v.strip()
        if len(v) < 5:
            raise ValueError("reason must be at least 5 characters")
        return v


class ApproveBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    comment: str | None = Field(default=None, max_length=1000)
    expected_version: int | None = None


class RejectBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=5, max_length=1000)
    expected_version: int | None = None


class ApprovalOut(BaseModel):
    checker_oid: str
    checker_name: str
    comment: str | None
    created_at: datetime


class RefundOut(BaseModel):
    id: str
    customer_id: str
    order_ref: str
    amount: str
    currency: str
    reason: str
    status: str
    required_approvals: int
    approvals: list[ApprovalOut]
    maker_oid: str
    maker_name: str
    rejection_reason: str | None
    created_at: datetime
    updated_at: datetime
    decided_at: datetime | None
    version: int

    @classmethod
    def of(cls, r: RefundRequest) -> "RefundOut":
        return cls(
            id=r.id,
            customer_id=r.customer_id,
            order_ref=r.order_ref,
            amount=f"{Decimal(r.amount_minor) / 100:.2f}",
            currency=r.currency,
            reason=r.reason,
            status=r.status,
            required_approvals=r.required_approvals,
            approvals=[
                ApprovalOut(
                    checker_oid=a.checker_oid,
                    checker_name=a.checker_name,
                    comment=a.comment,
                    created_at=a.created_at,
                )
                for a in r.approvals
            ],
            maker_oid=r.maker_oid,
            maker_name=r.maker_name,
            rejection_reason=r.rejection_reason,
            created_at=r.created_at,
            updated_at=r.updated_at,
            decided_at=r.decided_at,
            version=r.version,
        )


class AuditEventOut(BaseModel):
    seq: int
    occurred_at: str
    actor_oid: str
    actor_name: str
    actor_roles: list[str]
    action: str
    entity_type: str
    entity_id: str
    payload: dict[str, Any]
    prev_hash: str
    hash: str

    @classmethod
    def of(cls, e: AuditEvent) -> "AuditEventOut":
        return cls(
            seq=e.seq,
            occurred_at=e.occurred_at,
            actor_oid=e.actor_oid,
            actor_name=e.actor_name,
            actor_roles=[r for r in e.actor_roles.split(",") if r],
            action=e.action,
            entity_type=e.entity_type,
            entity_id=e.entity_id,
            payload=json.loads(e.payload),
            prev_hash=e.prev_hash,
            hash=e.hash,
        )


class ChainVerificationOut(BaseModel):
    valid: bool
    events_checked: int
    head_hash: str
    first_invalid_seq: int | None
    error: str | None


class MeOut(BaseModel):
    oid: str
    name: str
    roles: list[str]


class PublicLimits(BaseModel):
    dual_approval_threshold: str
    max_refund_amount: str


class LimitsOut(BaseModel):
    dual_approval_threshold: str
    max_refund_amount: str
    updated_at: datetime | None
    updated_by: str | None


class LimitsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dual_approval_threshold: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    max_refund_amount: Decimal = Field(gt=0, max_digits=12, decimal_places=2)


class KnownUserOut(BaseModel):
    oid: str
    name: str
    roles: list[str]
    last_seen: str
    last_action: str


class StatusSummary(BaseModel):
    count: int
    totals: dict[str, str]
