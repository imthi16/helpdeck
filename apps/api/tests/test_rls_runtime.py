"""The Phase 5.1 runtime cutover: requests are served under RLS.

`tests/test_rls.py` proves the database-level policies. This module proves the
*wiring*: that a real request handler receives a non-superuser session with
`app.current_tenant` set, so isolation no longer depends on remembering to write
`WHERE org_id = ...`. The probe route below issues a deliberately unscoped
`SELECT * FROM documents` through the same `TenantDbDep` the real routers use.
"""

import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from fastapi import APIRouter
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from app.main import app
from app.models import (
    Conversation,
    ConversationChannel,
    Document,
    DocumentSourceType,
    DocumentStatus,
    Organization,
    User,
)
from app.routers.auth import get_auth_sessionmaker
from app.routers.deps import TenantDbDep, get_app_session_factory

Sessionmaker = async_sessionmaker[AsyncSession]

probe_router = APIRouter(prefix="/test-only", tags=["test-only"])


@probe_router.get("/tenant-probe")
async def tenant_probe(db: TenantDbDep) -> dict[str, Any]:
    """Report who the handler is and what an unscoped query can see."""
    async with db() as session:
        is_superuser = await session.scalar(text("SHOW is_superuser"))
        tenant = await session.scalar(text("SELECT current_setting('app.current_tenant', true)"))
        titles = (await session.execute(text("SELECT title FROM documents"))).scalars().all()
    return {"is_superuser": is_superuser, "tenant": tenant, "titles": sorted(titles)}


@pytest.fixture(autouse=True)
def wired(db_sessionmaker: Sessionmaker, app_sessionmaker: Sessionmaker) -> AsyncIterator[None]:
    app.include_router(probe_router)
    app.dependency_overrides[get_auth_sessionmaker] = lambda: db_sessionmaker
    app.dependency_overrides[get_app_session_factory] = lambda: app_sessionmaker
    yield
    app.dependency_overrides.clear()
    app.router.routes = [
        route
        for route in app.router.routes
        if getattr(route, "path", "") != "/test-only/tenant-probe"
    ]


async def _signed_in(label: str) -> tuple[httpx.AsyncClient, str, uuid.UUID]:
    email = f"rt-{label}-{uuid.uuid4().hex[:8]}@example.com"
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
    resp = await client.post(
        "/auth/signup",
        json={"email": email, "password": "hunter2pw", "name": label, "org_name": f"org-{label}"},
    )
    assert resp.status_code == 201
    return client, email, uuid.UUID(resp.json()["memberships"][0]["org_id"])


async def _add_document(sm: Sessionmaker, org_id: uuid.UUID, title: str) -> uuid.UUID:
    """Seed as the superuser so the assertions test reads, not writes."""
    async with sm() as session:
        document = Document(
            org_id=org_id,
            title=title,
            source_type=DocumentSourceType.text,
            status=DocumentStatus.ready,
        )
        session.add(document)
        await session.commit()
        return document.id


async def _add_conversation(sm: Sessionmaker, org_id: uuid.UUID) -> uuid.UUID:
    async with sm() as session:
        conversation = Conversation(org_id=org_id, channel=ConversationChannel.playground)
        session.add(conversation)
        await session.commit()
        return conversation.id


async def _cleanup(sm: Sessionmaker, email: str) -> None:
    async with sm() as session:
        user = await session.scalar(
            select(User).where(User.email == email).options(selectinload(User.memberships))
        )
        if user is None:
            return
        org_ids = [m.org_id for m in user.memberships]
        await session.delete(user)
        await session.commit()
        for org_id in org_ids:
            org = await session.get(Organization, org_id)
            if org is not None:
                await session.delete(org)
        await session.commit()


async def test_handler_session_is_restricted_and_tenant_scoped(
    db_sessionmaker: Sessionmaker,
) -> None:
    client_a, email_a, org_a = await _signed_in("a")
    client_b, email_b, org_b = await _signed_in("b")
    try:
        await _add_document(db_sessionmaker, org_a, "runtime doc A")
        await _add_document(db_sessionmaker, org_b, "runtime doc B")

        probe_a = (await client_a.get("/test-only/tenant-probe")).json()
        probe_b = (await client_b.get("/test-only/tenant-probe")).json()

        # The app no longer serves requests as the superuser...
        assert probe_a["is_superuser"] == "off"
        # ...and each request carries its own tenant scope.
        assert probe_a["tenant"] == str(org_a)
        assert probe_b["tenant"] == str(org_b)

        # An unscoped SELECT — no WHERE org_id — still only sees the caller's rows.
        assert probe_a["titles"] == ["runtime doc A"]
        assert probe_b["titles"] == ["runtime doc B"]
    finally:
        await client_a.aclose()
        await client_b.aclose()
        await _cleanup(db_sessionmaker, email_a)
        await _cleanup(db_sessionmaker, email_b)


async def test_cross_tenant_reads_404_under_rls(db_sessionmaker: Sessionmaker) -> None:
    client_a, email_a, org_a = await _signed_in("a")
    client_b, email_b, _ = await _signed_in("b")
    try:
        document_id = await _add_document(db_sessionmaker, org_a, "private doc")
        conversation_id = await _add_conversation(db_sessionmaker, org_a)

        # Org A sees its own rows through the ordinary handlers.
        assert (await client_a.get(f"/api/v1/documents/{document_id}")).status_code == 200
        assert (await client_a.get(f"/api/v1/conversations/{conversation_id}")).status_code == 200

        # Org B does not — the rows are invisible to its tenant scope.
        assert (await client_b.get(f"/api/v1/documents/{document_id}")).status_code == 404
        assert (await client_b.delete(f"/api/v1/documents/{document_id}")).status_code == 404
        assert (await client_b.get(f"/api/v1/conversations/{conversation_id}")).status_code == 404
        listed = await client_b.get("/api/v1/conversations")
        assert listed.status_code == 200
        assert listed.json() == []
    finally:
        await client_a.aclose()
        await client_b.aclose()
        await _cleanup(db_sessionmaker, email_a)
        await _cleanup(db_sessionmaker, email_b)
