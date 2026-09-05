"""arq worker settings and lifecycle."""

from typing import Any

from arq import cron
from arq.connections import RedisSettings

from app.core.config import get_settings
from app.core.db import app_session_factory, async_session_factory
from app.services import tracing
from app.services.embeddings import EmbeddingService
from app.services.llm import LLMGateway
from app.services.storage import get_storage
from app.workers.ingest import ingest_document
from app.workers.quality import sample_conversation_quality


async def startup(ctx: dict[str, Any]) -> None:
    ctx["sessionmaker"] = async_session_factory
    ctx["embedding_service"] = EmbeddingService()
    ctx["storage"] = get_storage()
    # The nightly quality job reads real conversations, so it goes through the app
    # role and RLS rather than the privileged factory the ingest path uses.
    ctx["app_session_factory"] = app_session_factory
    ctx["gateway"] = LLMGateway()


async def shutdown(ctx: dict[str, Any]) -> None:
    # The nightly quality sweep emits Langfuse scores (services/evaluation/online.py),
    # and the SDK buffers them. Without this they are lost when the worker exits —
    # silently, since score emission is best-effort by design, so a dropped score
    # looks exactly like a night with nothing scorable. docs/observability.md states
    # the convention; this is the process that was not honouring it.
    tracing.flush()


class WorkerSettings:
    functions = [ingest_document]
    # 02:30 UTC: after the day being sampled has closed, and offset from :00 so it
    # does not contend with every other cron on the box.
    cron_jobs = [
        cron(
            sample_conversation_quality,
            hour={2},
            minute={30},
            # A sweep that overruns must not start a second one on top of it —
            # two concurrent sweeps would double-count the night into two rows.
            max_tries=1,
            run_at_startup=False,
        )
    ]
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    on_startup = startup
    on_shutdown = shutdown
