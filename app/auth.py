"""Server-side authentication and RBAC.

Tokens are Microsoft Entra ID access tokens (v2) for this API. Roles come from the
`roles` claim, which Entra populates from app-role assignments (see infra/entra).
The UI never decides what a user may do; every endpoint re-checks roles here.
"""

import logging
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

logger = logging.getLogger(__name__)

ROLE_MAKER = "Refund.Maker"
ROLE_CHECKER = "Refund.Checker"
ROLE_FINANCE = "Refund.Finance"
ROLE_ADMIN = "Refund.Admin"
ALL_ROLES = frozenset({ROLE_MAKER, ROLE_CHECKER, ROLE_FINANCE, ROLE_ADMIN})


@dataclass(frozen=True)
class Principal:
    oid: str
    name: str
    roles: frozenset[str]

    def has(self, role: str) -> bool:
        return role in self.roles


class AuthError(Exception):
    pass


class TokenValidator:
    def __init__(
        self,
        *,
        issuer: str,
        audience: str | Iterable[str],
        key_resolver: Callable[[str], Any],
        tenant_id: str | None = None,
        leeway_seconds: int = 60,
    ) -> None:
        self._issuer = issuer
        self._audience = [audience] if isinstance(audience, str) else list(audience)
        self._key_resolver = key_resolver
        self._tenant_id = tenant_id
        self._leeway = leeway_seconds

    def validate(self, token: str) -> Principal:
        try:
            key = self._key_resolver(token)
            claims = jwt.decode(
                token,
                key,
                algorithms=["RS256"],
                audience=self._audience,
                issuer=self._issuer,
                leeway=self._leeway,
                options={"require": ["exp", "iat", "iss", "aud", "sub"]},
            )
        except (jwt.PyJWTError, ValueError) as exc:
            raise AuthError(f"invalid token: {exc}") from exc

        oid = claims.get("oid")
        if not oid:
            raise AuthError("token has no oid claim")
        if self._tenant_id and claims.get("tid") != self._tenant_id:
            raise AuthError("token issued for a different tenant")

        raw_roles = claims.get("roles") or []
        if not isinstance(raw_roles, list):
            raise AuthError("roles claim must be a list")
        roles = frozenset(r for r in raw_roles if r in ALL_ROLES)
        name = claims.get("name") or claims.get("preferred_username") or oid
        return Principal(oid=oid, name=name, roles=roles)


def entra_validator(tenant_id: str, api_client_id: str) -> TokenValidator:
    jwks = jwt.PyJWKClient(
        f"https://login.microsoftonline.com/{tenant_id}/discovery/v2.0/keys", cache_keys=True
    )
    return TokenValidator(
        issuer=f"https://login.microsoftonline.com/{tenant_id}/v2.0",
        audience=[api_client_id, f"api://{api_client_id}"],
        key_resolver=lambda token: jwks.get_signing_key_from_jwt(token).key,
        tenant_id=tenant_id,
    )


DEV_USERS: dict[str, tuple[str, tuple[str, ...]]] = {
    "alice": ("Alice (Maker)", (ROLE_MAKER,)),
    "bob": ("Bob (Checker)", (ROLE_CHECKER,)),
    "carol": ("Carol (Checker)", (ROLE_CHECKER,)),
    "erin": ("Erin (Maker + Checker)", (ROLE_MAKER, ROLE_CHECKER)),
    "frank": ("Frank (Finance)", (ROLE_FINANCE,)),
    "adam": ("Adam (Admin)", (ROLE_ADMIN,)),
}


class DevTokenIssuer:
    """Local stand-in for Entra ID so the app runs without a tenant. Never enabled in prod."""

    ISSUER = "urn:refund-dashboard:dev"
    AUDIENCE = "refund-dashboard-dev"

    def __init__(self) -> None:
        self._key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.public_key = self._key.public_key()

    def mint(
        self,
        *,
        oid: str,
        name: str,
        roles: Iterable[str] | str,
        lifetime_seconds: int = 3600,
        **overrides: Any,
    ) -> str:
        now = int(time.time())
        claims: dict[str, Any] = {
            "iss": self.ISSUER,
            "aud": self.AUDIENCE,
            "sub": oid,
            "oid": oid,
            "tid": "dev-tenant",
            "name": name,
            "roles": roles if isinstance(roles, str) else list(roles),
            "iat": now,
            "nbf": now,
            "exp": now + lifetime_seconds,
        }
        claims.update(overrides)
        claims = {k: v for k, v in claims.items() if v is not None}
        return jwt.encode(claims, self._key, algorithm="RS256", headers={"kid": "dev"})

    def mint_for(self, username: str) -> str:
        name, roles = DEV_USERS[username]
        return self.mint(oid=f"dev-{username}", name=name, roles=roles)

    def validator(self) -> TokenValidator:
        return TokenValidator(
            issuer=self.ISSUER, audience=self.AUDIENCE, key_resolver=lambda _t: self.public_key
        )


_bearer = HTTPBearer(auto_error=False)


def get_principal(
    request: Request, creds: HTTPAuthorizationCredentials | None = Depends(_bearer)
) -> Principal:
    if creds is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    validator: TokenValidator = request.app.state.token_validator
    try:
        return validator.validate(creds.credentials)
    except AuthError as exc:
        logger.info("rejected token: %s", exc)
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Invalid or expired token",
            headers={"WWW-Authenticate": 'Bearer error="invalid_token"'},
        ) from exc


def require_any_role(*roles: str) -> Callable[..., Principal]:
    required = frozenset(roles)

    def _dependency(request: Request, principal: Principal = Depends(get_principal)) -> Principal:
        if not principal.roles & required:
            logger.warning(
                "RBAC denied oid=%s path=%s needs=%s has=%s",
                principal.oid,
                request.url.path,
                sorted(required),
                sorted(principal.roles),
            )
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Insufficient role for this action")
        return principal

    return _dependency
