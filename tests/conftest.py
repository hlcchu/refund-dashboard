from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


@pytest.fixture
def settings() -> Settings:
    return Settings(
        environment="test",
        database_url="sqlite://",
        auth_mode="dev",
        dual_approval_threshold=Decimal("50.00"),
        max_refund_amount=Decimal("50000.00"),
    )


@pytest.fixture
def app(settings):
    return create_app(settings)


@pytest.fixture
def client(app) -> TestClient:
    return TestClient(app)


@pytest.fixture
def auth(app):
    """auth("alice") -> Authorization header for a predefined dev user."""

    def _headers(user: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {app.state.dev_issuer.mint_for(user)}"}

    return _headers


@pytest.fixture
def make_refund(client, auth):
    def _make(user: str = "alice", amount: str = "25.00", **overrides) -> dict:
        body = {
            "customer_id": "CUST-1",
            "order_ref": "SUB-1",
            "amount": amount,
            "currency": "USD",
            "reason": "Duplicate subscription charge",
            **overrides,
        }
        resp = client.post("/api/refunds", json=body, headers=auth(user))
        assert resp.status_code == 201, resp.text
        return resp.json()

    return _make
