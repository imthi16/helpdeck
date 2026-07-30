"""Nightly online-quality sampling job (plan 6.5).

Runs once a night across every org that had traffic, scoring a sample of each
org's answers for faithfulness. One ``eval_runs`` row per org, so 6.6's Quality
card can show a tenant their own trend rather than a global average that would be
dominated by whichever tenant is busiest.

Isolation matters more here than in most jobs: this reads real conversations for
every tenant in one process. Each org is scored inside its own tenant-scoped
session, so RLS is doing its usual work rather than being bypassed for
convenience — a bug in the sampling query then leaks nothing.
"""

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import select

from app.core.db import tenant_session
from app.core.logging import get_logger
from app.models import Message, MessageRole, Organization
from app.services.evaluation.online import DEFAULT_SAMPLE_RATE, run_online_sampling
from app.services.llm import LLMGateway

logger = get_logger(__name__)


async def orgs_with_recent_traffic(sessionmaker: Any, *, since: dt.datetime) -> list[uuid.UUID]:
    """Orgs with at least one assistant message in the window.

    Read unscoped (as the worker, not as a tenant) purely to decide *which* tenant
    sessions to open; no message content crosses this boundary, only org ids.
    Skipping silent orgs keeps a fleet of dormant demo tenants from each producing
    an empty nightly row.
    """
    async with sessionmaker() as session:
        result = await session.execute(
            select(Message.org_id)
            .where(Message.role == MessageRole.assistant, Message.created_at >= since)
            .distinct()
        )
        return list(result.scalars())


async def sample_conversation_quality(ctx: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    """arq entrypoint: score every active org's recent answers.

    One org's failure must not abort the rest — a tenant whose chunks were mid
    re-ingest should not cost every other tenant their nightly datapoint.
    """
    sessionmaker = ctx["sessionmaker"]
    # Tests inject a loop-local factory; production omits it and gets the module
    # global. Either way each org is scored inside its own tenant-scoped
    # transaction, which commits on clean exit of the block.
    app_factory = ctx.get("app_session_factory")
    gateway: LLMGateway = ctx.get("gateway") or LLMGateway()
    now = kwargs.get("now") or dt.datetime.now(dt.UTC)
    rate = kwargs.get("rate", DEFAULT_SAMPLE_RATE)

    since = now - dt.timedelta(days=1)
    org_ids = await orgs_with_recent_traffic(sessionmaker, since=since)
    logger.info("online_sampling_started", orgs=len(org_ids), since=since.isoformat())

    summary = {"orgs": len(org_ids), "scored": 0, "alerted": 0, "failed": 0}
    for org_id in org_ids:
        try:
            async with tenant_session(org_id, session_factory=app_factory) as session:
                result = await run_online_sampling(session, gateway, org_id, now=now, rate=rate)
        except Exception as exc:  # noqa: BLE001 - one org must not void the sweep
            summary["failed"] += 1
            logger.warning("online_sampling_org_failed", org_id=str(org_id), error=str(exc))
            continue
        summary["scored"] += result.scored
        summary["alerted"] += int(result.alerted)

    logger.info("online_sampling_finished", **summary)
    return summary


async def known_org_ids(sessionmaker: Any) -> list[uuid.UUID]:
    """Every org id — used by tests and by a manual full sweep."""
    async with sessionmaker() as session:
        return list((await session.execute(select(Organization.id))).scalars())
