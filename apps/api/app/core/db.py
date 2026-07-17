import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings

# Anything callable that yields an AsyncSession context: a plain
# ``async_sessionmaker`` satisfies this, and so do the tenant-scoped factories
# below. Code that must run under RLS should require a factory produced by
# ``tenant_sessionmaker`` (the context then owns the transaction — no inner
# ``session.commit()``).
SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]

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
async def tenant_session(
    org_id: uuid.UUID,
    *,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> AsyncIterator[AsyncSession]:
    """An app-role session scoped to one tenant via ``app.current_tenant``.

    The context manager owns a single transaction (``session.begin()``): the
    tenant setting is applied transaction-locally and stays in effect for every
    query in the block. On clean exit the transaction commits; on error it rolls
    back. Callers must NOT commit inside the block — committing would end the
    transaction and drop ``app.current_tenant``, leaving later queries unscoped
    (they would then fail closed under RLS). Do all the work, then let the block
    commit for you.

    ``session_factory`` overrides the module-global app factory (used by tests
    that need a loop-local engine); production callers omit it.
    """
    factory = session_factory or app_session_factory
    async with factory() as session, session.begin():
        await session.execute(
            text("SELECT set_config('app.current_tenant', :tid, true)"),
            {"tid": str(org_id)},
        )
        yield session


def tenant_sessionmaker(
    org_id: uuid.UUID,
    *,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> SessionFactory:
    """A ``SessionFactory`` whose every session is a ``tenant_session``.

    Lets request handlers bind the tenant once and hand a plain factory to
    helpers (persistence, retrieval, the agent) that open short sessions of
    their own. Each opened session carries the same no-inner-commit contract as
    ``tenant_session``.
    """
    return lambda: tenant_session(org_id, session_factory=session_factory)


def transactional_sessionmaker(
    base: async_sessionmaker[AsyncSession],
) -> SessionFactory:
    """Wrap a plain sessionmaker so each session owns one committing transaction.

    Gives non-tenant callers (tests, seed/eval scripts running as the
    superuser) the same contract as ``tenant_sessionmaker``: do the work, let
    the block commit — never call ``session.commit()`` inside.
    """

    @asynccontextmanager
    async def _factory() -> AsyncIterator[AsyncSession]:
        async with base() as session, session.begin():
            yield session

    return _factory


@asynccontextmanager
async def tenant_worker_session(
    org_id: uuid.UUID,
    *,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> AsyncIterator[AsyncSession]:
    """An app-role session for long-running jobs that manage their own commits.

    Unlike ``tenant_session`` the tenant setting is session-scoped (survives
    the job's internal commits), so multi-transaction work like the ingestion
    pipeline stays scoped end to end. The setting is cleared before the
    connection returns to the pool so no later checkout inherits a stale
    tenant.
    """
    factory = session_factory or app_session_factory
    async with factory() as session:
        await session.execute(
            text("SELECT set_config('app.current_tenant', :tid, false)"),
            {"tid": str(org_id)},
        )
        try:
            yield session
        finally:
            await session.rollback()  # end any open transaction before resetting
            await session.execute(text("SELECT set_config('app.current_tenant', '', false)"))
            await session.commit()
