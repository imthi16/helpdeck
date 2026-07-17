"""Members API: list, invite (token link), change role, remove, accept.

Users/memberships/invitations are identity tables (no RLS — see the 5.2
migrations), so every query scopes explicitly by the caller's active org and
this router enforces roles: admin+ manages members; assigning or managing a
rank you don't outrank requires owner.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.core.db import app_session_factory
from app.core.deps import (
    MembershipContext,
    MembershipDep,
    require_role,
    role_rank,
)
from app.models import MembershipRole
from app.routers.auth import get_current_user
from app.schemas.auth import UserResponse
from app.schemas.members import (
    AcceptInviteRequest,
    InviteCreatedResponse,
    InviteCreateRequest,
    InviteResponse,
    MemberResponse,
    RoleUpdateRequest,
)
from app.services import members as members_service
from app.services.audit import (
    INVITE_REVOKED,
    MEMBER_INVITED,
    MEMBER_JOINED,
    MEMBER_REMOVED,
    MEMBER_ROLE_CHANGED,
    record_audit,
)
from app.services.demo import block_demo_writes

router = APIRouter(prefix="/api/v1/members", tags=["members"])


def get_members_sessionmaker() -> async_sessionmaker[AsyncSession]:
    # Identity lane: plain app-role sessions with explicit commits.
    return app_session_factory


SessionmakerDep = Annotated[async_sessionmaker[AsyncSession], Depends(get_members_sessionmaker)]
AdminDep = Annotated[MembershipContext, Depends(require_role(MembershipRole.admin))]
demo_guard = Depends(block_demo_writes)  # the public demo org is read-only (7.3)


def _require_manages(caller: MembershipContext, target_role: MembershipRole) -> None:
    """Admins manage roles strictly below their own; owners manage everyone."""
    if caller.role == MembershipRole.owner:
        return
    if role_rank(caller.role) <= role_rank(target_role):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="cannot manage a member at or above your role",
        )


def _to_member_response(membership, user) -> MemberResponse:
    return MemberResponse(
        user_id=user.id,
        email=user.email,
        name=user.name,
        role=membership.role,
        created_at=membership.created_at,
    )


def _to_invite_response(invitation) -> InviteResponse:
    return InviteResponse(
        id=invitation.id,
        email=invitation.email,
        role=invitation.role,
        expires_at=invitation.expires_at,
        created_at=invitation.created_at,
    )


@router.get("", response_model=list[MemberResponse])
async def list_members(
    sessionmaker: SessionmakerDep, membership: MembershipDep
) -> list[MemberResponse]:
    async with sessionmaker() as session:
        rows = await members_service.list_members(session, membership.org_id)
    return [_to_member_response(m, u) for m, u in rows]


@router.patch("/{user_id}", response_model=MemberResponse, dependencies=[demo_guard])
async def change_member_role(
    user_id: uuid.UUID,
    payload: RoleUpdateRequest,
    sessionmaker: SessionmakerDep,
    caller: AdminDep,
) -> MemberResponse:
    async with sessionmaker() as session:
        target = await members_service.get_membership(session, caller.org_id, user_id)
        if target is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="member not found")
        _require_manages(caller, target.role)
        _require_manages(caller, payload.role)
        old_role = target.role
        try:
            await members_service.change_role(session, target, payload.role)
        except members_service.LastOwnerError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="the organization must keep at least one owner",
            ) from exc
        await record_audit(
            session,
            org_id=caller.org_id,
            actor_user_id=caller.user.id,
            action=MEMBER_ROLE_CHANGED,
            target_type="user",
            target_id=str(user_id),
            payload={"from": old_role.value, "to": payload.role.value},
        )
        await session.commit()
        rows = await members_service.list_members(session, caller.org_id)
    for m, u in rows:
        if u.id == user_id:
            return _to_member_response(m, u)
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="member not found")


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[demo_guard])
async def remove_member(
    user_id: uuid.UUID, sessionmaker: SessionmakerDep, caller: AdminDep
) -> None:
    async with sessionmaker() as session:
        target = await members_service.get_membership(session, caller.org_id, user_id)
        if target is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="member not found")
        if user_id != caller.user.id:
            _require_manages(caller, target.role)
        removed_role = target.role
        try:
            await members_service.remove_member(session, target)
        except members_service.LastOwnerError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="the organization must keep at least one owner",
            ) from exc
        await record_audit(
            session,
            org_id=caller.org_id,
            actor_user_id=caller.user.id,
            action=MEMBER_REMOVED,
            target_type="user",
            target_id=str(user_id),
            payload={"role": removed_role.value},
        )
        await session.commit()


@router.get("/invites", response_model=list[InviteResponse])
async def list_invites(sessionmaker: SessionmakerDep, caller: AdminDep) -> list[InviteResponse]:
    async with sessionmaker() as session:
        invites = await members_service.pending_invites(session, caller.org_id)
    return [_to_invite_response(invitation) for invitation in invites]


@router.post(
    "/invites",
    response_model=InviteCreatedResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[demo_guard],
)
async def create_invite(
    payload: InviteCreateRequest, sessionmaker: SessionmakerDep, caller: AdminDep
) -> InviteCreatedResponse:
    _require_manages(caller, payload.role)
    async with sessionmaker() as session:
        invitation, token = await members_service.create_invite(
            session,
            org_id=caller.org_id,
            email=payload.email,
            role=payload.role,
            invited_by=caller.user.id,
        )
        response = InviteCreatedResponse(
            **_to_invite_response(invitation).model_dump(),
            invite_url=f"{get_settings().web_base_url.rstrip('/')}/invite/{token}",
        )
        await record_audit(
            session,
            org_id=caller.org_id,
            actor_user_id=caller.user.id,
            action=MEMBER_INVITED,
            target_type="invitation",
            # No email in the payload: audit rows are append-only and outlive
            # invite revocation/user deletion (PII-free payload contract).
            target_id=str(invitation.id),
            payload={"role": invitation.role.value},
        )
        await session.commit()
    return response


@router.delete(
    "/invites/{invite_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[demo_guard]
)
async def revoke_invite(
    invite_id: uuid.UUID, sessionmaker: SessionmakerDep, caller: AdminDep
) -> None:
    async with sessionmaker() as session:
        invites = await members_service.pending_invites(session, caller.org_id)
        target = next((i for i in invites if i.id == invite_id), None)
        if target is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="invite not found")
        await record_audit(
            session,
            org_id=caller.org_id,
            actor_user_id=caller.user.id,
            action=INVITE_REVOKED,
            target_type="invitation",
            target_id=str(target.id),
        )
        await session.delete(target)
        await session.commit()


@router.post("/invites/accept", response_model=MemberResponse)
async def accept_invite(
    payload: AcceptInviteRequest,
    sessionmaker: SessionmakerDep,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
) -> MemberResponse:
    """Redeem an invite as an already-authenticated user.

    Depends on auth only (not an active membership): a user who lost their
    last org must still be able to join a new one. Logged-out invitees go
    through signup with an ``invite_token``.
    """
    async with sessionmaker() as session:
        try:
            invitation = await members_service.resolve_invitation(session, payload.token)
            created = await members_service.accept_invitation(session, invitation, current_user.id)
        except members_service.InvalidInvitation as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="invalid or expired invite"
            ) from exc
        except members_service.AlreadyMember as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="already a member"
            ) from exc
        await record_audit(
            session,
            org_id=created.org_id,
            actor_user_id=current_user.id,
            action=MEMBER_JOINED,
            target_type="user",
            target_id=str(current_user.id),
            payload={"role": created.role.value, "via": "invite"},
        )
        await session.commit()
        rows = await members_service.list_members(session, created.org_id)
    for m, u in rows:
        if u.id == current_user.id:
            return _to_member_response(m, u)
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="member not found")
