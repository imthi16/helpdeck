"""Score a sample of real conversations for faithfulness (plan 6.5).

The golden set (6.3) measures the pipeline against a fixture corpus that never
changes. That catches regressions in the code but is blind to everything that
moves in production: the documents a tenant actually ingests, the questions their
users actually ask. This job closes that gap by re-scoring a nightly sample of
real answers with the same independent judge the offline eval uses.

**What it can and cannot see.** Faithfulness is scored against the chunks the
answer *cited*, refetched in full from ``chunks`` — the stored citation snippet is
truncated to 200 characters, which would under-support claims and depress the
score. The full retrieval set is not persisted anywhere, so an answer grounded in
a retrieved-but-uncited passage scores lower here than it would offline. Online
faithfulness is therefore a conservative lower bound, not directly comparable with
a golden-set number; the two are labelled with different ``kind`` values so they
are never averaged together.

**Refusals are excluded, not scored zero** — the same rule the offline metrics
follow. A refusal asserts nothing, so it cannot be unfaithful, and counting it as
0.0 would make a correctly cautious pipeline look like a hallucinating one.
"""

import datetime as dt
import random
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent import prompts as agent_prompts
from app.core.logging import get_logger
from app.models import Chunk, EvalRun, EvalRunKind, Message, MessageRole
from app.services.evaluation import metrics as m
from app.services.llm import LLMGateway, LLMRoute
from app.services.tracing import score_trace

logger = get_logger(__name__)

#: Share of the day's answers to score. The plan's range is 5-10%; the low end is
#: the default because every sampled item costs a judge call against a tenant's
#: real traffic, and the trend matters more than the precision of any one night.
DEFAULT_SAMPLE_RATE = 0.05
#: Never sample fewer than this, or a quiet day produces a meaningless one-item
#: average that the 7-day comparison then treats as signal.
MIN_SAMPLE = 5
#: Nor more than this, to bound cost on a very busy day.
MAX_SAMPLE = 100
#: Drop in mean faithfulness (in points, 0-1 scale) that warrants an alert.
ALERT_DROP = 0.05
#: Days of history the current run is compared against.
TREND_WINDOW_DAYS = 7


@dataclass
class ScoredAnswer:
    message_id: uuid.UUID
    conversation_id: uuid.UUID
    trace_id: str | None
    faithfulness: float
    claim_count: int
    supported_claim_count: int
    #: The pipeline's own in-line gate score, for comparison with this independent
    #: judgement. A systematic gap between the two is the signal that the serving
    #: gate is mis-calibrated.
    pipeline_confidence: float | None = None


@dataclass
class OnlineRunResult:
    org_id: uuid.UUID
    sampled: int
    scored: int
    skipped_refusals: int
    skipped_uncited: int
    errors: int
    mean_faithfulness: float | None
    #: Mean over the preceding window, or None when there is no history yet.
    baseline_faithfulness: float | None = None
    alerted: bool = False
    answers: list[ScoredAnswer] = field(default_factory=list)

    @property
    def drop(self) -> float | None:
        if self.mean_faithfulness is None or self.baseline_faithfulness is None:
            return None
        return self.baseline_faithfulness - self.mean_faithfulness


def sample_size(population: int, rate: float = DEFAULT_SAMPLE_RATE) -> int:
    """How many of ``population`` answers to score.

    Clamped at both ends: a floor so a quiet day still yields a usable average, and
    a ceiling so a busy one does not run up an unbounded judge bill. Never more
    than the population itself.
    """
    if population <= 0:
        return 0
    return min(population, max(MIN_SAMPLE, min(MAX_SAMPLE, round(population * rate))))


async def candidate_answers(
    session: AsyncSession, org_id: uuid.UUID, *, since: dt.datetime, until: dt.datetime
) -> list[Message]:
    """Assistant messages from the window, newest first.

    Scoped by ``org_id`` explicitly as well as by RLS — the project's rule, and it
    keeps the query correct if this is ever called on an unscoped session.
    """
    result = await session.execute(
        select(Message)
        .where(
            Message.org_id == org_id,
            Message.role == MessageRole.assistant,
            Message.created_at >= since,
            Message.created_at < until,
        )
        .order_by(Message.created_at.desc())
    )
    return list(result.scalars())


