"""API keys management (task 5.3). Owner-only; tenant lane (RLS enforced)."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.db import app_session_factory, tenant_session
from app.core.deps import MembershipContext, require_role
from app.models import ApiKey, MembershipRole
from app.schemas.api_keys import ApiKeyCreatedResponse, ApiKeyCreateRequest, ApiKeyResponse
from app.services import api_keys as keys_service
from app.services.audit import KEY_CREATED, KEY_REVOKED, record_audit
from app.services.demo import block_demo_writes

router = APIRouter(prefix="/api/v1/keys", tags=["keys"])


def get_keys_sessionmaker() -> async_sessionmaker[AsyncSession]:
    return app_session_factory


SessionmakerDep = Annotated[async_sessionmaker[AsyncSession], Depends(get_keys_sessionmaker)]
OwnerDep = Annotated[MembershipContext, Depends(require_role(MembershipRole.owner))]
demo_guard = Depends(block_demo_writes)  # the public demo org is read-only (7.3)


def _to_response(key: ApiKey) -> ApiKeyResponse:
    return ApiKeyResponse(
        id=key.id,
        name=key.name,
        key_type=key.key_type,
        prefix=key.prefix,
        public_value=key.public_value,
        scopes=list(key.scopes or []),
        last_used_at=key.last_used_at,
        revoked_at=key.revoked_at,
        created_at=key.created_at,
    )


@router.get("", response_model=list[ApiKeyResponse])
async def list_keys(sessionmaker: SessionmakerDep, caller: OwnerDep) -> list[ApiKeyResponse]:
    async with tenant_session(caller.org_id, session_factory=sessionmaker) as session:
        keys = await keys_service.list_keys(session, caller.org_id)
    return [_to_response(key) for key in keys]


@router.post(
    "",
    response_model=ApiKeyCreatedResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[demo_guard],
)
async def create_key(
    payload: ApiKeyCreateRequest, sessionmaker: SessionmakerDep, caller: OwnerDep
) -> ApiKeyCreatedResponse:
    key, token = keys_service.build_key(
        org_id=caller.org_id,
        name=payload.name,
        key_type=payload.key_type,
        created_by=caller.user.id,
    )
    async with tenant_session(caller.org_id, session_factory=sessionmaker) as session:
        session.add(key)
        await session.flush()
        await record_audit(
            session,
            org_id=caller.org_id,
            actor_user_id=caller.user.id,
            action=KEY_CREATED,
            target_type="api_key",
            target_id=str(key.id),
            payload={"name": key.name, "key_type": key.key_type},
        )
        response = ApiKeyCreatedResponse(**_to_response(key).model_dump(), token=token)
    return response


@router.delete("/{key_id}", response_model=ApiKeyResponse, dependencies=[demo_guard])
async def revoke_key(
    key_id: uuid.UUID, sessionmaker: SessionmakerDep, caller: OwnerDep
) -> ApiKeyResponse:
    async with tenant_session(caller.org_id, session_factory=sessionmaker) as session:
        key = await keys_service.revoke_key(session, caller.org_id, key_id)
        if key is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="key not found")
        await record_audit(
            session,
            org_id=caller.org_id,
            actor_user_id=caller.user.id,
            action=KEY_REVOKED,
            target_type="api_key",
            target_id=str(key.id),
            payload={"name": key.name, "key_type": key.key_type},
        )
        return _to_response(key)
