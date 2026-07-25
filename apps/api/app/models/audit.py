"""Append-only audit trail.

Append-only is enforced by *privileges*, not convention: the migration grants the
app role ``SELECT, INSERT`` on this table and nothing else, so the application
literally cannot rewrite or erase history. The superuser keeps full rights so
``ON DELETE CASCADE`` from ``organizations`` and test cleanup still work.

No RLS, like the other identity-adjacent tables (``users``, ``memberships``,
``invitations``, ``api_keys``): some events (a login) happen before any org
context exists. Reads are scoped by an explicit ``org_id`` filter — see
docs/NOTES.md.
"""

import datetime as dt
import enum
import uuid
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPrimaryKeyMixin


class AuditAction(enum.StrEnum):
    """Stable action names. Stored as text so old rows survive new values."""

    auth_signup = "auth.signup"
    auth_login = "auth.login"
    member_invited = "member.invited"
    member_invite_revoked = "member.invite_revoked"
    member_joined = "member.joined"
    member_role_changed = "member.role_changed"
    member_removed = "member.removed"
    api_key_created = "api_key.created"
    api_key_revoked = "api_key.revoked"
    document_deleted = "document.deleted"
    settings_updated = "settings.updated"


class AuditLog(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "audit_logs"
    __table_args__ = (Index("ix_audit_logs_org_created", "org_id", "created_at"),)

    #: Nullable: auth events can precede org context.
    org_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True
    )
    #: SET NULL, plus a denormalised email, so deleting a user cannot erase who
    #: did what.
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    actor_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    target_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    target_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: Small, non-sensitive detail only (roles, key kinds, titles) — never
    #: secrets, tokens, or document content.
    meta: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict, server_default="{}"
    )
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Only created_at: an audit row is never updated, so there is no updated_at.
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
