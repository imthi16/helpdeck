import uuid
from datetime import datetime

from pydantic import BaseModel, Field, model_validator

from app.models import DocumentSourceType, DocumentStatus


class DocumentCreate(BaseModel):
    """Create a URL or raw-text document (PDF uploads use the upload endpoint)."""

    source_type: DocumentSourceType
    title: str | None = Field(default=None, max_length=512)
    url: str | None = Field(default=None, max_length=2048)
    content: str | None = None

    @model_validator(mode="after")
    def _check(self) -> "DocumentCreate":
        if self.source_type == DocumentSourceType.url:
            if not self.url:
                raise ValueError("url is required for source_type=url")
        elif self.source_type == DocumentSourceType.text:
            if not self.content:
                raise ValueError("content is required for source_type=text")
        else:
            raise ValueError("use the upload endpoint for PDF documents")
        return self


class DocumentResponse(BaseModel):
    id: uuid.UUID
    title: str
    source_type: DocumentSourceType
    status: DocumentStatus
    error: str | None
    chunk_count: int
    created_at: datetime
