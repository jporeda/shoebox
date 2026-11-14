import json
import subprocess

import pytest
from sqlmodel import select

from conftest import make_image, reset_caches
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
    assert result["written"] == 6 and result["undated"] == 3 and not result["failed"]  # undated photos go too

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
        services.bulk_update(s, [a], date="~1985", event_kind_id=bday.id, event_label="Renée's 5th birthday")
    run_export()
    t = tags(exported(env, a), "IPTC:Keywords", "XMP-iptcExt:Event", "XMP-lr:HierarchicalSubject", "IPTC:CodedCharacterSet")
    assert "Renée's 5th birthday" in t["IPTC:Keywords"]
    assert "date:estimated" in t["IPTC:Keywords"]
    assert t["XMP-iptcExt:Event"] == "Renée's 5th birthday"
    assert "Events|Birthday|Renée's 5th birthday" in t["XMP-lr:HierarchicalSubject"]
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
    assert run_export()["unchanged"] == 6  # every front, dated or not
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
    assert result["written"] == result["planned"] - 1  # every photo but the tampered one
    assert result["failed"][0]["error"] == "original failed fixity check"


def test_failed_re_export_keeps_the_last_good_copy(album_tree, env, monkeypatch):
    from shoebox import export

    run_ingest()
    a = pid("Album 1980s/IMG_0001.jpg")
    with session_scope() as s:
        services.bulk_update(s, [a], date="1987")
    run_export()
    good = exported(env, a)
    with session_scope() as s:
        services.bulk_update(s, [a], date="1990")  # new name and contents next time

    def broken(*args, **kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(export, "_bake_rotation", broken)
    result = run_export()
    assert result["failed"] and result["removed_stale"] == 0
    assert good.exists() and exported(env, a) == good
    monkeypatch.undo()
    assert run_export()["written"] == 1  # retried once the problem is gone
    assert not good.exists() and "1990" in str(exported(env, a))


def test_undated_photos_are_exported_without_an_invented_date(album_tree, env):
    run_ingest()
    a = pid("Album 1980s/IMG_0002.jpg")
    with session_scope() as s:
        services.bulk_update(s, [a], caption="On the porch")
    result = run_export()
    assert result["undated"] == result["planned"] == 6 and not result["failed"]

    copy = exported(env, a)
    assert copy.relative_to(env.backup_dir / "export").as_posix() == "undated/Album-1980s_0002_IMG-0002.jpg"
    t = tags(copy, "ExifIFD:DateTimeOriginal", "XMP-photoshop:DateCreated", "XMP-dc:Subject", "XMP-dc:Description",
             "XMP-shoebox:DatePrecision")
    assert "ExifIFD:DateTimeOriginal" not in t and "XMP-photoshop:DateCreated" not in t  # no date made up
    assert "date:unknown" in t["XMP-dc:Subject"] and t["XMP-dc:Description"] == "On the porch"
    assert t["XMP-shoebox:DatePrecision"] == "unknown"

    with session_scope() as s:
        services.bulk_update(s, [a], date="1987")
    result = run_export()
    assert result["written"] == 1 and result["removed_stale"] == 1
    assert not copy.exists() and exported(env, a).name.startswith("1987")  # moved out of undated/


def test_place_is_set_in_bulk_suggested_backed_up_and_exported(album_tree, env):
    from conftest import api_client
    from shoebox import backup

    run_ingest()
    a, b = pid("Album 1980s/IMG_0001.jpg"), pid("Album 1980s/IMG_0002.jpg")
    with api_client() as c:
        body = {"ids": [a, b], "date": "1987", "country": "United States", "state": "Massachusetts", "city": " Salem "}
        assert c.patch("/api/photos", json=body).status_code == 200
        assert c.patch("/api/photos", json={"ids": [b], "city": ""}).status_code == 200  # clear one field only
        d = c.get(f"/api/photos/{b}").json()
        assert (d["country"], d["state"], d["city"]) == ("United States", "Massachusetts", None)
        assert sorted((p["city"] or "", p["count"]) for p in c.get("/api/places").json()) == [("", 1), ("Salem", 1)]

        truth = backup.build_truth()
        place = next(e["place"] for e in truth["photos"] if e.get("place", {}).get("city") == "Salem")
        assert place == {"country": "United States", "state": "Massachusetts", "city": "Salem"}

        run_export()
        t = tags(exported(env, a), "XMP-photoshop:Country", "XMP-photoshop:State", "XMP-photoshop:City",
                 "IPTC:City", "IPTC:Province-State", "XMP-lr:HierarchicalSubject", "IPTC:Keywords")
        assert (t["XMP-photoshop:Country"], t["XMP-photoshop:State"], t["XMP-photoshop:City"]) == (
            "United States", "Massachusetts", "Salem")
        assert (t["IPTC:City"], t["IPTC:Province-State"]) == ("Salem", "Massachusetts")
        assert "Places|United States|Massachusetts|Salem" in t["XMP-lr:HierarchicalSubject"]
        assert "Salem" in t["IPTC:Keywords"]

        c.post("/api/undo", json={})  # takes back clearing the city
        assert c.get(f"/api/photos/{b}").json()["city"] == "Salem"


def test_keep_both_exports_every_photo_in_the_duplicate_group(album_tree, env):
    make_image(album_tree / "Album 1990s" / "rescan.jpg", 2, size=(640, 480), quality=60)  # a rescan of IMG_0002
    run_ingest()
    a, b = pid("Album 1980s/IMG_0002.jpg"), pid("Album 1990s/rescan.jpg")
    run_export()
    index = json.loads((env.backup_dir / "export" / "export-index.json").read_text())
    assert (str(a) in index, str(b) in index) == (True, False)  # one file per group by default
    with session_scope() as s:
        services.keep_all(s, b)
    run_export()
    assert exported(env, a).exists() and exported(env, b).exists()


# --- map coordinates and the structured caption ---------------------------------------------

def _row(gid, name, ascii_name, lat, lon, cc, admin1, pop, fcode="PPL"):
    """One line of GeoNames' cities file (19 tab-separated columns)."""
    return "\t".join([str(gid), name, ascii_name, "", str(lat), str(lon), "P", fcode, cc, "", admin1, "", "", "",
                      str(pop), "", "", "", "2024-01-01"])


def num_tags(path, *names):
    """Like tags(), with numbers as numbers (-n): GPS in signed decimal degrees."""
    out = subprocess.run(["exiftool", "-j", "-n", "-G1", *[f"-{n}" for n in names], str(path)],
                         check=True, capture_output=True, text=True).stdout
    return json.loads(out)[0]


@pytest.fixture
def towns(env):
    """A tiny town list in GeoNames' format, where the app looks for it."""
    geo_dir = env.geonames_dir
    geo_dir.mkdir(parents=True, exist_ok=True)
    (geo_dir / "cities500.txt").write_text("\n".join([
        _row(1, "Wellfleet", "Wellfleet", 41.9376, -70.0331, "US", "MA", 3500),
        _row(2, "Salem", "Salem", 42.5195, -70.8967, "US", "MA", 41000),
        _row(3, "Salem", "Salem", 44.9429, -123.0351, "US", "OR", 160000),
        _row(4, "Montréal", "Montreal", 45.5088, -73.5878, "CA", "10", 1600000),
        _row(5, "Back Bay", "Back Bay", 42.35, -71.08, "US", "MA", 20000, fcode="PPLX"),  # a neighbourhood
    ]) + "\n", encoding="utf-8")
    (geo_dir / "admin1CodesASCII.txt").write_text(
        "US.MA\tMassachusetts\tMassachusetts\t1\nUS.OR\tOregon\tOregon\t2\nCA.10\tQuébec\tQuebec\t3\n", encoding="utf-8")
    (geo_dir / "countryInfo.txt").write_text(
        "#ISO\tISO3\tISO-Numeric\tfips\tCountry\n"
        "US\tUSA\t840\tUS\tUnited States\n"
        "CA\tCAN\t124\tCA\tCanada\n", encoding="utf-8")
    reset_caches()
    return geo_dir


def test_city_centres_disney_and_places_that_cant_be_pinned_down(towns):
    from shoebox import geo

    assert geo.locate(None, None, "Wellfleet") == (41.9376, -70.0331)  # one town of that name anywhere
    assert geo.locate("United States", "MA", "Salem") == (42.5195, -70.8967)  # state as its code
    assert geo.locate("USA", "massachusetts", " salem ") == (42.5195, -70.8967)  # case, spacing, aliases
    assert geo.locate("Canada", "Québec", "Montreal") == (45.5088, -73.5878)  # accents don't matter
    assert geo.locate("United States", None, "Salem") is None  # two Salems and no state: not guessed
    assert geo.locate(None, None, "Salem") is None
    assert geo.locate("Canada", None, "Salem") is None  # the country doesn't match
    assert geo.locate("Narnia", None, "Wellfleet") is None  # unknown country
    assert geo.locate(None, None, "Atlantis") is None  # unknown city
    assert geo.locate(None, None, "Back Bay") is None  # neighbourhoods aren't towns
    assert geo.locate("United States", "Massachusetts", None) is None  # no city: not on the map
    assert geo.locate("United States", "Florida", "Walt Disney World") == geo.DISNEY  # Disney anywhere
    assert geo.locate(None, "Disneyland", None) == geo.DISNEY
    assert geo.locate(None, None, "disney") == geo.DISNEY


def test_export_writes_gps_and_a_caption_of_place_event_text_and_faces(album_tree, env, towns):
    from conftest import api_client

    run_ingest()
    a, b, c = pid("Album 1980s/IMG_0001.jpg"), pid("Album 1980s/IMG_0002.jpg"), pid("Album 1990s/scan 1.jpg")
    with session_scope() as s:
        vacation = s.exec(select(EventKind).where(EventKind.name == "Vacation")).one()
        services.bulk_update(s, [a], date="1979-07", event_kind_id=vacation.id, event_label="Cape Cod",
                             caption="First day at the beach", country="United States", state="Massachusetts",
                             city="Wellfleet")
        services.bulk_update(s, [b], date="1979", city="Atlantis")  # can't be found
        services.bulk_update(s, [c], date="1990", city="Walt Disney World", state="Florida")
        width, height = s.get(Photo, a).width, s.get(Photo, a).height
    with api_client() as client:  # two named faces, left one first
        for x, name in ((0.5, "Dad"), (0.1, "Mom")):
            r = client.post(f"/api/photos/{a}/faces", json={"x": x, "y": 0.2, "w": 0.2, "h": 0.3, "name": name})
            assert r.status_code == 200, r.text

    result = run_export()
    assert not result["failed"]
    assert result["no_map_location"] == ["Atlantis"]

    t = num_tags(exported(env, a), "GPS:GPSLatitude", "GPS:GPSLongitude", "GPS:GPSLatitudeRef",
             "GPS:GPSLongitudeRef", "XMP-exif:GPSLatitude", "XMP-exif:GPSLongitude", "XMP-dc:Description",
             "IFD0:ImageDescription", "IPTC:Caption-Abstract", "IPTC:Keywords")
    assert (t["GPS:GPSLatitude"], t["GPS:GPSLongitude"]) == pytest.approx((41.9376, 70.0331))
    assert (t["GPS:GPSLatitudeRef"], t["GPS:GPSLongitudeRef"]) == ("N", "W")
    assert (t["XMP-exif:GPSLatitude"], t["XMP-exif:GPSLongitude"]) == pytest.approx((41.9376, -70.0331))
    caption = "\n".join([
        "Wellfleet, Massachusetts, United States",
        "Cape Cod",
        "First day at the beach",
        f"Mom - {round(0.2 * width)}, {round(0.35 * height)} px",
        f"Dad - {round(0.6 * width)}, {round(0.35 * height)} px",
    ])
    assert t["XMP-dc:Description"] == t["IFD0:ImageDescription"] == t["IPTC:Caption-Abstract"] == caption
    assert {"Mom", "Dad"} <= set(t["IPTC:Keywords"])  # people stay keywords

    t = num_tags(exported(env, b), "GPS:GPSLatitude", "XMP-dc:Description")
    assert "GPS:GPSLatitude" not in t and t["XMP-dc:Description"] == "Atlantis"
    t = num_tags(exported(env, c), "GPS:GPSLatitude", "GPS:GPSLongitude")
    assert (t["GPS:GPSLatitude"], t["GPS:GPSLongitude"]) == pytest.approx((28.419411, 81.5812))
