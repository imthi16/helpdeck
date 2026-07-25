"""API key management: list, create (reveal once), revoke.

Keys are a security surface, so — unlike the members roster — even reading them
is admin+, and issuing or revoking a *secret* key is owner-only. Identity session
throughout: `api_keys` sits outside RLS (see docs/NOTES.md).
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.db import async_session_factory
from app.core.logging import get_logger
from app.models import ApiKey, ApiKeyKind, MembershipRole
from app.routers.auth import get_current_user
from app.routers.deps import AdminDep
from app.schemas.api_key import ApiKeyCreate, ApiKeyResponse
from app.schemas.auth import UserResponse
from app.services import api_keys as api_keys_service

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/keys", tags=["api-keys"])


def get_api_keys_sessionmaker() -> async_sessionmaker[AsyncSession]:
    """Identity session: api_keys lives outside RLS (see docs/NOTES.md)."""
    return async_session_factory


SessionmakerDep = Annotated[async_sessionmaker[AsyncSession], Depends(get_api_keys_sessionmaker)]


def _to_response(key: ApiKey, *, secret: str | None = None) -> ApiKeyResponse:
    return ApiKeyResponse(
        id=key.id,
        name=key.name,
        kind=key.kind,
        prefix=key.prefix,
        value=key.value,
        scopes=list(key.scopes),
        last_used_at=key.last_used_at,
        revoked_at=key.revoked_at,
        created_at=key.created_at,
        secret=secret,
    )


def _require_owner_for_secrets(kind: ApiKeyKind, role: MembershipRole) -> None:
    if kind == ApiKeyKind.secret and role != MembershipRole.owner:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="only an owner can issue or revoke secret keys",
        )


@router.get("", response_model=list[ApiKeyResponse])
async def list_api_keys(
    sessionmaker: SessionmakerDep, membership: AdminDep
) -> list[ApiKeyResponse]:
    async with sessionmaker() as session:
        keys = await api_keys_service.list_keys(session, membership.org_id)
    return [_to_response(key) for key in keys]


@router.get("/widget", response_model=ApiKeyResponse)
async def widget_key(sessionmaker: SessionmakerDep, membership: AdminDep) -> ApiKeyResponse:
    """The public key the embed snippet should carry."""
    async with sessionmaker() as session:
        key = await api_keys_service.active_widget_key(session, membership.org_id)
        if key is None:
            # Every org gets one at signup; re-issue if they revoked it.
            key, _ = await api_keys_service.ensure_public_key(session, org_id=membership.org_id)
    return _to_response(key)


@router.post("", response_model=ApiKeyResponse, status_code=status.HTTP_201_CREATED)
async def create_api_key(
    payload: ApiKeyCreate,
    sessionmaker: SessionmakerDep,
    membership: AdminDep,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
) -> ApiKeyResponse:
    _require_owner_for_secrets(payload.kind, membership.role)
    async with sessionmaker() as session:
        key, raw = await api_keys_service.create_key(
            session,
            org_id=membership.org_id,
            kind=payload.kind,
            name=payload.name,
            created_by_user_id=current_user.id,
        )
    logger.info(
        "api_key_created",
        org_id=str(membership.org_id),
        key_id=str(key.id),
        kind=str(key.kind),
    )
    # The only moment a secret exists outside the caller's hands.
    return _to_response(key, secret=raw if key.kind == ApiKeyKind.secret else None)


@router.delete("/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_api_key(
    key_id: uuid.UUID, sessionmaker: SessionmakerDep, membership: AdminDep
) -> None:
    async with sessionmaker() as session:
        existing = await api_keys_service.resolve_key_by_id(
            session, org_id=membership.org_id, key_id=key_id
        )
        if existing is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="key not found")
        _require_owner_for_secrets(existing.kind, membership.role)
        await api_keys_service.revoke_key(session, org_id=membership.org_id, key_id=key_id)
    logger.info("api_key_revoked", org_id=str(membership.org_id), key_id=str(key_id))
