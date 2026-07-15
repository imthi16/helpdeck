"""Analytics overview queries (task 5.5).

All numbers come straight from ``conversations``/``messages`` window queries
(no rollup table yet — see ROADMAP). "Top unanswered questions" clusters the
user messages that led to a low-confidence answer or an escalation: greedy
centroid agglomeration over embeddings from the existing ``EmbeddingService``
(cosine ≥ ``CLUSTER_SIMILARITY`` joins a cluster). Results are cached in
Redis for ``CACHE_TTL_SECONDS`` per (org, window) because embedding the
candidates is the only real cost here.

Definitions:
- escalation_rate: share of conversations in the window with status
  ``escalated``.
- deflection_rate: among *answered* conversations (≥1 assistant message),
  the share that did NOT escalate — i.e. resolved without a human.
"""

import datetime
import json
import math
import uuid
from dataclasses import asdict, dataclass, field

import redis.asyncio as redis
from sqlalchemy import text

from app.core.config import get_settings
from app.core.db import SessionFactory
from app.services.embeddings import EmbeddingService

CACHE_TTL_SECONDS = 600
CLUSTER_SIMILARITY = 0.85
MAX_CANDIDATES = 200
TOP_CLUSTERS = 10


@dataclass
class UnansweredCluster:
    question: str
    count: int
    last_seen: str


@dataclass
class AnalyticsOverview:
    days: int
    total_conversations: int
    escalated_conversations: int
    answered_conversations: int
    escalation_rate: float | None
    deflection_rate: float | None
    csat_average: float | None
    csat_responses: int
    conversations_per_day: list[dict]  # [{date, count}]
    top_unanswered: list[UnansweredCluster] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, raw: str) -> "AnalyticsOverview":
        data = json.loads(raw)
        data["top_unanswered"] = [UnansweredCluster(**c) for c in data["top_unanswered"]]
        return cls(**data)


def _cache_key(org_id: uuid.UUID, days: int) -> str:
    return f"helpdeck:analytics:{org_id}:{days}"


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


def cluster_questions(
    questions: list[tuple[str, str]], vectors: list[list[float]]
) -> list[UnansweredCluster]:
    """Greedy centroid agglomeration; returns the TOP_CLUSTERS biggest groups.

    ``questions`` is [(content, iso_timestamp)]; order should be most recent
    first (the cluster keeps its shortest member as the display question).
    """
    centroids: list[list[float]] = []
    members: list[list[int]] = []
    for i, vector in enumerate(vectors):
        best, best_sim = -1, CLUSTER_SIMILARITY
        for j, centroid in enumerate(centroids):
            sim = cosine(vector, centroid)
            if sim >= best_sim:
                best, best_sim = j, sim
        if best == -1:
            centroids.append(list(vector))
            members.append([i])
        else:
            group = members[best]
            group.append(i)
            n = len(group)
            centroids[best] = [
                (c * (n - 1) + v) / n for c, v in zip(centroids[best], vector, strict=True)
            ]

    clusters = []
    for group in members:
        texts = [questions[i][0] for i in group]
        last_seen = max(questions[i][1] for i in group)
        clusters.append(
            UnansweredCluster(
                question=min(texts, key=len),
                count=len(group),
                last_seen=last_seen,
            )
        )
    clusters.sort(key=lambda c: (-c.count, c.last_seen), reverse=False)
    return clusters[:TOP_CLUSTERS]


