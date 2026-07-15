"""Onboarding completion: optionally rename the org and flip the onboarded flag."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.db import app_session_factory
from app.models import Organization
from app.routers.auth import get_current_user
from app.schemas.auth import UserResponse
from app.services.auth import load_user_response

router = APIRouter(prefix="/api/v1/onboarding", tags=["onboarding"])


class CompleteOnboardingRequest(BaseModel):
    org_name: str | None = Field(default=None, max_length=255)


def get_onboarding_sessionmaker() -> async_sessionmaker[AsyncSession]:
    # Identity lane: organizations is an identity table (not RLS'd), so this
    # stays a plain app-role session with an explicit commit.
    return app_session_factory


def current_org_id(
    current_user: Annotated[UserResponse, Depends(get_current_user)],
) -> uuid.UUID:
    if not current_user.memberships:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="no organization")
    return current_user.memberships[0].org_id


@router.post("/complete", response_model=UserResponse)
async def complete_onboarding(
    payload: CompleteOnboardingRequest,
    sessionmaker: Annotated[async_sessionmaker[AsyncSession], Depends(get_onboarding_sessionmaker)],
    org_id: Annotated[uuid.UUID, Depends(current_org_id)],
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
    assert refreshed is not None
    return refreshed
