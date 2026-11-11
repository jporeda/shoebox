"""A face that's one of two people, not known which (twins): "Ana or Ben"."""
import json

import numpy as np
import pytest
from sqlmodel import select

from conftest import api_client, make_image, reset_caches
from shoebox import backup, db, people
from shoebox.db import session_scope
from shoebox.export import run_export
from shoebox.faces import DetectedFace
from shoebox.ingest import run_ingest
from shoebox.matching import load_exemplars
from shoebox.models import Face, Person, Photo
from shoebox.people import merge_detections
from test_faces import tags

rng = np.random.default_rng(3)


def det(x, y=0.1):
    v = rng.normal(size=512)
    return DetectedFace(x, y, 0.2, 0.25, 0.9, (v / np.linalg.norm(v)).astype(np.float32), 30.0)


@pytest.fixture
def faces(env):
    """Three photos with one detected face each; returns the face ids by photo."""
    for i in range(3):
        make_image(env.photos_root / "A1" / f"p{i}.jpg", i)
    run_ingest()
    with session_scope() as s:
        for p in s.exec(select(Photo).order_by(Photo.rel_path)).all():
            merge_detections(s, p, [det(0.1)], "r")
        return [f.id for f in s.exec(select(Face).order_by(Face.photo_id)).all()]


def name_pair(c, face_ids):
    """Ana on face 0 (to have both people), then face_ids as "Ana or Ben"."""
    ana = c.post("/api/faces/assign", json={"face_ids": face_ids, "name": "Ana"}).json()["person"]
    res = c.post("/api/faces/assign", json={"face_ids": face_ids, "person_id": ana["id"], "alt_name": "Ben"})
    assert res.status_code == 200, res.text
    ben = next(p for p in c.get("/api/people").json() if p["name"] == "Ben")
    return ana, ben


def test_a_face_can_be_one_of_two_people_and_shows_under_both(faces):
    with api_client() as c:
        ana, ben = name_pair(c, [faces[0]])
        f = c.get(f"/api/people/{ana['id']}/faces").json()
        assert [(x["id"], x["person_name"], x["alt_name"]) for x in f] == [(faces[0], "Ana", "Ben")]
        assert [x["id"] for x in c.get(f"/api/people/{ben['id']}/faces").json()] == [faces[0]]
        people_by_name = {p["name"]: p for p in c.get("/api/people").json()}
        assert people_by_name["Ben"]["face_count"] == 1 == people_by_name["Ana"]["face_count"]
        # The grid's people filter finds the photo under either name.
        for pid in (ana["id"], ben["id"]):
            assert c.get("/api/photos", params={"people": str(pid)}).json()["total"] == 1
            assert c.get("/api/photos", params={"person_id": pid}).json()["total"] == 1
        # The same person twice, or a second person without a first, is refused.
        assert c.post("/api/faces/assign", json={"face_ids": [faces[1]], "person_id": ana["id"], "alt_person_id": ana["id"]}).status_code == 422
        assert c.post("/api/faces/assign", json={"face_ids": [faces[1]], "alt_person_id": ben["id"]}).status_code == 422


def test_resolving_who_it_is(faces):
    with api_client() as c:
        ana, ben = name_pair(c, faces)
        # "It's Ben": a plain naming clears the second person.
        c.post("/api/faces/assign", json={"face_ids": [faces[0]], "person_id": ben["id"]})
        # "Not Ana" (from Ana's page) leaves Ben, and Ana is never suggested again.
        c.post("/api/faces/reject", json={"face_ids": [faces[1]], "person_id": ana["id"]})
        # "Neither" clears both.
        c.post("/api/faces/reject", json={"face_ids": [faces[2]]})
    with session_scope() as s:
        f0, f1, f2 = (s.get(Face, i) for i in faces)
        assert (f0.person_id, f0.alt_person_id) == (ben["id"], None)
        assert (f1.person_id, f1.alt_person_id, f1.rejected_person_ids) == (ben["id"], None, [ana["id"]])
        assert (f2.person_id, f2.alt_person_id, sorted(f2.rejected_person_ids)) == (None, None, sorted([ana["id"], ben["id"]]))


def test_undo_restores_the_pair_and_removes_the_new_person(faces):
    with api_client() as c:
        ana, _ = name_pair(c, [faces[0]])
        c.post("/api/undo", json={})  # undoes "or Ben", and Ben, whom it created
        assert [p["name"] for p in c.get("/api/people").json()] == ["Ana"]
    with session_scope() as s:
        f = s.get(Face, faces[0])
        assert (f.person_id, f.alt_person_id) == (ana["id"], None)