def is_scorable(message: Message) -> bool:
    """Whether this answer can be judged at all.

    Two exclusions, both of which would otherwise poison the average:
    a refusal asserts nothing so it cannot be unfaithful, and an answer with no
    citations has no recorded context to judge it against — scoring it would
    measure our missing data, not the pipeline's grounding.
    """
    if not message.citations:
        return False
    normalized = " ".join(message.content.split()).lower()
    return agent_prompts.REFUSAL_TEXT.lower() not in normalized


async def cited_chunks(
    session: AsyncSession, org_id: uuid.UUID, message: Message
) -> list[dict[str, Any]]:
    """Refetch the full text of the chunks an answer cited.

    The stored citation carries a 200-character snippet, which is enough for a UI
    tooltip and not enough to judge a claim against — a claim supported by the
    tail of a chunk would read as unsupported. Chunks the answer cited may since
    have been deleted by a re-ingest, in which case they are simply absent and the
    answer is judged on what remains.
    """
    chunk_ids = [c["chunk_id"] for c in message.citations if c.get("chunk_id")]
    if not chunk_ids:
        return []
    result = await session.execute(
        select(Chunk).where(Chunk.org_id == org_id, Chunk.id.in_(chunk_ids))
    )
    return [{"content": chunk.content, "chunk_id": str(chunk.id)} for chunk in result.scalars()]


async def score_answer(
    session: AsyncSession,
    gateway: LLMGateway,
    org_id: uuid.UUID,
    message: Message,
    *,
    route: LLMRoute = m.JUDGE_ROUTE,
) -> ScoredAnswer | None:
    """Judge one answer, or None when its cited chunks are all gone."""
    chunks = await cited_chunks(session, org_id, message)
    if not chunks:
        return None
    faith = await m.faithfulness(gateway, message.content, chunks, route=route)
    return ScoredAnswer(
        message_id=message.id,
        conversation_id=message.conversation_id,
        trace_id=message.trace_id,
        faithfulness=faith.score,
        claim_count=len(faith.claims),
        supported_claim_count=faith.supported_count,
        pipeline_confidence=message.confidence,
    )


async def baseline_faithfulness(
    session: AsyncSession,
    org_id: uuid.UUID,
    *,
    before: dt.datetime,
    window_days: int = TREND_WINDOW_DAYS,
) -> float | None:
    """Mean online faithfulness over the preceding window, or None with no history.

    Compares against the window rather than against yesterday alone: a single quiet
    night is noisy, and one bad night should not raise an alert that a week of
    context would show is normal variation.
    """
    since = before - dt.timedelta(days=window_days)
    result = await session.execute(
        select(EvalRun.metrics).where(
            EvalRun.org_id == org_id,
            EvalRun.kind == EvalRunKind.online,
            EvalRun.created_at >= since,
            EvalRun.created_at < before,
        )
    )
    scores = [
        value
        for metrics in result.scalars()
        if isinstance(value := (metrics or {}).get("faithfulness"), int | float)
    ]
    return m.mean_or_none([float(s) for s in scores])


