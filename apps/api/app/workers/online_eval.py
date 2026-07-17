"""Nightly online quality sampling (task 6.5).

Samples a share of the previous day's cited assistant answers across all
tenants, re-scores each with the same faithfulness judge the agent uses, and
records the result as a Langfuse score per trace plus one aggregate
``eval_runs`` row (kind=``online``). Alerts (log + optional webhook) when the
7-day mean drops more than ``ALERT_DROP_THRESHOLD`` vs the prior 7 days.

Tenancy note: this batch is deliberately cross-tenant, so it runs on the
superuser engine (read-only sampling + a platform-level ``eval_runs``
insert), not the app role — RLS would hide every org from a single session.
The data never leaves the process except as numeric scores.
"""

import datetime
import statistics
import uuid
from typing import Any

import httpx
from sqlalchemy import select, text

from app.agent import prompts
from app.agent.graph import parse_confidence
from app.core.config import get_settings
from app.core.db import async_session_factory
from app.core.logging import get_logger
from app.models import Chunk, EvalRun
from app.services.llm import LLMGateway, LLMMessage, LLMRoute, OfflineGroundedProvider
from app.services.tracing import record_score

logger = get_logger(__name__)

SAMPLE_RATE = 0.10
MIN_SAMPLE = 10
MAX_SAMPLE = 50
ALERT_DROP_THRESHOLD = 0.05


async def sample_online_quality(ctx: dict[str, Any]) -> dict[str, Any]:
    settings = get_settings()
    gateway: LLMGateway = ctx.get("gateway") or LLMGateway()
    if isinstance(gateway._provider, OfflineGroundedProvider):  # noqa: SLF001
        # A stub judge would fabricate scores; never write those.
        logger.info("online_eval_skipped", reason="offline provider (no LLM available)")
        return {"skipped": "offline"}

    since = datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=1)
    async with async_session_factory() as session:
        rows = (
            await session.execute(
                text(
                    """
                    SELECT id, content, citations, trace_id
                    FROM messages
                    WHERE role = 'assistant'
                      AND created_at >= :since
                      AND content != ''
                      AND jsonb_array_length(citations) > 0
                    """
                ),
                {"since": since},
            )
        ).all()

    if not rows:
        logger.info("online_eval_skipped", reason="no cited answers in window")
        return {"skipped": "empty"}

    sample_size = min(MAX_SAMPLE, max(MIN_SAMPLE, int(len(rows) * SAMPLE_RATE)))
    import random

    sampled = random.sample(list(rows), min(sample_size, len(rows)))

    scores: list[float] = []
    skipped_missing_chunks = 0
    # One query for every cited chunk across the sample (not per message).
    all_chunk_ids = {
        uuid.UUID(c["chunk_id"])
        for message in sampled
        for c in message.citations
        if c.get("chunk_id")
    }
    async with async_session_factory() as session:
        found = (
            (await session.execute(select(Chunk).where(Chunk.id.in_(all_chunk_ids))))
            .scalars()
            .all()
        )
    content_by_id = {chunk.id: chunk.content for chunk in found}

    for message in sampled:
        cited = [
            (int(c["n"]), uuid.UUID(c["chunk_id"]))
            for c in message.citations
            if c.get("chunk_id") and c.get("n")
        ]
        if not cited or any(chunk_id not in content_by_id for _, chunk_id in cited):
            # Any cited chunk re-ingested/deleted -> the judge context would
            # be incomplete; skip rather than under-score a correct answer.
            skipped_missing_chunks += 1
            continue
        # Rebuild the context so [n] in the answer still points at slot n:
        # build_judge_prompt numbers passages 1..N by position, so pad the
        # slots between cited numbers.
        max_n = max(n for n, _ in cited)
        slot_content = {n: content_by_id[chunk_id] for n, chunk_id in cited}
        chunk_dicts = [
            {"content": slot_content.get(n, "(passage not cited by this answer)")}
            for n in range(1, max_n + 1)
        ]
        response = await gateway.complete(
            [
                LLMMessage("system", prompts.JUDGE_SYSTEM),
                LLMMessage("user", prompts.build_judge_prompt(message.content, chunk_dicts)),
            ],
            route=LLMRoute.cheap,
        )
        score = parse_confidence(response.text)
        scores.append(score)
        if message.trace_id:
            record_score(name="online_faithfulness", value=score, trace_id=message.trace_id)

    mean = statistics.mean(scores) if scores else None
    async with async_session_factory() as session:
        session.add(
            EvalRun(
                kind="online",
                dataset="production-sample",
                item_count=len(scores),
                model_config={"judge": gateway.model_for(LLMRoute.cheap)},
                metrics={
                    "faithfulness_mean": mean,
                    "sampled": len(sampled),
                    "skipped_missing_chunks": skipped_missing_chunks,
                },
            )
        )
        await session.commit()

    logger.info(
        "online_eval_complete",
        sampled=len(sampled),
        scored=len(scores),
        faithfulness_mean=mean,
    )
    await _alert_on_regression(settings.alert_webhook_url)
    return {"scored": len(scores), "faithfulness_mean": mean}


async def _alert_on_regression(webhook_url: str) -> None:
    """Compare the 7-day online-faithfulness mean with the prior 7 days."""
    now = datetime.datetime.now(datetime.UTC)
    week = datetime.timedelta(days=7)

    async def window_mean(start: datetime.datetime, end: datetime.datetime) -> float | None:
        async with async_session_factory() as session:
            rows = (
                (
                    await session.execute(
                        select(EvalRun.metrics).where(
                            EvalRun.kind == "online",
                            EvalRun.created_at >= start,
                            EvalRun.created_at < end,
                        )
                    )
                )
                .scalars()
                .all()
            )
        values = [m["faithfulness_mean"] for m in rows if m.get("faithfulness_mean") is not None]
        return statistics.mean(values) if values else None

    current = await window_mean(now - week, now + datetime.timedelta(minutes=1))
    previous = await window_mean(now - 2 * week, now - week)
    if current is None or previous is None:
        return
    drop = previous - current
    if drop <= ALERT_DROP_THRESHOLD:
        return

    logger.error(
        "faithfulness_regression",
        current_7d=round(current, 3),
        previous_7d=round(previous, 3),
        drop=round(drop, 3),
    )
    if webhook_url:
        payload = {
            "text": (
                f"HelpDeck faithfulness regression: 7-day mean {current:.2f} "
                f"vs {previous:.2f} previously (drop {drop:.2f})."
            )
        }
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                await client.post(webhook_url, json=payload)
        except httpx.HTTPError as exc:
            logger.warning("alert_webhook_failed", error=str(exc))
