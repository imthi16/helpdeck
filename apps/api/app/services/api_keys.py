"""API key service: token generation, tenant-lane CRUD, pre-tenant resolution.

Tokens: ``pk_…`` (widget, public — plaintext kept for re-display) and
``sk_…`` (secret — hash only, shown once). Widget auth resolves keys through
the SECURITY DEFINER ``resolve_api_key`` SQL function (no tenant is known
yet); ``touch_last_used`` bumps ``last_used_at`` at most once a minute per
key via a Redis SETNX throttle so the auth hot path stays read-only.
"""

import datetime
import hashlib
import secrets
import uuid
from dataclasses import dataclass

import redis.asyncio as redis
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ApiKey, ApiKeyType

TOUCH_THROTTLE_SECONDS = 60


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def generate_token(key_type: ApiKeyType) -> str:
    prefix = "pk" if key_type == ApiKeyType.widget else "sk"
    return f"{prefix}_{secrets.token_urlsafe(24)}"


@dataclass
class ResolvedKey:
    key_id: uuid.UUID
    org_id: uuid.UUID
    key_type: str


async def resolve_key(session: AsyncSession, raw_token: str) -> ResolvedKey | None:
    """Pre-tenant lookup (widget auth) via the SECURITY DEFINER function."""
    row = (
        await session.execute(
            text("SELECT key_id, org_id, key_type FROM resolve_api_key(:h)"),
            {"h": hash_token(raw_token)},
        )
    ).first()
    if row is None:
        return None
    return ResolvedKey(key_id=row.key_id, org_id=row.org_id, key_type=row.key_type)


async def touch_last_used(
    session: AsyncSession, key_id: uuid.UUID, client: redis.Redis | None
) -> None:
    """Bump last_used_at, at most once per TOUCH_THROTTLE_SECONDS per key."""
    if client is not None:
        won = await client.set(
            f"helpdeck:key_used:{key_id}", "1", nx=True, ex=TOUCH_THROTTLE_SECONDS
        )
        if not won:
            return
    await session.execute(text("SELECT touch_api_key(:kid)"), {"kid": str(key_id)})


def build_key(
    *,
    org_id: uuid.UUID,
    name: str,
    key_type: ApiKeyType,
    created_by: uuid.UUID | None = None,
    token: str | None = None,
) -> tuple[ApiKey, str]:
    """Construct an ApiKey row (unsaved) plus its raw token."""
    raw = token or generate_token(key_type)
    key = ApiKey(
        org_id=org_id,
        name=name,
        key_type=key_type.value,
        prefix=raw[:9],
        secret_hash=hash_token(raw),
        public_value=raw if key_type == ApiKeyType.widget else None,
        created_by=created_by,
    )
    return key, raw


async def list_keys(session: AsyncSession, org_id: uuid.UUID) -> list[ApiKey]:
    rows = await session.scalars(
        select(ApiKey).where(ApiKey.org_id == org_id).order_by(ApiKey.created_at.desc())
    )
    return list(rows.all())


async def revoke_key(session: AsyncSession, org_id: uuid.UUID, key_id: uuid.UUID) -> ApiKey | None:
    key = await session.get(ApiKey, key_id)
    if key is None or key.org_id != org_id or key.revoked_at is not None:
        return None
    key.revoked_at = datetime.datetime.now(datetime.UTC)
    return key
