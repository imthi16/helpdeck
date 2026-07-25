"""Onboarding completion: optionally rename the org and flip the onboarded flag."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.db import async_session_factory
from app.models import AuditAction, Organization
from app.routers.auth import get_current_user
from app.routers.deps import ADMIN_ROLES, AppSessionFactoryDep, current_org_id, require_role
from app.schemas.auth import UserResponse
from app.services import audit as audit_service
from app.services.auth import load_user_response

router = APIRouter(prefix="/api/v1/onboarding", tags=["onboarding"])


class CompleteOnboardingRequest(BaseModel):
    org_name: str | None = Field(default=None, max_length=255)


def get_onboarding_sessionmaker() -> async_sessionmaker[AsyncSession]:
    """Identity session: this handler only touches ``organizations``/``users``."""
    return async_session_factory


@router.post(
    "/complete",
    response_model=UserResponse,
    dependencies=[Depends(require_role(*ADMIN_ROLES))],
)
async def complete_onboarding(
    payload: CompleteOnboardingRequest,
    sessionmaker: Annotated[async_sessionmaker[AsyncSession], Depends(get_onboarding_sessionmaker)],
    app_session_factory: AppSessionFactoryDep,
    org_id: Annotated[uuid.UUID, Depends(current_org_id)],
    request: Request,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
) -> UserResponse:
    async with sessionmaker() as session:
        org = await session.get(Organization, org_id)
        if org is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="org not found")
        if payload.org_name and payload.org_name.strip():
            org.name = payload.org_name.strip()
        org.onboarded = True
        await session.commit()
        refreshed = await load_user_response(session, current_user.id)
        org_name = org.name
    assert refreshed is not None
    await audit_service.record(
        AuditAction.settings_updated,
        org_id=org_id,
        actor=current_user,
        target_type="organization",
        target_id=org_id,
        metadata={"name": org_name},
        request=request,
        session_factory=app_session_factory,
    )
    return refreshed
