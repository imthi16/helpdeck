"""Read-only audit log viewer API (task 5.4). Admin+; SELECT-only via RLS."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.db import app_session_factory, tenant_session
from app.core.deps import MembershipContext, require_role
from app.models import MembershipRole

router = APIRouter(prefix="/api/v1/audit-logs", tags=["audit"])


def get_audit_sessionmaker() -> async_sessionmaker[AsyncSession]:
    return app_session_factory


class AuditLogEntry(BaseModel):
    id: int
    actor_user_id: str | None
    actor_type: str
    action: str
    target_type: str | None
    target_id: str | None
    payload: dict[str, Any]
    ip: str | None
    created_at: str


SessionmakerDep = Annotated[async_sessionmaker[AsyncSession], Depends(get_audit_sessionmaker)]
AdminDep = Annotated[MembershipContext, Depends(require_role(MembershipRole.admin))]


@router.get("", response_model=list[AuditLogEntry])
async def list_audit_logs(
    sessionmaker: SessionmakerDep,
    caller: AdminDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    before: Annotated[int | None, Query()] = None,
) -> list[AuditLogEntry]:
    clauses = "WHERE org_id = :org" + (" AND id < :before" if before is not None else "")
    params: dict[str, Any] = {"org": str(caller.org_id), "limit": limit}
    if before is not None:
        params["before"] = before
    async with tenant_session(caller.org_id, session_factory=sessionmaker) as session:
        rows = (
            await session.execute(
                text(
                    "SELECT id, actor_user_id, actor_type, action, target_type, target_id,"
                    f" payload, ip, created_at FROM audit_logs {clauses}"
                    " ORDER BY id DESC LIMIT :limit"
                ),
                params,
            )
        ).mappings()
        return [
            AuditLogEntry(
                id=row["id"],
                actor_user_id=str(row["actor_user_id"]) if row["actor_user_id"] else None,
                actor_type=row["actor_type"],
                action=row["action"],
                target_type=row["target_type"],
                target_id=row["target_id"],
                payload=row["payload"],
                ip=row["ip"],
                created_at=row["created_at"].isoformat(),
            )
            for row in rows
        ]
