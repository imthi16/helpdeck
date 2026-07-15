"""Members service: invites (token link, no SMTP) and membership management.

All queries here are identity-lane (users/memberships/invitations are not
RLS'd), so every function scopes explicitly by org_id and the router enforces
roles. Sessions are plain app-role sessions; callers own the commit.
"""

import datetime
import hashlib
import secrets
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Invitation, Membership, MembershipRole, User

INVITE_TTL_DAYS = 7


class MembersError(Exception):
    pass


class LastOwnerError(MembersError):
    """The org must always retain at least one owner."""


class InvalidInvitation(MembersError):
    pass


class AlreadyMember(MembersError):
    pass


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_invite_token() -> str:
    return secrets.token_urlsafe(32)


async def list_members(session: AsyncSession, org_id: uuid.UUID) -> list[tuple[Membership, User]]:
    rows = await session.execute(
        select(Membership, User)
        .join(User, User.id == Membership.user_id)
        .where(Membership.org_id == org_id)
        .order_by(Membership.created_at)
    )
    return [(membership, user) for membership, user in rows.all()]


async def count_owners(session: AsyncSession, org_id: uuid.UUID) -> int:
    return (
        await session.scalar(
            select(func.count(Membership.id)).where(
                Membership.org_id == org_id, Membership.role == MembershipRole.owner
            )
        )
    ) or 0


async def get_membership(
    session: AsyncSession, org_id: uuid.UUID, user_id: uuid.UUID
) -> Membership | None:
    return await session.scalar(
        select(Membership).where(Membership.org_id == org_id, Membership.user_id == user_id)
    )


async def change_role(
    session: AsyncSession, membership: Membership, new_role: MembershipRole
) -> None:
    if membership.role == MembershipRole.owner and new_role != MembershipRole.owner:
        if await count_owners(session, membership.org_id) <= 1:
            raise LastOwnerError()
    membership.role = new_role


async def remove_member(session: AsyncSession, membership: Membership) -> None:
    if membership.role == MembershipRole.owner:
        if await count_owners(session, membership.org_id) <= 1:
            raise LastOwnerError()
    await session.delete(membership)


async def create_invite(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    email: str,
    role: MembershipRole,
    invited_by: uuid.UUID,
) -> tuple[Invitation, str]:
    """Create an invite; returns (row, raw token). The raw token is shown once."""
    token = new_invite_token()
    invitation = Invitation(
        org_id=org_id,
        email=email.strip().lower(),
        role=role,
        token_hash=hash_token(token),
        invited_by=invited_by,
        expires_at=datetime.datetime.now(datetime.UTC) + datetime.timedelta(days=INVITE_TTL_DAYS),
    )
    session.add(invitation)
    await session.flush()
    return invitation, token


async def resolve_invitation(session: AsyncSession, token: str) -> Invitation:
    """Look up a redeemable invitation by raw token; raises InvalidInvitation."""
    invitation = await session.scalar(
        select(Invitation).where(Invitation.token_hash == hash_token(token))
    )
    if (
        invitation is None
        or invitation.accepted_at is not None
        or invitation.expires_at < datetime.datetime.now(datetime.UTC)
    ):
        raise InvalidInvitation()
    return invitation


async def accept_invitation(
    session: AsyncSession, invitation: Invitation, user_id: uuid.UUID
) -> Membership:
    existing = await get_membership(session, invitation.org_id, user_id)
    if existing is not None:
        raise AlreadyMember()
    membership = Membership(org_id=invitation.org_id, user_id=user_id, role=invitation.role)
    session.add(membership)
    invitation.accepted_at = datetime.datetime.now(datetime.UTC)
    await session.flush()
    return membership


async def pending_invites(session: AsyncSession, org_id: uuid.UUID) -> list[Invitation]:
    rows = await session.scalars(
        select(Invitation)
        .where(
            Invitation.org_id == org_id,
            Invitation.accepted_at.is_(None),
            Invitation.expires_at > datetime.datetime.now(datetime.UTC),
        )
        .order_by(Invitation.created_at.desc())
    )
    return list(rows.all())
