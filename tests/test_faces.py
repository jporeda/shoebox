import json
import subprocess

import numpy as np
import pytest
from sqlmodel import select

from conftest import api_client, make_image, reset_caches
from shoebox import backup, db, services
from shoebox.db import session_scope
from shoebox.export import run_export
from shoebox.faces import DetectedFace, iou, rotate_box
from shoebox.ingest import run_ingest
from shoebox.models import Face, Person, Photo
from shoebox.people import merge_detections, run_cluster, run_faces

rng = np.random.default_rng(0)
A, B = rng.normal(size=512), rng.normal(size=512)


def emb(base, noise=0.25):
    v = base + rng.normal(size=512) * noise * np.linalg.norm(base) / np.sqrt(512)
    return (v / np.linalg.norm(v)).astype(np.float32)


def det(x, y, w=0.2, h=0.25, base=A):
    return DetectedFace(x, y, w, h, 0.9, emb(base), 30.0)


def test_rotate_box_and_iou():
    box = (0.1, 0.2, 0.3, 0.4)
    assert rotate_box(box, 0, 90) == pytest.approx((1 - 0.2 - 0.4, 0.1, 0.4, 0.3))
    assert rotate_box(rotate_box(box, 0, 270), 270, 0) == pytest.approx(box)
    assert rotate_box(box, 90, 90) == box
    assert iou(box, box) == pytest.approx(1.0) and iou(box, (0.6, 0.7, 0.1, 0.1)) == 0


@pytest.fixture
def photos(env):
    root = env.photos_root
    for i in range(4):
        make_image(root / "A1" / f"p{i}.jpg", i, size=(320, 240))
    run_ingest()
    with session_scope() as s:
        return [p.id for p in s.exec(select(Photo).order_by(Photo.rel_path)).all()]


def detector_for(mapping):
    """Fake detector keyed by image content (first pixel) -> detections."""
    def detect(rgb):
        return mapping.get(tuple(int(v) for v in rgb[5, 5]), [])
    return detect


def test_redetection_keeps_names_and_drops_stale_unnamed(photos):
    with session_scope() as s:
        p = s.get(Photo, photos[0])
        merge_detections(s, p, [det(0.1, 0.1), det(0.6, 0.1)], "r1")
    with session_scope() as s:
        faces = s.exec(select(Face).order_by(Face.x)).all()
        person = Person(name="Rose")
        s.add(person)
        s.flush()
        faces[0].person_id, faces[0].source = person.id, "manual"
        s.add(faces[0])
    with session_scope() as s:  # second run: first face moved slightly, second face gone
        result = merge_detections(s, s.get(Photo, photos[0]), [det(0.12, 0.11)], "r2")
        assert result == {"added": 0, "updated": 1, "removed": 1}
        f = s.exec(select(Face)).one()
        assert f.person_id is not None and f.source == "manual" and f.x == pytest.approx(0.12)


def test_run_faces_and_cluster(photos):
    calls = iter([[det(0.1, 0.1, base=A)], [det(0.1, 0.1, base=A), det(0.6, 0.1, base=B)], [det(0.3, 0.2, base=B)], []])
    result = run_faces(detector=lambda rgb: next(calls))
    assert result["photos"] == 4 and result["faces_added"] == 4 and "seconds_per_photo" in result
    assert run_faces(detector=lambda rgb: [])["photos"] == 0  # stage marker: nothing left to do
    c = run_cluster()
    assert c["clusters"] == 2 and c["singletons"] == 0
    with session_scope() as s:
        by_cluster = {}
        for f in s.exec(select(Face)).all():
            by_cluster.setdefault(f.cluster_id, set()).add(round(float(np.frombuffer(f.embedding, np.float32) @ (A / np.linalg.norm(A))), 0))
        assert len(by_cluster) == 2


