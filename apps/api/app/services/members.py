"""Members service: roster, invites, role changes, removal.

All of it operates on identity tables (``users``, ``memberships``,
``invitations``), so callers pass the privileged identity session — the app role
has no grants there and RLS does not apply. See docs/NOTES.md.

Two invariants live here rather than in the router, so they hold for every
caller: an org always keeps at least one owner, and only an owner may grant or
revoke the ``owner`` role.
"""

import datetime as dt
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.security import hash_password
from app.models import Invitation, Membership, MembershipRole, User
from app.models.tenancy import INVITE_TTL_DAYS, generate_invite_token, hash_invite_token


class MemberError(Exception):
    """Base for member-management failures the router maps to HTTP codes."""


class MemberNotFound(MemberError):
    pass


class LastOwner(MemberError):
    """The org would be left without an owner."""


class OwnerRoleRequired(MemberError):
    """Only an owner may grant or revoke ownership."""


class AlreadyMember(MemberError):
    pass


class InviteInvalid(MemberError):
    """Unknown, expired, or already-redeemed token."""


class InviteEmailMismatch(MemberError):
    """The signed-in user is not the person who was invited."""


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


async def find_user_by_email(session: AsyncSession, email: str) -> User | None:
    return await session.scalar(select(User).where(User.email == email.strip().lower()))


async def list_members(session: AsyncSession, org_id: uuid.UUID) -> list[tuple[Membership, User]]:
    rows = await session.execute(
        select(Membership, User)
        .join(User, User.id == Membership.user_id)
        .where(Membership.org_id == org_id)
        .order_by(Membership.created_at)
    )
    return [(membership, user) for membership, user in rows.all()]


async def _count_owners(session: AsyncSession, org_id: uuid.UUID) -> int:
    return (
        await session.scalar(
            select(func.count(Membership.id)).where(
                Membership.org_id == org_id, Membership.role == MembershipRole.owner
            )
        )
    ) or 0


async def _membership_for(
    session: AsyncSession, org_id: uuid.UUID, user_id: uuid.UUID
) -> Membership:
    membership = await session.scalar(
        select(Membership).where(Membership.org_id == org_id, Membership.user_id == user_id)
    )
    if membership is None:
        raise MemberNotFound(str(user_id))
    return membership


async def change_role(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    new_role: MembershipRole,
    actor_role: MembershipRole,
) -> Membership:
    membership = await _membership_for(session, org_id, user_id)
    # Ownership may only be handed out or taken away by an owner.
    if MembershipRole.owner in (new_role, membership.role) and actor_role != MembershipRole.owner:
        raise OwnerRoleRequired()
    if (
        membership.role == MembershipRole.owner
        and new_role != MembershipRole.owner
        and await _count_owners(session, org_id) <= 1
    ):
        raise LastOwner()
    membership.role = new_role
    await session.commit()
    return membership


async def remove_member(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    actor_role: MembershipRole,
) -> None:
    membership = await _membership_for(session, org_id, user_id)
    if membership.role == MembershipRole.owner:
        if actor_role != MembershipRole.owner:
            raise OwnerRoleRequired()
        if await _count_owners(session, org_id) <= 1:
            raise LastOwner()
    await session.delete(membership)
    await session.commit()


async def create_invite(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    email: str,
    role: MembershipRole,
    actor_user_id: uuid.UUID,
    actor_role: MembershipRole,
) -> tuple[Invitation, str]:
    """Create (or replace) a pending invite. Returns the row and the raw token.

    The raw token is returned once, for the invite link — only its hash is
    stored, so a leaked database gives no usable invites.
    """
    if role == MembershipRole.owner and actor_role != MembershipRole.owner:
        raise OwnerRoleRequired()

    normalized = email.strip().lower()
    existing_user = await session.scalar(select(User).where(User.email == normalized))
    if existing_user is not None:
        already = await session.scalar(
            select(Membership).where(
                Membership.org_id == org_id, Membership.user_id == existing_user.id
            )
        )
        if already is not None:
            raise AlreadyMember(normalized)

    # Re-inviting replaces the pending invite (the partial unique index allows
    # only one), which also rotates the token.
    pending = await session.scalar(
        select(Invitation).where(
            Invitation.org_id == org_id,
            Invitation.email == normalized,
            Invitation.accepted_at.is_(None),
        )
    )
    if pending is not None:
        await session.delete(pending)
        await session.flush()

    token = generate_invite_token()
    invitation = Invitation(
        org_id=org_id,
        email=normalized,
        role=role,
        token_hash=hash_invite_token(token),
        invited_by_user_id=actor_user_id,
        expires_at=_now() + dt.timedelta(days=INVITE_TTL_DAYS),
    )
    session.add(invitation)
    await session.commit()
    return invitation, token


async def list_invites(session: AsyncSession, org_id: uuid.UUID) -> list[Invitation]:
    rows = await session.execute(
        select(Invitation)
        .where(Invitation.org_id == org_id, Invitation.accepted_at.is_(None))
        .order_by(Invitation.created_at.desc())
    )
    return list(rows.scalars().all())


async def revoke_invite(session: AsyncSession, *, org_id: uuid.UUID, invite_id: uuid.UUID) -> None:
    invitation = await session.scalar(
        select(Invitation).where(
            Invitation.id == invite_id,
            Invitation.org_id == org_id,
            Invitation.accepted_at.is_(None),
        )
    )
    if invitation is None:
        raise MemberNotFound(str(invite_id))
    await session.delete(invitation)
    await session.commit()


async def _load_pending_invite(session: AsyncSession, token: str) -> Invitation:
    invitation = await session.scalar(
        select(Invitation)
        .where(Invitation.token_hash == hash_invite_token(token))
        .options(selectinload(Invitation.organization))
    )
    if invitation is None or invitation.accepted_at is not None:
        raise InviteInvalid("unknown or already accepted invite")
    if invitation.expires_at <= _now():
        raise InviteInvalid("invite expired")
    return invitation


async def preview_invite(session: AsyncSession, token: str) -> Invitation:
    """Validate a token without redeeming it (for the accept page)."""
    return await _load_pending_invite(session, token)


async def accept_invite(
    session: AsyncSession,
    *,
    token: str,
    user_id: uuid.UUID | None = None,
    name: str | None = None,
    password: str | None = None,
) -> User:
    """Redeem an invite, creating the user when there is no session yet.

    With ``user_id`` the signed-in user must be the invited email. Without it,
    ``password`` creates the account for that email.
    """
    invitation = await _load_pending_invite(session, token)

    if user_id is not None:
        user = await session.get(User, user_id)
        if user is None:
            raise InviteInvalid("user not found")
        if user.email != invitation.email:
            raise InviteEmailMismatch(invitation.email)
    else:
        user = await session.scalar(select(User).where(User.email == invitation.email))
        if user is None:
            if not password:
                raise InviteInvalid("password required to create an account")
            user = User(
                email=invitation.email,
                password_hash=hash_password(password),
                name=name or "",
            )
            session.add(user)
            await session.flush()
        else:
            # The account exists — it must sign in first, so its own password is
            # never bypassed by holding an invite link.
            raise InviteEmailMismatch(invitation.email)

    existing = await session.scalar(
        select(Membership).where(
            Membership.org_id == invitation.org_id, Membership.user_id == user.id
        )
    )
    if existing is None:
        session.add(Membership(org_id=invitation.org_id, user_id=user.id, role=invitation.role))
    invitation.accepted_at = _now()
    await session.commit()
    return user
