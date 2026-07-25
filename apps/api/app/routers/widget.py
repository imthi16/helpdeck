"""Public widget API: config, chat (SSE), feedback.

Auth is via the ``X-Public-Key`` header mapped to an org. Each org may pin an
Origin allowlist, and requests are rate limited per key and per IP.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sse_starlette.sse import EventSourceResponse

from app.core.db import TenantSessionFactory, async_session_factory
from app.models import (
    ApiKey,
    ApiKeyKind,
    Conversation,
    ConversationChannel,
    Message,
    MessageRole,
    Organization,
)
from app.routers.chat import (
    get_chat_cache,
    get_chat_checkpointer,
    get_chat_gateway,
    run_chat_stream,
)
from app.routers.deps import AppSessionFactoryDep
from app.schemas.widget import (
    WidgetChatRequest,
    WidgetConfig,
    WidgetCsatRequest,
    WidgetFeedbackRequest,
)
from app.services import api_keys as api_keys_service
from app.services.cache import ResponseCache
from app.services.llm import LLMGateway
from app.services.rate_limit import RateLimiter

router = APIRouter(prefix="/api/v1/widget", tags=["widget"])

WIDGET_RATE_LIMIT_PER_MINUTE = 30


def get_widget_sessionmaker() -> async_sessionmaker[AsyncSession]:
    """Identity session: the key -> org lookup reads ``api_keys``/``organizations``,
    which have no RLS and are not granted to the app role."""
    return async_session_factory


def get_widget_rate_limiter(request: Request) -> RateLimiter | None:
    cache = get_chat_cache()  # reuses the shared Redis client factory
    client = getattr(cache, "_client", None)
    if client is None:
        return None
    return RateLimiter(client, limit=WIDGET_RATE_LIMIT_PER_MINUTE, window_seconds=60)


async def _org_for_key(
    sessionmaker: async_sessionmaker[AsyncSession], public_key: str | None
) -> tuple[Organization, ApiKey]:
    """Resolve the ``X-Public-Key`` header to its org, recording the usage.

    Revoked keys do not resolve, and nothing caches this lookup, so revoking a
    key takes effect on the very next request.
    """
    if not public_key:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="missing public key")
    async with sessionmaker() as session:
        key = await api_keys_service.resolve_key(session, public_key)
        if key is None or key.kind != ApiKeyKind.public:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid public key"
            )
        org = key.organization
        await api_keys_service.touch_last_used(session, key)
    return org, key


def _check_origin(org: Organization, origin: str | None) -> None:
    allowed = [o.strip() for o in org.widget_allowed_origins.split(",") if o.strip()]
    if not allowed:
        return  # No allowlist configured -> permit any origin (dev-friendly).
    if origin not in allowed:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="origin not allowed")


async def _enforce_rate_limit(limiter: RateLimiter | None, key: ApiKey, request: Request) -> None:
    if limiter is None:
        return
    ip = request.client.host if request.client else "unknown"
    for identifier in (f"key:{key.id}", f"ip:{ip}"):
        result = await limiter.hit(identifier)
        if not result.allowed:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="rate limit exceeded",
                headers={"Retry-After": str(result.retry_after)},
            )


SessionmakerDep = Annotated[async_sessionmaker[AsyncSession], Depends(get_widget_sessionmaker)]
LimiterDep = Annotated["RateLimiter | None", Depends(get_widget_rate_limiter)]


@router.get("/config", response_model=WidgetConfig)
async def widget_config(
    sessionmaker: SessionmakerDep,
    limiter: LimiterDep,
    request: Request,
    x_public_key: Annotated[str | None, Header()] = None,
    origin: Annotated[str | None, Header()] = None,
) -> WidgetConfig:
    org, key = await _org_for_key(sessionmaker, x_public_key)
    _check_origin(org, origin)
    await _enforce_rate_limit(limiter, key, request)
    return WidgetConfig(
        org_name=org.name,
        welcome_message=org.widget_welcome_message,
        color=org.widget_color,
    )


@router.post("/chat")
async def widget_chat(
    payload: WidgetChatRequest,
    sessionmaker: SessionmakerDep,
    app_session_factory: AppSessionFactoryDep,
    limiter: LimiterDep,
    request: Request,
    gateway: Annotated[LLMGateway, Depends(get_chat_gateway)],
    cache: Annotated[ResponseCache, Depends(get_chat_cache)],
    x_public_key: Annotated[str | None, Header()] = None,
    origin: Annotated[str | None, Header()] = None,
) -> EventSourceResponse:
    org, key = await _org_for_key(sessionmaker, x_public_key)
    _check_origin(org, origin)
    await _enforce_rate_limit(limiter, key, request)

    checkpointer = get_chat_checkpointer(request)
    return await run_chat_stream(
        db=TenantSessionFactory(org.id, session_factory=app_session_factory),
        gateway=gateway,
        checkpointer=checkpointer,
        cache=cache,
        org_id=org.id,
        message=payload.message,
        conversation_id=payload.conversation_id,
        channel=ConversationChannel.widget,
        user_identifier=payload.user_identifier,
    )


@router.post("/feedback", status_code=status.HTTP_204_NO_CONTENT)
async def widget_feedback(
    payload: WidgetFeedbackRequest,
    sessionmaker: SessionmakerDep,
    app_session_factory: AppSessionFactoryDep,
    limiter: LimiterDep,
    request: Request,
    response: Response,
    x_public_key: Annotated[str | None, Header()] = None,
    origin: Annotated[str | None, Header()] = None,
) -> None:
    org, key = await _org_for_key(sessionmaker, x_public_key)
    _check_origin(org, origin)
    await _enforce_rate_limit(limiter, key, request)

    db = TenantSessionFactory(org.id, session_factory=app_session_factory)
    async with db() as session:
        message = await session.get(Message, payload.message_id)
        if message is None or message.org_id != org.id or message.role != MessageRole.assistant:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="message not found")
        message.feedback = payload.rating


@router.post("/csat", status_code=status.HTTP_204_NO_CONTENT)
async def widget_csat(
    payload: WidgetCsatRequest,
    sessionmaker: SessionmakerDep,
    app_session_factory: AppSessionFactoryDep,
    limiter: LimiterDep,
    request: Request,
    x_public_key: Annotated[str | None, Header()] = None,
    origin: Annotated[str | None, Header()] = None,
) -> None:
    """Record a 1-5 satisfaction score for one conversation.

    A rating is write-once: re-rating would silently move the org's average, and
    the widget only asks once per conversation anyway.
    """
    org, key = await _org_for_key(sessionmaker, x_public_key)
    _check_origin(org, origin)
    await _enforce_rate_limit(limiter, key, request)

    db = TenantSessionFactory(org.id, session_factory=app_session_factory)
    async with db() as session:
        conversation = await session.get(Conversation, payload.conversation_id)
        if conversation is None or conversation.org_id != org.id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="conversation not found"
            )
        if conversation.csat_score is not None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="already rated")
        conversation.csat_score = payload.score
