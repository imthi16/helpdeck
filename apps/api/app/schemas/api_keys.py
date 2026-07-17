import datetime
import uuid

from pydantic import BaseModel, Field

from app.models import ApiKeyType


class ApiKeyResponse(BaseModel):
    id: uuid.UUID
    name: str
    key_type: ApiKeyType
    prefix: str
    # Plaintext for widget keys (public by design); None for secret keys.
    public_value: str | None
    scopes: list[str]
    last_used_at: datetime.datetime | None
    revoked_at: datetime.datetime | None
    created_at: datetime.datetime


class ApiKeyCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    key_type: ApiKeyType = ApiKeyType.secret


class ApiKeyCreatedResponse(ApiKeyResponse):
    """Returned only at creation — ``token`` is never recoverable for secret
    keys afterwards (only its hash is stored)."""

    token: str
