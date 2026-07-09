import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import (
    Conversation,
    ConversationChannel,
    ConversationStatus,
    Escalation,
    EscalationStatus,
    Message,
    MessageRole,
    Organization,
)

Sessionmaker = async_sessionmaker[AsyncSession]


async def _make_org(session: AsyncSession) -> uuid.UUID:
    org = Organization(name=f"org-{uuid.uuid4()}")
    session.add(org)
    await session.flush()
    return org.id


async def test_conversation_message_escalation_crud(db_sessionmaker: Sessionmaker) -> None:
    async with db_sessionmaker() as session:
        org_id = await _make_org(session)
        conversation = Conversation(
            org_id=org_id,
            channel=ConversationChannel.playground,
            user_identifier="visitor-1",
        )
        session.add(conversation)
        await session.flush()

        assert conversation.status == ConversationStatus.open

        user_msg = Message(
            org_id=org_id,
            conversation_id=conversation.id,
            role=MessageRole.user,
            content="How do I descale?",
        )
        assistant_msg = Message(
            org_id=org_id,
            conversation_id=conversation.id,
            role=MessageRole.assistant,
            content="Descale every three months [1].",
            citations=[{"n": 1, "chunk_id": str(uuid.uuid4()), "document_title": "descaling"}],
            confidence=0.92,
            model_used="claude-sonnet-5",
            tokens_in=320,
            tokens_out=42,
            latency_ms=1180,
        )
        session.add_all([user_msg, assistant_msg])
        await session.commit()
        conversation_id = conversation.id

    # Read back
    async with db_sessionmaker() as session:
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
        assert [m.role for m in messages] == [MessageRole.user, MessageRole.assistant]
        assistant = messages[1]
        assert assistant.citations[0]["n"] == 1
        assert assistant.confidence == 0.92
        assert assistant.tokens_in == 320

    # Update: escalate the conversation and create an escalation row
    async with db_sessionmaker() as session:
        conversation = await session.get(Conversation, conversation_id)
        assert conversation is not None
        conversation.status = ConversationStatus.escalated
        escalation = Escalation(
            org_id=conversation.org_id,
            conversation_id=conversation.id,
            reason="low confidence",
        )
        session.add(escalation)
        await session.commit()
        escalation_id = escalation.id

    async with db_sessionmaker() as session:
        escalation = await session.get(Escalation, escalation_id)
        assert escalation is not None
        assert escalation.status == EscalationStatus.pending
        conversation = await session.get(Conversation, conversation_id)
        assert conversation.status == ConversationStatus.escalated

    # Resolve escalation
    async with db_sessionmaker() as session:
        escalation = await session.get(Escalation, escalation_id)
        escalation.status = EscalationStatus.resolved
        await session.commit()

    async with db_sessionmaker() as session:
        escalation = await session.get(Escalation, escalation_id)
        assert escalation.status == EscalationStatus.resolved

    # Delete conversation cascades to messages + escalations
    async with db_sessionmaker() as session:
        conversation = await session.get(Conversation, conversation_id)
        await session.delete(conversation)
        await session.commit()

    async with db_sessionmaker() as session:
        remaining_messages = (
            await session.execute(
                select(Message.id).where(Message.conversation_id == conversation_id)
            )
        ).all()
        remaining_escalations = (
            await session.execute(
                select(Escalation.id).where(Escalation.conversation_id == conversation_id)
            )
        ).all()
        assert remaining_messages == []
        assert remaining_escalations == []

    # Clean up org
    async with db_sessionmaker() as session:
        org = await session.get(Organization, org_id)
        await session.delete(org)
        await session.commit()