def test_one_of_two_faces_teach_the_matcher_nothing(faces):
    with api_client() as c:
        ana, _ = name_pair(c, [faces[0]])
        c.post("/api/faces/assign", json={"face_ids": [faces[1]], "person_id": ana["id"]})
    with session_scope() as s:
        ex = load_exemplars(list(s.exec(select(Face)).all()), {})
        assert list(ex.person_ids) == [ana["id"]]  # only the face known to be Ana
        assert set(people.person_centroids(s)) == {ana["id"]}


def test_merging_one_twin_into_the_other_leaves_just_one(faces):
    with api_client() as c:
        ana, ben = name_pair(c, [faces[0]])
        c.post(f"/api/people/{ben['id']}/merge", json={"into_id": ana["id"]})
    with session_scope() as s:
        f = s.get(Face, faces[0])
        assert (f.person_id, f.alt_person_id) == (ana["id"], None)


def test_export_names_the_pair_together_and_truth_round_trips(faces, env, tmp_path, monkeypatch):
    with api_client() as c:
        name_pair(c, [faces[0]])
    run_export()
    index = json.loads((env.backup_dir / "export" / "export-index.json").read_text())
    with session_scope() as s:
        photo_id = s.get(Face, faces[0]).photo_id
    out = env.backup_dir / "export" / index[str(photo_id)]["path"]
    t = tags(out, "XMP-mwg-rs:RegionInfo", "XMP-iptcExt:PersonInImage")
    assert t["XMP-mwg-rs:RegionInfo"]["RegionList"][0]["Name"] == "Ana or Ben"
    assert t["XMP-iptcExt:PersonInImage"] in ("Ana or Ben", ["Ana or Ben"])  # never Ana or Ben alone

    backup.run_backup("t")
    truth = json.loads((env.backup_dir / "truth" / "truth.json").read_text())
    monkeypatch.setenv("SHOEBOX_DATA_DIR", str(tmp_path / "fresh"))
    reset_caches()
    db.migrate()
    run_ingest()
    backup.restore_truth(truth)
    with session_scope() as s:
        names = {p.id: p.name for p in s.exec(select(Person)).all()}
        f = s.exec(select(Face)).one()
        assert (names[f.person_id], names[f.alt_person_id]) == ("Ana", "Ben")


def test_people_list_their_known_twins(faces):
    with api_client() as c:
        ana, ben = name_pair(c, [faces[0]])
        cy = c.post("/api/faces/assign", json={"face_ids": [faces[1]], "name": "Cy"}).json()["person"]
        twins = {p["name"]: p["twin_ids"] for p in c.get("/api/people").json()}
        assert twins == {"Ana": [ben["id"]], "Ben": [ana["id"]], "Cy": []}
        # Once nobody is "one of the two" any more, they're no longer offered as a pair.
        c.post("/api/faces/assign", json={"face_ids": [faces[0]], "person_id": ana["id"]})
        assert all(p["twin_ids"] == [] for p in c.get("/api/people").json())
        assert cy["twin_ids"] == []


def test_a_pair_is_always_named_in_one_order(faces, env):
    with api_client() as c:
        ana, ben = name_pair(c, [faces[0]])
        # Named the other way round: still stored, shown and exported as "Ana or Ben".
        c.post("/api/faces/assign", json={"face_ids": [faces[1]], "person_id": ben["id"], "alt_person_id": ana["id"]})
    with session_scope() as s:
        for i in faces[:2]:
            f = s.get(Face, i)
            assert (f.person_id, f.alt_person_id) == (ana["id"], ben["id"])
        f = s.get(Face, faces[1])
        f.person_id, f.alt_person_id = ben["id"], ana["id"]  # stored the other way before this rule
        s.add(f)
    run_export()
    index = json.loads((env.backup_dir / "export" / "export-index.json").read_text())
    with session_scope() as s:
        photo_id = s.get(Face, faces[1]).photo_id
    t = tags(env.backup_dir / "export" / index[str(photo_id)]["path"], "XMP-mwg-rs:RegionInfo")
    assert t["XMP-mwg-rs:RegionInfo"]["RegionList"][0]["Name"] == "Ana or Ben"
