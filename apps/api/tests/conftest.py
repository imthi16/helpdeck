import os

# Tests must be deterministic and never depend on a live model server, so pin the
# AI stack to the offline stubs regardless of the developer's shell env or .env:
# an empty OLLAMA_BASE_URL is treated as unreachable, and every hosted provider key
# and the generic gateway endpoint are blanked. This is load-bearing, not
# belt-and-braces — a key left in apps/api/.env makes the suite issue real, billed,
# rate-limited API calls, which is slow, non-deterministic, and silently voids this
# guarantee. LLM_API_BASE matters most of all: it is the FIRST branch of
# _default_provider, so a stray value captures the chat path ahead of every other
# guard here. Environment variables outrank env_file in pydantic-settings, so
# setting them here wins.
os.environ["OLLAMA_BASE_URL"] = ""
for _provider_key in (
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "GEMINI_API_KEY",
    "COHERE_API_KEY",
    "LLM_API_KEY",
    "LLM_API_BASE",
):
    os.environ[_provider_key] = ""

import pytest  # noqa: E402
from alembic.config import Config  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from alembic import command  # noqa: E402
from app.core.config import get_settings  # noqa: E402


@pytest.fixture(scope="session")
def migrated_db() -> None:
    command.upgrade(Config("alembic.ini"), "head")


@pytest.fixture
async def db_engine(migrated_db: None) -> AsyncEngine:
    engine = create_async_engine(get_settings().database_url, poolclass=None)
    yield engine
    await engine.dispose()


@pytest.fixture
async def app_login(db_engine: AsyncEngine) -> None:
    """Provision the app role's LOGIN + password for the test environment.

    The RLS migration deliberately no longer handles credentials (so no secret
    is ever emitted into migration SQL); deployment secret management does this
    in prod, docker-compose init does it in dev, and this fixture does it for
    tests. Runs as the superuser and is idempotent.
    """
    password = make_url(get_settings().app_database_url).password or "helpdeck_app"
    escaped = password.replace("'", "''")
    async with db_engine.begin() as conn:
        await conn.execute(text(f"ALTER ROLE helpdeck_app LOGIN PASSWORD '{escaped}'"))


@pytest.fixture
def db_sessionmaker(db_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(db_engine, expire_on_commit=False)


@pytest.fixture
async def app_engine(app_login: None) -> AsyncEngine:
    """Loop-local engine for the restricted `helpdeck_app` role.

    The module-global engine in `app.core.db` is bound to the server's loop, so
    tests build their own (same reason `tests/test_rls.py` does).
    """
    engine = create_async_engine(get_settings().app_database_url)
    yield engine
    await engine.dispose()


@pytest.fixture
def app_sessionmaker(app_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """The sessionmaker API tests inject so requests are served as the app role.

    Override `app.routers.deps.get_app_session_factory` with this and the whole
    request path runs under RLS, exactly as it does in production — a missing
    grant or an unset tenant scope then fails the test instead of surfacing at
    runtime. Test setup/teardown keeps using `db_sessionmaker` (superuser).
    """
    return async_sessionmaker(app_engine, expire_on_commit=False)