def test_naming_flow_via_api(photos):
    calls = iter([[det(0.1, 0.1, base=A)], [det(0.1, 0.1, base=A), det(0.6, 0.1, base=B)], [det(0.3, 0.2, base=A)], []])
    run_faces(detector=lambda rgb: next(calls))
    run_cluster()
    with api_client() as client:
        clusters = client.get("/api/clusters").json()
        assert [c["size"] for c in clusters] == [3]
        ids = [f["id"] for f in clusters[0]["faces"]]
        res = client.post("/api/faces/assign", json={"face_ids": ids, "name": "Sam"}).json()
        sam = res["person"]
        assert sam["name"] == "Sam" and sam["face_count"] == 3
        assert client.get("/api/clusters").json() == []
        single = client.get("/api/clusters", params={"singles": True}).json()[0]["faces"]
        assert len(single) == 1
        # suggestion for a new look-alike group comes from Sam's faces
        client.post("/api/undo", json={})
        assert client.get("/api/people").json() == []  # undoing the naming removes the person it added
        sam = client.post("/api/faces/assign", json={"face_ids": ids[:2], "name": "Sam"}).json()["person"]
        run_cluster()
        c = client.get("/api/clusters").json()
        assert c == [] or c[0]["suggestion"] is None or c[0]["suggestion"]["name"] == "Sam"
        # photos filter and person page
        assert client.get("/api/photos", params={"person_id": sam["id"]}).json()["total"] == 2
        assert len(client.get(f"/api/people/{sam['id']}/faces").json()) == 2
        # ignore the stranger
        client.post("/api/faces/ignore", json={"face_ids": [single[0]["id"]]})
        assert client.get("/api/faces/stats").json()["ignored"] == 1
        # draw a missed face and name it; delete drawn
        drawn = client.post(f"/api/photos/{photos[3]}/faces", json={"x": 0.2, "y": 0.2, "w": 0.2, "h": 0.2, "name": "Rose"}).json()
        assert drawn["person_name"] == "Rose" and drawn["source"] == "drawn"
        assert client.delete(f"/api/faces/{drawn['id']}").json() == {"deleted": True}
        # merge people
        rose = client.post("/api/people", json={"name": "Rosie"}).json()
        client.post("/api/faces/assign", json={"face_ids": [ids[2]], "person_id": rose["id"]})
        merged = client.post(f"/api/people/{rose['id']}/merge", json={"into_id": sam["id"]}).json()
        assert merged["person"]["face_count"] == 3
        assert client.patch(f"/api/people/{sam['id']}", json={"birth_year": 1975}).json()["birth_year"] == 1975
        assert client.get(f"/api/face/{ids[0]}.jpg").headers["content-type"] == "image/jpeg"


def tags(path, *names):
    out = subprocess.run(["exiftool", "-j", "-G1", "-struct", *[f"-{n}" for n in names], str(path)],
                         check=True, capture_output=True, text=True).stdout
    return json.loads(out)[0]


def test_export_regions_people_and_raw_frame_sidecar(photos, env):
    with session_scope() as s:
        p = s.get(Photo, photos[0])
        merge_detections(s, p, [det(0.1, 0.2, 0.2, 0.3)], "r")
        f = s.exec(select(Face)).one()
        person = Person(name="Smith, Jo")
        s.add(person)
        s.flush()
        f.person_id, f.source = person.id, "manual"
        s.add(f)
        services.bulk_update(s, [p.id], date="1985", rotate_by=90)
    run_export()
    index = json.loads((env.backup_dir / "export" / "export-index.json").read_text())
    out = env.backup_dir / "export" / index[str(photos[0])]["path"]
    t = tags(out, "XMP-mwg-rs:RegionInfo", "XMP-iptcExt:PersonInImage", "IPTC:Keywords", "XMP-lr:HierarchicalSubject")
    region = t["XMP-mwg-rs:RegionInfo"]
    assert region["AppliedToDimensions"] == {"W": 240, "H": 320, "Unit": "pixel"}  # baked 90°
    r = region["RegionList"][0]
    assert r["Name"] == "Smith, Jo" and r["Type"] == "Face"
    # box (0.1,0.2,0.2,0.3) at rotation 0 -> rotated 90° cw: (0.5, 0.1, 0.3, 0.2); centre (0.65, 0.2)
    assert (r["Area"]["X"], r["Area"]["Y"], r["Area"]["W"], r["Area"]["H"]) == pytest.approx((0.65, 0.2, 0.3, 0.2), abs=1e-4)
    assert t["XMP-iptcExt:PersonInImage"] in ("Smith, Jo", ["Smith, Jo"])
    assert "Smith, Jo" in t["IPTC:Keywords"] and "People|Smith, Jo" in t["XMP-lr:HierarchicalSubject"]
    side = env.backup_dir / "sidecars" / "A1" / "p0.jpg.xmp"
    sr = tags(side, "XMP-mwg-rs:RegionInfo")["XMP-mwg-rs:RegionInfo"]
    assert sr["AppliedToDimensions"] == {"W": 320, "H": 240, "Unit": "pixel"}  # original's raw pixels
    a = sr["RegionList"][0]["Area"]
    assert (a["X"], a["Y"]) == pytest.approx((0.2, 0.35), abs=1e-4)


