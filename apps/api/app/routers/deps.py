"""Shared router dependencies: the caller's org, role, and tenant-scoped session.

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

RLS (5.1) keeps other tenants out; ``require_role`` (5.2) governs what a member
*inside* the tenant may do. Reads stay open to every role — viewer is read-only,
not blind — so only write routes carry a role dependency.
"""

import uuid
from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.db import SessionFactory, TenantSessionFactory, get_app_session_factory
from app.models import MembershipRole
from app.routers.auth import get_current_user
from app.schemas.auth import OrgMembership, UserResponse

# Role bundles used by the routers. `agent` covers conversation work; `admin`
# covers knowledge base, members and settings; `owner` covers org-level acts
# (deleting the org, keys, granting ownership).
ADMIN_ROLES = (MembershipRole.owner, MembershipRole.admin)
AGENT_ROLES = (MembershipRole.owner, MembershipRole.admin, MembershipRole.agent)
OWNER_ROLES = (MembershipRole.owner,)


def current_membership(
    current_user: Annotated[UserResponse, Depends(get_current_user)],
) -> OrgMembership:
    """The caller's membership in the active org (its first, as elsewhere)."""
    if not current_user.memberships:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="no organization")
    return current_user.memberships[0]


def current_org_id(
    membership: Annotated[OrgMembership, Depends(current_membership)],
) -> uuid.UUID:
    return membership.org_id


def require_role(
    *allowed: MembershipRole,
) -> Callable[[OrgMembership], OrgMembership]:
    """Dependency factory: 403 unless the caller's role is in ``allowed``."""

    def dependency(
        membership: Annotated[OrgMembership, Depends(current_membership)],
    ) -> OrgMembership:
        if membership.role not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"role '{membership.role}' cannot perform this action",
            )
        return membership

    return dependency


def tenant_db(
    org_id: Annotated[uuid.UUID, Depends(current_org_id)],
    session_factory: Annotated[async_sessionmaker[AsyncSession], Depends(get_app_session_factory)],
) -> SessionFactory:
    return TenantSessionFactory(org_id, session_factory=session_factory)


OrgDep = Annotated[uuid.UUID, Depends(current_org_id)]
TenantDbDep = Annotated[SessionFactory, Depends(tenant_db)]
AppSessionFactoryDep = Annotated[async_sessionmaker[AsyncSession], Depends(get_app_session_factory)]

# Any member (all four roles) — for reads.
MemberDep = Annotated[OrgMembership, Depends(current_membership)]
# Write bundles.
AgentDep = Annotated[OrgMembership, Depends(require_role(*AGENT_ROLES))]
AdminDep = Annotated[OrgMembership, Depends(require_role(*ADMIN_ROLES))]
OwnerDep = Annotated[OrgMembership, Depends(require_role(*OWNER_ROLES))]
