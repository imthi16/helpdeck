import uuid

from pydantic import BaseModel, Field


class WidgetConfig(BaseModel):
    org_name: str
    welcome_message: str
    color: str


class WidgetChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    conversation_id: uuid.UUID | None = None
    user_identifier: str | None = Field(default=None, max_length=255)


class WidgetFeedbackRequest(BaseModel):
    message_id: uuid.UUID
    rating: int = Field(ge=-1, le=1)  # -1 down, +1 up
