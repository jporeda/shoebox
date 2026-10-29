import shutil
import subprocess

from sqlmodel import select

from conftest import make_image
from shoebox import services
from shoebox.db import session_scope
from shoebox.ingest import run_ingest
from shoebox.models import Album, LooseBack, Photo


def photos_by_path():
    with session_scope() as s:
        return {p.rel_path: p for p in s.exec(select(Photo)).all()}


def test_scan_pairs_backs_and_reports(album_tree):
    report = run_ingest()
    assert report["albums"] == 2
    assert report["backs_paired"] == 2
    assert report["orphan_backs"] == ["Album 1980s/IMG_0099_back.jpg"]
    assert report["backlike_unmatched"] == ["Album 1990s/backyard.jpg"]
    photos = photos_by_path()
    assert report["loose_backs"] == 1
    # a back with no front becomes a loose back, not a photo
    assert set(photos) == {
        "Album 1980s/IMG_0001.jpg", "Album 1980s/IMG_0002.jpg", "Album 1980s/IMG_0010.jpg",
        "Album 1990s/scan 1.jpg", "Album 1990s/scan 2.jpg", "Album 1990s/backyard.jpg",
    }
    with session_scope() as s:
        lb = s.exec(select(LooseBack)).one()
        assert (lb.rel_path, lb.list_index) == ("Album 1980s/IMG_0099_back.jpg", 4)
    # positions count every file in the folder, backs included
    assert [photos[f"Album 1980s/IMG_00{n}.jpg"].list_index for n in ("01", "02", "10")] == [0, 2, 3]
    assert photos["Album 1980s/IMG_0001.jpg"].back_rel_path == "Album 1980s/IMG_0001_back.jpg"
    assert photos["Album 1990s/scan 1.jpg"].back_rel_path == "Album 1990s/scan 1 reverse.jpg"
    # natural sort: 0001, 0002, 0010, 0099
    assert [photos[f"Album 1980s/IMG_00{n}.jpg"].album_index for n in ("01", "02", "10")] == [0, 1, 2]
    assert all(p.thumb_ver == 1 and p.phash for p in photos.values())


def test_reingest_is_idempotent(album_tree):
    run_ingest()
    before = {k: v.id for k, v in photos_by_path().items()}
    report = run_ingest()
    assert report["new"] == 0 and report["moved"] == 0 and report["missing"] == []
    assert {k: v.id for k, v in photos_by_path().items()} == before


def test_renamed_album_keeps_tags(album_tree):
    run_ingest()
    pid = photos_by_path()["Album 1980s/IMG_0002.jpg"].id
    with session_scope() as s:
        services.bulk_update(s, [pid], date="1984-12-25")
    shutil.move(album_tree / "Album 1980s", album_tree / "Grandma's album")
    report = run_ingest()
    assert report["new"] == 0 and report["moved"] == 3
    p = photos_by_path()["Grandma's album/IMG_0002.jpg"]
    assert p.id == pid and p.t_year == 1984 and p.t_day == 25
    with session_scope() as s:
        old = s.exec(select(Album).where(Album.rel_path == "Album 1980s")).one()
        assert old.missing


def test_metadata_only_edit_matched_by_image_hash(album_tree):
    run_ingest()
    before = photos_by_path()["Album 1990s/scan 2.jpg"]
    with session_scope() as s:
        services.bulk_update(s, [before.id], date="1993")
    subprocess.run(
        ["exiftool", "-overwrite_original", "-XMP-dc:Subject=edited elsewhere", str(album_tree / "Album 1990s/scan 2.jpg")],
        check=True, capture_output=True,
    )
    report = run_ingest()
    after = photos_by_path()["Album 1990s/scan 2.jpg"]
    assert report["new"] == 0
    assert after.id == before.id and after.sha256 != before.sha256 and after.t_year == 1993


def test_exact_duplicate_file_not_imported_twice(album_tree):
    shutil.copy(album_tree / "Album 1980s/IMG_0001.jpg", album_tree / "Album 1990s/copy.jpg")
    report = run_ingest()
    assert report["exact_duplicate_files"] == [{"path": "Album 1990s/copy.jpg", "same_as": "Album 1980s/IMG_0001.jpg"}]


def test_rescans_grouped_as_duplicates(album_tree):
    make_image(album_tree / "Album 1990s" / "rescan.jpg", 2, size=(640, 480), quality=60)
    report = run_ingest()
    assert report["duplicate_groups"] == 1
    photos = photos_by_path()
    assert photos["Album 1990s/rescan.jpg"].dup_group_id == photos["Album 1980s/IMG_0002.jpg"].dup_group_id is not None


def test_missing_file_flagged(album_tree):
    run_ingest()
    (album_tree / "Album 1990s/scan 2.jpg").unlink()
    report = run_ingest()
    assert report["missing"] == ["Album 1990s/scan 2.jpg"]
    assert photos_by_path()["Album 1990s/scan 2.jpg"].missing


def test_swapped_file_names_keep_their_tags(album_tree):
    run_ingest()
    a, b = "Album 1980s/IMG_0001.jpg", "Album 1980s/IMG_0002.jpg"
    with session_scope() as s:
        ids = photos_by_path()
        services.bulk_update(s, [ids[a].id], date="1971")
        services.bulk_update(s, [ids[b].id], date="1972")
    tmp = album_tree / "swap.tmp"
    (album_tree / a).rename(tmp)
    (album_tree / b).rename(album_tree / a)
    tmp.rename(album_tree / b)
    report = run_ingest()
    assert report["exact_duplicate_files"] == []
    after = photos_by_path()
    assert (after[a].id, after[a].t_year, after[a].missing) == (ids[b].id, 1972, False)
    assert (after[b].id, after[b].t_year, after[b].missing) == (ids[a].id, 1971, False)
