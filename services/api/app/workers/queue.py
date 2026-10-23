"""Durable DB-backed job queue (no in-memory background tasks).

* Jobs are rows; enqueueing with an ``idempotency_key`` never creates duplicates.
* A worker claims a job with a conditional UPDATE (works on SQLite and PostgreSQL).
* Failures are retried with exponential backoff up to ``max_attempts``; then the
  job is marked ``dead`` and stays visible for inspection.
* Jobs left ``running`` by a crashed worker are re-queued after a timeout.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any, cast

from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.base import session_scope, utcnow
from app.models import Job

STALE_RUNNING = timedelta(minutes=30)


def enqueue(
    s: Session, type: str, payload: dict[str, Any], idempotency_key: str | None = None, max_attempts: int = 3
) -> Job:
    if idempotency_key:
        existing = s.scalars(select(Job).where(Job.idempotency_key == idempotency_key)).first()
        if existing:
            return existing
    job = Job(type=type, payload=payload, idempotency_key=idempotency_key, max_attempts=max_attempts)
    try:
        with s.begin_nested():
            s.add(job)
            s.flush()
    except IntegrityError:
        found = s.scalars(select(Job).where(Job.idempotency_key == idempotency_key)).first()
        assert found is not None
        return found
    return job


def claim(worker_id: str, types: list[str] | None = None) -> Job | None:
    now = utcnow()
    with session_scope() as s:
        # recover jobs orphaned by a crashed worker
        s.execute(
            update(Job)
            .where(Job.status == "running", Job.locked_at < now - STALE_RUNNING)
            .values(status="queued", locked_by=None, error="re-queued after worker timeout")
        )
        q = (
            select(Job.id)
            .where(Job.status == "queued", Job.run_after <= now)
            .order_by(Job.created_at)
            .limit(5)
        )
        if types:
            q = q.where(Job.type.in_(types))
        for jid in s.scalars(q).all():
            res = s.execute(
                update(Job)
                .where(Job.id == jid, Job.status == "queued")
                .values(
                    status="running",
                    locked_by=worker_id,
                    locked_at=now,
                    started_at=now,
                    attempts=Job.attempts + 1,
                )
            )
            if cast(CursorResult, res).rowcount == 1:
                s.flush()
                return s.get(Job, jid)
    return None


def complete(job_id: str, result: dict[str, Any] | None = None) -> None:
    with session_scope() as s:
        j = s.get(Job, job_id)
        if j:
            j.status, j.result, j.finished_at, j.error = "succeeded", result or {}, utcnow(), None


def fail(job_id: str, error: str) -> None:
    with session_scope() as s:
        j = s.get(Job, job_id)
        if j is None:
            return
        j.error = error[:4000]
        if j.attempts >= j.max_attempts:
            j.status, j.finished_at = "dead", utcnow()
        else:
            j.status = "queued"
            j.locked_by = None
            j.run_after = utcnow() + timedelta(seconds=min(300, 5 * 2**j.attempts))
