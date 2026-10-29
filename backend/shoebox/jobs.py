"""DB-backed job queue with a single worker process.

Handlers are idempotent and track per-photo stage markers, so a job interrupted
by a crash or restart is simply re-run from the queue and skips finished work.
"""

from __future__ import annotations

import json
import logging
import time
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from sqlmodel import select

from .db import migrate, session_scope
from .jobs_ctx import JobCancelled, JobContext
from .models import Job, Photo, utcnow

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
POLL_SECONDS = 1.0


def _ingest(ctx: JobContext, params: dict) -> dict:
    from .ingest import run_ingest

    result = run_ingest(ctx)
    # Dates first (what you review), then the slower auto-rotate pass.
    if params.get("read_backs", True):
        enqueue("read_backs")
    enqueue("estimate")
    if params.get("orient", True):
        enqueue("orient")
    return result


def _orient(ctx: JobContext, params: dict) -> dict:
    from .orient import run_orient

    result = run_orient(ctx)
    enqueue("faces")  # detect on the corrected orientation
    return result


def _faces(ctx: JobContext, params: dict) -> dict:
    from .people import run_embed_missing, run_faces

    result = run_faces(ctx)
    result["drawn"] = run_embed_missing(ctx)
    enqueue("cluster")
    enqueue("estimate")  # new faces may match named people
    return result


def _cluster(ctx: JobContext, params: dict) -> dict:
    from .people import run_cluster

    return run_cluster(ctx)


def _read_backs(ctx: JobContext, params: dict) -> dict:
    from .backs import run_read_backs

    result = run_read_backs(ctx)
    enqueue("estimate")
    return result


def _claude_backs(ctx: JobContext, params: dict) -> dict:
    from .vision import run_claude_backs

    return run_claude_backs(ctx, scope=params.get("scope", "unread"))


def _estimate(ctx: JobContext, params: dict) -> dict:
    from .estimate import run_estimates
    from .matching import run_matching

    # Names first: a newly named person with a birth year can date the photo by age.
    names = run_matching(ctx)
    dates = run_estimates(ctx)
    return {**dates, "names": names}


def _evaluate(ctx: JobContext, params: dict) -> dict:
    from .evaluate import run_evaluate

    return run_evaluate(ctx, fraction=params.get("fraction", 0.2), seed=params.get("seed", 7))


def _export(ctx: JobContext, params: dict) -> dict:
    from .export import run_export

    return run_export(ctx, sidecars=params.get("sidecars", True))


def _backup(ctx: JobContext, params: dict) -> dict:
    from .backup import run_backup

    return run_backup(params.get("reason", "manual"))


def _fixity(ctx: JobContext, params: dict) -> dict:
    from .backup import run_fixity

    return run_fixity(ctx)


def _restore(ctx: JobContext, params: dict) -> dict:
    from .backup import latest_truth_path, restore_truth

    path = Path(params["path"]) if params.get("path") else latest_truth_path()
    return {**restore_truth(json.loads(path.read_text(encoding="utf-8")), ctx), "file": path.name}


HANDLERS: dict[str, Callable[[JobContext, dict], dict]] = {
    "ingest": _ingest,
    "orient": _orient,
    "read_backs": _read_backs,
    "faces": _faces,
    "cluster": _cluster,
    "estimate": _estimate,
    "evaluate": _evaluate,
    "claude_backs": _claude_backs,
    "export": _export,
    "backup": _backup,
    "fixity": _fixity,
    "restore": _restore,
}
NO_BACKUP_AFTER = {"backup", "fixity", "cluster", "evaluate"}


def enqueue(job_type: str, params: dict | None = None) -> Job:
    if job_type not in HANDLERS:
        raise ValueError(f"unknown job type {job_type}")
    with session_scope() as s:
        existing = s.exec(
            select(Job).where(Job.type == job_type, Job.state == "queued")
        ).first()
        if existing and existing.params == (params or {}):
            return existing
        job = Job(type=job_type, params=params or {})
        s.add(job)
        s.flush()
        return job


def request_cancel(job_id: int) -> None:
    with session_scope() as s:
        job = s.get(Job, job_id)
        if job and job.state in ("queued", "running"):
            job.state = "cancelled" if job.state == "queued" else "cancelling"
            s.add(job)


def _claim() -> int | None:
    with session_scope() as s:
        job = s.exec(select(Job).where(Job.state == "queued").order_by(Job.id)).first()
        if job is None:
            return None
        job.state = "running"
        job.attempts += 1
        job.started_at = utcnow()
        job.heartbeat_at = utcnow()
        job.error = None
        s.add(job)
        return job.id


def run_job(job_id: int) -> None:
    with session_scope() as s:
        job = s.get(Job, job_id)
        job_type, params = job.type, dict(job.params or {})
    last = [0.0]

    def report(done: int, total: int, message: str) -> None:
        now = time.monotonic()
        if now - last[0] < 0.5 and done not in (0, total):
            return
        last[0] = now
        with session_scope() as s:
            j = s.get(Job, job_id)
            j.progress, j.total, j.message, j.heartbeat_at = done, total, message[:300], utcnow()
            s.add(j)

    def cancelled() -> bool:
        with session_scope() as s:
            return s.get(Job, job_id).state == "cancelling"

    ctx = JobContext(job_id, report, cancelled)
    try:
        result = HANDLERS[job_type](ctx, params)
        if job_type not in NO_BACKUP_AFTER:
            from .backup import run_backup

            result = {**result, "backup": run_backup(f"after-{job_type}")}
        state, error = "done", None
    except JobCancelled:
        result, state, error = {}, "cancelled", None
    except Exception:
        log.exception("job %s (%s) failed", job_id, job_type)
        result, state, error = {}, "failed", traceback.format_exc()[-4000:]
    with session_scope() as s:
        j = s.get(Job, job_id)
        j.state, j.result, j.error, j.finished_at = state, result, error, utcnow()
        if state == "done":
            j.progress = j.total
            j.message = "done"
        s.add(j)


def recover_interrupted() -> int:
    """Requeue jobs a crashed worker left running (handlers resume via stage markers)."""
    n = 0
    with session_scope() as s:
        for job in s.exec(select(Job).where(Job.state.in_(("running", "cancelling")))).all():
            if job.state == "cancelling":
                job.state = "cancelled"
            elif job.attempts >= MAX_ATTEMPTS:
                job.state, job.error = "failed", "gave up after repeated interruptions"
            else:
                job.state, job.message = "queued", "resuming after restart"
                n += 1
            s.add(job)
    return n


def _maybe_schedule_fixity() -> None:
    from .backup import fixity_due

    with session_scope() as s:
        pending = s.exec(select(Job).where(Job.type == "fixity", Job.state.in_(("queued", "running")))).first()
        has_photos = s.exec(select(Photo.id).limit(1)).first() is not None
    if has_photos and not pending and fixity_due():
        enqueue("fixity", {"scheduled": True})


def worker_loop() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    migrate()
    log.info("worker started; requeued %d interrupted job(s)", recover_interrupted())
    last_schedule_check = datetime.min.replace(tzinfo=timezone.utc)
    while True:
        job_id = _claim()
        if job_id is not None:
            log.info("running job %s", job_id)
            run_job(job_id)
            continue
        if utcnow() - last_schedule_check > timedelta(hours=1):
            last_schedule_check = utcnow()
            try:
                _maybe_schedule_fixity()
            except Exception:
                log.exception("fixity scheduling failed")
        time.sleep(POLL_SECONDS)
