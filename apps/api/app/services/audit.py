"""Audit trail writer and reader.

The writer runs on the **app role**, which holds ``SELECT, INSERT`` on
``audit_logs`` and nothing else — so the code path that records history has no
privilege to rewrite it. ``audit_logs`` has no RLS, so no tenant scope is needed
on the session; reads filter by ``org_id`` explicitly.

Recording never breaks the request it describes: a failed write is logged and
swallowed, because losing an audit row is bad but failing an invite the user
already completed is worse. The failure is visible in the logs either way.
"""

import datetime as dt
import uuid
from typing import Any

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.db import app_session_factory
from app.core.logging import get_logger
from app.models import AuditAction, AuditLog
from app.schemas.auth import UserResponse

logger = get_logger(__name__)

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200


def client_ip(request: Request | None) -> str | None:
    if request is None:
        return None
    # Trust the proxy header only for its first hop; fall back to the socket.
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()[:64]
    return request.client.host if request.client else None


async def record(
    action: AuditAction,
    *,
    org_id: uuid.UUID | None = None,
    actor: UserResponse | None = None,
    actor_email: str | None = None,
    actor_user_id: uuid.UUID | None = None,
    target_type: str | None = None,
    target_id: Any = None,
    metadata: dict[str, Any] | None = None,
    request: Request | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> None:
    """Append one audit row. Best-effort: never raises into the caller."""
    factory = session_factory or app_session_factory
    try:
        async with factory() as session:
            session.add(
                AuditLog(
                    org_id=org_id,
                    actor_user_id=actor.id if actor else actor_user_id,
                    actor_email=actor.email if actor else actor_email,
                    action=str(action),
                    target_type=target_type,
                    target_id=str(target_id) if target_id is not None else None,
                    meta=metadata or {},
                    ip=client_ip(request),
                )
            )
            await session.commit()
    except Exception as exc:  # noqa: BLE001 - auditing must not break the action
        logger.warning("audit_write_failed", action=str(action), error=str(exc))


async def list_events(
    session: AsyncSession,
    org_id: uuid.UUID,
    *,
    limit: int = DEFAULT_PAGE_SIZE,
    before: dt.datetime | None = None,
) -> list[AuditLog]:
    """Newest-first page of an org's events, keyset paginated on ``created_at``."""
    stmt = select(AuditLog).where(AuditLog.org_id == org_id)
    if before is not None:
        stmt = stmt.where(AuditLog.created_at < before)
    stmt = stmt.order_by(AuditLog.created_at.desc()).limit(min(limit, MAX_PAGE_SIZE))
    rows = await session.execute(stmt)
    return list(rows.scalars().all())
