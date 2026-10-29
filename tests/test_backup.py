import json
import os
import subprocess

from sqlmodel import select

import pytest

from conftest import reset_caches
from shoebox import backup, db, jobs, services
from shoebox.db import get_meta, session_scope
from shoebox.ingest import run_ingest
from shoebox.models import EventInstance, EventKind, Job, Meta, Photo


def tag_some():
    with session_scope() as s:
        photos = s.exec(select(Photo).order_by(Photo.rel_path)).all()
        xmas = s.exec(select(EventKind).where(EventKind.name == "Christmas")).one()
        services.bulk_update(s, [photos[0].id, photos[1].id], date="1984", event_kind_id=xmas.id)
        services.bulk_update(s, [photos[2].id], date="1990s", caption="Grandpa's boat")
        services.bulk_update(s, [photos[3].id], rotate_by=270)
        # Dated only by its event (no date of its own): derived, not stored in truth.json.
        xmas84 = s.exec(select(EventInstance).where(EventInstance.label == "Christmas 1984")).one()
        services.bulk_update(s, [photos[4].id], event_instance_id=xmas84.id)


def event_dated_photo():
    with session_scope() as s:
        p = s.exec(select(Photo).order_by(Photo.rel_path)).all()[4]
        return p.t_year, p.t_month, p.t_day, p.t_source


def drain():
    while (job_id := jobs._claim()) is not None:
        jobs.run_job(job_id)


def lose_live_volume(tmp_path, monkeypatch, name="data2"):
    """New empty data dir; same photos and backup folder."""
    monkeypatch.setenv("SHOEBOX_DATA_DIR", str(tmp_path / name))
    reset_caches()
    db.migrate()


def normalized(truth):
    truth = dict(truth)
    truth.pop("generated_at")
    truth.pop("db_id")
    return json.dumps(truth, sort_keys=True)


def test_truth_round_trip_into_fresh_database(album_tree, env, tmp_path, monkeypatch):
    run_ingest()
    tag_some()
    before = backup.build_truth()
    backup.run_backup("test")
    assert (env.backup_dir / "truth" / "truth.json").exists()

    lose_live_volume(tmp_path, monkeypatch)
    run_ingest()
    truth = json.loads((env.backup_dir / "truth" / "truth.json").read_text())
    result = backup.restore_truth(truth)
    assert result == {"matched": 5, "unmatched": []}
    assert normalized(backup.build_truth()) == normalized(before)
    assert event_dated_photo() == (1984, 12, 25, "event")


def test_rebuild_through_job_queue_restores_everything(album_tree, env, tmp_path, monkeypatch):
    """The README procedure: lose the volume, Import (which backs up), then Restore."""
    jobs.enqueue("ingest", {"orient": False, "read_backs": False})
    drain()
    tag_some()
    jobs.enqueue("backup")
    drain()
    before = backup.build_truth()
    truth_file = env.backup_dir / "truth" / "truth.json"
    saved = truth_file.read_text()

    lose_live_volume(tmp_path, monkeypatch)
    jobs.enqueue("ingest", {"orient": False, "read_backs": False})
    drain()
    assert truth_file.read_text() == saved  # the empty database didn't overwrite it

    restore = jobs.enqueue("restore")
    drain()
    with session_scope() as s:
        job = s.get(Job, restore.id)
        assert job.state == "done", job.error
        assert (job.result["matched"], job.result["unmatched"]) == (5, [])
        new_id = get_meta(s, "db_id")
    assert normalized(backup.build_truth()) == normalized(before)
    assert event_dated_photo() == (1984, 12, 25, "event")
    # The restored database now owns truth.json and keeps it current.
    assert json.loads(truth_file.read_text())["db_id"] == new_id


def test_restore_refuses_before_import_and_on_no_match(album_tree, env, tmp_path, monkeypatch):
    run_ingest()
    tag_some()
    backup.run_backup("test")
    truth = json.loads((env.backup_dir / "truth" / "truth.json").read_text())

    lose_live_volume(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="Run Import first"):
        backup.restore_truth(truth)
    run_ingest()
    for e in truth["photos"]:
        e.update(sha256="0" * 64, image_data_hash=None, rel_path="elsewhere/" + e["rel_path"])
    with pytest.raises(ValueError, match="nothing was restored"):
        backup.restore_truth(truth)


def test_restore_falls_back_to_newest_history_with_entries(album_tree, env):
    run_ingest()
    tag_some()
    backup.run_backup("test")
    truth_dir = env.backup_dir / "truth"
    (truth_dir / "truth.json").write_text(json.dumps({"format_version": 1, "photos": []}))
    assert backup.latest_truth_path().parent.name == "history"


