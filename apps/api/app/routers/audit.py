"""Audit log viewer: read-only, owners and admins only.

The app role can read and append but not modify (see the 5.4 migration), so this
router is genuinely a viewer — there is no endpoint, and no privilege, to edit
history.
"""

import datetime as dt
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.db import app_session_factory
from app.routers.deps import AdminDep
from app.schemas.audit import AuditEventResponse
from app.services import audit as audit_service

router = APIRouter(prefix="/api/v1/audit", tags=["audit"])


def get_audit_sessionmaker() -> async_sessionmaker[AsyncSession]:
    """App-role session: audit_logs has no RLS but the app role is granted only
    SELECT + INSERT, which is exactly what this viewer needs."""
    return app_session_factory


SessionmakerDep = Annotated[async_sessionmaker[AsyncSession], Depends(get_audit_sessionmaker)]


@router.get("", response_model=list[AuditEventResponse])
async def list_audit_events(
    sessionmaker: SessionmakerDep,
    membership: AdminDep,
    limit: Annotated[int, Query(ge=1, le=audit_service.MAX_PAGE_SIZE)] = (
        audit_service.DEFAULT_PAGE_SIZE
    ),
    before: dt.datetime | None = None,
) -> list[AuditEventResponse]:
    async with sessionmaker() as session:
        events = await audit_service.list_events(
            session, membership.org_id, limit=limit, before=before
        )
    return [
        AuditEventResponse(
            id=event.id,
            action=event.action,
            actor_email=event.actor_email,
            target_type=event.target_type,
            target_id=event.target_id,
            metadata=event.meta,
            created_at=event.created_at,
        )
        for event in events
    ]
