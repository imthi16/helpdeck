"""Append-only audit trail (task 5.4).

``record_audit`` is the single write path: it calls the SECURITY DEFINER
``audit_log_insert`` SQL function, because the app role has no INSERT (or
UPDATE/DELETE) privilege on ``audit_logs``. Call it inside the same session/
transaction as the mutation it describes so the audit row commits atomically
with the action. Payloads must stay small and PII-free (no document contents,
no message bodies) — record identifiers and changed fields only.
"""

import json
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# Canonical action names (keep kebab-free dotted style: <target>.<verb>).
AUTH_LOGIN = "auth.login"
AUTH_SIGNUP = "auth.signup"
MEMBER_INVITED = "member.invited"
MEMBER_JOINED = "member.joined"
MEMBER_ROLE_CHANGED = "member.role_changed"
MEMBER_REMOVED = "member.removed"
INVITE_REVOKED = "invite.revoked"
KEY_CREATED = "key.created"
KEY_REVOKED = "key.revoked"
DOCUMENT_DELETED = "document.deleted"
SETTINGS_UPDATED = "settings.updated"


async def record_audit(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    action: str,
    actor_user_id: uuid.UUID | None = None,
    actor_type: str = "user",
    target_type: str | None = None,
    target_id: str | None = None,
    payload: dict[str, Any] | None = None,
    ip: str | None = None,
) -> None:
    await session.execute(
        text(
            "SELECT audit_log_insert(:org_id, :actor_user_id, :actor_type, :action,"
            " :target_type, :target_id, cast(:payload AS jsonb), :ip)"
        ),
        {
            "org_id": str(org_id),
            "actor_user_id": str(actor_user_id) if actor_user_id else None,
            "actor_type": actor_type,
            "action": action,
            "target_type": target_type,
            "target_id": target_id,
            "payload": json.dumps(payload or {}),
            "ip": ip,
        },
    )
