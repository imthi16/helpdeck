"""Auth service: signup (user + org + owner membership), login, user loading."""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.security import hash_password, verify_password
from app.models import Membership, MembershipRole, Organization, User
from app.models.tenancy import generate_public_key
from app.schemas.auth import OrgMembership, UserResponse

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
    org_name: str,
) -> User:
    normalized = email.strip().lower()
    existing = await session.scalar(select(User).where(User.email == normalized))
    if existing is not None:
        raise EmailAlreadyExists(normalized)

    user = User(email=normalized, password_hash=hash_password(password), name=name)
    org = Organization(name=org_name, public_key=generate_public_key())
    session.add_all([user, org])
    await session.flush()
    session.add(Membership(org_id=org.id, user_id=user.id, role=MembershipRole.owner))
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