def test_truth_from_before_database_ids(album_tree, env, tmp_path, monkeypatch):
    """A truth.json with no db_id belongs to the database that already has photos, not a new one."""
    run_ingest()
    tag_some()
    backup.run_backup("test")
    truth_file = env.backup_dir / "truth" / "truth.json"
    legacy = json.loads(truth_file.read_text())
    legacy.pop("db_id")
    truth_file.write_text(json.dumps(legacy))
    with session_scope() as s:
        for key in ("db_id", "truth_adopted"):
            if (row := s.get(Meta, key)) is not None:
                s.delete(row)
    db.seed()  # what the upgrade does: this database has photos, so it adopts the old file
    services_caption(0, "updated")
    assert backup.export_truth() is not None

    legacy_text = truth_file.read_text()
    legacy = json.loads(legacy_text)
    legacy.pop("db_id")
    truth_file.write_text(json.dumps(legacy))
    lose_live_volume(tmp_path, monkeypatch)
    run_ingest()
    assert backup.export_truth() is None  # a fresh database adopts nothing


def services_caption(index, text):
    with session_scope() as s:
        photos = s.exec(select(Photo).order_by(Photo.rel_path)).all()
        services.bulk_update(s, [photos[index].id], caption=text)


def test_manifest_verifies_with_shasum(album_tree, env):
    run_ingest()
    manifest = backup.write_manifest()
    proc = subprocess.run(["sha256sum", "-c", str(manifest)], cwd=album_tree, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert proc.stdout.count(": OK") == 9  # 6 fronts + 2 paired backs + 1 loose back


def test_fixity_reports_changed_missing_new(album_tree, env):
    run_ingest()
    (album_tree / "Album 1990s/scan 2.jpg").unlink()
    (album_tree / "Album 1980s/IMG_0002.jpg").write_bytes(b"corrupted")
    (album_tree / "Album 1990s/new.jpg").write_bytes((album_tree / "Album 1980s/IMG_0001.jpg").read_bytes())
    result = backup.run_fixity()
    assert result["changed"] == ["Album 1980s/IMG_0002.jpg"]
    assert result["missing"] == ["Album 1990s/scan 2.jpg"]
    assert result["new_files"] == ["Album 1990s/new.jpg"]
    assert not backup.fixity_due()


def test_snapshots_pruned_and_consistent(env, monkeypatch):
    monkeypatch.setenv("SHOEBOX_SNAPSHOT_KEEP", "2")
    reset_caches()
    paths = []
    for i in range(4):
        paths.append(backup.snapshot(f"t{i}"))
        os.utime(paths[-1])
    remaining = sorted((env.backup_dir / "db").glob("shoebox-*.db"))
    assert len(remaining) == 2
    import sqlite3

    conn = sqlite3.connect(remaining[-1])
    assert conn.execute("pragma integrity_check").fetchone()[0] == "ok"
    conn.close()


def test_snapshot_retention_thins_out_with_age_and_keeps_pre_upgrade(env):
    from datetime import datetime, timedelta

    out = env.backup_dir / "db"
    out.mkdir(parents=True, exist_ok=True)
    now = datetime(2026, 10, 28, 12)
    regular = []
    for hours in range(0, 24 * 500, 6):  # a snapshot every 6 hours for 500 days
        name = f"shoebox-{(now - timedelta(hours=hours)).strftime('%Y%m%d-%H%M%S-%f')}-after-estimate.db"
        (out / name).touch()
        regular.append(out / name)
    old_upgrade = out / f"shoebox-{(now - timedelta(days=499)).strftime('%Y%m%d-%H%M%S-%f')}-pre-upgrade-abc.db"
    old_upgrade.touch()

    backup.prune_snapshots(out, keep_recent=3)
    kept = sorted(p for p in out.glob("*.db") if "pre-upgrade" not in p.name)
    assert old_upgrade.exists()
    assert all(p.exists() for p in regular[:3])  # the newest few
    months = {p.name[8:14] for p in kept}
    assert len(months) == 12  # one per month for a year
    assert not any(p.exists() for p in regular if p.name[8:14] < min(months))
    assert len(kept) <= 3 + 24 + 14 + 8 + 12


def test_pre_upgrade_snapshot_is_reused_while_the_db_is_unchanged(env):
    first = backup.pre_upgrade_snapshot("abc123")
    assert backup.pre_upgrade_snapshot("abc123") == first  # e.g. a failing upgrade, restarted
    with session_scope() as s:
        db.set_meta(s, "touched", True)
    assert backup.pre_upgrade_snapshot("abc123") != first
