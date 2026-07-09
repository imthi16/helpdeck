"""Redis exact-match response cache.

Keyed on ``(org_id, normalized_query, kb_version)`` so an identical question for
an org with an unchanged knowledge base is served without re-running the agent.
The ``kb_version`` component means any re-ingest naturally invalidates stale
answers. A bypass flag lets the playground always hit the live agent.
"""

import hashlib
import json
import re
import uuid
from dataclasses import asdict, dataclass
from typing import Any

import redis.asyncio as redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.models import Document, DocumentStatus

_WHITESPACE = re.compile(r"\s+")


def normalize_query(query: str) -> str:
    return _WHITESPACE.sub(" ", query.strip().lower())


def cache_key(org_id: str, query: str, kb_version: str) -> str:
    digest = hashlib.sha256(
        f"{org_id}\x00{normalize_query(query)}\x00{kb_version}".encode()
    ).hexdigest()
    return f"helpdeck:answer:{digest}"


@dataclass
class CachedAnswer:
    content: str
    citations: list[dict[str, Any]]
    confidence: float | None
    escalated: bool
    model_used: str | None = None

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, raw: str) -> "CachedAnswer":
        return cls(**json.loads(raw))


class ResponseCache:
    def __init__(self, client: redis.Redis, *, ttl_seconds: int | None = None) -> None:
        self._client = client
        self._ttl = (
            ttl_seconds if ttl_seconds is not None else (get_settings().response_cache_ttl_seconds)
        )

    async def get(self, org_id: str, query: str, kb_version: str) -> CachedAnswer | None:
        raw = await self._client.get(cache_key(org_id, query, kb_version))
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode()
        return CachedAnswer.from_json(raw)

    async def set(self, org_id: str, query: str, kb_version: str, answer: CachedAnswer) -> None:
        await self._client.set(cache_key(org_id, query, kb_version), answer.to_json(), ex=self._ttl)


async def compute_kb_version(
    sessionmaker: async_sessionmaker[AsyncSession], org_id: uuid.UUID
) -> str:
    """A token that changes whenever the org's ready knowledge base changes."""
    async with sessionmaker() as session:
        count, latest = (
            await session.execute(
                select(func.count(Document.id), func.max(Document.updated_at)).where(
                    Document.org_id == org_id, Document.status == DocumentStatus.ready
                )
            )
        ).one()
    return f"{count}:{latest.isoformat() if latest else 'none'}"


def get_redis() -> redis.Redis:
    return redis.from_url(get_settings().redis_url)
