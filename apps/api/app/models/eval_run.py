"""Eval run results (task 6.3). Platform-level — no org_id, no RLS."""

import datetime
import uuid

from sqlalchemy import Boolean, DateTime, Float, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class EvalRun(Base):
    __tablename__ = "eval_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # ci|nightly|local|online
    git_sha: Mapped[str | None] = mapped_column(String(40), nullable=True)
    dataset: Mapped[str] = mapped_column(String(32), nullable=False, default="golden")
    item_count: Mapped[int] = mapped_column(Integer, nullable=False)
    model_config: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    metrics: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    thresholds: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    passed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    duration_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    report: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
