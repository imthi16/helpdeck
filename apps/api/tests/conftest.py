import pytest
from alembic.config import Config
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from alembic import command
from app.core.config import get_settings


@pytest.fixture(scope="session")
def migrated_db() -> None:
    command.upgrade(Config("alembic.ini"), "head")


@pytest.fixture
async def db_engine(migrated_db: None) -> AsyncEngine:
    engine = create_async_engine(get_settings().database_url, poolclass=None)
    yield engine
    await engine.dispose()
