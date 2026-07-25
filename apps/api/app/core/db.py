import uuid
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Protocol

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


class SessionFactory(Protocol):
    """Hands out a session for one unit of work.

    Satisfied by ``async_sessionmaker`` (plain session; the caller commits) and
    by ``TenantSessionFactory``/``unscoped_session_factory`` (the block owns a
    transaction and commits on clean exit). Code that only needs "give me a
    session" depends on this rather than the concrete sessionmaker, so the same
    handler works with a tenant-scoped app-role session or a privileged one.
    """

    def __call__(self) -> AbstractAsyncContextManager[AsyncSession]: ...


async def get_session() -> AsyncIterator[AsyncSession]:
    async with async_session_factory() as session:
        yield session


@asynccontextmanager
async def tenant_session(
    org_id: uuid.UUID | None,
    *,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> AsyncIterator[AsyncSession]:
    """A session owning one transaction, scoped to ``org_id`` if given.

    The context manager owns a single transaction (``session.begin()``): the
    tenant setting is applied transaction-locally and stays in effect for every
    query in the block. On clean exit the transaction commits; on error it rolls
    back. Callers must NOT commit inside the block — committing would end the
    transaction and drop ``app.current_tenant``, leaving later queries unscoped
    (they would then fail closed under RLS). Do all the work, then let the block
    commit for you.

    ``org_id=None`` skips the tenant setting entirely: that form is only for
    privileged (superuser) factories where RLS does not apply — see
    ``unscoped_session_factory``.

    ``session_factory`` overrides the module-global app factory (used by tests
    that need a loop-local engine); production callers omit it.
    """
    factory = session_factory or app_session_factory
    async with factory() as session, session.begin():
        if org_id is not None:
            await session.execute(
                text("SELECT set_config('app.current_tenant', :tid, true)"),
                {"tid": str(org_id)},
            )
        yield session


class TenantSessionFactory:
    """A ``SessionFactory`` bound to one tenant.

    Call-compatible with ``async_sessionmaker`` so request handlers keep the
    familiar ``async with db() as session:`` shape, but every call yields an
    app-role session inside a transaction with ``app.current_tenant`` set. Each
    call is its own short transaction — handlers must not commit inside the
    block (see ``tenant_session``); it commits for them on clean exit.
    """

    __slots__ = ("org_id", "_session_factory")

    def __init__(
        self,
        org_id: uuid.UUID | None,
        *,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
    ) -> None:
        self.org_id = org_id
        self._session_factory = session_factory

    def __call__(self) -> AbstractAsyncContextManager[AsyncSession]:
        return tenant_session(self.org_id, session_factory=self._session_factory)


def unscoped_session_factory(
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> TenantSessionFactory:
    """Same commit-on-exit unit of work, without a tenant scope.

    For privileged callers (background jobs, eval harnesses, tests) that need the
    write semantics handlers now rely on but have no request tenant. Pointing it
    at the *app* role would fail closed on every read, so pass a superuser
    sessionmaker (the default is ``async_session_factory``).
    """
    return TenantSessionFactory(None, session_factory=session_factory or async_session_factory)
