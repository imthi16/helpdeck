import uuid

from pydantic import BaseModel, Field

from app.models import ConversationChannel


class ChatRequest(BaseModel):
    # Optional since 5.2: the org is derived from the caller's membership; if
    # supplied it must match (the endpoint 403s otherwise).
    org_id: uuid.UUID | None = None
    message: str = Field(min_length=1, max_length=4000)
    conversation_id: uuid.UUID | None = None
    channel: ConversationChannel = ConversationChannel.playground
    bypass_cache: bool = False
    debug: bool = False
