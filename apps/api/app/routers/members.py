"""Members management: roster, invites, role changes, removal.

Reads are open to every role; every mutation requires admin+ (and ownership acts
require an owner — enforced in ``app.services.members``). These handlers touch
identity tables only, so they use the identity session, not the tenant one.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.core.db import async_session_factory
from app.core.logging import get_logger
from app.models import AuditAction, User
from app.routers.auth import get_current_user
from app.routers.deps import ADMIN_ROLES, AdminDep, AppSessionFactoryDep, MemberDep, require_role
from app.schemas.auth import UserResponse
from app.schemas.member import (
    InviteRequest,
    InviteResponse,
    MemberResponse,
    RoleUpdateRequest,
)
from app.services import audit as audit_service
from app.services import members as members_service

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/members", tags=["members"])


def get_members_sessionmaker() -> async_sessionmaker[AsyncSession]:
    """Identity session: members/invites live outside RLS (see docs/NOTES.md)."""
    return async_session_factory


SessionmakerDep = Annotated[async_sessionmaker[AsyncSession], Depends(get_members_sessionmaker)]


def invite_url(token: str) -> str:
    return f"{get_settings().web_app_url.rstrip('/')}/invite/{token}"


def _handle(exc: members_service.MemberError) -> HTTPException:
    """Map service invariants onto HTTP codes."""
    if isinstance(exc, members_service.MemberNotFound):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")
    if isinstance(exc, members_service.OwnerRoleRequired):
        return HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="only an owner can grant or revoke ownership",
        )
    if isinstance(exc, members_service.LastOwner):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="an organization must keep at least one owner",
        )
    if isinstance(exc, members_service.AlreadyMember):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="already a member of this organization"
        )
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.get("", response_model=list[MemberResponse])
async def list_members(
    sessionmaker: SessionmakerDep, membership: MemberDep
) -> list[MemberResponse]:
    async with sessionmaker() as session:
        rows = await members_service.list_members(session, membership.org_id)
    return [
        MemberResponse(
            user_id=user.id,
            email=user.email,
            name=user.name,
            role=member.role,
            joined_at=member.created_at,
        )
        for member, user in rows
    ]


@router.get("/invites", response_model=list[InviteResponse])
async def list_invites(
    sessionmaker: SessionmakerDep, membership: MemberDep
) -> list[InviteResponse]:
    async with sessionmaker() as session:
        invites = await members_service.list_invites(session, membership.org_id)
    return [
        InviteResponse(
            id=invite.id,
            email=invite.email,
            role=invite.role,
            expires_at=invite.expires_at,
            created_at=invite.created_at,
        )
        for invite in invites
    ]


@router.post("/invites", response_model=InviteResponse, status_code=status.HTTP_201_CREATED)
async def create_invite(
    payload: InviteRequest,
    sessionmaker: SessionmakerDep,
    app_session_factory: AppSessionFactoryDep,
    membership: AdminDep,
    request: Request,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
) -> InviteResponse:
    async with sessionmaker() as session:
        try:
            invitation, token = await members_service.create_invite(
                session,
                org_id=membership.org_id,
                email=payload.email,
                role=payload.role,
                actor_user_id=current_user.id,
                actor_role=membership.role,
            )
        except members_service.MemberError as exc:
            raise _handle(exc) from exc

    url = invite_url(token)
    # No mailer yet: the inviter copies this link. Never log the token itself.
    logger.info(
        "member_invited",
        org_id=str(membership.org_id),
        invite_id=str(invitation.id),
        role=str(invitation.role),
    )
    await audit_service.record(
        AuditAction.member_invited,
        org_id=membership.org_id,
        actor=current_user,
        target_type="invitation",
        target_id=invitation.id,
        metadata={"email": invitation.email, "role": str(invitation.role)},
        request=request,
        session_factory=app_session_factory,
    )
    return InviteResponse(
        id=invitation.id,
        email=invitation.email,
        role=invitation.role,
        expires_at=invitation.expires_at,
        created_at=invitation.created_at,
        invite_url=url,
    )


@router.delete(
    "/invites/{invite_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_role(*ADMIN_ROLES))],
)
async def revoke_invite(
    invite_id: uuid.UUID,
    sessionmaker: SessionmakerDep,
    app_session_factory: AppSessionFactoryDep,
    membership: MemberDep,
    request: Request,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
) -> None:
    async with sessionmaker() as session:
        try:
            await members_service.revoke_invite(
                session, org_id=membership.org_id, invite_id=invite_id
            )
        except members_service.MemberError as exc:
            raise _handle(exc) from exc
    await audit_service.record(
        AuditAction.member_invite_revoked,
        org_id=membership.org_id,
        actor=current_user,
        target_type="invitation",
        target_id=invite_id,
        request=request,
        session_factory=app_session_factory,
    )


@router.patch("/{user_id}", response_model=MemberResponse)
async def update_member_role(
    user_id: uuid.UUID,
    payload: RoleUpdateRequest,
    sessionmaker: SessionmakerDep,
    app_session_factory: AppSessionFactoryDep,
    membership: AdminDep,
    request: Request,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
) -> MemberResponse:
    async with sessionmaker() as session:
        try:
            updated = await members_service.change_role(
                session,
                org_id=membership.org_id,
                user_id=user_id,
                new_role=payload.role,
                actor_role=membership.role,
            )
        except members_service.MemberError as exc:
            raise _handle(exc) from exc
        user = await session.get(User, user_id)

    assert user is not None
    await audit_service.record(
        AuditAction.member_role_changed,
        org_id=membership.org_id,
        actor=current_user,
        target_type="user",
        target_id=user_id,
        metadata={"email": user.email, "role": str(updated.role)},
        request=request,
        session_factory=app_session_factory,
    )
    return MemberResponse(
        user_id=user.id,
        email=user.email,
        name=user.name,
        role=updated.role,
        joined_at=updated.created_at,
    )


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_member(
    user_id: uuid.UUID,
    sessionmaker: SessionmakerDep,
    app_session_factory: AppSessionFactoryDep,
    membership: AdminDep,
    request: Request,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
) -> None:
    async with sessionmaker() as session:
        # Read the email before the membership goes, so the trail names a person
        # even if that user is later deleted entirely.
        removed = await session.get(User, user_id)
        removed_email = removed.email if removed is not None else None
        try:
            await members_service.remove_member(
                session,
                org_id=membership.org_id,
                user_id=user_id,
                actor_role=membership.role,
            )
        except members_service.MemberError as exc:
            raise _handle(exc) from exc
    await audit_service.record(
        AuditAction.member_removed,
        org_id=membership.org_id,
        actor=current_user,
        target_type="user",
        target_id=user_id,
        metadata={"email": removed_email} if removed_email else {},
        request=request,
        session_factory=app_session_factory,
    )
