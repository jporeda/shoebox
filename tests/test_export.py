import json
import subprocess

from sqlmodel import select

from conftest import make_image
from shoebox import services
from shoebox.db import session_scope
from shoebox.export import run_export
from shoebox.ingest import run_ingest
from shoebox.models import EventKind, Photo


def pid(rel):
    with session_scope() as s:
        return s.exec(select(Photo).where(Photo.rel_path == rel)).one().id


def tags(path, *names):
    out = subprocess.run(
        ["exiftool", "-j", "-G1", "-struct", *[f"-{n}" for n in names], str(path)],
        check=True, capture_output=True, text=True,
    ).stdout
    return json.loads(out)[0]


def exported(env, photo_id):
    index = json.loads((env.backup_dir / "export" / "export-index.json").read_text())
    return env.backup_dir / "export" / index[str(photo_id)]["path"]


def test_partial_date_and_ordering_across_albums(album_tree, env):
    run_ingest()
    a = pid("Album 1980s/IMG_0001.jpg")
    b = pid("Album 1980s/IMG_0002.jpg")
    c = pid("Album 1990s/scan 1.jpg")
    with session_scope() as s:
        services.bulk_update(s, [a, b, c], date="1987")
    result = run_export()
    assert result["written"] == 3 and not result["failed"]

    t = tags(exported(env, a), "XMP-photoshop:DateCreated", "ExifIFD:DateTimeOriginal", "ExifIFD:OffsetTimeOriginal",
             "XMP-shoebox:all", "XMP-dc:Subject")
    assert str(t["XMP-photoshop:DateCreated"]) == "1987"  # partial date survives
    assert t["ExifIFD:DateTimeOriginal"] == "1987:07:01 09:00:00"
    assert t["ExifIFD:OffsetTimeOriginal"] == "-05:00"
    assert t["XMP-shoebox:DatePrecision"] == "year"
    assert "Album: Album 1980s" in t["XMP-dc:Subject"]
    times = [tags(exported(env, i), "ExifIFD:DateTimeOriginal")["ExifIFD:DateTimeOriginal"] for i in (a, b, c)]
    assert times == ["1987:07:01 09:00:00", "1987:07:01 09:00:01", "1987:07:01 09:00:02"]
    assert exported(env, a).name.startswith("1987-07-01_0000_Album-1980s_IMG-0001")


def test_utf8_event_and_estimated_keyword(album_tree, env):
    run_ingest()
    a = pid("Album 1980s/IMG_0001.jpg")
    with session_scope() as s:
        bday = s.exec(select(EventKind).where(EventKind.name == "Birthday")).one()
        services.bulk_update(s, [a], date="~1985", event_kind_id=bday.id, event_label="Zoë's 5th birthday")
    run_export()
    t = tags(exported(env, a), "IPTC:Keywords", "XMP-iptcExt:Event", "XMP-lr:HierarchicalSubject", "IPTC:CodedCharacterSet")
    assert "Zoë's 5th birthday" in t["IPTC:Keywords"]
    assert "date:estimated" in t["IPTC:Keywords"]
    assert t["XMP-iptcExt:Event"] == "Zoë's 5th birthday"
    assert "Events|Birthday|Zoë's 5th birthday" in t["XMP-lr:HierarchicalSubject"]
    assert t["IPTC:CodedCharacterSet"] == "UTF8"


def test_rotation_baked_losslessly(album_tree, env):
    run_ingest()
    a = pid("Album 1980s/IMG_0001.jpg")  # 320x240, MCU-aligned
    with session_scope() as s:
        services.bulk_update(s, [a], date="1987", rotate_by=90)
    run_export()
    t = tags(exported(env, a), "File:ImageWidth", "File:ImageHeight", "IFD0:Orientation", "XMP-shoebox:RotationBaked")
    assert (t["File:ImageWidth"], t["File:ImageHeight"]) == (240, 320)
    assert t.get("IFD0:Orientation") in (None, "Horizontal (normal)")
    assert t["XMP-shoebox:RotationBaked"] is True or t["XMP-shoebox:RotationBaked"] == "True"


def test_unaligned_jpeg_falls_back_to_orientation_tag(env):
    make_image(env.photos_root / "A" / "odd.jpg", 7, size=(301, 203))
    run_ingest()
    a = pid("A/odd.jpg")
    with session_scope() as s:
        services.bulk_update(s, [a], date="1990", rotate_by=90)
    result = run_export()
    assert result["rotation_tag_only"] == ["A/odd.jpg"]
    t = tags(exported(env, a), "IFD0:Orientation")
    assert t["IFD0:Orientation"] == "Rotate 90 CW"


def test_sidecar_and_originals_untouched(album_tree, env):
    run_ingest()
    a = pid("Album 1980s/IMG_0001.jpg")
    original = (album_tree / "Album 1980s/IMG_0001.jpg").read_bytes()
    with session_scope() as s:
        services.bulk_update(s, [a], date="1987-06", caption="Line one\nLine & two")
    run_export()
    side = env.backup_dir / "sidecars" / "Album 1980s" / "IMG_0001.jpg.xmp"
    t = tags(side, "XMP-exif:DateTimeOriginal", "XMP-photoshop:DateCreated", "XMP-dc:Description")
    assert t["XMP-exif:DateTimeOriginal"].startswith("1987:06:01 09:00:00")
    assert t["XMP-photoshop:DateCreated"] == "1987:06"
    assert t["XMP-dc:Description"] == "Line one\nLine & two"
    raw = subprocess.run(["exiftool", "-b", "-XMP", str(side)], capture_output=True, text=True).stdout
    assert "<photoshop:DateCreated>1987-06</photoshop:DateCreated>" in raw
    assert (album_tree / "Album 1980s/IMG_0001.jpg").read_bytes() == original


def test_reexport_skips_unchanged_and_removes_stale(album_tree, env):
    run_ingest()
    a = pid("Album 1980s/IMG_0001.jpg")
    with session_scope() as s:
        services.bulk_update(s, [a], date="1987")
    run_export()
    first = exported(env, a)
    assert run_export()["unchanged"] == 1
    with session_scope() as s:
        services.bulk_update(s, [a], date="1988")
    result = run_export()
    assert result["written"] == 1 and result["removed_stale"] == 1
    assert not first.exists() and exported(env, a).name.startswith("1988")


def test_changed_original_is_not_exported(album_tree, env):
    run_ingest()
    a = pid("Album 1980s/IMG_0001.jpg")
    with session_scope() as s:
        services.bulk_update(s, [a], date="1987")
    make_image(album_tree / "Album 1980s/IMG_0001.jpg", 999)  # tampered after import
    result = run_export()
    assert result["written"] == 0
    assert result["failed"][0]["error"] == "original failed fixity check"
