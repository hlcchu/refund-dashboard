from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.auth import DEV_USERS, Principal, get_principal
from app.schemas import MeOut

router = APIRouter(tags=["identity"])
dev_router = APIRouter(prefix="/dev", tags=["dev-only"])


@router.get("/api/me", response_model=MeOut)
def me(principal: Principal = Depends(get_principal)) -> MeOut:
    return MeOut(oid=principal.oid, name=principal.name, roles=sorted(principal.roles))


class DevTokenRequest(BaseModel):
    user: str


@dev_router.get("/users")
def dev_users() -> list[dict]:
    return [{"user": u, "name": n, "roles": list(r)} for u, (n, r) in DEV_USERS.items()]


@dev_router.post("/token")
def dev_token(body: DevTokenRequest, request: Request) -> dict:
    if body.user not in DEV_USERS:
        raise HTTPException(404, "Unknown dev user")
    token = request.app.state.dev_issuer.mint_for(body.user)
    return {"access_token": token, "token_type": "Bearer"}
