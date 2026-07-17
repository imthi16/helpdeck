import uuid
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.main import app
from app.models import Document, DocumentStatus, Organization, User
from app.routers.auth import get_auth_sessionmaker
from app.routers.documents import (
    get_documents_sessionmaker,
    get_documents_storage,
    get_ingest_queue,
)
from app.services.storage import LocalFileStorage, document_key

FIXTURES = Path(__file__).parent / "fixtures"
Sessionmaker = async_sessionmaker[AsyncSession]


class RecordingQueue:
    def __init__(self) -> None:
        self.enqueued: list[uuid.UUID] = []

    async def enqueue_ingest(self, document_id: uuid.UUID, org_id: uuid.UUID) -> None:
        self.enqueued.append(document_id)


@pytest.fixture
def queue() -> RecordingQueue:
    return RecordingQueue()


@pytest.fixture(autouse=True)
def overrides(db_sessionmaker: Sessionmaker, tmp_path: Path, queue: RecordingQueue):
    storage = LocalFileStorage(tmp_path)
    app.dependency_overrides[get_auth_sessionmaker] = lambda: db_sessionmaker
    app.dependency_overrides[get_documents_sessionmaker] = lambda: db_sessionmaker
    app.dependency_overrides[get_documents_storage] = lambda: storage
    app.dependency_overrides[get_ingest_queue] = lambda: queue
    yield storage
    app.dependency_overrides.clear()


async def _signed_in_client(email: str | None = None) -> tuple[httpx.AsyncClient, str]:
    email = email or f"doc-{uuid.uuid4().hex[:10]}@example.com"
    transport = httpx.ASGITransport(app=app)
    client = httpx.AsyncClient(transport=transport, base_url="http://test")
    resp = await client.post(
        "/auth/signup",
        json={"email": email, "password": "hunter2pw", "name": "D", "org_name": "DocOrg"},
    )
    assert resp.status_code == 201
    return client, email


async def _cleanup(sm: Sessionmaker, email: str) -> None:
    from sqlalchemy.orm import selectinload

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


async def test_requires_auth(db_sessionmaker: Sessionmaker) -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        assert (await client.get("/api/v1/documents")).status_code == 401


async def test_upload_pdf_creates_pending_and_enqueues(
    db_sessionmaker: Sessionmaker, queue: RecordingQueue, overrides: LocalFileStorage
) -> None:
    client, email = await _signed_in_client()
    try:
        pdf = (FIXTURES / "sample.pdf").read_bytes()
        resp = await client.post(
            "/api/v1/documents/upload",
            files={"file": ("manual.pdf", pdf, "application/pdf")},
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["source_type"] == "pdf"
        assert body["status"] == "pending"
        assert body["title"] == "manual"
        assert body["chunk_count"] == 0

        document_id = uuid.UUID(body["id"])
        assert queue.enqueued == [document_id]
        # Raw bytes were stored for the worker to read.
        stored = await overrides.get(document_key(str(document_id)))
        assert stored == pdf

        listed = await client.get("/api/v1/documents")
        assert listed.status_code == 200
        assert any(d["id"] == body["id"] for d in listed.json())
    finally:
        await client.aclose()
        await _cleanup(db_sessionmaker, email)


async def test_upload_rejects_non_pdf(db_sessionmaker: Sessionmaker) -> None:
    client, email = await _signed_in_client()
    try:
        resp = await client.post(
            "/api/v1/documents/upload",
            files={"file": ("notes.txt", b"hello", "text/plain")},
        )
        assert resp.status_code == 415
    finally:
        await client.aclose()
        await _cleanup(db_sessionmaker, email)


async def test_create_text_and_url_documents(
    db_sessionmaker: Sessionmaker, queue: RecordingQueue, overrides: LocalFileStorage
) -> None:
    client, email = await _signed_in_client()
    try:
        text_resp = await client.post(
            "/api/v1/documents",
            json={"source_type": "text", "title": "Policy", "content": "# Returns\n30 days."},
        )
        assert text_resp.status_code == 201
        text_id = text_resp.json()["id"]
        stored = await overrides.get(document_key(text_id))
        assert b"Returns" in stored

        url_resp = await client.post(
            "/api/v1/documents",
            json={"source_type": "url", "url": "https://example.com/help"},
        )
        assert url_resp.status_code == 201
        assert url_resp.json()["title"] == "https://example.com/help"

        assert len(queue.enqueued) == 2
    finally:
        await client.aclose()
        await _cleanup(db_sessionmaker, email)


async def test_reindex_sets_pending_and_enqueues(
    db_sessionmaker: Sessionmaker, queue: RecordingQueue
) -> None:
    client, email = await _signed_in_client()
    try:
        created = await client.post(
            "/api/v1/documents",
            json={"source_type": "text", "title": "T", "content": "body"},
        )
        document_id = created.json()["id"]

        # Simulate the worker having marked it ready.
        async with db_sessionmaker() as session:
            doc = await session.get(Document, uuid.UUID(document_id))
            doc.status = DocumentStatus.ready
            await session.commit()

        queue.enqueued.clear()
        reindexed = await client.post(f"/api/v1/documents/{document_id}/reindex")
        assert reindexed.status_code == 200
        assert reindexed.json()["status"] == "pending"
        assert queue.enqueued == [uuid.UUID(document_id)]
    finally:
        await client.aclose()
        await _cleanup(db_sessionmaker, email)


async def test_delete_document(db_sessionmaker: Sessionmaker) -> None:
    client, email = await _signed_in_client()
    try:
        created = await client.post(
            "/api/v1/documents",
            json={"source_type": "text", "title": "T", "content": "body"},
        )
        document_id = created.json()["id"]

        deleted = await client.delete(f"/api/v1/documents/{document_id}")
        assert deleted.status_code == 204

        missing = await client.get(f"/api/v1/documents/{document_id}")
        assert missing.status_code == 404
    finally:
        await client.aclose()
        await _cleanup(db_sessionmaker, email)


async def test_cannot_access_other_orgs_document(db_sessionmaker: Sessionmaker) -> None:
    client_a, email_a = await _signed_in_client()
    client_b, email_b = await _signed_in_client()
    try:
        created = await client_a.post(
            "/api/v1/documents",
            json={"source_type": "text", "title": "Secret", "content": "body"},
        )
        document_id = created.json()["id"]

        # Org B cannot see or delete org A's document.
        assert (await client_b.get(f"/api/v1/documents/{document_id}")).status_code == 404
        assert (await client_b.delete(f"/api/v1/documents/{document_id}")).status_code == 404
    finally:
        await client_a.aclose()
        await client_b.aclose()
        await _cleanup(db_sessionmaker, email_a)
        await _cleanup(db_sessionmaker, email_b)
