"""Analytics endpoints. Read-only, so open to every role."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.routers.deps import MemberDep, OrgDep, TenantDbDep
from app.schemas.analytics import DayCountResponse, OverviewResponse, QuestionClusterResponse
from app.services import analytics as analytics_service
from app.services.embeddings import EmbeddingService

router = APIRouter(prefix="/api/v1/analytics", tags=["analytics"])

MAX_DAYS = 365


def get_analytics_embedding_service() -> EmbeddingService:
    return EmbeddingService()


@router.get("/overview", response_model=OverviewResponse)
async def overview(
    db: TenantDbDep,
    org_id: OrgDep,
    membership: MemberDep,
    embedding_service: Annotated[EmbeddingService, Depends(get_analytics_embedding_service)],
    days: Annotated[int, Query(ge=1, le=MAX_DAYS)] = 30,
) -> OverviewResponse:
    async with db() as session:
        result = await analytics_service.overview(
            session, org_id, days=days, embedding_service=embedding_service
        )
    return OverviewResponse(
        days=result.days,
        total_conversations=result.total_conversations,
        escalated_conversations=result.escalated_conversations,
        deflected_conversations=result.deflected_conversations,
        escalation_rate=result.escalation_rate,
        deflection_rate=result.deflection_rate,
        csat_average=result.csat_average,
        csat_responses=result.csat_responses,
        thumbs_up=result.thumbs_up,
        thumbs_down=result.thumbs_down,
        conversations_over_time=[
            DayCountResponse(day=point.day, conversations=point.conversations)
            for point in result.conversations_over_time
        ],
        top_unanswered=[
            QuestionClusterResponse(
                question=cluster.question, count=cluster.count, variants=cluster.variants
            )
            for cluster in result.top_unanswered
        ],
    )
