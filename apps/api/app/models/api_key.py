"""API keys: the org's public widget key(s) plus secret server-to-server keys.

Tenant table (FORCE RLS). Widget keys are public by design — the plaintext is
kept in ``public_value`` so the embed snippet can always be re-displayed.
Secret keys store only the sha256; the token is shown once at creation.
Pre-tenant lookup (widget auth) goes through the SECURITY DEFINER
``resolve_api_key`` function created in the migration, so other tenants' rows
stay invisible even there.
"""

import datetime
import enum
import uuid

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class ApiKeyType(enum.StrEnum):
    widget = "widget"
    secret = "secret"


class ApiKey(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "api_keys"

    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    key_type: Mapped[str] = mapped_column(String(16), nullable=False)
    scopes: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    # Display prefix, e.g. "pk_a1b2c3" / "sk_a1b2c3" — never the full token.
    prefix: Mapped[str] = mapped_column(String(16), nullable=False)
    secret_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    # Plaintext for WIDGET keys only (public by design); NULL for secret keys.
    public_value: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    last_used_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
