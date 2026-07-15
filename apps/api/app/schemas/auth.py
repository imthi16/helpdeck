import uuid

from pydantic import BaseModel, EmailStr, Field

from app.models import MembershipRole


class SignupRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=72)
    name: str = Field(default="", max_length=255)
    # Exactly one path: create an org (org_name) or join one (invite_token).
    org_name: str | None = Field(default=None, min_length=1, max_length=255)
    invite_token: str | None = Field(default=None, min_length=16, max_length=128)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=72)


class OrgMembership(BaseModel):
    org_id: uuid.UUID
    org_name: str
    role: MembershipRole
    onboarded: bool


class UserResponse(BaseModel):
    id: uuid.UUID
    email: EmailStr
    name: str
    memberships: list[OrgMembership]
