import datetime as dt
import uuid

from pydantic import BaseModel, EmailStr, Field

from app.models import MembershipRole


class MemberResponse(BaseModel):
    user_id: uuid.UUID
    email: EmailStr
    name: str
    role: MembershipRole
    joined_at: dt.datetime


class InviteRequest(BaseModel):
    email: EmailStr
    role: MembershipRole = MembershipRole.viewer


class InviteResponse(BaseModel):
    id: uuid.UUID
    email: EmailStr
    role: MembershipRole
    expires_at: dt.datetime
    created_at: dt.datetime
    # Present only on creation: the raw token exists exactly once, in the link.
    invite_url: str | None = None


class InvitePreview(BaseModel):
    org_name: str
    email: EmailStr
    role: MembershipRole
    needs_account: bool


class RoleUpdateRequest(BaseModel):
    role: MembershipRole


class AcceptInviteRequest(BaseModel):
    name: str = Field(default="", max_length=255)
    password: str | None = Field(default=None, min_length=8, max_length=72)
