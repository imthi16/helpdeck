"""Dev-only internal routes. Gated behind ENABLE_INTERNAL_ROUTES."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from app.core.config import get_settings
from app.core.db import TenantSessionFactory
from app.routers.deps import AppSessionFactoryDep
from app.schemas.search import SearchRequest, SearchResponse, SearchResultChunk
from app.services.embeddings import EmbeddingService
from app.services.reranker import Reranker, get_reranker, retrieve_reranked
from app.services.retrieval import HybridRetriever

router = APIRouter(prefix="/internal", tags=["internal"])


def require_internal_enabled() -> None:
    if not get_settings().enable_internal_routes:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")


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
    session_factory: AppSessionFactoryDep,
    embedding_service: Annotated[EmbeddingService, Depends(get_search_embedding_service)],
    reranker: Annotated[Reranker, Depends(get_search_reranker)],
) -> SearchResponse:
    db = TenantSessionFactory(request.org_id, session_factory=session_factory)
    retriever = HybridRetriever(db, embedding_service)
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
