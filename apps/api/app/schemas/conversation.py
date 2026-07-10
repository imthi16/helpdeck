import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.models import (
    ConversationChannel,
    ConversationStatus,
    EscalationStatus,
    MessageRole,
)


class ConversationSummary(BaseModel):
    id: uuid.UUID
    channel: ConversationChannel
    status: ConversationStatus
    user_identifier: str | None
    csat_score: int | None
    message_count: int
    created_at: datetime


class MessageResponse(BaseModel):
    id: uuid.UUID
    role: MessageRole
    content: str
    citations: list[dict[str, Any]]
    confidence: float | None
    created_at: datetime


class EscalationResponse(BaseModel):
    id: uuid.UUID
    reason: str
    status: EscalationStatus
    created_at: datetime


class ConversationDetail(ConversationSummary):
    messages: list[MessageResponse]
    escalations: list[EscalationResponse]


class ReplyRequest(BaseModel):
    content: str = Field(min_length=1, max_length=4000)
