"""Shared request dependencies: active-org resolution and role enforcement.

Every dashboard endpoint resolves a ``MembershipContext`` (who + which org +
role) through ``current_membership``; write endpoints layer ``require_role``
on top. Roles are strictly nested (viewer < agent < admin < owner), so a
minimum-rank check is sufficient — no per-permission sets.
"""

import uuid
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status

from app.models import MembershipRole
from app.routers.auth import get_current_user
from app.schemas.auth import OrgMembership, UserResponse

_RANK: dict[MembershipRole, int] = {
    MembershipRole.viewer: 0,
    MembershipRole.agent: 1,
    MembershipRole.admin: 2,
    MembershipRole.owner: 3,
}


@dataclass
class MembershipContext:
    user: UserResponse
    org_id: uuid.UUID
    role: MembershipRole


def _resolve(user: UserResponse, x_org_id: str | None) -> OrgMembership:
    if not user.memberships:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="no organization")
    if x_org_id is None:
        return user.memberships[0]
    try:
        wanted = uuid.UUID(x_org_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="invalid X-Org-Id"
        ) from exc
    for membership in user.memberships:
        if membership.org_id == wanted:
            return membership
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN, detail="not a member of this organization"
    )


async def current_membership(
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    x_org_id: Annotated[str | None, Header(alias="X-Org-Id")] = None,
) -> MembershipContext:
    """The caller's active org membership.

    Defaults to the first membership; an ``X-Org-Id`` header selects another
    org the caller belongs to (403 if they don't).
    """
    membership = _resolve(current_user, x_org_id)
    return MembershipContext(user=current_user, org_id=membership.org_id, role=membership.role)


def require_role(minimum: MembershipRole):
    """Dependency factory: the active membership must be at least ``minimum``."""

    async def dependency(
        membership: Annotated[MembershipContext, Depends(current_membership)],
    ) -> MembershipContext:
        if _RANK[membership.role] < _RANK[minimum]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"requires {minimum.value} role or higher",
            )
        return membership

    return dependency


def role_rank(role: MembershipRole) -> int:
    return _RANK[role]


MembershipDep = Annotated[MembershipContext, Depends(current_membership)]
