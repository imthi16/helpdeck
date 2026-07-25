"""Conversations inbox: list, transcript, resolve, and internal reply."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Conversation,
    ConversationChannel,
    ConversationStatus,
    Escalation,
    EscalationStatus,
    Message,
    MessageRole,
)
from app.routers.deps import AGENT_ROLES, OrgDep, TenantDbDep, require_role
from app.schemas.conversation import (
    ConversationDetail,
    ConversationSummary,
    EscalationResponse,
    MessageResponse,
    ReplyRequest,
)

router = APIRouter(prefix="/api/v1/conversations", tags=["conversations"])


async def _load_owned(
    session: AsyncSession, conversation_id: uuid.UUID, org_id: uuid.UUID
) -> Conversation:
    conversation = await session.get(Conversation, conversation_id)
    if conversation is None or conversation.org_id != org_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="conversation not found")
    return conversation


@router.get("", response_model=list[ConversationSummary])
async def list_conversations(
    db: TenantDbDep,
    org_id: OrgDep,
    status_filter: Annotated[ConversationStatus | None, Query(alias="status")] = None,
    channel: ConversationChannel | None = None,
) -> list[ConversationSummary]:
    filters = [Conversation.org_id == org_id]
    if status_filter is not None:
        filters.append(Conversation.status == status_filter)
    if channel is not None:
        filters.append(Conversation.channel == channel)

    async with db() as session:
        rows = (
            await session.execute(
                select(Conversation, func.count(Message.id))
                .outerjoin(Message, Message.conversation_id == Conversation.id)
                .where(*filters)
                .group_by(Conversation.id)
                .order_by(Conversation.created_at.desc())
            )
        ).all()
    return [
        ConversationSummary(
            id=c.id,
            channel=c.channel,
            status=c.status,
            user_identifier=c.user_identifier,
            csat_score=c.csat_score,
            message_count=count,
            created_at=c.created_at,
        )
        for c, count in rows
    ]


@router.get("/{conversation_id}", response_model=ConversationDetail)
async def get_conversation(
    conversation_id: uuid.UUID, db: TenantDbDep, org_id: OrgDep
) -> ConversationDetail:
    async with db() as session:
        conversation = await _load_owned(session, conversation_id, org_id)
        messages = (
            (
                await session.execute(
                    select(Message)
                    .where(Message.conversation_id == conversation_id)
                    .order_by(Message.created_at)
                )
            )
            .scalars()
            .all()
        )
        escalations = (
            (
                await session.execute(
                    select(Escalation)
                    .where(Escalation.conversation_id == conversation_id)
                    .order_by(Escalation.created_at)
                )
            )
            .scalars()
            .all()
        )

    return ConversationDetail(
        id=conversation.id,
        channel=conversation.channel,
        status=conversation.status,
        user_identifier=conversation.user_identifier,
        csat_score=conversation.csat_score,
        message_count=len(messages),
        created_at=conversation.created_at,
        messages=[
            MessageResponse(
                id=m.id,
                role=m.role,
                content=m.content,
                citations=m.citations,
                confidence=m.confidence,
                created_at=m.created_at,
            )
            for m in messages
        ],
        escalations=[
            EscalationResponse(id=e.id, reason=e.reason, status=e.status, created_at=e.created_at)
            for e in escalations
        ],
    )


@router.post(
    "/{conversation_id}/resolve",
    response_model=ConversationDetail,
    dependencies=[Depends(require_role(*AGENT_ROLES))],
)
async def resolve_conversation(
    conversation_id: uuid.UUID, db: TenantDbDep, org_id: OrgDep
) -> ConversationDetail:
    async with db() as session:
        conversation = await _load_owned(session, conversation_id, org_id)
        conversation.status = ConversationStatus.closed
        pending = (
            (
                await session.execute(
                    select(Escalation).where(
                        Escalation.conversation_id == conversation_id,
                        Escalation.status == EscalationStatus.pending,
                    )
                )
            )
            .scalars()
            .all()
        )
        for escalation in pending:
            escalation.status = EscalationStatus.resolved
    return await get_conversation(conversation_id, db, org_id)


@router.post(
    "/{conversation_id}/reply",
    response_model=ConversationDetail,
    dependencies=[Depends(require_role(*AGENT_ROLES))],
)
async def reply_to_conversation(
    conversation_id: uuid.UUID,
    payload: ReplyRequest,
    db: TenantDbDep,
    org_id: OrgDep,
) -> ConversationDetail:
    async with db() as session:
        conversation = await _load_owned(session, conversation_id, org_id)
        session.add(
            Message(
                org_id=org_id,
                conversation_id=conversation.id,
                role=MessageRole.assistant,
                content=payload.content,
            )
        )
    return await get_conversation(conversation_id, db, org_id)
