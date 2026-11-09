"""Known backup, restore and migration bugs, reproduced. Each test states the behaviour we want
and is a strict xfail until its fix lands: the fix removes the marker (and may move the test
next to test_backup.py).

IDs are from the team review (J = Jordan, M = Morgan); "plan" is the item in the team plan.
"""

import json
import shutil
import sqlite3

import pytest
from alembic import command
from sqlmodel import select

from conftest import api_client, drain, lose_live_volume, require, reset_caches
from shoebox import backup, config, db, jobs, services
from shoebox.db import session_scope
from shoebox.ingest import run_ingest
from shoebox.models import EventInstance, EventKind, Job, Photo


def truth_file(env):
    return env.backup_dir / "truth" / "truth.json"


@pytest.mark.xfail(strict=True, raises=AssertionError,
                   reason="M-B1 / plan 1.1: A partial Restore shrinks truth.json for good")
def test_partial_restore_keeps_every_truth_entry(album_tree, env, tmp_path, monkeypatch):
    jobs.enqueue("ingest", {"orient": False, "read_backs": False})
    drain()
    with session_scope() as s:
        services.bulk_update(s, list(s.exec(select(Photo.id)).all()), date="1984")
    jobs.enqueue("backup")
    drain()
    full = {e["sha256"] for e in json.loads(truth_file(env).read_text())["photos"]}
    require(len(full) == 6, f"setup: six photos in truth.json, got {len(full)}")

    # Live volume lost; this time only one album is mounted (or Import was cancelled).
    lose_live_volume(tmp_path, monkeypatch)
    parked = tmp_path / "parked"
    shutil.move(str(album_tree / "Album 1990s"), str(parked))
    jobs.enqueue("ingest", {"orient": False, "read_backs": False})
    drain()
    restore = jobs.enqueue("restore")
    drain()
    with session_scope() as s:
        job = s.get(Job, restore.id)
        require(job.state == "done" and job.result["matched"] == 3, f"setup: partial restore ran: {job.state} {job.error}")
    jobs.enqueue("backup")
    drain()
    kept = {e["sha256"] for e in json.loads(truth_file(env).read_text())["photos"]}
    assert full <= kept, f"truth.json lost {len(full - kept)} of {len(full)} entries after a partial restore"

    # The album comes back: Import and Restore again must date every photo.
    shutil.move(str(parked), str(album_tree / "Album 1990s"))
    jobs.enqueue("ingest", {"orient": False, "read_backs": False})
    drain()
    jobs.enqueue("restore")
    drain()
    with session_scope() as s:
        undated = s.exec(select(Photo.rel_path).where(Photo.t_year == None)).all()  # noqa: E711
    assert not undated, f"still undated after the second restore: {undated}"


@pytest.mark.xfail(strict=True, raises=AssertionError,
                   reason="J-B9 = M-B2 / plan 1.2: A date that came from an event is lost on Restore once the event is cleared")
def test_photo_whose_event_was_cleared_restores_with_the_date_it_shows(album_tree, env, tmp_path, monkeypatch):
    run_ingest()
    with session_scope() as s:
        photos = s.exec(select(Photo).order_by(Photo.rel_path)).all()
        first, second, rel = photos[0].id, photos[1].id, photos[1].rel_path
        xmas = s.exec(select(EventKind).where(EventKind.name == "Christmas")).one()
        services.bulk_update(s, [first], date="1984", event_kind_id=xmas.id)  # creates "Christmas 1984"
        inst = s.exec(select(EventInstance).where(EventInstance.label == "Christmas 1984")).one()
        services.bulk_update(s, [second], event_instance_id=inst.id)  # dated by the event only
        services.bulk_update(s, [second], clear_event=True)  # the event is removed again
    with session_scope() as s:
        p = s.get(Photo, second)
        shown = (p.t_year, p.t_month, p.t_day)
    require(shown == (1984, 12, 25), f"setup: the app still shows the event's date, got {shown}")
    backup.run_backup("test")

    lose_live_volume(tmp_path, monkeypatch)
    run_ingest()
    backup.restore_truth(json.loads(truth_file(env).read_text()))
    with session_scope() as s:
        p = s.exec(select(Photo).where(Photo.rel_path == rel)).one()
        assert (p.t_year, p.t_month, p.t_day) == shown, "Restore disagrees with the date the app showed"


@pytest.mark.xfail(strict=True, raises=AssertionError,
                   reason="J-B5 / plan 1.3: Restore records one undo batch per photo; one undo reverts one photo")
def test_restore_is_undone_in_one_step(album_tree, env):
    run_ingest()
    with session_scope() as s:
        ids = list(s.exec(select(Photo.id).order_by(Photo.rel_path)).all())[:3]
        services.bulk_update(s, ids, date="1984")
    backup.run_backup("test")
    truth = json.loads(truth_file(env).read_text())
    with session_scope() as s:
        services.bulk_update(s, ids, date="1999")  # newer work the restore overwrites
    backup.restore_truth(truth)
    with session_scope() as s:
        require({s.get(Photo, i).t_year for i in ids} == {1984}, "setup: the restore applied 1984")

    with api_client() as client:
        require(client.post("/api/undo", json={}).status_code == 200, "setup: undo accepted")
    with session_scope() as s:
        years = [s.get(Photo, i).t_year for i in ids]
    assert years == [1999] * 3, f"one undo after a restore gives {years}"


@pytest.mark.xfail(strict=True, raises=AssertionError,
                   reason="J-B6 = M-S1 / plan 1.5: A failed reference check after migrating doesn't roll the upgrade back")
def test_failed_reference_check_leaves_database_on_its_old_revision(env, tmp_path, monkeypatch):
    old = "c430b98f0b32"  # a database from before the face_suggestions migration
    monkeypatch.setenv("SHOEBOX_DATA_DIR", str(tmp_path / "old"))
    reset_caches()
    command.upgrade(db.alembic_config(), old)
    conn = sqlite3.connect(config.get_settings().db_path)
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute("INSERT INTO face (photo_id, x, y, w, h, frame_rotation, source, ignored) "
                 "VALUES (999, 0, 0, 0.1, 0.1, 0, 'auto', 0)")  # a dangling reference
    conn.commit()
    conn.close()

    with pytest.raises(RuntimeError, match="broken reference"):
        db.migrate()
    assert db.current_revision() == old, "the upgrade was committed although its check failed"
    with pytest.raises(RuntimeError, match="broken reference"):
        db.migrate()  # the next container start must refuse again, not carry on silently
