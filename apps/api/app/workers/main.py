"""arq worker settings and lifecycle."""

from typing import Any

from arq.connections import RedisSettings

from app.core.config import get_settings
from app.core.db import async_session_factory
from app.services.embeddings import EmbeddingService
from app.services.storage import get_storage
from app.workers.ingest import ingest_document


async def startup(ctx: dict[str, Any]) -> None:
    ctx["sessionmaker"] = async_session_factory
    ctx["embedding_service"] = EmbeddingService()
    ctx["storage"] = get_storage()


async def shutdown(ctx: dict[str, Any]) -> None:
    return None


class WorkerSettings:
    functions = [ingest_document]
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    on_startup = startup
    on_shutdown = shutdown
