import json
import os
import subprocess

from sqlmodel import select

from conftest import reset_caches
from shoebox import backup, db, services
from shoebox.db import session_scope
from shoebox.ingest import run_ingest
from shoebox.models import EventKind, Photo


def tag_some():
    with session_scope() as s:
        photos = s.exec(select(Photo).order_by(Photo.rel_path)).all()
        xmas = s.exec(select(EventKind).where(EventKind.name == "Christmas")).one()
        services.bulk_update(s, [photos[0].id, photos[1].id], date="1984", event_kind_id=xmas.id)
        services.bulk_update(s, [photos[2].id], date="1990s", caption="Grandpa's boat")
        services.bulk_update(s, [photos[3].id], rotate_by=270)


def normalized(truth):
    truth = dict(truth)
    truth.pop("generated_at")
    return json.dumps(truth, sort_keys=True)


def test_truth_round_trip_into_fresh_database(album_tree, env, tmp_path, monkeypatch):
    run_ingest()
    tag_some()
    before = backup.build_truth()
    backup.run_backup("test")
    assert (env.backup_dir / "truth" / "truth.json").exists()

    # Simulate losing the live volume: new empty data dir, same photos and backup.
    monkeypatch.setenv("SHOEBOX_DATA_DIR", str(tmp_path / "data2"))
    reset_caches()
    db.migrate()
    run_ingest()
    truth = json.loads((env.backup_dir / "truth" / "truth.json").read_text())
    result = backup.restore_truth(truth)
    assert result == {"matched": 4, "unmatched": []}
    assert normalized(backup.build_truth()) == normalized(before)


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
