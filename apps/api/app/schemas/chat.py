import uuid

from pydantic import BaseModel, Field

from app.models import ConversationChannel


class ChatRequest(BaseModel):
    # org_id is supplied in the body until Phase 3 auth derives it from a token.
    org_id: uuid.UUID
    message: str = Field(min_length=1, max_length=4000)
    conversation_id: uuid.UUID | None = None
    channel: ConversationChannel = ConversationChannel.playground
    bypass_cache: bool = False
