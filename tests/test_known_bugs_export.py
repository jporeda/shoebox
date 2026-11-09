"""Known Export bugs, reproduced. Each test states the behaviour we want and is a strict xfail
until its fix lands: the fix removes the marker (and may move the test next to test_export.py).

IDs are from the team review (J = Jordan, M = Morgan); "plan" is the item in the team plan.
"""

import json
import shutil

import pytest
from sqlmodel import select

from conftest import make_image, require
from shoebox import services
from shoebox.db import session_scope
from shoebox.export import run_export
from shoebox.ingest import run_ingest
from shoebox.models import Photo


def pid(rel):
    with session_scope() as s:
        return s.exec(select(Photo).where(Photo.rel_path == rel)).one().id


def export_index(env):
    return json.loads((env.backup_dir / "export" / "export-index.json").read_text())


def exported(env, photo_id):
    return env.backup_dir / "export" / export_index(env)[str(photo_id)]["path"]


def export_files(env):
    return sorted((env.backup_dir / "export").rglob("*.jpg"))


def date_all(year):
    with session_scope() as s:
        services.bulk_update(s, list(s.exec(select(Photo.id)).all()), date=year)


@pytest.mark.xfail(strict=True, raises=AssertionError,
                   reason="J-B1 / plan 1.6: Import of an empty photo root then Export deletes every copy")
def test_export_after_importing_an_empty_photo_root_keeps_existing_copies(album_tree, env):
    run_ingest()
    date_all("1987")
    run_export()
    require(len(export_files(env)) == 6, "setup: six dated photos exported")

    # The drive holding the scans isn't mounted: Docker hands the container an empty folder.
    shutil.move(str(env.photos_root), str(env.photos_root.parent / "unplugged"))
    env.photos_root.mkdir()
    run_ingest()
    result = run_export()

    assert len(export_files(env)) == 6, f"Export removed {result['removed_stale']} of 6 copies"


@pytest.mark.xfail(strict=True, raises=AssertionError,
                   reason="J-B2 / plan 3.1: Export deletes a copy it just wrote when it reuses another photo's old name")
def test_export_never_deletes_a_copy_it_just_wrote(env):
    # Two nested albums with the same folder name and scanner-style file names: after A is
    # re-dated, B is written under A's old name, then removed as A's stale file.
    root = env.photos_root
    make_image(root / "Box A" / "Summer" / "IMG_0001.jpg", 1)
    make_image(root / "Box B" / "Summer" / "IMG_0001.jpg", 2)
    run_ingest()
    a, b = pid("Box A/Summer/IMG_0001.jpg"), pid("Box B/Summer/IMG_0001.jpg")
    date_all("1987")
    run_export()
    with session_scope() as s:
        services.bulk_update(s, [a], date="1988")
    result = run_export()

    index = export_index(env)
    require({str(a), str(b)} <= index.keys(), f"setup: both photos in the export index: {index}")
    dangling = {k: v["path"] for k, v in index.items() if not (env.backup_dir / "export" / v["path"]).exists()}
    assert not dangling, f"index points at deleted files {dangling}; failed={result['failed']}"


@pytest.mark.xfail(strict=True, raises=AssertionError,
                   reason="J-B3 / plan 3.1: Re-dating one photo renames every later export file that day")
def test_redating_one_photo_does_not_rename_other_exports_that_day(album_tree, env):
    run_ingest()
    a, b, c = pid("Album 1980s/IMG_0001.jpg"), pid("Album 1980s/IMG_0002.jpg"), pid("Album 1980s/IMG_0010.jpg")
    with session_scope() as s:
        services.bulk_update(s, [a, b, c], date="1987")
    run_export()
    before = {i: exported(env, i).name for i in (b, c)}
    with session_scope() as s:
        services.bulk_update(s, [a], date="1988")  # only a changes
    run_export()

    after = {i: exported(env, i).name for i in (b, c)}
    assert after == before, "untouched photos were renamed (Immich sees a delete plus an add for each)"


@pytest.mark.xfail(strict=True, raises=AssertionError,
                   reason="J-B4 / plan 3.1: Sidecars for a renamed album's old path are never removed")
def test_renamed_album_leaves_no_stale_sidecars(album_tree, env):
    """The README suggests rsync'ing sidecars/ into PHOTO_ROOT; stale ones would land there too."""
    run_ingest()
    with session_scope() as s:
        services.bulk_update(s, [pid("Album 1980s/IMG_0001.jpg")], date="1987")
    run_export()
    old_sidecar = env.backup_dir / "sidecars" / "Album 1980s" / "IMG_0001.jpg.xmp"
    require(old_sidecar.exists(), "setup: sidecar written for the old path")
    shutil.move(album_tree / "Album 1980s", album_tree / "Renamed album")
    run_ingest()
    run_export()
    require((env.backup_dir / "sidecars" / "Renamed album" / "IMG_0001.jpg.xmp").exists(),
            "setup: sidecar written for the new path")

    assert not old_sidecar.exists(), "sidecar for the old path is still there"


@pytest.mark.xfail(strict=True, raises=AssertionError,
                   reason="J-B4 / plan 3.1: Clearing a date removes the export copy but leaves its sidecar")
def test_clearing_a_date_removes_the_sidecar_with_the_export_copy(album_tree, env):
    run_ingest()
    a = pid("Album 1980s/IMG_0001.jpg")
    with session_scope() as s:
        services.bulk_update(s, [a], date="1987")
    run_export()
    sidecar = env.backup_dir / "sidecars" / "Album 1980s" / "IMG_0001.jpg.xmp"
    require(sidecar.exists(), "setup: sidecar written")
    with session_scope() as s:
        services.bulk_update(s, [a], clear_date=True)
    require(run_export()["removed_stale"] == 1, "setup: the export copy is removed")

    assert not sidecar.exists(), "the sidecar still says 1987"
