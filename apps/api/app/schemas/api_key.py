import datetime as dt
import uuid

from pydantic import BaseModel, Field

from app.models import ApiKeyKind


class ApiKeyCreate(BaseModel):
    kind: ApiKeyKind = ApiKeyKind.secret
    name: str = Field(default="", max_length=120)


class ApiKeyResponse(BaseModel):
    """A key as the dashboard sees it — never the hash.

    ``secret`` is populated only in the response to a create call: that is the
    one and only time a secret key's value exists outside the caller's hands.
    Public keys carry their value in ``value`` because they are not secrets.
    """

    id: uuid.UUID
    name: str
    kind: ApiKeyKind
    prefix: str
    value: str | None = None
    scopes: list[str]
    last_used_at: dt.datetime | None
    revoked_at: dt.datetime | None
    created_at: dt.datetime
    secret: str | None = None
