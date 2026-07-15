"""Dev-only internal routes. Gated behind ENABLE_INTERNAL_ROUTES."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.core.db import app_session_factory, tenant_sessionmaker
from app.schemas.search import SearchRequest, SearchResponse, SearchResultChunk
from app.services.embeddings import EmbeddingService
from app.services.reranker import Reranker, get_reranker, retrieve_reranked
from app.services.retrieval import HybridRetriever

router = APIRouter(prefix="/internal", tags=["internal"])


def require_internal_enabled() -> None:
    if not get_settings().enable_internal_routes:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")


def get_search_sessionmaker() -> async_sessionmaker[AsyncSession]:
    return app_session_factory


def get_search_embedding_service() -> EmbeddingService:
    return EmbeddingService()


def get_search_reranker() -> Reranker:
    return get_reranker()


@router.post(
    "/search",
    response_model=SearchResponse,
    dependencies=[Depends(require_internal_enabled)],
)
async def internal_search(
    request: SearchRequest,
    sessionmaker: Annotated[async_sessionmaker[AsyncSession], Depends(get_search_sessionmaker)],
    embedding_service: Annotated[EmbeddingService, Depends(get_search_embedding_service)],
    reranker: Annotated[Reranker, Depends(get_search_reranker)],
) -> SearchResponse:
    retriever = HybridRetriever(
        tenant_sessionmaker(request.org_id, session_factory=sessionmaker), embedding_service
    )
    candidates = max(50, request.top_n)
    results = await retrieve_reranked(
        retriever,
        reranker,
        request.org_id,
        request.query,
        candidates=candidates,
        top_n=request.top_n,
    )
    return SearchResponse(
        query=request.query,
        results=[SearchResultChunk.from_scored(chunk) for chunk in results],
    )
