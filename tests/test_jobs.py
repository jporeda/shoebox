from sqlmodel import select

from shoebox import db, jobs
from shoebox.backup import snapshot
from shoebox.db import session_scope
from shoebox.jobs_ctx import JobCancelled, JobContext
from shoebox.ingest import run_ingest
from shoebox.models import Job, Photo
from shoebox.orient import best_rotation, run_orient


def test_job_runs_and_backs_up(album_tree, env):
    job = jobs.enqueue("ingest", {"orient": False})
    claimed = jobs._claim()
    assert claimed == job.id
    jobs.run_job(job.id)
    with session_scope() as s:
        j = s.get(Job, job.id)
        assert j.state == "done", j.error
        assert j.result["fronts_seen"] == 6
        assert "snapshot" in j.result["backup"]
    assert (env.backup_dir / "manifest-sha256.txt").exists()


def test_interrupted_job_is_requeued_and_resumes(album_tree, env):
    # First run is cancelled part-way through, as if the worker died.
    calls = {"n": 0}

    def cancel_after_three():
        calls["n"] += 1
        return calls["n"] > 3

    try:
        run_ingest(JobContext(None, None, cancel_after_three))
    except JobCancelled:
        pass
    with session_scope() as s:
        partial = len(s.exec(select(Photo)).all())
        s.add(Job(type="ingest", state="running", attempts=1, params={"orient": False}))
    assert 0 < partial < 6
    assert jobs.recover_interrupted() == 1
    job_id = jobs._claim()
    jobs.run_job(job_id)
    with session_scope() as s:
        assert s.get(Job, job_id).state == "done"
        rel_paths = [p.rel_path for p in s.exec(select(Photo)).all()]
    assert len(rel_paths) == len(set(rel_paths)) == 6


def test_gives_up_after_repeated_interruptions(env):
    with session_scope() as s:
        s.add(Job(type="backup", state="running", attempts=jobs.MAX_ATTEMPTS))
    assert jobs.recover_interrupted() == 0
    with session_scope() as s:
        assert s.exec(select(Job)).one().state == "failed"


def test_enqueue_dedupes_queued_jobs(env):
    a = jobs.enqueue("export")
    b = jobs.enqueue("export")
    assert a.id == b.id


def test_migration_snapshots_before_upgrade(env):
    from alembic import command

    command.downgrade(db.alembic_config(), "base")
    assert db.current_revision() is None
    db.migrate()  # empty DB: no snapshot needed
    before = set((env.backup_dir / "db").glob("*pre-upgrade*")) if (env.backup_dir / "db").exists() else set()
    assert db.current_revision() == db.head_revision()
    # Pretend the DB is one revision behind by stamping a fake older revision.
    snapshot("unrelated")
    with db.get_engine().begin() as conn:
        conn.exec_driver_sql("UPDATE alembic_version SET version_num='0000old'")
    try:
        db.migrate()
    except Exception:
        pass  # alembic can't find the fake revision; the snapshot must exist anyway
    after = set((env.backup_dir / "db").glob("*pre-upgrade-0000old*"))
    assert len(after - before) == 1


def test_best_rotation_needs_clear_margin():
    assert best_rotation({0: 0.0, 90: 0.9, 180: 0.0, 270: 0.1}, 0.25) == 90
    assert best_rotation({0: 0.8, 90: 0.9, 180: 0.0, 270: 0.1}, 0.25) == 0
    assert best_rotation({0: 0.0, 90: 0.0, 180: 0.0, 270: 0.0}, 0.25) == 0


def test_orient_skips_manual_rotation(album_tree, env):
    run_ingest()
    with session_scope() as s:
        photos = s.exec(select(Photo).order_by(Photo.id)).all()
        manual = photos[0]
        manual.rotation, manual.rotation_source = 180, "manual"
        s.add(manual)
    # Fake scorer: claims faces only when the image is rotated 270.
    import numpy as np

    def scorer(arr: np.ndarray) -> float:
        return 1.0 if arr.shape[0] > arr.shape[1] else 0.0  # portrait = rotated 90/270

    result = run_orient(scorer=scorer)
    assert result["checked"] == 6
    with session_scope() as s:
        ps = s.exec(select(Photo).order_by(Photo.id)).all()
        assert (ps[0].rotation, ps[0].rotation_source) == (180, "manual")
        assert all(p.rotation == 90 and p.rotation_source == "auto" for p in ps[1:])
