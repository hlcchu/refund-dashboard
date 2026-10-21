import logging

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DatabaseError

from app import audit


def run_workflow(client, auth, make_refund):
    a = make_refund()
    b = make_refund(amount="2000.00", order_ref="SUB-2")
    c = make_refund(order_ref="SUB-3")
    client.post(f"/api/refunds/{a['id']}/approve", headers=auth("bob"))
    client.post(f"/api/refunds/{b['id']}/approve", headers=auth("bob"))
    client.post(f"/api/refunds/{b['id']}/approve", headers=auth("carol"))
    client.post(
        f"/api/refunds/{c['id']}/reject", json={"reason": "Fraud flag"}, headers=auth("bob")
    )
    return a, b, c


def test_every_change_is_audited_with_actor(client, auth, make_refund):
    a, b, c = run_workflow(client, auth, make_refund)
    events = client.get("/api/audit/events", headers=auth("frank")).json()
    assert [e["seq"] for e in events] == list(range(1, 8))
    assert [(e["action"], e["actor_oid"]) for e in events] == [
        ("refund.created", "dev-alice"),
        ("refund.created", "dev-alice"),
        ("refund.created", "dev-alice"),
        ("refund.approved", "dev-bob"),
        ("refund.approval_recorded", "dev-bob"),
        ("refund.approved", "dev-carol"),
        ("refund.rejected", "dev-bob"),
    ]
    assert events[0]["prev_hash"] == audit.GENESIS_HASH
    for prev, cur in zip(events, events[1:], strict=False):
        assert cur["prev_hash"] == prev["hash"]
    assert events[-1]["payload"]["rejection_reason"] == "Fraud flag"


def test_filter_by_entity(client, auth, make_refund):
    _, b, _ = run_workflow(client, auth, make_refund)
    events = client.get(f"/api/audit/events?entity_id={b['id']}", headers=auth("frank")).json()
    assert [e["action"] for e in events] == [
        "refund.created",
        "refund.approval_recorded",
        "refund.approved",
    ]


def test_chain_verifies(client, auth, make_refund):
    run_workflow(client, auth, make_refund)
    result = client.get("/api/audit/verify", headers=auth("frank")).json()
    assert result["valid"] is True
    assert result["events_checked"] == 7
    assert result["first_invalid_seq"] is None


def test_empty_chain_verifies(client, auth):
    result = client.get("/api/audit/verify", headers=auth("frank")).json()
    assert result == {
        "valid": True,
        "events_checked": 0,
        "head_hash": audit.GENESIS_HASH,
        "first_invalid_seq": None,
        "error": None,
    }


@pytest.mark.parametrize(
    "stmt",
    [
        "UPDATE audit_events SET actor_oid = 'someone-else' WHERE seq = 1",
        "DELETE FROM audit_events WHERE seq = 1",
        "DELETE FROM audit_events",
    ],
)
def test_database_blocks_update_and_delete(app, client, auth, make_refund, stmt):
    run_workflow(client, auth, make_refund)
    with pytest.raises(DatabaseError, match="append-only"):
        with app.state.engine.begin() as conn:
            conn.execute(text(stmt))
    assert client.get("/api/audit/verify", headers=auth("frank")).json()["valid"] is True


def _bypass_db_guard(app, stmt: str, params: dict | None = None) -> None:
    """Simulate a privileged attacker who disables the append-only triggers."""
    with app.state.engine.begin() as conn:
        conn.execute(text("DROP TRIGGER audit_events_no_update"))
        conn.execute(text("DROP TRIGGER audit_events_no_delete"))
        conn.execute(text(stmt), params or {})


@pytest.mark.parametrize(
    "stmt,bad_seq,error",
    [
        (
            "UPDATE audit_events SET payload = replace(payload, 'Fraud flag', 'ok') WHERE seq = 7",
            7,
            "hash mismatch",
        ),
        ("UPDATE audit_events SET actor_oid = 'dev-carol' WHERE seq = 4", 4, "hash mismatch"),
        ("DELETE FROM audit_events WHERE seq = 3", 4, "sequence gap"),
    ],
)
def test_tampering_is_detected(app, client, auth, make_refund, stmt, bad_seq, error, caplog):
    run_workflow(client, auth, make_refund)
    _bypass_db_guard(app, stmt)
    with caplog.at_level(logging.ERROR, logger="app.routers.audit"):
        result = client.get("/api/audit/verify", headers=auth("frank")).json()
    assert result["valid"] is False
    assert result["first_invalid_seq"] == bad_seq
    assert result["error"] == error
    assert any(r.getMessage().startswith("audit chain verification failed") for r in caplog.records)


def test_rewriting_tail_with_recomputed_hash_still_breaks_successor(app, client, auth, make_refund):
    """Recomputing one row's hash isn't enough: the next row's prev_hash no longer matches."""
    run_workflow(client, auth, make_refund)
    with app.state.session_factory() as s:
        ev = s.get(audit.AuditEvent, 5)
        forged_payload = ev.payload.replace('"approvals":1', '"approvals":2')
        forged_hash = audit.compute_hash(
            prev_hash=ev.prev_hash,
            seq=ev.seq,
            occurred_at=ev.occurred_at,
            actor_oid=ev.actor_oid,
            actor_name=ev.actor_name,
            actor_roles=ev.actor_roles,
            action=ev.action,
            entity_type=ev.entity_type,
            entity_id=ev.entity_id,
            payload=forged_payload,
        )
    _bypass_db_guard(
        app,
        "UPDATE audit_events SET payload = :payload, hash = :hash WHERE seq = 5",
        {"payload": forged_payload, "hash": forged_hash},
    )
    result = client.get("/api/audit/verify", headers=auth("frank")).json()
    assert result["valid"] is False
    assert result["first_invalid_seq"] == 6
    assert result["error"] == "prev_hash mismatch"
