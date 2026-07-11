import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings

_settings = get_settings()

# Superuser connection: migrations, seed, and tests (bypasses RLS).
engine = create_async_engine(_settings.database_url, pool_pre_ping=True)
async_session_factory = async_sessionmaker(engine, expire_on_commit=False)

# Restricted role the app serves requests as; RLS is enforced against it.
app_engine = create_async_engine(_settings.app_database_url, pool_pre_ping=True)
app_session_factory = async_sessionmaker(app_engine, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with async_session_factory() as session:
        yield session


@asynccontextmanager
async def tenant_session(org_id: uuid.UUID) -> AsyncIterator[AsyncSession]:
    """An app-role session scoped to one tenant via ``app.current_tenant``.

    Every query in the block is filtered by RLS to ``org_id``. The setting is
    transaction-local, so it is discarded when the session closes.
    """
    async with app_session_factory() as session:
        await session.execute(
            text("SELECT set_config('app.current_tenant', :tid, true)"),
            {"tid": str(org_id)},
        )
        yield session
