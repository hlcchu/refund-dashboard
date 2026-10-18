import csv
import io

import pytest


def _approve(client, auth, refund, user):
    return client.post(f"/api/refunds/{refund['id']}/approve", headers=auth(user))


@pytest.mark.parametrize(("amount", "required"), [("50.00", 1), ("50.01", 2), ("49.99", 1)])
def test_dual_approval_only_strictly_above_threshold(make_refund, amount, required):
    assert make_refund(amount=amount)["required_approvals"] == required


def test_finance_reads_everything_but_cannot_act(client, auth, make_refund):
    refund = make_refund()
    path = f"/api/refunds/{refund['id']}"
    assert client.get("/api/refunds", headers=auth("frank")).status_code == 200
    assert client.get(path, headers=auth("frank")).status_code == 200
    assert client.get("/api/audit/events", headers=auth("frank")).status_code == 200
    assert client.get("/api/audit/verify", headers=auth("frank")).json()["valid"] is True
    body = {
        "customer_id": "C1",
        "order_ref": "O1",
        "amount": "1.00",
        "currency": "USD",
        "reason": "test reason",
    }
    assert client.post("/api/refunds", json=body, headers=auth("frank")).status_code == 403
    assert client.post(f"{path}/approve", headers=auth("frank")).status_code == 403
    assert client.get("/api/admin/settings", headers=auth("frank")).status_code == 403


@pytest.mark.parametrize("user", ["alice", "bob", "erin", "adam"])
def test_only_finance_sees_audit_and_export(client, auth, user):
    assert client.get("/api/audit/events", headers=auth(user)).status_code == 403
    assert client.get("/api/refunds/export.csv", headers=auth(user)).status_code == 403


def test_admin_cannot_raise_or_decide_or_read_refunds(client, auth, make_refund):
    refund = make_refund()
    path = f"/api/refunds/{refund['id']}"
    assert client.get("/api/refunds", headers=auth("adam")).status_code == 403
    assert client.post(f"{path}/approve", headers=auth("adam")).status_code == 403
    assert client.post(f"{path}/cancel", headers=auth("adam")).status_code == 403


def test_export_csv_is_formula_safe(client, auth, make_refund):
    refund = make_refund(reason='=HYPERLINK("http://evil")')
    _approve(client, auth, refund, "bob")
    resp = client.get("/api/refunds/export.csv", headers=auth("frank"))
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    rows = list(csv.DictReader(io.StringIO(resp.text)))
    assert len(rows) == 1
    assert rows[0]["reason"].startswith("'=")
    assert rows[0]["status"] == "APPROVED"
    assert rows[0]["approvers"] == "Bob (Checker)"


def test_summary_counts_and_totals(client, auth, make_refund):
    a = make_refund(amount="10.00", order_ref="O-1")
    make_refund(amount="20.50", order_ref="O-2")
    _approve(client, auth, a, "bob")
    summary = client.get("/api/refunds/summary", headers=auth("frank")).json()
    assert summary["APPROVED"] == {"count": 1, "totals": {"USD": "10.00"}}
    assert summary["PENDING"] == {"count": 1, "totals": {"USD": "20.50"}}
    assert summary["REJECTED"]["count"] == 0


def test_summary_for_maker_only_counts_own(client, auth, make_refund):
    make_refund(user="alice", order_ref="O-1")
    make_refund(user="erin", order_ref="O-2")
    assert client.get("/api/refunds/summary", headers=auth("alice")).json()["PENDING"]["count"] == 1


def test_history_lists_refund_events(client, auth, make_refund):
    refund = make_refund()
    _approve(client, auth, refund, "bob")
    events = client.get(f"/api/refunds/{refund['id']}/history", headers=auth("alice")).json()
    assert [e["action"] for e in events] == ["refund.created", "refund.approved"]
    assert events[1]["actor_roles"] == ["Refund.Checker"]
    other = make_refund(user="erin", order_ref="O-9")
    assert (
        client.get(f"/api/refunds/{other['id']}/history", headers=auth("alice")).status_code == 404
    )


def test_admin_updates_limits_and_it_is_audited(client, auth, make_refund):
    resp = client.get("/api/admin/settings", headers=auth("adam"))
    assert resp.json()["dual_approval_threshold"] == "50.00"
    new = {"dual_approval_threshold": "100.00", "max_refund_amount": "500.00"}
    resp = client.put("/api/admin/settings", json=new, headers=auth("adam"))
    assert resp.status_code == 200
    assert resp.json()["updated_by"] == "Adam (Admin)"

    assert make_refund(amount="75.00")["required_approvals"] == 1
    assert make_refund(amount="100.01", order_ref="O-2")["required_approvals"] == 2
    too_big = {
        "customer_id": "C1",
        "order_ref": "O-3",
        "amount": "500.01",
        "currency": "USD",
        "reason": "test reason",
    }
    assert client.post("/api/refunds", json=too_big, headers=auth("alice")).status_code == 422

    events = client.get("/api/audit/events", headers=auth("frank")).json()
    change = next(e for e in events if e["action"] == "settings.updated")
    assert change["payload"]["before"]["dual_approval_threshold"] == "50.00"
    assert change["payload"]["after"]["dual_approval_threshold"] == "100.00"
    assert change["actor_roles"] == ["Refund.Admin"]


def test_admin_settings_validation(client, auth):
    bad = {"dual_approval_threshold": "600.00", "max_refund_amount": "500.00"}
    assert client.put("/api/admin/settings", json=bad, headers=auth("adam")).status_code == 422
    neg = {"dual_approval_threshold": "-1", "max_refund_amount": "500.00"}
    assert client.put("/api/admin/settings", json=neg, headers=auth("adam")).status_code == 422


def test_admin_with_checker_role_cannot_change_limits(app, client):
    token = app.state.dev_issuer.mint(
        oid="u-mixed", name="Mixed", roles=["Refund.Admin", "Refund.Checker"]
    )
    body = {"dual_approval_threshold": "1000.00", "max_refund_amount": "5000.00"}
    resp = client.put(
        "/api/admin/settings", json=body, headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 403


def test_admin_known_users(client, auth, make_refund):
    make_refund()
    users = client.get("/api/admin/users", headers=auth("adam")).json()
    assert users[0]["name"] == "Alice (Maker)"
    assert users[0]["roles"] == ["Refund.Maker"]
    assert "Refund.Finance" in client.get("/api/admin/roles", headers=auth("adam")).json()


def test_demo_seed(tmp_path):
    from fastapi.testclient import TestClient

    from app.config import Settings
    from app.main import create_app

    settings = Settings(
        environment="test", database_url=f"sqlite:///{tmp_path}/seed.db", seed_demo_data=True
    )
    app = create_app(settings)
    token = app.state.dev_issuer.mint(oid="f", name="F", roles=["Refund.Finance"])
    client = TestClient(app)
    refunds = client.get("/api/refunds", headers={"Authorization": f"Bearer {token}"}).json()
    assert len(refunds) == 7
    verify = client.get("/api/audit/verify", headers={"Authorization": f"Bearer {token}"}).json()
    assert verify["valid"] is True
    create_app(settings)  # idempotent re-seed
    assert len(client.get("/api/refunds", headers={"Authorization": f"Bearer {token}"}).json()) == 7