def test_truth_round_trip_restores_faces(photos, env, tmp_path, monkeypatch):
    with session_scope() as s:
        p = s.get(Photo, photos[1])
        merge_detections(s, p, [det(0.1, 0.1), det(0.6, 0.1)], "r")
        a, b = s.exec(select(Face).order_by(Face.x)).all()
        rose = Person(name="Rose", birth_year=1950)
        s.add(rose)
        s.flush()
        a.person_id, a.source = rose.id, "manual"
        b.ignored = True
        s.add_all([a, b, Face(photo_id=p.id, x=0.3, y=0.6, w=0.1, h=0.1, source="drawn", person_id=rose.id)])
    backup.run_backup("t")
    monkeypatch.setenv("SHOEBOX_DATA_DIR", str(tmp_path / "fresh"))
    reset_caches()
    db.migrate()
    run_ingest()
    with session_scope() as s:  # detector finds the two faces again, slightly moved
        merge_detections(s, s.exec(select(Photo).where(Photo.rel_path == "A1/p1.jpg")).one(), [det(0.11, 0.1), det(0.61, 0.1)], "r")
    backup.restore_truth(json.loads((env.backup_dir / "truth" / "truth.json").read_text()))
    with session_scope() as s:
        faces = s.exec(select(Face).order_by(Face.x)).all()
        rose = s.exec(select(Person)).one()
        assert rose.birth_year == 1950
        assert [(round(f.x, 2), f.person_id == rose.id, f.ignored, f.source) for f in faces] == [
            (0.11, True, False, "manual"), (0.3, True, False, "drawn"), (0.61, False, True, "auto")]


def test_drawn_faces_and_new_people_can_be_undone(album_tree, env):
    run_ingest()
    with session_scope() as s:
        pid = s.exec(select(Photo).order_by(Photo.id)).first().id

    def state():
        with session_scope() as s:
            faces = s.exec(select(Face).where(Face.photo_id == pid)).all()
            return [(round(f.x, 2), f.person_id) for f in faces], sorted(p.name for p in s.exec(select(Person)).all())

    box = {"x": 0.1, "y": 0.1, "w": 0.2, "h": 0.2}
    with api_client() as c:
        c.post(f"/api/photos/{pid}/faces", json={**box, "name": "Zed"})
        assert state()[1] == ["Zed"]
        assert c.post("/api/undo", json={}).json()["reverted"] == 2
        assert state() == ([], [])  # the box and the person it created are both gone

        face_id = c.post(f"/api/photos/{pid}/faces", json=box).json()["id"]
        c.post("/api/faces/assign", json={"face_ids": [face_id], "name": "Yan"})
        c.post("/api/undo", json={})
        assert state() == ([(0.1, None)], [])

        c.delete(f"/api/faces/{face_id}")
        assert state() == ([], [])
        c.post("/api/undo", json={})
        assert state() == ([(0.1, None)], [])
