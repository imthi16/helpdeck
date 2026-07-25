"""API key service: issue, list, revoke, and resolve keys.

Keys live on identity tables (see ``ApiKey``), so callers pass the privileged
identity session — the app role has no grants there and RLS does not apply.

Authentication always goes through ``resolve_key``, which looks a key up by its
SHA-256 hash. A revoked key resolves to ``None`` on the very next request: there
is no cache in front of this, so revocation is immediate.
"""

import datetime as dt
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import ApiKey, ApiKeyKind
from app.models.tenancy import (
    DEFAULT_SCOPES,
    generate_public_key,
    generate_secret_key,
    sha256_hex,
)

#: How stale ``last_used_at`` may get before we write again. Without this the
#: widget would issue a write on every single request.
LAST_USED_THROTTLE = dt.timedelta(minutes=1)

PREFIX_LENGTH = 11


class ApiKeyError(Exception):
    pass


class ApiKeyNotFound(ApiKeyError):
    pass


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def generate_key(kind: ApiKeyKind) -> str:
    return generate_public_key() if kind == ApiKeyKind.public else generate_secret_key()


async def create_key(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    kind: ApiKeyKind,
    name: str = "",
    created_by_user_id: uuid.UUID | None = None,
    raw: str | None = None,
    commit: bool = True,
) -> tuple[ApiKey, str]:
    """Issue a key. Returns the row and the raw value (shown once for secrets).

    ``raw`` is only for seeding fixed demo keys; normal callers omit it.
    ``commit=False`` lets a caller fold this into a larger unit of work (signup
    creates the org, membership and widget key in one transaction).
    """
    raw = raw or generate_key(kind)
    key = ApiKey(
        org_id=org_id,
        name=name or ("Widget key" if kind == ApiKeyKind.public else "Secret key"),
        kind=kind,
        prefix=raw[:PREFIX_LENGTH],
        key_hash=sha256_hex(raw),
        # Public keys are not secrets — they sit in page HTML and the dashboard
        # has to render them. Secrets are never stored in the clear.
        value=raw if kind == ApiKeyKind.public else None,
        scopes=list(DEFAULT_SCOPES[kind]),
        created_by_user_id=created_by_user_id,
    )
    session.add(key)
    if commit:
        await session.commit()
    else:
        await session.flush()
    return key, raw


async def list_keys(session: AsyncSession, org_id: uuid.UUID) -> list[ApiKey]:
    rows = await session.execute(
        select(ApiKey).where(ApiKey.org_id == org_id).order_by(ApiKey.created_at.desc())
    )
    return list(rows.scalars().all())


async def active_widget_key(session: AsyncSession, org_id: uuid.UUID) -> ApiKey | None:
    """The public key the embed snippet should use (newest active one)."""
    return await session.scalar(
        select(ApiKey)
        .where(
            ApiKey.org_id == org_id,
            ApiKey.kind == ApiKeyKind.public,
            ApiKey.revoked_at.is_(None),
        )
        .order_by(ApiKey.created_at.desc())
        .limit(1)
    )


async def resolve_key_by_id(
    session: AsyncSession, *, org_id: uuid.UUID, key_id: uuid.UUID
) -> ApiKey | None:
    return await session.scalar(select(ApiKey).where(ApiKey.id == key_id, ApiKey.org_id == org_id))


async def revoke_key(session: AsyncSession, *, org_id: uuid.UUID, key_id: uuid.UUID) -> ApiKey:
    key = await resolve_key_by_id(session, org_id=org_id, key_id=key_id)
    if key is None:
        raise ApiKeyNotFound(str(key_id))
    if key.revoked_at is None:
        key.revoked_at = _now()
        await session.commit()
    return key


async def resolve_key(session: AsyncSession, raw: str) -> ApiKey | None:
    """The active key matching ``raw``, with its org loaded, else ``None``."""
    if not raw:
        return None
    return await session.scalar(
        select(ApiKey)
        .where(ApiKey.key_hash == sha256_hex(raw), ApiKey.revoked_at.is_(None))
        .options(selectinload(ApiKey.organization))
    )


async def touch_last_used(session: AsyncSession, key: ApiKey) -> None:
    """Record usage, at most once per ``LAST_USED_THROTTLE`` per key."""
    now = _now()
    if key.last_used_at is not None and now - key.last_used_at < LAST_USED_THROTTLE:
        return
    key.last_used_at = now
    await session.commit()


async def ensure_public_key(
    session: AsyncSession, *, org_id: uuid.UUID, raw: str | None = None
) -> tuple[ApiKey, str]:
    """Give a new org its widget key (used by signup and the seed scripts)."""
    key, value = await create_key(
        session, org_id=org_id, kind=ApiKeyKind.public, name="Widget key", raw=raw
    )
    return key, value
