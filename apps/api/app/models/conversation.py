import enum
import uuid
from typing import Any

from sqlalchemy import (
    Enum,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class ConversationChannel(enum.StrEnum):
    playground = "playground"
    widget = "widget"
    api = "api"


class ConversationStatus(enum.StrEnum):
    open = "open"
    escalated = "escalated"
    closed = "closed"


class MessageRole(enum.StrEnum):
    user = "user"
    assistant = "assistant"
    system = "system"


class EscalationStatus(enum.StrEnum):
    pending = "pending"
    resolved = "resolved"


class Conversation(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "conversations"
    # Target for the composite (id, org_id) FKs from messages/escalations —
    # keeps a child's parent conversation in the same tenant (FKs bypass RLS).
    __table_args__ = (UniqueConstraint("id", "org_id", name="uq_conversations_id_org"),)

    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    channel: Mapped[ConversationChannel] = mapped_column(
        Enum(ConversationChannel, name="conversation_channel"), nullable=False
    )
    status: Mapped[ConversationStatus] = mapped_column(
        Enum(ConversationStatus, name="conversation_status"),
        nullable=False,
        default=ConversationStatus.open,
    )
    user_identifier: Mapped[str | None] = mapped_column(String(255), nullable=True)
    csat_score: Mapped[int | None] = mapped_column(Integer, nullable=True)


class Message(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "messages"
    __table_args__ = (
        # Composite FK: the parent conversation must share this message's org_id.
        ForeignKeyConstraint(
            ["conversation_id", "org_id"],
            ["conversations.id", "conversations.org_id"],
            ondelete="CASCADE",
            name="messages_conversation_org_fkey",
        ),
    )

    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )
    role: Mapped[MessageRole] = mapped_column(
        Enum(MessageRole, name="message_role"), nullable=False
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    citations: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    model_used: Mapped[str | None] = mapped_column(String(128), nullable=True)
    tokens_in: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tokens_out: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Langfuse/W3C trace id of the turn that produced this message (6.1).
    trace_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    feedback: Mapped[int | None] = mapped_column(Integer, nullable=True)  # -1 down, +1 up


class Escalation(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "escalations"
    __table_args__ = (
        # Composite FK: the parent conversation must share this escalation's org_id.
        ForeignKeyConstraint(
            ["conversation_id", "org_id"],
            ["conversations.id", "conversations.org_id"],
            ondelete="CASCADE",
            name="escalations_conversation_org_fkey",
        ),
    )

    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[EscalationStatus] = mapped_column(
        Enum(EscalationStatus, name="escalation_status"),
        nullable=False,
        default=EscalationStatus.pending,
    )
