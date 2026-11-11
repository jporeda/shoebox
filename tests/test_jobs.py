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


def test_upgrade_can_rebuild_a_table_other_rows_reference(env, tmp_path):
    """A future migration that rebuilds `photo` (batch mode) must work once faces exist."""
    from alembic import command

    from shoebox.models import Album, Face

    with session_scope() as s:
        album = Album(rel_path="A", name="A")
        s.add(album)
        s.flush()
        photo = Photo(sha256="x" * 64, rel_path="A/x.jpg", album_id=album.id)
        s.add(photo)
        s.flush()
        s.add(Face(photo_id=photo.id, x=0, y=0, w=0.1, h=0.1))
    versions = tmp_path / "versions"
    versions.mkdir()
    (versions / "zz_rebuild_photo.py").write_text(f'''
import sqlalchemy as sa
from alembic import op

revision = "zz_rebuild_photo"
down_revision = "{db.head_revision()}"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("photo", recreate="always") as batch:
        batch.add_column(sa.Column("zz_note", sa.String(), nullable=True))


def downgrade():
    pass
''')
    cfg = db.alembic_config()
    cfg.set_main_option("version_locations", f"{db.MIGRATIONS_DIR / 'versions'} {versions}")
    command.upgrade(cfg, "heads")
    with db.get_engine().connect() as conn:
        assert conn.exec_driver_sql("SELECT count(*) FROM face JOIN photo ON face.photo_id = photo.id").scalar() == 1
        assert conn.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1  # enforcement is back on


def test_snapshots_after_jobs_are_at_most_one_per_setting_minutes(album_tree, env):
    import os
    import time

    from conftest import api_client
    from shoebox.backup import SNAPSHOT_GAP_DEFAULT

    def snaps():
        return sorted((env.backup_dir / "db").glob("shoebox-*-after-*.db"))

    def run(job_type):
        job = jobs.enqueue(job_type)
        jobs.run_job(jobs._claim())
        with session_scope() as s:
            result = s.get(Job, job.id).result
            return result.get("backup", result)  # the Backup job's own result is the backup

    assert SNAPSHOT_GAP_DEFAULT == 30
    run("estimate")
    assert len(snaps()) == 1  # the first one is always taken
    second = run("estimate")
    assert second["snapshot"].startswith("skipped") and len(snaps()) == 1
    truth = env.backup_dir / "truth" / "truth.json"
    assert truth.exists()  # truth.json is still written after every job
    old = time.time() - 31 * 60  # half an hour later...
    os.utime(snaps()[0], (old, old))
    run("estimate")
    assert len(snaps()) == 2
    with api_client() as c:  # 0: after every job, as before
        assert c.put("/api/settings", json={"snapshot_gap_minutes": 0}).json()["snapshot_gap_minutes"] == 0
        assert c.put("/api/settings", json={"snapshot_gap_minutes": -1}).status_code == 422
    run("estimate")
    assert len(snaps()) == 3
    # A manual backup always takes one.
    with api_client() as c:
        c.put("/api/settings", json={"snapshot_gap_minutes": 30})
    run("backup")
    assert len(list((env.backup_dir / "db").glob("shoebox-*-manual.db"))) == 1
