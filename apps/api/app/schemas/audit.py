import datetime as dt
import uuid
from typing import Any

from pydantic import BaseModel


class AuditEventResponse(BaseModel):
    id: uuid.UUID
    action: str
    actor_email: str | None
    target_type: str | None
    target_id: str | None
    metadata: dict[str, Any]
    created_at: dt.datetime
