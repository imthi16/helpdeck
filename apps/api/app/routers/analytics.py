"""Analytics overview endpoint (task 5.5). Any member may read."""

from typing import Annotated

import redis.asyncio as redis
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.db import app_session_factory, tenant_sessionmaker
from app.core.deps import MembershipDep
from app.models import EvalRun
from app.services.analytics import AnalyticsOverview, compute_overview
from app.services.cache import get_redis
from app.services.embeddings import EmbeddingService

router = APIRouter(prefix="/api/v1/analytics", tags=["analytics"])


def get_analytics_sessionmaker() -> async_sessionmaker[AsyncSession]:
    return app_session_factory


def get_analytics_embedding_service() -> EmbeddingService:
    return EmbeddingService()


def get_analytics_redis() -> redis.Redis | None:
    return get_redis()


class UnansweredClusterResponse(BaseModel):
    question: str
    count: int
    last_seen: str


class AnalyticsOverviewResponse(BaseModel):
    days: int
    total_conversations: int
    escalated_conversations: int
    answered_conversations: int
    escalation_rate: float | None
    deflection_rate: float | None
    csat_average: float | None
    csat_responses: int
    conversations_per_day: list[dict]
    top_unanswered: list[UnansweredClusterResponse]


def _to_response(overview: AnalyticsOverview) -> AnalyticsOverviewResponse:
    return AnalyticsOverviewResponse(
        days=overview.days,
        total_conversations=overview.total_conversations,
        escalated_conversations=overview.escalated_conversations,
        answered_conversations=overview.answered_conversations,
        escalation_rate=overview.escalation_rate,
        deflection_rate=overview.deflection_rate,
        csat_average=overview.csat_average,
        csat_responses=overview.csat_responses,
        conversations_per_day=overview.conversations_per_day,
        top_unanswered=[
            UnansweredClusterResponse(question=c.question, count=c.count, last_seen=c.last_seen)
            for c in overview.top_unanswered
        ],
    )


@router.get("/overview", response_model=AnalyticsOverviewResponse)
async def analytics_overview(
    membership: MembershipDep,
    sessionmaker: Annotated[async_sessionmaker[AsyncSession], Depends(get_analytics_sessionmaker)],
    embedding_service: Annotated[EmbeddingService, Depends(get_analytics_embedding_service)],
    redis_client: Annotated["redis.Redis | None", Depends(get_analytics_redis)],
    days: Annotated[int, Query(ge=1, le=365)] = 30,
) -> AnalyticsOverviewResponse:
    tenant_sm = tenant_sessionmaker(membership.org_id, session_factory=sessionmaker)
    overview = await compute_overview(
        tenant_sm,
        membership.org_id,
        days=days,
        embedding_service=embedding_service,
        redis_client=redis_client,
    )
    return _to_response(overview)


class QualityRunResponse(BaseModel):
    kind: str
    dataset: str
    item_count: int
    metrics: dict
    created_at: str


class QualityResponse(BaseModel):
    latest: QualityRunResponse | None
    trend: list[QualityRunResponse]


@router.get("/quality", response_model=QualityResponse)
async def analytics_quality(
    membership: MembershipDep,
    sessionmaker: Annotated[async_sessionmaker[AsyncSession], Depends(get_analytics_sessionmaker)],
) -> QualityResponse:
    """Latest eval metrics + trend (task 6.6). eval_runs is platform-level
    (no tenant data), readable by any authenticated member."""

    def to_response(run: EvalRun) -> QualityRunResponse:
        return QualityRunResponse(
            kind=run.kind,
            dataset=run.dataset,
            item_count=run.item_count,
            metrics=run.metrics,
            created_at=run.created_at.isoformat(),
        )

    async with sessionmaker() as session:
        latest = (
            (
                await session.execute(
                    select(EvalRun)
                    .where(EvalRun.kind.in_(("ci", "nightly", "local")))
                    .order_by(EvalRun.created_at.desc())
                    .limit(1)
                )
            )
            .scalars()
            .first()
        )
        trend_rows = (
            (await session.execute(select(EvalRun).order_by(EvalRun.created_at.desc()).limit(14)))
            .scalars()
            .all()
        )
    # Deployments may only have online-sampling rows (offline runs happen in
    # CI's ephemeral DB) — fall back so the Quality card still renders.
    if latest is None and trend_rows:
        latest = trend_rows[0]
    return QualityResponse(
        latest=to_response(latest) if latest else None,
        trend=[to_response(run) for run in reversed(trend_rows)],
    )
