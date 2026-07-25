"""Shared router dependencies: the caller's org and its tenant-scoped session.

Two session kinds exist, and the split is deliberate:

* **identity** — the privileged ``async_session_factory``. ``users``,
  ``memberships`` and ``organizations`` have no RLS (auth queries them
  cross-tenant, before any org context exists) and the app role has no grants on
  them, so auth/onboarding/public-key lookups keep using it.
* **tenant** — ``tenant_db`` below: an app-role (``helpdeck_app``) session with
  ``app.current_tenant`` set, which RLS is enforced against. Every handler that
  touches ``documents``/``chunks``/``conversations``/``messages``/``escalations``
  goes through it.

``get_app_session_factory`` is the single DI seam for the app-role sessionmaker
(tests override it with a loop-local app-role engine).
"""

import uuid
from typing import Annotated

from fastapi import Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.db import SessionFactory, TenantSessionFactory, app_session_factory
from app.routers.auth import get_current_user
from app.schemas.auth import UserResponse


def current_org_id(
    current_user: Annotated[UserResponse, Depends(get_current_user)],
) -> uuid.UUID:
    if not current_user.memberships:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="no organization")
    return current_user.memberships[0].org_id


def get_app_session_factory() -> async_sessionmaker[AsyncSession]:
    """The restricted-role sessionmaker the request path serves traffic with."""
    return app_session_factory


def tenant_db(
    org_id: Annotated[uuid.UUID, Depends(current_org_id)],
    session_factory: Annotated[async_sessionmaker[AsyncSession], Depends(get_app_session_factory)],
) -> SessionFactory:
    return TenantSessionFactory(org_id, session_factory=session_factory)


OrgDep = Annotated[uuid.UUID, Depends(current_org_id)]
TenantDbDep = Annotated[SessionFactory, Depends(tenant_db)]
AppSessionFactoryDep = Annotated[async_sessionmaker[AsyncSession], Depends(get_app_session_factory)]
