"""Public widget API: config, chat (SSE), feedback.

Auth is via the ``X-Public-Key`` header mapped to an org. Each org may pin an
Origin allowlist, and requests are rate limited per key and per IP.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sse_starlette.sse import EventSourceResponse

from app.core.db import app_session_factory, tenant_session
from app.models import ConversationChannel, Message, MessageRole, Organization
from app.routers.chat import (
    get_chat_cache,
    get_chat_checkpointer,
    get_chat_gateway,
    run_chat_stream,
)
from app.schemas.widget import WidgetChatRequest, WidgetConfig, WidgetFeedbackRequest
from app.services.cache import ResponseCache
from app.services.llm import LLMGateway
from app.services.rate_limit import RateLimiter

router = APIRouter(prefix="/api/v1/widget", tags=["widget"])

WIDGET_RATE_LIMIT_PER_MINUTE = 30


def get_widget_sessionmaker() -> async_sessionmaker[AsyncSession]:
    # App-role base factory. Key->org resolution runs as a plain identity-lane
    # session (it happens before the tenant is known); everything after wraps
    # this base in a tenant session so RLS is enforced.
    return app_session_factory


def get_widget_rate_limiter(request: Request) -> RateLimiter | None:
    cache = get_chat_cache()  # reuses the shared Redis client factory
    client = getattr(cache, "_client", None)
    if client is None:
        return None
    return RateLimiter(client, limit=WIDGET_RATE_LIMIT_PER_MINUTE, window_seconds=60)


async def _org_for_key(
    sessionmaker: async_sessionmaker[AsyncSession], public_key: str | None
) -> Organization:
    if not public_key:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="missing public key")
    async with sessionmaker() as session:
        org = await session.scalar(
            select(Organization).where(Organization.public_key == public_key)
        )
    if org is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid public key")
    return org


def _check_origin(org: Organization, origin: str | None) -> None:
    allowed = [o.strip() for o in org.widget_allowed_origins.split(",") if o.strip()]
    if not allowed:
        return  # No allowlist configured -> permit any origin (dev-friendly).
    if origin not in allowed:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="origin not allowed")


async def _enforce_rate_limit(
    limiter: RateLimiter | None, org: Organization, request: Request
) -> None:
    if limiter is None:
        return
    ip = request.client.host if request.client else "unknown"
    for identifier in (f"key:{org.public_key}", f"ip:{ip}"):
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
    org = await _org_for_key(sessionmaker, x_public_key)
    _check_origin(org, origin)
    await _enforce_rate_limit(limiter, org, request)
    return WidgetConfig(
        org_name=org.name,
        welcome_message=org.widget_welcome_message,
        color=org.widget_color,
    )


@router.post("/chat")
async def widget_chat(
    payload: WidgetChatRequest,
    sessionmaker: SessionmakerDep,
    limiter: LimiterDep,
    request: Request,
    gateway: Annotated[LLMGateway, Depends(get_chat_gateway)],
    cache: Annotated[ResponseCache, Depends(get_chat_cache)],
    x_public_key: Annotated[str | None, Header()] = None,
    origin: Annotated[str | None, Header()] = None,
) -> EventSourceResponse:
    org = await _org_for_key(sessionmaker, x_public_key)
    _check_origin(org, origin)
    await _enforce_rate_limit(limiter, org, request)

    checkpointer = get_chat_checkpointer(request)
    return await run_chat_stream(
        sessionmaker=sessionmaker,
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
    limiter: LimiterDep,
    request: Request,
    response: Response,
    x_public_key: Annotated[str | None, Header()] = None,
    origin: Annotated[str | None, Header()] = None,
) -> None:
    org = await _org_for_key(sessionmaker, x_public_key)
    _check_origin(org, origin)
    await _enforce_rate_limit(limiter, org, request)

    async with tenant_session(org.id, session_factory=sessionmaker) as session:
        message = await session.get(Message, payload.message_id)
        if message is None or message.org_id != org.id or message.role != MessageRole.assistant:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="message not found")
        message.feedback = payload.rating
