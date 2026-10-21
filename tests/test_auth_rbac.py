import jwt
import pytest
from fastapi.testclient import TestClient

from app.auth import ROLE_CHECKER, ROLE_FINANCE, ROLE_MAKER, DevTokenIssuer
from app.config import Settings
from app.main import create_app

REFUND = {
    "customer_id": "C1",
    "order_ref": "O1",
    "amount": "10.00",
    "currency": "USD",
    "reason": "Duplicate subscription charge",
}


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_healthz_is_public(client):
    assert client.get("/healthz").json() == {"status": "ok"}


def test_security_headers(client):
    resp = client.get("/healthz")
    assert resp.headers["X-Frame-Options"] == "DENY"
    assert "default-src 'self'" in resp.headers["Content-Security-Policy"]


@pytest.mark.parametrize(
    "method,path",
    [
        ("get", "/api/me"),
        ("get", "/api/refunds"),
        ("post", "/api/refunds"),
        ("post", "/api/refunds/x/approve"),
        ("get", "/api/audit/events"),
        ("get", "/api/audit/verify"),
    ],
)
def test_missing_token_is_401(client, method, path):
    resp = getattr(client, method)(path)
    assert resp.status_code == 401
    assert resp.headers["WWW-Authenticate"].startswith("Bearer")


def test_garbage_token_is_401(client):
    assert client.get("/api/me", headers=bearer("not-a-jwt")).status_code == 401


def test_token_signed_by_foreign_key_is_401(client):
    forged = DevTokenIssuer().mint(oid="x", name="Mallory", roles=[ROLE_CHECKER])
    assert client.get("/api/me", headers=bearer(forged)).status_code == 401


def test_alg_none_token_is_401(client):
    token = jwt.encode(
        {
            "iss": DevTokenIssuer.ISSUER,
            "aud": DevTokenIssuer.AUDIENCE,
            "sub": "x",
            "oid": "x",
            "roles": [ROLE_FINANCE],
            "iat": 0,
            "exp": 9999999999,
        },
        key=None,
        algorithm="none",
    )
    assert client.get("/api/me", headers=bearer(token)).status_code == 401


def test_hs256_token_is_401(client):
    token = jwt.encode(
        {
            "iss": DevTokenIssuer.ISSUER,
            "aud": DevTokenIssuer.AUDIENCE,
            "sub": "x",
            "oid": "x",
            "roles": [ROLE_FINANCE],
            "iat": 0,
            "exp": 9999999999,
        },
        "a-shared-secret-that-is-long-enough",
        algorithm="HS256",
    )
    assert client.get("/api/me", headers=bearer(token)).status_code == 401


@pytest.mark.parametrize(
    "overrides",
    [
        {"lifetime_seconds": -3600},
        {"aud": "some-other-api"},
        {"iss": "https://evil.example.com"},
        {"oid": None},
        {"exp": None},
        {"roles": "Refund.Checker"},
    ],
)
def test_invalid_claims_are_401(app, client, overrides):
    kwargs = {"oid": "u1", "name": "U", "roles": [ROLE_CHECKER]}
    kwargs.update(overrides)
    token = app.state.dev_issuer.mint(**kwargs)
    assert client.get("/api/me", headers=bearer(token)).status_code == 401


def test_me_returns_server_side_roles(client, auth):
    resp = client.get("/api/me", headers=auth("erin"))
    assert resp.status_code == 200
    assert resp.json()["roles"] == [ROLE_CHECKER, ROLE_MAKER]


def test_unknown_roles_are_ignored(app, client):
    token = app.state.dev_issuer.mint(
        oid="u1", name="U", roles=["Refund.Superuser", "Global.Admin"]
    )
    assert client.get("/api/me", headers=bearer(token)).json()["roles"] == []
    assert client.get("/api/refunds", headers=bearer(token)).status_code == 403


def test_authenticated_user_without_roles_is_403(app, client):
    token = app.state.dev_issuer.mint(oid="u1", name="U", roles=[])
    assert client.get("/api/refunds", headers=bearer(token)).status_code == 403
    assert client.post("/api/refunds", json=REFUND, headers=bearer(token)).status_code == 403


def test_checker_cannot_create(client, auth):
    assert client.post("/api/refunds", json=REFUND, headers=auth("bob")).status_code == 403


def test_auditor_cannot_create_or_decide(client, auth, make_refund):
    refund = make_refund()
    assert client.post("/api/refunds", json=REFUND, headers=auth("frank")).status_code == 403
    path = f"/api/refunds/{refund['id']}"
    assert client.post(f"{path}/approve", headers=auth("frank")).status_code == 403
    assert (
        client.post(f"{path}/reject", json={"reason": "nope nope"}, headers=auth("frank"))
    ).status_code == 403


def test_maker_cannot_approve_or_reject(client, auth, make_refund):
    refund = make_refund(user="erin")
    path = f"/api/refunds/{refund['id']}"
    assert client.post(f"{path}/approve", headers=auth("alice")).status_code == 403
    assert (
        client.post(f"{path}/reject", json={"reason": "nope nope"}, headers=auth("alice"))
    ).status_code == 403


@pytest.mark.parametrize("user", ["alice", "bob", "erin"])
def test_only_auditor_reads_audit(client, auth, user):
    assert client.get("/api/audit/events", headers=auth(user)).status_code == 403
    assert client.get("/api/audit/verify", headers=auth(user)).status_code == 403
    assert client.get("/api/audit/events", headers=auth("frank")).status_code == 200


def test_production_requires_entra():
    with pytest.raises(ValueError, match="entra"):
        Settings(environment="production", auth_mode="dev")


def test_entra_requires_tenant_and_client_id():
    with pytest.raises(ValueError):
        Settings(auth_mode="entra")


def test_dev_token_endpoint_absent_in_entra_mode():
    settings = Settings(
        environment="test",
        database_url="sqlite://",
        auth_mode="entra",
        entra_tenant_id="00000000-0000-0000-0000-000000000001",
        entra_api_client_id="00000000-0000-0000-0000-000000000002",
    )
    client = TestClient(create_app(settings))
    assert client.post("/dev/token", json={"user": "bob"}).status_code == 404
    assert client.get("/dev/users").status_code == 404