async def _unanswered_candidates(
    tenant_sm: SessionFactory, org_id: uuid.UUID, since: datetime.datetime
) -> list[tuple[str, str]]:
    """User messages whose answer was low-confidence or whose conversation
    escalated — the raw material for "top unanswered". Most recent first."""
    threshold = get_settings().faithfulness_threshold
    sql = text(
        """
        WITH ordered AS (
            SELECT m.role, m.content, m.created_at,
                   lead(m.role) OVER w AS next_role,
                   lead(m.confidence) OVER w AS next_confidence,
                   c.status AS conv_status
            FROM messages m
            JOIN conversations c ON c.id = m.conversation_id
            WHERE m.org_id = :org AND m.created_at >= :since
            WINDOW w AS (PARTITION BY m.conversation_id ORDER BY m.created_at, m.id)
        )
        SELECT content, created_at FROM ordered
        WHERE role = 'user' AND (
            conv_status = 'escalated'
            OR (next_role = 'assistant' AND coalesce(next_confidence, 0) < :threshold)
        )
        ORDER BY created_at DESC
        LIMIT :limit
        """
    )
    async with tenant_sm() as session:
        rows = (
            await session.execute(
                sql,
                {
                    "org": str(org_id),
                    "since": since,
                    "threshold": threshold,
                    "limit": MAX_CANDIDATES,
                },
            )
        ).all()
    return [(row.content, row.created_at.isoformat()) for row in rows]


async def compute_overview(
    tenant_sm: SessionFactory,
    org_id: uuid.UUID,
    *,
    days: int,
    embedding_service: EmbeddingService,
    redis_client: redis.Redis | None = None,
) -> AnalyticsOverview:
    if redis_client is not None:
        cached = await redis_client.get(_cache_key(org_id, days))
        if cached is not None:
            raw = cached.decode() if isinstance(cached, bytes) else cached
            return AnalyticsOverview.from_json(raw)

    now = datetime.datetime.now(datetime.UTC)
    since = now - datetime.timedelta(days=days)

    async with tenant_sm() as session:
        stats = (
            await session.execute(
                text(
                    """
                    SELECT
                      count(*) AS total,
                      count(*) FILTER (WHERE status = 'escalated') AS escalated,
                      count(*) FILTER (WHERE EXISTS (
                        SELECT 1 FROM messages m
                        WHERE m.conversation_id = conversations.id
                          AND m.role = 'assistant'
                      )) AS answered,
                      count(*) FILTER (WHERE status != 'escalated' AND EXISTS (
                        SELECT 1 FROM messages m
                        WHERE m.conversation_id = conversations.id
                          AND m.role = 'assistant'
                      )) AS deflected,
                      avg(csat_score) AS csat_avg,
                      count(csat_score) AS csat_n
                    FROM conversations
                    WHERE org_id = :org AND created_at >= :since
                    """
                ),
                {"org": str(org_id), "since": since},
            )
        ).one()
        per_day_rows = (
            await session.execute(
                text(
                    """
                    SELECT date_trunc('day', created_at) AS day, count(*) AS n
                    FROM conversations
                    WHERE org_id = :org AND created_at >= :since
                    GROUP BY day ORDER BY day
                    """
                ),
                {"org": str(org_id), "since": since},
            )
        ).all()

    counts = {row.day.date().isoformat(): row.n for row in per_day_rows}
    per_day = []
    for offset in range(days, -1, -1):
        day = (now - datetime.timedelta(days=offset)).date().isoformat()
        per_day.append({"date": day, "count": counts.get(day, 0)})

    candidates = await _unanswered_candidates(tenant_sm, org_id, since)
    top_unanswered: list[UnansweredCluster] = []
    if candidates:
        vectors = await embedding_service.embed_texts([content for content, _ in candidates])
        top_unanswered = cluster_questions(candidates, vectors)

    overview = AnalyticsOverview(
        days=days,
        total_conversations=stats.total,
        escalated_conversations=stats.escalated,
        answered_conversations=stats.answered,
        escalation_rate=(stats.escalated / stats.total) if stats.total else None,
        deflection_rate=(stats.deflected / stats.answered) if stats.answered else None,
        csat_average=float(stats.csat_avg) if stats.csat_avg is not None else None,
        csat_responses=stats.csat_n,
        conversations_per_day=per_day,
        top_unanswered=top_unanswered,
    )

    if redis_client is not None:
        await redis_client.set(_cache_key(org_id, days), overview.to_json(), ex=CACHE_TTL_SECONDS)
    return overview
