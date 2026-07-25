import datetime as dt
import enum
import hashlib
import secrets
import uuid

from sqlalchemy import ARRAY, Boolean, DateTime, Enum, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

INVITE_TTL_DAYS = 7


def sha256_hex(value: str) -> str:
    """Lookup hash for credentials we never want to store in the clear."""
    return hashlib.sha256(value.encode()).hexdigest()


def generate_public_key() -> str:
    return f"pk_{secrets.token_urlsafe(24)}"


def generate_secret_key() -> str:
    return f"sk_{secrets.token_urlsafe(32)}"


def generate_invite_token() -> str:
    return secrets.token_urlsafe(32)


def hash_invite_token(token: str) -> str:
    """Invites are stored hashed: the raw token only ever exists in the link."""
    return sha256_hex(token)


class MembershipRole(enum.StrEnum):
    owner = "owner"
    admin = "admin"
    agent = "agent"
    viewer = "viewer"


class ApiKeyKind(enum.StrEnum):
    #: Embedded in a page by the widget — identifies the org, is not a secret.
    public = "public"
    #: Server-to-server credential. Stored hashed, shown once at creation.
    secret = "secret"


DEFAULT_SCOPES: dict[ApiKeyKind, list[str]] = {
    ApiKeyKind.public: ["widget:chat"],
    ApiKeyKind.secret: ["chat:write", "documents:read"],
}


class Organization(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "organizations"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    onboarded: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false", default=False
    )
    widget_allowed_origins: Mapped[str] = mapped_column(
        String(2048), nullable=False, server_default="", default=""
    )
    widget_welcome_message: Mapped[str] = mapped_column(
        String(500), nullable=False, server_default="Hi! How can I help you today?"
    )
    widget_color: Mapped[str] = mapped_column(String(16), nullable=False, server_default="#4f46e5")

    memberships: Mapped[list["Membership"]] = relationship(
        back_populates="organization", cascade="all, delete-orphan"
    )
    api_keys: Mapped[list["ApiKey"]] = relationship(
        back_populates="organization", cascade="all, delete-orphan"
    )


class User(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False, default="")

    memberships: Mapped[list["Membership"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class Membership(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "memberships"
    __table_args__ = (UniqueConstraint("org_id", "user_id", name="uq_memberships_org_user"),)

    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role: Mapped[MembershipRole] = mapped_column(
        Enum(MembershipRole, name="membership_role"), nullable=False
    )

    organization: Mapped["Organization"] = relationship(back_populates="memberships")
    user: Mapped["User"] = relationship(back_populates="memberships")


class ApiKey(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A named, revocable credential for one org.

    Both kinds are looked up by ``key_hash``, so authentication never compares a
    raw secret. Public keys additionally keep their plaintext ``value``: they are
    embedded in page HTML, are not secrets, and the dashboard has to render them
    in the widget snippet. Secret keys keep ``value`` NULL — they are shown once
    at creation and cannot be recovered afterwards.

    Deliberately NOT under RLS (like ``users``/``memberships``/``invitations``):
    the widget resolves a key to an org *before* any tenant context exists, so
    this table is reached through the identity session and is not granted to the
    app role. See docs/NOTES.md.
    """

    __tablename__ = "api_keys"

    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    kind: Mapped[ApiKeyKind] = mapped_column(Enum(ApiKeyKind, name="api_key_kind"), nullable=False)
    #: Leading characters, for identifying a key in the UI without revealing it.
    prefix: Mapped[str] = mapped_column(String(16), nullable=False)
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    #: Public keys only; NULL for secrets.
    value: Mapped[str | None] = mapped_column(String(128), nullable=True)
    scopes: Mapped[list[str]] = mapped_column(
        ARRAY(String(64)), nullable=False, server_default="{}"
    )
    last_used_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    organization: Mapped["Organization"] = relationship(back_populates="api_keys")

    @property
    def active(self) -> bool:
        return self.revoked_at is None


class Invitation(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A pending invite to join an org, redeemed with a single-use token.

    Deliberately NOT under RLS (like ``users``/``memberships``): redeeming a token
    is a cross-tenant lookup that happens before any org context exists, so this
    table is reached through the identity session and is not granted to the app
    role. See docs/NOTES.md.
    """

    __tablename__ = "invitations"
    __table_args__ = (
        # One live invite per email per org; accepted ones are kept for history.
        Index(
            "uq_invitations_org_email_pending",
            "org_id",
            "email",
            unique=True,
            postgresql_where="accepted_at IS NULL",
        ),
    )

    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    role: Mapped[MembershipRole] = mapped_column(
        Enum(MembershipRole, name="membership_role"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    invited_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accepted_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    organization: Mapped["Organization"] = relationship()
