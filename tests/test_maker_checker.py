import pytest
from sqlalchemy import func, select

from app import services
from app.auth import ROLE_CHECKER, Principal
from app.config import Settings
from app.db import create_schema, make_engine, make_session_factory
from app.models import AuditEvent, RefundRequest
from app.schemas import RefundCreate


def audit_actions(app, entity_id):
    with app.state.session_factory() as s:
        return [
            e.action
            for e in s.scalars(
                select(AuditEvent).where(AuditEvent.entity_id == entity_id).order_by(AuditEvent.seq)
            )
        ]


def test_maker_creates_pending_refund(make_refund):
    refund = make_refund(amount="50.00")
    assert refund["status"] == "PENDING"
    assert refund["amount"] == "50.00"
    assert refund["required_approvals"] == 1
    assert refund["maker_oid"] == "dev-alice"
    assert refund["version"] == 1


def test_single_checker_approves_below_threshold(app, client, auth, make_refund):
    refund = make_refund()
    resp = client.post(
        f"/api/refunds/{refund['id']}/approve", json={"comment": "ok"}, headers=auth("bob")
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "APPROVED"
    assert [a["checker_oid"] for a in body["approvals"]] == ["dev-bob"]
    assert body["decided_at"] is not None
    assert audit_actions(app, refund["id"]) == ["refund.created", "refund.approved"]


def test_maker_with_checker_role_cannot_approve_own_refund(app, client, auth, make_refund):
    refund = make_refund(user="erin")
    resp = client.post(f"/api/refunds/{refund['id']}/approve", headers=auth("erin"))
    assert resp.status_code == 403
    assert "Segregation of duties" in resp.json()["detail"]
    assert client.get(f"/api/refunds/{refund['id']}", headers=auth("bob")).json()["status"] == (
        "PENDING"
    )
    assert audit_actions(app, refund["id"]) == ["refund.created", "refund.approve.denied"]


def test_maker_with_checker_role_cannot_reject_own_refund(app, client, auth, make_refund):
    refund = make_refund(user="erin")
    resp = client.post(
        f"/api/refunds/{refund['id']}/reject", json={"reason": "changed mind"}, headers=auth("erin")
    )
    assert resp.status_code == 403
    assert audit_actions(app, refund["id"])[-1] == "refund.reject.denied"


def test_dual_role_user_can_check_someone_elses_refund(client, auth, make_refund):
    refund = make_refund(user="alice")
    resp = client.post(f"/api/refunds/{refund['id']}/approve", headers=auth("erin"))
    assert resp.status_code == 200
    assert resp.json()["status"] == "APPROVED"


def test_high_value_needs_two_distinct_checkers(app, client, auth, make_refund):
    refund = make_refund(amount="50.01")
    assert refund["required_approvals"] == 2
    path = f"/api/refunds/{refund['id']}/approve"

    first = client.post(path, headers=auth("bob"))
    assert first.status_code == 200
    assert first.json()["status"] == "PENDING"

    again = client.post(path, headers=auth("bob"))
    assert again.status_code == 409

    second = client.post(path, headers=auth("carol"))
    assert second.status_code == 200
    assert second.json()["status"] == "APPROVED"
    assert {a["checker_oid"] for a in second.json()["approvals"]} == {"dev-bob", "dev-carol"}
    assert audit_actions(app, refund["id"]) == [
        "refund.created",
        "refund.approval_recorded",
        "refund.approved",
    ]


def test_reject_requires_reason_and_is_final(client, auth, make_refund):
    refund = make_refund()
    path = f"/api/refunds/{refund['id']}"
    assert client.post(f"{path}/reject", json={}, headers=auth("bob")).status_code == 422
    resp = client.post(f"{path}/reject", json={"reason": "No proof of damage"}, headers=auth("bob"))
    assert resp.status_code == 200
    assert resp.json()["status"] == "REJECTED"
    assert resp.json()["rejection_reason"] == "No proof of damage"
    assert client.post(f"{path}/approve", headers=auth("carol")).status_code == 409


def test_cannot_decide_twice(client, auth, make_refund):
    refund = make_refund()
    path = f"/api/refunds/{refund['id']}"
    assert client.post(f"{path}/approve", headers=auth("bob")).status_code == 200
    assert client.post(f"{path}/approve", headers=auth("carol")).status_code == 409
    assert (
        client.post(f"{path}/reject", json={"reason": "too late"}, headers=auth("carol"))
    ).status_code == 409


def test_maker_cancels_own_pending_refund(app, client, auth, make_refund):
    refund = make_refund()
    path = f"/api/refunds/{refund['id']}"
    resp = client.post(f"{path}/cancel", headers=auth("alice"))
    assert resp.status_code == 200
    assert resp.json()["status"] == "CANCELLED"
    assert client.post(f"{path}/approve", headers=auth("bob")).status_code == 409
    assert audit_actions(app, refund["id"])[-1] == "refund.cancelled"


def test_other_maker_cannot_cancel(client, auth, make_refund):
    refund = make_refund(user="alice")
    # erin is also a checker so she can see it, but she is not the maker
    assert client.post(f"/api/refunds/{refund['id']}/cancel", headers=auth("erin")).status_code == (
        403
    )


def test_maker_only_sees_own_refunds(client, auth, make_refund):
    mine = make_refund(user="alice")
    theirs = make_refund(user="erin", order_ref="ORD-2")
    ids = {r["id"] for r in client.get("/api/refunds", headers=auth("alice")).json()}
    assert ids == {mine["id"]}
    assert client.get(f"/api/refunds/{theirs['id']}", headers=auth("alice")).status_code == 404
    for user in ("bob", "frank"):
        ids = {r["id"] for r in client.get("/api/refunds", headers=auth(user)).json()}
        assert ids == {mine["id"], theirs["id"]}


def test_status_filter(client, auth, make_refund):
    a = make_refund()
    make_refund(order_ref="ORD-2")
    client.post(f"/api/refunds/{a['id']}/approve", headers=auth("bob"))
    approved = client.get("/api/refunds?status=APPROVED", headers=auth("bob")).json()
    assert [r["id"] for r in approved] == [a["id"]]


def test_idempotency_key_replays_same_refund(client, auth):
    body = {
        "customer_id": "C1",
        "order_ref": "O1",
        "amount": "25.00",
        "currency": "USD",
        "reason": "Late delivery",
    }
    headers = {**auth("alice"), "Idempotency-Key": "abc-123"}
    first = client.post("/api/refunds", json=body, headers=headers)
    second = client.post("/api/refunds", json=body, headers=headers)
    assert first.status_code == 201
    assert second.status_code == 200
    assert first.json()["id"] == second.json()["id"]

    changed = client.post("/api/refunds", json={**body, "amount": "26.00"}, headers=headers)
    assert changed.status_code == 409


@pytest.mark.parametrize(
    "field,value",
    [
        ("amount", "-5.00"),
        ("amount", "0"),
        ("amount", "10.001"),
        ("currency", "usd"),
        ("reason", "    "),
        ("customer_id", "x; DROP TABLE"),
    ],
)
def test_validation(client, auth, field, value):
    body = {
        "customer_id": "C1",
        "order_ref": "O1",
        "amount": "10.00",
        "currency": "USD",
        "reason": "Late delivery",
        field: value,
    }
    assert client.post("/api/refunds", json=body, headers=auth("alice")).status_code == 422


def test_amount_above_maximum_rejected(client, auth):
    body = {
        "customer_id": "C1",
        "order_ref": "O1",
        "amount": "50000.01",
        "currency": "USD",
        "reason": "Large order returned",
    }
    resp = client.post("/api/refunds", json=body, headers=auth("alice"))
    assert resp.status_code == 422
    assert "maximum" in resp.json()["detail"]


def test_expected_version_mismatch_is_409_and_not_audited(app, client, auth, make_refund):
    refund = make_refund(amount="1500.00")
    path = f"/api/refunds/{refund['id']}/approve"
    assert client.post(path, json={"expected_version": 1}, headers=auth("bob")).status_code == 200
    stale = client.post(path, json={"expected_version": 1}, headers=auth("carol"))
    assert stale.status_code == 409
    assert audit_actions(app, refund["id"]) == ["refund.created", "refund.approval_recorded"]


def test_concurrent_approvals_cannot_double_approve(tmp_path):
    """Two checkers load the same single-approval refund; only one commit may win."""
    engine = make_engine(f"sqlite:///{tmp_path / 'race.db'}")
    create_schema(engine)
    factory = make_session_factory(engine)
    settings = Settings(environment="test")
    maker = Principal("m1", "Maker", frozenset({"Refund.Maker"}))
    bob = Principal("c1", "Bob", frozenset({ROLE_CHECKER}))
    carol = Principal("c2", "Carol", frozenset({ROLE_CHECKER}))

    with factory() as s:
        refund, _ = services.create_refund(
            s,
            settings,
            maker,
            RefundCreate(
                customer_id="C", order_ref="O", amount="10.00", currency="USD", reason="Damaged"
            ),
            None,
        )
        refund_id = refund.id

    s_bob, s_carol = factory(), factory()
    s_bob.get(RefundRequest, refund_id)
    s_carol.get(RefundRequest, refund_id)  # carol now holds a stale copy (version 1)

    services.approve_refund(s_bob, bob, refund_id, None, None)
    with pytest.raises(services.ConflictError):
        services.approve_refund(s_carol, carol, refund_id, None, None)

    with factory() as s:
        final = s.get(RefundRequest, refund_id)
        assert final.status == "APPROVED"
        assert [a.checker_oid for a in final.approvals] == ["c1"]
        assert s.scalar(select(func.count()).select_from(AuditEvent)) == 2
