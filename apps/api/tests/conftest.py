import os

# Tests must be deterministic and never depend on a live model server, so pin the
# AI stack to the offline stubs regardless of the developer's shell env: an empty
# OLLAMA_BASE_URL is treated as unreachable, and no provider key is set.
os.environ["OLLAMA_BASE_URL"] = ""

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
