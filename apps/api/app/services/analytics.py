"""Analytics over one org's conversation history.

Everything here reads the RLS-protected tenant tables, so callers pass a
tenant-scoped session factory (``TenantDbDep``) — the numbers are the caller's
org by construction, not by remembering a filter.

Live queries, no rollup table: one org's traffic at this scale is a few index
scans. Issue #63 tracks the nightly rollup for when that stops being true.
"""

import datetime as dt
import math
import uuid
from dataclasses import dataclass, field

from sqlalchemy import Integer, and_, cast, distinct, exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Conversation, Escalation, Message, MessageRole
from app.services.embeddings import EmbeddingService

#: An answer below this faithfulness score counts as "unanswered" for the
#: top-questions list, alongside anything that escalated.
LOW_CONFIDENCE = 0.5
#: Cap on how many questions get embedded for clustering, to bound the work.
MAX_QUESTIONS = 200
#: Cosine similarity at which two questions are treated as the same question.
SIMILARITY_THRESHOLD = 0.85
TOP_QUESTIONS = 10


@dataclass
class DayCount:
    day: dt.date
    conversations: int


@dataclass
class QuestionCluster:
    question: str
    count: int
    variants: list[str] = field(default_factory=list)


@dataclass
class Overview:
    days: int
    total_conversations: int
    escalated_conversations: int
    deflected_conversations: int
    escalation_rate: float
    deflection_rate: float
    csat_average: float | None
    csat_responses: int
    thumbs_up: int
    thumbs_down: int
    conversations_over_time: list[DayCount]
    top_unanswered: list[QuestionCluster]


def _window_start(days: int) -> dt.datetime:
    return dt.datetime.now(dt.UTC) - dt.timedelta(days=days)


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def cluster_questions(
    questions: list[str],
    vectors: list[list[float]],
    *,
    threshold: float = SIMILARITY_THRESHOLD,
    limit: int = TOP_QUESTIONS,
) -> list[QuestionCluster]:
    """Greedy single-pass clustering: each question joins the first cluster it is
    close enough to, else starts its own. Cheap, order-stable, and good enough to
    surface "people keep asking this" — the point of the list.
    """
    centroids: list[list[float]] = []
    clusters: list[QuestionCluster] = []
    for question, vector in zip(questions, vectors, strict=False):
        for index, centroid in enumerate(centroids):
            if _cosine(vector, centroid) >= threshold:
                clusters[index].count += 1
                if question not in clusters[index].variants:
                    clusters[index].variants.append(question)
                break
        else:
            centroids.append(vector)
            clusters.append(QuestionCluster(question=question, count=1, variants=[question]))
    clusters.sort(key=lambda c: (-c.count, c.question))
    return clusters[:limit]


async def _unanswered_questions(
    session: AsyncSession, org_id: uuid.UUID, since: dt.datetime
) -> list[str]:
    """User questions whose turn escalated or came back poorly grounded."""
    escalated = (
        select(Escalation.conversation_id)
        .where(Escalation.org_id == org_id, Escalation.created_at >= since)
        .subquery()
    )
    low_confidence = (
        select(Message.conversation_id)
        .where(
            Message.org_id == org_id,
            Message.role == MessageRole.assistant,
            Message.confidence.is_not(None),
            Message.confidence < LOW_CONFIDENCE,
            Message.created_at >= since,
        )
        .subquery()
    )
    rows = await session.execute(
        select(Message.content)
        .where(
            Message.org_id == org_id,
            Message.role == MessageRole.user,
            Message.created_at >= since,
            Message.conversation_id.in_(
                select(escalated.c.conversation_id).union(select(low_confidence.c.conversation_id))
            ),
        )
        .order_by(Message.created_at.desc())
        .limit(MAX_QUESTIONS)
    )
    return [content.strip() for content in rows.scalars().all() if content.strip()]


async def top_unanswered(
    session: AsyncSession,
    org_id: uuid.UUID,
    *,
    since: dt.datetime,
    embedding_service: EmbeddingService,
) -> list[QuestionCluster]:
    questions = await _unanswered_questions(session, org_id, since)
    if not questions:
        return []
    vectors = await embedding_service.embed_texts(questions)
    return cluster_questions(questions, vectors)


async def conversations_over_time(
    session: AsyncSession, org_id: uuid.UUID, *, days: int
) -> list[DayCount]:
    """Per-day counts, zero-filled so the chart has no missing days."""
    series = select(
        func.generate_series(
            func.date_trunc("day", _window_start(days - 1)),
            func.date_trunc("day", func.now()),
            dt.timedelta(days=1),
        ).label("day")
    ).subquery()

    day = func.date_trunc("day", Conversation.created_at)
    counts = (
        select(day.label("day"), func.count(Conversation.id).label("total"))
        .where(Conversation.org_id == org_id, Conversation.created_at >= _window_start(days))
        .group_by(day)
        .subquery()
    )

    rows = await session.execute(
        select(series.c.day, func.coalesce(counts.c.total, 0))
        .outerjoin(counts, counts.c.day == series.c.day)
        .order_by(series.c.day)
    )
    return [DayCount(day=row[0].date(), conversations=row[1]) for row in rows.all()]


async def overview(
    session: AsyncSession,
    org_id: uuid.UUID,
    *,
    days: int = 30,
    embedding_service: EmbeddingService,
) -> Overview:
    since = _window_start(days)
    scoped = and_(Conversation.org_id == org_id, Conversation.created_at >= since)

    has_escalation = exists().where(
        and_(
            Escalation.conversation_id == Conversation.id,
            Escalation.org_id == Conversation.org_id,
        )
    )
    has_answer = exists().where(
        and_(
            Message.conversation_id == Conversation.id,
            Message.org_id == Conversation.org_id,
            Message.role == MessageRole.assistant,
        )
    )

    totals = (
        await session.execute(
            select(
                func.count(Conversation.id),
                func.count(distinct(Conversation.id)).filter(has_escalation),
                # Deflected: the agent answered and nobody had to step in.
                func.count(distinct(Conversation.id)).filter(and_(has_answer, ~has_escalation)),
                func.avg(cast(Conversation.csat_score, Integer)),
                func.count(Conversation.csat_score),
            ).where(scoped)
        )
    ).one()
    total, escalated, deflected, csat_average, csat_responses = totals

    thumbs = (
        await session.execute(
            select(
                func.count(Message.id).filter(Message.feedback > 0),
                func.count(Message.id).filter(Message.feedback < 0),
            ).where(
                Message.org_id == org_id,
                Message.created_at >= since,
                Message.feedback.is_not(None),
            )
        )
    ).one()

    return Overview(
        days=days,
        total_conversations=total or 0,
        escalated_conversations=escalated or 0,
        deflected_conversations=deflected or 0,
        escalation_rate=round((escalated or 0) / total, 4) if total else 0.0,
        deflection_rate=round((deflected or 0) / total, 4) if total else 0.0,
        csat_average=round(float(csat_average), 2) if csat_average is not None else None,
        csat_responses=csat_responses or 0,
        thumbs_up=thumbs[0] or 0,
        thumbs_down=thumbs[1] or 0,
        conversations_over_time=await conversations_over_time(session, org_id, days=days),
        top_unanswered=await top_unanswered(
            session, org_id, since=since, embedding_service=embedding_service
        ),
    )
