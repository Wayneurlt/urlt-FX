"""Worker process: ``python -m app.workers.runner``.

Two loops in one process:
* trading loop - every ``FX_WORKER_TICK_SECONDS``: reconcile and manage all open
  exposure, enforce risk halts, run pre-start checks, evaluate RUNNING bots.
  Starting the worker never starts a bot: bots only trade after a user starts them.
* job loop - backtests, validations, maintenance jobs from the durable queue.
"""

from __future__ import annotations

import logging
import os
import signal
import socket
import threading
import time
import traceback
from datetime import timedelta
from typing import Any, cast

from sqlalchemy import delete
from sqlalchemy.engine import CursorResult

from app.bootstrap import bootstrap
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.db.base import session_scope, utcnow
from app.models import AuditLog, WorkerHeartbeat
from app.services.audit import audit, notify
from app.services.leases import LeaseUnavailable
from app.services.trading_runtime import run_tick
from app.workers import queue

log = logging.getLogger("worker")
WORKER_ID = f"{socket.gethostname()}:{os.getpid()}"
stop_event = threading.Event()


def heartbeat(info: dict[str, Any]) -> None:
    with session_scope() as s:
        hb = s.get(WorkerHeartbeat, WORKER_ID)
        if hb is None:
            s.add(WorkerHeartbeat(worker_id=WORKER_ID, last_seen=utcnow(), info=info))
        else:
            hb.last_seen, hb.info = utcnow(), info


def trading_loop() -> None:
    st = get_settings()
    consecutive_errors = 0
    while not stop_event.is_set():
        started = time.monotonic()
        info: dict[str, Any] = {}
        try:
            info = run_tick()
            consecutive_errors = 0
        except LeaseUnavailable:
            info = {"skipped": "trading lease busy"}
        except Exception as exc:  # noqa: BLE001 - keep the loop alive, but make the failure loud
            consecutive_errors += 1
            log.exception("trading tick failed")
            try:
                with session_scope() as s:
                    audit(
                        s,
                        "system",
                        f"trading tick failed: {exc}",
                        "error",
                        data={"trace": traceback.format_exc()[-2000:]},
                    )
                    if consecutive_errors >= 3:
                        notify(
                            s,
                            "critical_error",
                            f"worker trading tick failing repeatedly: {exc}",
                            "critical",
                            dedupe_window=timedelta(minutes=15),
                        )
            except Exception:  # noqa: BLE001 - DB itself may be down
                log.exception("could not record tick failure")
        try:
            heartbeat(
                {"tick": info, "pid": os.getpid(), "tick_seconds": round(time.monotonic() - started, 2)}
            )
        except Exception:  # noqa: BLE001
            log.exception("heartbeat failed")
        stop_event.wait(max(0.2, st.worker_tick_seconds - (time.monotonic() - started)))


def handle_job(job_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    from app.services.backtest_service import execute_run, execute_validation

    if job_type == "backtest":
        return execute_run(payload["run_id"])
    if job_type == "backtest_validation":
        return execute_validation(payload["run_id"], payload["kind"], payload.get("params") or {})
    if job_type == "retention":
        days = get_settings().audit_retention_days
        with session_scope() as s:
            res = s.execute(
                delete(AuditLog).where(
                    AuditLog.created_at < utcnow() - timedelta(days=days),
                    AuditLog.category.in_(("strategy_evaluation", "system")),
                )
            )
            n = cast(CursorResult, res).rowcount
        return {"deleted": n}
    raise ValueError(f"unknown job type {job_type}")


def job_loop() -> None:
    st = get_settings()
    last_retention = 0.0
    while not stop_event.is_set():
        try:
            if time.monotonic() - last_retention > 24 * 3600:
                with session_scope() as s:
                    queue.enqueue(
                        s, "retention", {}, idempotency_key=f"retention:{utcnow().date().isoformat()}"
                    )
                last_retention = time.monotonic()
            job = queue.claim(WORKER_ID)
        except Exception:  # noqa: BLE001 - e.g. network/DB outage; retry instead of killing the thread
            log.exception("job queue unavailable")
            stop_event.wait(st.worker_job_poll_seconds)
            continue
        if job is None:
            stop_event.wait(st.worker_job_poll_seconds)
            continue
        try:
            res = handle_job(job.type, job.payload)
            queue.complete(job.id, res)
        except Exception as exc:  # noqa: BLE001
            log.exception("job %s failed", job.id)
            try:
                queue.fail(job.id, f"{type(exc).__name__}: {exc}")
            except Exception:  # noqa: BLE001 - the job lease expires and it is retried
                log.exception("could not mark job %s failed", job.id)


def main() -> None:
    st = get_settings()
    configure_logging(st.log_level)
    bootstrap()
    log.info("worker %s starting (tick %.1fs)", WORKER_ID, st.worker_tick_seconds)
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop_event.set())
    loops = {"trading": trading_loop, "jobs": job_loop}
    threads = {name: threading.Thread(target=fn, name=name, daemon=True) for name, fn in loops.items()}
    for t in threads.values():
        t.start()
    while not stop_event.is_set():
        stop_event.wait(1.0)
        for name, t in list(threads.items()):
            if not t.is_alive() and not stop_event.is_set():
                log.error("worker loop %s died; restarting it", name)
                threads[name] = threading.Thread(target=loops[name], name=name, daemon=True)
                threads[name].start()
    log.info("worker stopping")
    for t in threads.values():
        t.join(timeout=30)


if __name__ == "__main__":
    main()
