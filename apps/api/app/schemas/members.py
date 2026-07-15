import datetime
import uuid

from pydantic import BaseModel, EmailStr, Field

from app.models import MembershipRole


class MemberResponse(BaseModel):
    user_id: uuid.UUID
    email: EmailStr
    name: str
    role: MembershipRole
    created_at: datetime.datetime


class RoleUpdateRequest(BaseModel):
    role: MembershipRole


class InviteCreateRequest(BaseModel):
    email: EmailStr
    role: MembershipRole = MembershipRole.agent


class InviteResponse(BaseModel):
    id: uuid.UUID
    email: EmailStr
    role: MembershipRole
    expires_at: datetime.datetime
    created_at: datetime.datetime


class InviteCreatedResponse(InviteResponse):
    """Returned only at creation time — the URL embeds the raw token and is
    never recoverable afterwards (only its hash is stored)."""

    invite_url: str


class AcceptInviteRequest(BaseModel):
    token: str = Field(min_length=16, max_length=128)
