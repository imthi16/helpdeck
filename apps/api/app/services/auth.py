"""Auth service: signup (user + org + owner membership), login, user loading."""

import uuid

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.security import hash_password, verify_password
from app.models import ApiKeyType, Membership, MembershipRole, Organization, User
from app.models.tenancy import generate_public_key
from app.schemas.auth import OrgMembership, UserResponse
from app.services import api_keys

__all__ = ["generate_public_key"]


class AuthError(Exception):
    pass


class EmailAlreadyExists(AuthError):
    pass


class InvalidCredentials(AuthError):
    pass


async def signup(
    session: AsyncSession,
    *,
    email: str,
    password: str,
    name: str,
    org_name: str | None = None,
    invite_token: str | None = None,
) -> User:
    """Create a user plus either a new org (owner) or an invited membership.

    With ``invite_token`` the user joins the inviting org at the invited role
    and no org is created; ``org_name`` is required otherwise. Raises
    ``members.InvalidInvitation`` for a bad/expired token.
    """
    from app.services import members as members_service

    normalized = email.strip().lower()
    existing = await session.scalar(select(User).where(User.email == normalized))
    if existing is not None:
        raise EmailAlreadyExists(normalized)

    invitation = None
    if invite_token is not None:
        invitation = await members_service.resolve_invitation(session, invite_token)

    user = User(email=normalized, password_hash=hash_password(password), name=name)
    if invitation is not None:
        session.add(user)
        await session.flush()
        await members_service.accept_invitation(session, invitation, user.id)
    else:
        if not org_name:
            raise AuthError("org_name required without an invite")
        org = Organization(name=org_name, public_key=generate_public_key())
        session.add_all([user, org])
        await session.flush()
        session.add(Membership(org_id=org.id, user_id=user.id, role=MembershipRole.owner))
        # The org's widget key lives in api_keys (5.3). That table is RLS'd, so
        # scope this transaction to the new org before inserting (works under
        # the app role; a superuser test session is unaffected).
        await session.execute(
            text("SELECT set_config('app.current_tenant', :tid, true)"), {"tid": str(org.id)}
        )
        widget_key, _ = api_keys.build_key(
            org_id=org.id,
            name="Widget key",
            key_type=ApiKeyType.widget,
            created_by=user.id,
            token=org.public_key,
        )
        session.add(widget_key)
    await session.commit()
    await session.refresh(user)
    return user


async def authenticate(session: AsyncSession, *, email: str, password: str) -> User:
    normalized = email.strip().lower()
    user = await session.scalar(select(User).where(User.email == normalized))
    if user is None or not verify_password(password, user.password_hash):
        raise InvalidCredentials()
    return user


async def load_user_response(session: AsyncSession, user_id: uuid.UUID) -> UserResponse | None:
    user = await session.scalar(
        select(User)
        .where(User.id == user_id)
        .options(selectinload(User.memberships).selectinload(Membership.organization))
    )
    if user is None:
        return None
    memberships = [
        OrgMembership(
            org_id=membership.org_id,
            org_name=membership.organization.name,
            role=membership.role,
            onboarded=membership.organization.onboarded,
        )
        for membership in user.memberships
    ]
    return UserResponse(id=user.id, email=user.email, name=user.name, memberships=memberships)
