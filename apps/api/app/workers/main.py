"""arq worker settings and lifecycle."""

from typing import Any

from arq import cron
from arq.connections import RedisSettings

from app.core.config import get_settings
from app.core.db import app_session_factory
from app.services.demo import reset_demo_org
from app.services.embeddings import EmbeddingService
from app.services.storage import get_storage
from app.workers.ingest import ingest_document
from app.workers.online_eval import sample_online_quality


async def startup(ctx: dict[str, Any]) -> None:
    # The worker serves tenant data as the restricted app role; each job binds
    # its own tenant via tenant_worker_session.
    ctx["sessionmaker"] = app_session_factory
    ctx["embedding_service"] = EmbeddingService()
    ctx["storage"] = get_storage()


async def shutdown(ctx: dict[str, Any]) -> None:
    return None


class WorkerSettings:
    functions = [ingest_document, sample_online_quality, reset_demo_org]
    cron_jobs = [
        # Nightly online quality sampling (task 6.5) at 03:30 UTC.
        cron(sample_online_quality, hour={3}, minute={30}, timeout=3600),
        # Nightly demo-org reset (task 7.3) at 04:00 UTC; no-op unless
        # DEMO_ORG_ID is set.
        cron(reset_demo_org, hour={4}, minute={0}, timeout=3600),
    ]
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    on_startup = startup
    on_shutdown = shutdown