async def run_online_sampling(
    session: AsyncSession,
    gateway: LLMGateway,
    org_id: uuid.UUID,
    *,
    now: dt.datetime | None = None,
    rate: float = DEFAULT_SAMPLE_RATE,
    rng: random.Random | None = None,
    persist: bool = True,
) -> OnlineRunResult:
    """Sample the last 24h of one org's answers, score them, record the run."""
    now = now or dt.datetime.now(dt.UTC)
    since = now - dt.timedelta(days=1)
    rng = rng or random.Random()

    answers = await candidate_answers(session, org_id, since=since, until=now)
    scorable = [msg for msg in answers if is_scorable(msg)]
    refusals = len(answers) - len(scorable)

    # Sample from the scorable population, not from all answers: sampling first
    # and filtering after would shrink an already-small sample by the refusal rate,
    # so a cautious pipeline would be measured on fewer answers than a reckless one.
    take = sample_size(len(scorable), rate)
    sampled = rng.sample(scorable, take) if take else []

    scored: list[ScoredAnswer] = []
    skipped_uncited = 0
    errors = 0
    for message in sampled:
        try:
            result = await score_answer(session, gateway, org_id, message)
        except Exception as exc:  # noqa: BLE001 - one bad answer must not void the run
            errors += 1
            logger.warning("online_score_failed", message_id=str(message.id), error=str(exc))
            continue
        if result is None:
            skipped_uncited += 1
            continue
        scored.append(result)
        # Lands next to the turn that produced it, so a low score in Langfuse is one
        # click from the trace that explains it. No-ops without tracing or a trace id.
        score_trace(
            result.trace_id,
            name="online_faithfulness",
            value=result.faithfulness,
            comment=f"{result.supported_claim_count}/{result.claim_count} claims supported",
        )

    mean = m.mean_or_none([s.faithfulness for s in scored])
    baseline = await baseline_faithfulness(session, org_id, before=now)

    run = OnlineRunResult(
        org_id=org_id,
        sampled=len(sampled),
        scored=len(scored),
        skipped_refusals=refusals,
        skipped_uncited=skipped_uncited,
        errors=errors,
        mean_faithfulness=mean,
        baseline_faithfulness=baseline,
        answers=scored,
    )
    run.alerted = should_alert(run)

    if run.alerted:
        # A log line is the alert channel for now; the plan allows log or webhook,
        # and a webhook with nowhere to point is worse than a searchable log event.
        logger.error(
            "online_faithfulness_regression",
            org_id=str(org_id),
            mean_faithfulness=mean,
            baseline_faithfulness=baseline,
            drop=run.drop,
            window_days=TREND_WINDOW_DAYS,
            scored=len(scored),
        )
    logger.info(
        "online_sampling_complete",
        org_id=str(org_id),
        sampled=run.sampled,
        scored=run.scored,
        mean_faithfulness=mean,
        alerted=run.alerted,
    )

    if persist and scored:
        # add + flush, never commit: this runs inside a ``tenant_session`` block,
        # and committing here would end that transaction and drop
        # ``app.current_tenant``, leaving anything after it unscoped. The flush
        # still surfaces a constraint or RLS violation here rather than at exit.
        session.add(build_eval_run(run))
        await session.flush()
    return run


def should_alert(run: OnlineRunResult) -> bool:
    """Whether this run's drop warrants waking someone.

    Requires an actual measurement and an actual baseline: a night with nothing
    scorable has no score to have dropped, and the first ever run has nothing to
    have dropped *from*. Treating either as a regression would make the alert cry
    wolf on exactly the days it carries no information.
    """
    drop = run.drop
    return drop is not None and drop > ALERT_DROP


def build_eval_run(run: OnlineRunResult) -> EvalRun:
    """One eval_runs row per org per night, shaped like the golden runs 6.6 charts."""
    return EvalRun(
        kind=EvalRunKind.online,
        org_id=run.org_id,
        dataset="production",
        subset=f"{TREND_WINDOW_DAYS}d-sample",
        item_count=run.scored,
        metrics={
            "faithfulness": run.mean_faithfulness,
            "baseline_faithfulness": run.baseline_faithfulness,
            "drop": run.drop,
            "count_sampled": run.sampled,
            "count_scored": run.scored,
            "count_skipped_refusals": run.skipped_refusals,
            "count_skipped_uncited": run.skipped_uncited,
            "count_errors": run.errors,
        },
        environment={"alerted": run.alerted, "alert_drop_threshold": ALERT_DROP},
        # Not a pass/fail gate: online sampling reports, it does not block. The
        # alert flag above is the signal; `passed` stays NULL so 6.4's gate verdict
        # and this run's health are never confused for one another.
        passed=None,
        duration_ms=0,
    )


def mean_confidence_gap(answers: Sequence[ScoredAnswer]) -> float | None:
    """Mean (independent judge − pipeline gate) over answers where both scored.

    A persistently negative gap means the serving gate is more generous than an
    independent judge and is letting weak answers through; a positive one means it
    is over-escalating, which 6.3 measured on the local models.
    """
    gaps = [
        a.faithfulness - a.pipeline_confidence for a in answers if a.pipeline_confidence is not None
    ]
    return m.mean_or_none(gaps)
